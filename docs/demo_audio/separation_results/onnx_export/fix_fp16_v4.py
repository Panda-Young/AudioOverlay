# -*- coding: utf-8 -*-
"""
htdemucs fp16 迭代修复器 v4（报错驱动，鲁棒类型推导）：
v4 关键修正：
- Concat 有 int64 输入 → 全部转 int64（不是 fp32！因为输出通常用作 Reshape 等的 shape）
- 新增 INT64_INPUT_OPS：对必须为 int64 的输入（Reshape shape、Gather indices、Slice starts/ends 等）定向 Cast
- 打印完整 ORT 错误
- get_type_robust 追踪生产者推导类型
- 从 htdemucs_fp16.onnx 原始文件干净启动（v3 的 ok 文件有错误的 fp32 Concat cast）
"""
import os, time, re, sys
import numpy as np
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
SRC = os.path.join(BASE, "htdemucs_fp16.onnx")
OUT = os.path.join(BASE, "htdemucs_fp16_ok.onnx")

COMPUTE_OPS = {
    "Conv", "ConvTranspose", "MatMul", "Gemm", "Add", "Sub", "Mul", "Div",
    "Relu", "Sigmoid", "Tanh", "Softmax", "LayerNormalization", "BatchNormalization",
    "InstanceNormalization", "GroupNormalization", "GRU", "LSTM", "RNN", "Attention",
    "Gelu", "FastGelu", "Erf", "Exp", "Sqrt", "Pow", "Clip", "HardSigmoid", "PRelu",
    "AveragePool", "MaxPool", "GlobalAveragePool", "GlobalMaxPool",
}
SHAPE_OPS = {
    "Range", "Shape", "Reshape", "Gather", "Slice", "Pad", "ScatterND",
    "Unsqueeze", "Squeeze", "ConstantOfShape", "Expand", "Tile", "Split",
    "Where", "Equal", "Greater", "Less", "Mod", "TopK", "ArgMax", "ArgMin",
    "NonZero", "OneHot", "IsNaN", "IsInf", "Resize", "CumSum", "GatherElements",
    "GatherND", "ScatterElements", "Transpose", "Flatten", "Cast", "CastLike",
}

# 必须为 int64 的输入索引（op_type → [input_indices]）
# 这些输入是 shape / indices / axes / starts / ends 等，ORT 要求 int64（或 int32）
INT64_INPUTS = {
    "Reshape": [1],              # shape
    "ConstantOfShape": [0],      # shape
    "Expand": [1],                # shape
    "Tile": [1],                  # repeats
    "Slice": [1, 2, 3, 4],       # starts, ends, axes, steps (axes/steps may be empty)
    "Pad": [1],                   # pads
    "Gather": [1],                # indices
    "GatherElements": [1],        # indices
    "GatherND": [1],              # indices
    "ScatterND": [1],             # indices
    "ScatterElements": [1],       # indices
    "OneHot": [1],                # indices
    "TopK": [1],                  # K
    "Split": [1],                 # split (optional)
    "Resize": [3],                # sizes (roi=fp32, scales=fp32, sizes=int64)
    "CumSum": [1],                # axis (optional)
    "Unsqueeze": [1],             # axes (opset >= 13)
    "Squeeze": [1],               # axes (opset >= 13)
    "ConcatFromSequence": [1],    # axis
}

TYPE_NAME = {
    TensorProto.FLOAT: "fp32", TensorProto.FLOAT16: "fp16", TensorProto.DOUBLE: "fp64",
    TensorProto.INT32: "int32", TensorProto.INT64: "int64", TensorProto.INT8: "int8",
    TensorProto.UINT8: "uint8", TensorProto.BOOL: "bool", TensorProto.STRING: "string",
}

def type_str(t):
    if t is None: return "None"
    return TYPE_NAME.get(t, f"type_{t}")

def load_model(path):
    return onnx.load(path)

def find_producer(m, name):
    for n in m.graph.node:
        for o in n.output:
            if o == name:
                return n
    return None

