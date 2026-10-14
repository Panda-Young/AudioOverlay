# -*- coding: utf-8 -*-
"""
htdemucs fp16 迭代修复器 v3（报错驱动，鲁棒类型推导）：
- 打印完整 ORT 错误（不截断）
- get_type_robust: 追踪生产者节点推导类型（Constant/Cast/其他）
- Concat: 用鲁棒类型判断；有 int 则全部 fp32，否则全部 fp16
- 0 fixes 时打印所有输入的名称/类型/生产者，便于诊断
- 从 htdemucs_fp16_ok.onnx 续跑
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

# 类型名映射
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
    """找到输出 name 的节点"""
    for n in m.graph.node:
        for o in n.output:
            if o == name:
                return n
    return None

def get_type_robust(m, name, depth=0):
    """鲁棒类型推导：value_info → initializer → graph io → 追踪生产者"""
    if not name or depth > 3:
        return None
    g = m.graph
    # 1. graph input
    for i in g.input:
        if i.name == name and i.type.HasField("tensor_type"):
            return i.type.tensor_type.elem_type
    # 2. graph output
    for o in g.output:
        if o.name == name and o.type.HasField("tensor_type"):
            return o.type.tensor_type.elem_type
    # 3. value_info
    for vi in g.value_info:
        if vi.name == name and vi.type.HasField("tensor_type"):
            return vi.type.tensor_type.elem_type
    # 4. initializer
    for init in g.initializer:
        if init.name == name:
            return init.data_type
    # 5. 追踪生产者节点
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
        elif prod.op_type == "Rank":
            return TensorProto.INT64
        elif prod.op_type in ("Equal", "Greater", "Less", "GreaterOrEqual", "LessOrEqual", "And", "Or", "Not", "IsNaN", "IsInf"):
            return TensorProto.BOOL
        elif prod.op_type == "Where":
            # Where output type = X input type (input[1])
            if len(prod.input) > 1 and prod.input[1]:
                return get_type_robust(m, prod.input[1], depth+1)
            return None
        elif prod.op_type in ("Concat", "Add", "Sub", "Mul", "Div", "Max", "Min"):
            # 输出类型 = 第一个非 None 输入类型
            for inp in prod.input:
                if inp:
                    t = get_type_robust(m, inp, depth+1)
                    if t is not None:
                        return t
            return None
        elif prod.op_type in ("Reshape", "Transpose", "Squeeze", "Unsqueeze", "Flatten", "Expand", "Tile", "Slice", "Pad", "Identity"):
            # 输出类型 = 数据输入类型
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type in ("Gather", "GatherElements", "GatherND", "ScatterND", "ScatterElements"):
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type == "Split":
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type == "Range":
            # Range 输出类型 = start 输入类型
            if prod.input and prod.input[0]:
                return get_type_robust(m, prod.input[0], depth+1)
            return None
        elif prod.op_type == "ConstantOfShape":
            # 输出类型 = value 属性类型，默认 fp32
            for a in prod.attribute:
                if a.name == "value" and a.HasField("t"):
                    return a.t.data_type
            return TensorProto.FLOAT
        elif prod.op_type == "OneHot":
            # output type = values input type (input[2])
            if len(prod.input) > 2 and prod.input[2]:
                return get_type_robust(m, prod.input[2], depth+1)
            return TensorProto.FLOAT
        elif prod.op_type == "TopK":
            # output[0]=values type=input type, output[1]=indices int64
            # 我们需要知道是哪个输出
            for idx, o in enumerate(prod.output):
                if o == name:
                    if idx == 0:
                        if prod.input and prod.input[0]:
                            return get_type_robust(m, prod.input[0], depth+1)
                    else:
                        return TensorProto.INT64
            return None
        # 默认：尝试从第一个输入推导
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
    """打印节点所有输入的详细信息，用于诊断 0 fixes"""
    print(f"  --- node {node.op_type} '{node.name}' inputs debug ---", flush=True)
    for i, inp in enumerate(node.input):
        if not inp:
            print(f"  input[{i}]: <empty>", flush=True)
            continue
        t = get_type_robust(m, inp)
        prod = find_producer(m, inp)
        prod_info = f"producer={prod.op_type}('{prod.name}')" if prod else "producer=None(graph_io/init)"
        # 检查是否是 initializer
        is_init = any(init.name == inp for init in m.graph.initializer)
        print(f"  input[{i}] name='{inp}' type={type_str(t)} {prod_info} is_init={is_init}", flush=True)
    print(f"  --- end debug ---", flush=True)

def fix_node(m, node, cast_no):
    """定向修复一个节点，返回修复次数"""
    fixes = 0
    g = m.graph

    def cast_in(i, inp, to):
        nonlocal fixes
        out_name = f"fix_{cast_no[0]}_{(node.name or node.op_type).replace('/','_')}_{i}"
        cast_no[0] += 1
        c = helper.make_node("Cast", [inp], [out_name], to=to, name=out_name)
        g.node.append(c)
        node.input[i] = out_name
        set_type(m, out_name, to)
        fixes += 1

    op = node.op_type
    if op == "Range":
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type_robust(m, inp)
            if t != TensorProto.FLOAT:
                cast_in(i, inp, TensorProto.FLOAT)
        for o in node.output:
            ot = get_type_robust(m, o)
            if ot == TensorProto.FLOAT16:
                out2 = f"fix_{cast_no[0]}_{(node.name or '').replace('/','_')}_out"
                cast_no[0] += 1
                c = helper.make_node("Cast", [o], [out2], to=TensorProto.FLOAT16, name=out2)
                g.node.append(c)
                set_type(m, out2, TensorProto.FLOAT16)
                node.output[0] = out2
                fixes += 1
    elif op in COMPUTE_OPS:
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type_robust(m, inp)
            if t in (TensorProto.FLOAT, TensorProto.DOUBLE):
                cast_in(i, inp, TensorProto.FLOAT16)
    elif op in SHAPE_OPS:
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type_robust(m, inp)
            if t == TensorProto.FLOAT16:
                cast_in(i, inp, TensorProto.FLOAT)
    elif op == "Concat":
        # 用鲁棒类型判断每个输入
        input_types = []
        for inp in node.input:
            if not inp:
                input_types.append(None)
            else:
                input_types.append(get_type_robust(m, inp))

        has_int = any(t in (TensorProto.INT64, TensorProto.INT32) for t in input_types)
        has_fp16 = any(t == TensorProto.FLOAT16 for t in input_types)
        has_fp32 = any(t == TensorProto.FLOAT for t in input_types)
        has_none = any(t is None for t in input_types)

        if has_int:
            # 有整数输入 → 全部转 fp32（Concat 不允许混合 float/int）
            target = TensorProto.FLOAT
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = input_types[i]
                if t != target and t is not None:
                    cast_in(i, inp, target)
                elif t is None:
                    # 类型未知，保守也转 fp32（避免隐藏的 int/fp16 冲突）
                    cast_in(i, inp, target)
        elif has_fp16 and has_fp32:
            # 混合 fp16+fp32 → 统一 fp16
            target = TensorProto.FLOAT16
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = input_types[i]
                if t == TensorProto.FLOAT:
                    cast_in(i, inp, target)
        elif has_fp32 and not has_fp16:
            # 全 fp32 → 转 fp16（fp16 模型中数据 Concat 应为 fp16）
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = input_types[i]
                if t == TensorProto.FLOAT:
                    cast_in(i, inp, TensorProto.FLOAT16)
        # 全 fp16 或全 None → 不动
    else:
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type_robust(m, inp)
            if t == TensorProto.FLOAT:
                cast_in(i, inp, TensorProto.FLOAT16)
    return fixes

def parse_err(err):
    """从 ORT 错误中提取节点名"""
    # 多种格式尝试
    patterns = [
        r"node \(/([^)]+)\)",
        r"in node \(/([^)]+)\)",
        r"Node \(name: /([^,)]+)",
        r"node '([^']+)'",
        r"\((/[^)]+)\)",
    ]
    for p in patterns:
        m = re.search(p, err)
        if m:
            return m.group(1)
    return None

def generic_fix_all(m, cast_no):
    """全图扫描修复"""
    total = 0
    for node in list(m.graph.node):
        if node.op_type in COMPUTE_OPS or node.op_type in SHAPE_OPS or node.op_type == "Concat" or node.op_type == "Range":
            total += fix_node(m, node, cast_no)
    return total

def main():
    t0 = time.time()
    start = OUT if os.path.exists(OUT) else SRC
    m = load_model(start)
    print(f"loaded {start}, nodes={len(m.graph.node)}", flush=True)
    cast_no = [0]

    for it in range(60):
        cur = OUT if os.path.exists(OUT) else SRC
        ok, err, sess = try_ort(cur)
        if ok:
            print(f"[iter {it}] ORT LOAD OK ({cur})", flush=True)
            break

        # 打印完整错误（不截断）
        print(f"[iter {it}] FULL ERROR:", flush=True)
        print(err, flush=True)
        print(f"[iter {it}] --- end error ---", flush=True)

        node_name = parse_err(err)
        if node_name:
            # 尝试带 / 和不带 /
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

        # 通用兜底
        total = generic_fix_all(m, cast_no)
        print(f"[iter {it}] generic fixes: {total}", flush=True)
        if total == 0:
            print("FINAL FAIL (no fixes possible):", flush=True)
            print(err, flush=True)
            return 1
        onnx.save(m, OUT)
        print(f"[iter {it}] saved ({os.path.getsize(OUT)/1e6:.1f}MB)", flush=True)

    # 最终验证：加载 + 推理
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
