# -*- coding: utf-8 -*-
"""
htdemucs fp16 迭代修复器 v2（报错驱动）：
- 解析 ORT 报错节点名 → 定向修复类型冲突
- 计算类节点(Conv/MatMul/Gemm/Add/Mul/...) → 所有输入统一 fp16
- 形状类节点(Range/Shape/Reshape/Gather/...) → fp16 输入转 fp32
- Concat → 有 int64 则统一 fp32，否则统一 fp16
- 迭代直到 ORT 可加载并完成一次推理
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

def load_model(path):
    return onnx.load(path)

def get_type(m, name):
    if not name:
        return None
    g = m.graph
    for i in g.input:
        if i.name == name: return i.type.tensor_type.elem_type
    for o in g.output:
        if o.name == name: return o.type.tensor_type.elem_type
    for vi in g.value_info:
        if vi.name == name and vi.type.HasField("tensor_type"):
            return vi.type.tensor_type.elem_type
    for init in g.initializer:
        if init.name == name: return init.data_type
    # Constant 节点输出类型推导（常未进 value_info）
    for n in g.node:
        if n.op_type == "Constant" and n.output[0] == name:
            for a in n.attribute:
                if a.name == "value" and a.HasField("t"):
                    return a.t.data_type
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

def fix_node(m, node, cast_no):
    """定向修复一个节点，返回修复次数"""
    fixes = 0
    g = m.graph

    def cast_in(i, inp, to):
        nonlocal fixes
        out_name = f"fix_{cast_no[0]}_{node.name or node.op_type}_{i}"
        cast_no[0] += 1
        c = helper.make_node("Cast", [inp], [out_name], to=to, name=out_name)
        g.node.append(c)
        node.input[i] = out_name
        set_type(m, out_name, to)
        fixes += 1

    op = node.op_type
    if op == "Range":
        # Range 输入必须 int32/int64/float32/float64：全部强制 Cast 到 fp32
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type(m, inp)
            if t != TensorProto.FLOAT:
                cast_in(i, inp, TensorProto.FLOAT)
        # Range 输出若被期望 fp16，插 Cast fp32->fp16（保持下游 fp16 域）
        for o in node.output:
            ot = get_type(m, o)
            if ot == TensorProto.FLOAT16:
                out2 = f"fix_{cast_no[0]}_{node.name}_out"
                cast_no[0] += 1
                c = helper.make_node("Cast", [o], [out2], to=TensorProto.FLOAT16, name=out2)
                g.node.append(c)
                set_type(m, out2, TensorProto.FLOAT16)
                node.output[0] = out2
                fixes += 1
    elif op in COMPUTE_OPS:
        # 所有 float 输入统一 fp16（权重/数据都在 fp16 域）
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type(m, inp)
            if t in (TensorProto.FLOAT, TensorProto.DOUBLE):
                cast_in(i, inp, TensorProto.FLOAT16)
    elif op in SHAPE_OPS:
        # 形状类：fp16 输入转 fp32
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type(m, inp)
            if t == TensorProto.FLOAT16:
                cast_in(i, inp, TensorProto.FLOAT)
    elif op == "Concat":
        types = {get_type(m, i) for i in node.input if i}
        if types and types != {TensorProto.FLOAT16} and not (types - {TensorProto.FLOAT16, None}):
            # 全 fp16/未知 → 不动
            pass
        has_int = any(get_type(m, i) in (TensorProto.INT64, TensorProto.INT32) for i in node.input if i)
        if has_int:
            # 统一 fp32
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = get_type(m, inp)
                if t not in (TensorProto.FLOAT, None):
                    cast_in(i, inp, TensorProto.FLOAT)
        else:
            # 统一 fp16
            for i, inp in enumerate(node.input):
                if not inp: continue
                t = get_type(m, inp)
                if t == TensorProto.FLOAT:
                    cast_in(i, inp, TensorProto.FLOAT16)
    else:
        # 未知 op：把所有 float32 输入转 fp16（假设在 fp16 域）
        for i, inp in enumerate(node.input):
            if not inp: continue
            t = get_type(m, inp)
            if t == TensorProto.FLOAT:
                cast_in(i, inp, TensorProto.FLOAT16)
    return fixes

def parse_err(err):
    m = re.search(r"node \(/([^)]+)\)", err)
    if m:
        return m.group(1)
    m2 = re.search(r"in node \(/([^)]+)\)", err)
    if m2:
        return m2.group(1)
    return None

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
        print(f"[iter {it}] error: {str(err)[:200]}", flush=True)
        node_name = parse_err(err)
        if node_name:
            node = find_node(m, "/" + node_name) or find_node(m, node_name)
            if node is not None:
                fixes = fix_node(m, node, cast_no)
                print(f"[iter {it}] fixed node {node.op_type} '{node.name}' -> {fixes} casts", flush=True)
                if fixes > 0:
                    onnx.save(m, OUT)
                else:
                    print(f"[iter {it}] 0 fixes on {node.op_type}; generic scan", flush=True)
                    total = 0
                    for nn in list(m.graph.node):
                        total += fix_node(m, nn, cast_no)
                    print(f"[iter {it}] generic fixes: {total}", flush=True)
                    if total == 0:
                        print("FINAL FAIL:", err[:400], flush=True)
                        return 1
                    onnx.save(m, OUT)
                continue
            else:
                print(f"[iter {it}] node '{node_name}' not found; try generic fix", flush=True)
        # 通用兜底：全图扫描修复
        total = 0
        for node in list(m.graph.node):
            if node.op_type in COMPUTE_OPS or node.op_type in SHAPE_OPS or node.op_type == "Concat":
                total += fix_node(m, node, cast_no)
        print(f"[iter {it}] generic fixes: {total}", flush=True)
        if total == 0:
            print("FINAL FAIL (no fixes possible):", err[:500], flush=True)
            return 1
        onnx.save(m, OUT)

    ok, err, sess = try_ort(OUT)
    if not ok:
        print("FINAL LOAD FAILED:", err[:500], flush=True)
        return 1
    inp = sess.get_inputs()[0]
    x = (np.random.default_rng(1).standard_normal((1, 2, 343980)) * 0.05).astype(np.float32)
    t1 = time.time()
    y = sess.run(None, {inp.name: x})[0]
    dt = (time.time() - t1) * 1000
    print(f"FINAL INFERENCE OK: out={y.shape} dtype={y.dtype} time={dt:.1f}ms", flush=True)
    print(f"size MB: {os.path.getsize(OUT)/1e6:.1f} total_time={time.time()-t0:.1f}s", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