def get_type_robust(m, name, depth=0):
    if not name or depth > 3:
        return None
    g = m.graph
    for i in g.input:
        if i.name == name and i.type.HasField("tensor_type"):
            return i.type.tensor_type.elem_type
    for o in g.output:
        if o.name == name and o.type.HasField("tensor_type"):
            return o.type.tensor_type.elem_type
    for vi in g.value_info:
        if vi.name == name and vi.type.HasField("tensor_type"):
            return vi.type.tensor_type.elem_type
    for init in g.initializer:
        if init.name == name:
            return init.data_type
    prod = find_producer(m, name)
    if prod is not None:
        if prod.op_type == "Constant":
            for a in prod.attribute:
                if a.name == "value" and a.HasField("t"):
                    return a.t.data_type
            return None
        elif prod.op_type == "Cast":
            for a in prod.attribute:
                if a.name == "to":
                    return a.i
            return None
        elif prod.op_type == "Shape":
            return TensorProto.INT64
        elif prod.op_type == "Size":
            return TensorProto.INT64
        elif prod.op_type in ("Equal", "Greater", "Less", "GreaterOrEqual", "LessOrEqual", "And", "Or", "Not", "IsNaN", "IsInf"):
            return TensorProto.BOOL
        elif prod.op_type == "Where":
            if len(prod.input) > 1 and prod.input[1]:
                return get_type_robust(m, prod.input[1], depth+1)
            return None
        elif prod.op_type in ("Concat", "Add", "Sub", "Mul", "Div", "Max", "Min"):
            for inp in prod.input:
                if inp:
                    t = get_type_robust(m, inp, depth+1)
                    if t is not None:
                        return t
            return None
        elif prod.op_type in ("Reshape", "Transpose", "Squeeze", "Unsqueeze", "Flatten", "Expand", "Tile", "Slice", "Pad", "Identity"):
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type in ("Gather", "GatherElements", "GatherND", "ScatterND", "ScatterElements", "Split"):
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type == "Range":
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type == "ConstantOfShape":
            for a in prod.attribute:
                if a.name == "value" and a.HasField("t"):
                    return a.t.data_type
            return TensorProto.FLOAT
        elif prod.op_type == "OneHot":
            if len(prod.input) > 2 and prod.input[2]:
                return get_type_robust(m, prod.input[2], depth+1)
            return TensorProto.FLOAT
        elif prod.op_type == "TopK":
            for idx, o in enumerate(prod.output):
                if o == name:
                    if idx == 0:
                        if prod.input and prod.input[0]:
                            return get_type_robust(m, prod.input[0], depth+1)
                    else:
                        return TensorProto.INT64
            return None
        if prod.input and prod.input[0]:
            return get_type_robust(m, prod.input[0], depth+1)
    return None

def set_type(m, name, t):
    g = m.graph
    for vi in g.value_info:
        if vi.name == name:
            vi.type.tensor_type.elem_type = t
            return
    g.value_info.append(helper.make_tensor_value_info(name, t, None))

def try_ort(path):
    try:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        return True, None, sess
    except Exception as e:
        return False, str(e), None

def find_node(m, name):
    for n in m.graph.node:
        if n.name == name:
            return n
    return None

def dump_node_inputs(m, node):
    print(f"  --- node {node.op_type} '{node.name}' inputs debug ---", flush=True)
    for i, inp in enumerate(node.input):
        if not inp:
            print(f"  input[{i}]: <empty>", flush=True)
            continue
        t = get_type_robust(m, inp)
        prod = find_producer(m, inp)
        prod_info = f"producer={prod.op_type}('{prod.name}')" if prod else "producer=None(graph_io/init)"
        is_init = any(init.name == inp for init in m.graph.initializer)
        print(f"  input[{i}] name='{inp}' type={type_str(t)} {prod_info} is_init={is_init}", flush=True)
    print(f"  --- end debug ---", flush=True)

