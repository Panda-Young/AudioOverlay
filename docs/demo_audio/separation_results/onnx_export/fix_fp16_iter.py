# -*- coding: utf-8 -*-
"""
htdemucs fp16 迭代修复器：以 onnxruntime 实际加载为准，
逐个修复类型错误节点（Range/Concat 等），直到模型可加载可推理。
"""
import os, time, re, sys
import numpy as np
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
SRC = os.path.join(BASE, "htdemucs_fp16.onnx")   # 转换器原始输出
OUT = os.path.join(BASE, "htdemucs_fp16_ok.onnx")

# 形状/索引类算子：输入不允许 fp16
FP32_REQUIRED_OPS = {
    "Range", "Shape", "Gather", "Reshape", "Pad", "Slice", "ScatterND",
    "Unsqueeze", "Squeeze", "ConstantOfShape", "Expand", "Tile", "Split",
    "Where", "Equal", "Greater", "Less", "Mod", "TopK", "ArgMax", "ArgMin",
    "NonZero", "OneHot", "IsNaN", "IsInf", "Resize", "CumSum", "GatherElements",
    "GatherND", "ScatterElements", "Transpose", "Flatten", "CastLike",
}

def load_model(path):
    m = onnx.load(path)
    return m

def get_type(m, name):
    if not name:
        return None
    g = m.graph
    for i in g.input:
        if i.name == name:
            return i.type.tensor_type.elem_type
    for o in g.output:
        if o.name == name:
            return o.type.tensor_type.elem_type
    for vi in g.value_info:
        if vi.name == name and vi.type.HasField("tensor_type"):
            return vi.type.tensor_type.elem_type
    for init in g.initializer:
        if init.name == name:
            return init.data_type
    return None

def set_type(m, name, t):
    g = m.graph
    for vi in g.value_info:
        if vi.name == name:
            vi.type.tensor_type.elem_type = t
            return
    vi = helper.make_tensor_value_info(name, t, None)
    g.value_info.append(vi)

def try_ort(path):
    try:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        return True, None, sess
    except Exception as e:
        return False, str(e), None

def fix_pass(m):
    """扫描一遍：按规则插 Cast。返回修复次数。"""
    g = m.graph
    fixes = 0
    cast_no = [0]
    nodes_add = []

    def new_cast_name():
        cast_no[0] += 1
        return f"fp16fix_cast_{cast_no[0]}"

    for node in list(g.node):
        # ---- Range: 所有输入必须 fp32(或int) ----
        if node.op_type == "Range":
            for i, inp in enumerate(node.input):
                if not inp:
                    continue
                t = get_type(m, inp)
                if t == TensorProto.FLOAT16:
                    out_name = f"{node.name or node.op_type}_rngfix_{i}"
                    c = helper.make_node("Cast", [inp], [out_name], to=TensorProto.FLOAT,
                                         name=new_cast_name())
                    nodes_add.append(c)
                    node.input[i] = out_name
                    set_type(m, out_name, TensorProto.FLOAT)
                    fixes += 1

        # ---- Concat: 所有输入必须同类型；若有 int64 输入则统一转 fp32 ----
        elif node.op_type == "Concat":
            types = {get_type(m, i) for i in node.input if i}
            if len(types) > 1:
                for i, inp in enumerate(node.input):
                    if not inp:
                        continue
                    t = get_type(m, inp)
                    if t is not None and t != TensorProto.FLOAT:
                        out_name = f"{node.name or 'concat'}_fix_{i}"
                        c = helper.make_node("Cast", [inp], [out_name], to=TensorProto.FLOAT,
                                             name=new_cast_name())
                        nodes_add.append(c)
                        node.input[i] = out_name
                        set_type(m, out_name, TensorProto.FLOAT)
                        fixes += 1

        # ---- 其它形状/索引类算子：fp16 输入统一转 fp32（保守处理） ----
        elif node.op_type in FP32_REQUIRED_OPS:
            for i, inp in enumerate(node.input):
                if not inp:
                    continue
                t = get_type(m, inp)
                if t == TensorProto.FLOAT16:
                    out_name = f"{node.name or node.op_type}_fix_{i}"
                    c = helper.make_node("Cast", [inp], [out_name], to=TensorProto.FLOAT,
                                         name=new_cast_name())
                    nodes_add.append(c)
                    node.input[i] = out_name
                    set_type(m, out_name, TensorProto.FLOAT)
                    fixes += 1

    if nodes_add:
        g.node.extend(nodes_add)
    return fixes

def main():
    t0 = time.time()
    m = load_model(SRC)
    print(f"loaded {SRC}, nodes={len(m.graph.node)}", flush=True)

    # 先跑 onnxruntime 的官方修复工具（如果可用）
    try:
        from onnxruntime.transformers.onnx_model import OnnxModel
        om = OnnxModel(m)
        om.update_graph()  # 可移除孤儿节点/统一类型工具
        print("onnx_model.update_graph done", flush=True)
    except Exception as e:
        print(f"update_graph skipped: {e}", flush=True)

    # 迭代：修复 → ORT 加载 → 若失败解析错误 → 再修复
    for it in range(12):
        ok, err, sess = try_ort(OUT if os.path.exists(OUT) else SRC)
        path = OUT if os.path.exists(OUT) else SRC
        if not ok and it == 0:
            # 第一轮用 SRC
            pass
        if ok:
            print(f"[iter {it}] ORT LOAD OK: {path}", flush=True)
            break
        print(f"[iter {it}] ORT error: {str(err)[:300]}", flush=True)
        fixes = fix_pass(m)
        print(f"[iter {it}] fixes applied: {fixes}", flush=True)
        if fixes == 0:
            print("no more fixes possible; dumping error details", flush=True)
            print(err, flush=True)
            break
        onnx.save(m, OUT)
        print(f"[iter {it}] saved {OUT}", flush=True)

    # 最终验证：加载 + 推理
    ok, err, sess = try_ort(OUT)
    if not ok:
        print(f"FINAL LOAD FAILED: {err}", flush=True)
        return 1
    print("FINAL: ORT loaded OK", flush=True)
    inp = sess.get_inputs()[0]
    x = (np.random.default_rng(1).standard_normal((1, 2, 343980)) * 0.05).astype(np.float32)
    t1 = time.time()
    y = sess.run(None, {inp.name: x})[0]
    dt = (time.time() - t1) * 1000
    print(f"inference OK: out={y.shape} dtype={y.dtype} avg={dt:.1f}ms", flush=True)
    print(f"size MB: {os.path.getsize(OUT)/1e6:.1f} total_time={time.time()-t0:.1f}s", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