def fix_node(m, node, cast_no):
    fixes = 0
    g = m.graph

    def cast_in(i, inp, to):
        nonlocal fixes
        safe_name = (node.name or node.op_type).replace('/', '_')
        out_name = f"fix_{cast_no[0]}_{safe_name}_{i}"
        cast_no[0] += 1
        c = helper.make_node("Cast", [inp], [out_name], to=to, name=out_name)
        g.node.append(c)
        node.input[i] = out_name
        set_type(m, out_name, to)
        fixes += 1

    op = node.op_type

    # 1. 优先处理必须为 int64 的输入（shape / indices / axes 等）
    if op in INT64_INPUTS:
        for idx in INT64_INPUTS[op]:
            if idx < len(node.input) and node.input[idx]:
                t = get_type_robust(m, node.input[idx])
                if t not in (TensorProto.INT64, TensorProto.INT32, None):
                    cast_in(idx, node.input[idx], TensorProto.INT64)
                elif t is None:
                    # 类型未知，保守转 int64（这些输入必须是整数）
                    cast_in(idx, node.input[idx], TensorProto.INT64)

    if op == "Range":
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type_robust(m, inp)
            if t != TensorProto.FLOAT:
                cast_in(i, inp, TensorProto.FLOAT)
        for o in node.output:
            ot = get_type_robust(m, o)
            if ot == TensorProto.FLOAT16:
                safe_name = (node.name or '').replace('/', '_')
                out2 = f"fix_{cast_no[0]}_{safe_name}_out"
                cast_no[0] += 1
                c = helper.make_node("Cast", [o], [out2], to=TensorProto.FLOAT16, name=out2)
                g.node.append(c)
                set_type(m, out2, TensorProto.FLOAT16)
                node.output[0] = out2
                fixes += 1
    elif op in COMPUTE_OPS:
        for i, inp in enumerate(node.input):
            if not inp: continue
            # 跳过已被 INT64_INPUTS 处理的索引
            if op in INT64_INPUTS and i in INT64_INPUTS[op]:
                continue
            t = get_type_robust(m, inp)
            if t in (TensorProto.FLOAT, TensorProto.DOUBLE):
                cast_in(i, inp, TensorProto.FLOAT16)
    elif op in SHAPE_OPS:
        for i, inp in enumerate(node.input):
            if not inp: continue
            if op in INT64_INPUTS and i in INT64_INPUTS[op]:
                continue
            t = get_type_robust(m, inp)
            if t == TensorProto.FLOAT16:
                cast_in(i, inp, TensorProto.FLOAT)
    elif op == "Concat":
        input_types = []
        for inp in node.input:
            if not inp:
                input_types.append(None)
            else:
                input_types.append(get_type_robust(m, inp))

        has_int = any(t in (TensorProto.INT64, TensorProto.INT32) for t in input_types)
        has_fp16 = any(t == TensorProto.FLOAT16 for t in input_types)
        has_fp32 = any(t == TensorProto.FLOAT for t in input_types)

        if has_int:
            # v4 修正：有整数输入 → 全部转 int64（这些是 shape 张量，输出用作 Reshape 等的 shape）
            target = TensorProto.INT64
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = input_types[i]
                if t != target and t is not None:
                    cast_in(i, inp, target)
                elif t is None:
                    cast_in(i, inp, target)
        elif has_fp16 and has_fp32:
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = input_types[i]
                if t == TensorProto.FLOAT:
                    cast_in(i, inp, TensorProto.FLOAT16)
        elif has_fp32 and not has_fp16:
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = input_types[i]
                if t == TensorProto.FLOAT:
                    cast_in(i, inp, TensorProto.FLOAT16)
    else:
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type_robust(m, inp)
            if t == TensorProto.FLOAT:
                cast_in(i, inp, TensorProto.FLOAT16)
    return fixes

def parse_err(err):
    patterns = [
        r"node \(/([^)]+)\)",
        r"in node \(/([^)]+)\)",
        r"Node \(name: /([^,)]+)",
        r"node '([^']+)'",
        r"\((/[^)]+)\)",
        r"in node \(name: ([^,)]+)",
    ]
    for p in patterns:
        m = re.search(p, err)
        if m:
            return m.group(1)
    return None

def generic_fix_all(m, cast_no):
    total = 0
    for node in list(m.graph.node):
        if (node.op_type in COMPUTE_OPS or node.op_type in SHAPE_OPS or
            node.op_type == "Concat" or node.op_type == "Range" or
            node.op_type in INT64_INPUTS):
            total += fix_node(m, node, cast_no)
    return total

def main():
    t0 = time.time()
    # v4: 从原始 fp16 干净启动（v3 的 ok 文件有错误的 fp32 Concat cast）
    start = SRC
    m = load_model(start)
    print(f"loaded {start}, nodes={len(m.graph.node)}", flush=True)
    cast_no = [0]

    for it in range(80):
        # 第一轮用内存中的 m，后续用已保存的 OUT
        if it == 0:
            # 先保存初始状态到 OUT
            onnx.save(m, OUT)
        cur = OUT
        ok, err, sess = try_ort(cur)
        if ok:
            print(f"[iter {it}] ORT LOAD OK ({cur})", flush=True)
            break

        print(f"[iter {it}] FULL ERROR:", flush=True)
        print(err, flush=True)
        print(f"[iter {it}] --- end error ---", flush=True)

        node_name = parse_err(err)
        if node_name:
            node = find_node(m, "/" + node_name) or find_node(m, node_name)
            if node is not None:
                print(f"[iter {it}] targeting node {node.op_type} '{node.name}'", flush=True)
                dump_node_inputs(m, node)
                fixes = fix_node(m, node, cast_no)
                print(f"[iter {it}] fixed node {node.op_type} '{node.name}' -> {fixes} casts", flush=True)
                if fixes > 0:
                    onnx.save(m, OUT)
                    print(f"[iter {it}] saved {OUT} ({os.path.getsize(OUT)/1e6:.1f}MB)", flush=True)
                else:
                    print(f"[iter {it}] 0 fixes on targeted node; running generic full-graph scan", flush=True)
                    total = generic_fix_all(m, cast_no)
                    print(f"[iter {it}] generic fixes: {total}", flush=True)
                    if total == 0:
                        print("FINAL FAIL: no fixes possible", flush=True)
                        print("FULL ERROR:", err, flush=True)
                        return 1
                    onnx.save(m, OUT)
                    print(f"[iter {it}] saved after generic fix ({os.path.getsize(OUT)/1e6:.1f}MB)", flush=True)
                continue
            else:
                print(f"[iter {it}] node '{node_name}' not found in graph; generic scan", flush=True)
        else:
            print(f"[iter {it}] could not parse node name from error; generic scan", flush=True)

        total = generic_fix_all(m, cast_no)
        print(f"[iter {it}] generic fixes: {total}", flush=True)
        if total == 0:
            print("FINAL FAIL (no fixes possible):", flush=True)
            print(err, flush=True)
            return 1
        onnx.save(m, OUT)
        print(f"[iter {it}] saved ({os.path.getsize(OUT)/1e6:.1f}MB)", flush=True)

    # 最终验证
    ok, err, sess = try_ort(OUT)
    if not ok:
        print("FINAL LOAD FAILED:", flush=True)
        print(err, flush=True)
        return 1

    inp = sess.get_inputs()[0]
    print(f"input name='{inp.name}' shape={inp.shape} type={inp.type}", flush=True)
    x = (np.random.default_rng(1).standard_normal((1, 2, 343980)) * 0.05).astype(np.float32)
    t1 = time.time()
    y = sess.run(None, {inp.name: x})[0]
    dt = (time.time() - t1) * 1000
    print(f"FINAL INFERENCE OK: out={y.shape} dtype={y.dtype} time={dt:.1f}ms", flush=True)
    print(f"size MB: {os.path.getsize(OUT)/1e6:.1f} total_time={time.time()-t0:.1f}s", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
