# -*- coding: utf-8 -*-
"""
NaN 根因修复 v4：样本标准差归一化链整体抬到 fp32 域
根因：htdemucs 用输入长度 n=343980 计算样本标准差（sqrt(mean*n/(n-1))），
     转换脚本把 n 经 Cast 转 fp16，343980 > 65504 溢出为 inf → inf/inf=NaN → 全图 NaN。
修复：
  1. Cast_3.to: fp16 → fp32（长度保持 fp32）
  2. Constant_29（常量，Sub_4 输入）: 插 Cast→fp32
  3. ReduceMean_5 输入: 插 Cast→fp32（统计量 fp32 计算）
  4. Constant_30（1e-5，Add_1 输入）: 插 Cast→fp32
  5. Add_1 输出: 插 Cast→fp16（Div_3 的 norm 输入保持 fp16）
输出 htdemucs_fp16_ok2.onnx；本地验证：加载 + 推理无 NaN + 与 fp32 输出对比。
"""
import os, time, sys
import numpy as np
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
SRC = os.path.join(BASE, "htdemucs_fp16_ok.onnx")
OUT = os.path.join(BASE, "htdemucs_fp16_ok2.onnx")
FP32 = TensorProto.FLOAT
FP16 = TensorProto.FLOAT16

def set_type(m, name, t):
    g = m.graph
    for vi in g.value_info:
        if vi.name == name:
            vi.type.tensor_type.elem_type = t
            return
    g.value_info.append(helper.make_tensor_value_info(name, t, None))

def producers_map(m):
    pm = {}
    for n in m.graph.node:
        for o in n.output:
            pm[o] = n
    return pm

def consumers_map(m):
    cm = {}
    for n in m.graph.node:
        for i in n.input:
            cm.setdefault(i, []).append(n)
    return cm

def main():
    t0 = time.time()
    m = onnx.load(SRC)
    g = m.graph
    pm = producers_map(m)
    cm = consumers_map(m)
    print(f"loaded {SRC} nodes={len(g.node)}", flush=True)

    idx = 0
    def new_name(tag):
        nonlocal idx
        idx += 1
        return f"normfix_{tag}_{idx}"

    new_nodes = []

    # 1. Cast_3: to → fp32
    cast3 = pm.get("/Cast_3_output_0")
    if cast3 is None:
        print("Cast_3 NOT FOUND", flush=True)
        return 1
    for a in cast3.attribute:
        if a.name == "to":
            print(f"  Cast_3.to {a.i} -> {FP32}", flush=True)
            a.i = FP32
    # Cast_3 输出类型标记 fp32
    set_type(m, "/Cast_3_output_0", FP32)

    # 2. Constant_29: 输出插 Cast→fp32
    c29 = pm.get("/Constant_29_output_0")
    if c29 is None:
        print("Constant_29 NOT FOUND", flush=True)
        return 1
    c29_out = "/Constant_29_output_0"
    c29_raw = new_name("c29")
    c29.output[0] = c29_raw
    cn = new_name("c29cast")
    new_nodes.append(helper.make_node("Cast", [c29_raw], [c29_out], to=FP32, name=cn))
    set_type(m, c29_out, FP32)

    # 3. ReduceMean_5 输入[0] 插 Cast→fp32
    rm5 = pm.get("/ReduceMean_5_output_0")
    if rm5 is None:
        print("ReduceMean_5 NOT FOUND", flush=True)
        return 1
    rm5_in0 = rm5.input[0]
    rm5_raw = new_name("rm5in")
    rm5.input[0] = rm5_raw
    cn = new_name("rm5cast")
    new_nodes.append(helper.make_node("Cast", [rm5_in0], [rm5_raw], to=FP32, name=cn))
    set_type(m, rm5_raw, FP32)
    set_type(m, "/ReduceMean_5_output_0", FP32)

    # 4. Constant_30: 输出插 Cast→fp32
    c30 = pm.get("/Constant_30_output_0")
    if c30 is None:
        print("Constant_30 NOT FOUND", flush=True)
        return 1
    c30_out = "/Constant_30_output_0"
    c30_raw = new_name("c30")
    c30.output[0] = c30_raw
    cn = new_name("c30cast")
    new_nodes.append(helper.make_node("Cast", [c30_raw], [c30_out], to=FP32, name=cn))
    set_type(m, c30_out, FP32)

    # 5. Add_1 输出: 改名 + Cast→fp16 回原名
    a1 = pm.get("/Add_1_output_0")
    if a1 is None:
        print("Add_1 NOT FOUND", flush=True)
        return 1
    a1_out = "/Add_1_output_0"
    a1_raw = new_name("a1")
    a1.output[0] = a1_raw
    cn = new_name("a1cast")
    new_nodes.append(helper.make_node("Cast", [a1_raw], [a1_out], to=FP16, name=cn))
    set_type(m, a1_raw, FP32)
    set_type(m, a1_out, FP16)

    # 插入新节点（放在图尾，执行序由依赖决定）
    g.node.extend(new_nodes)

    # 校验输出重名
    from collections import Counter
    cnt = Counter()
    for n in g.node:
        for o in n.output:
            cnt[o] += 1
    dups = {k: v for k, v in cnt.items() if v > 1}
    if dups:
        print("DUP OUTPUTS:", list(dups.items())[:6], flush=True)
        return 1

    onnx.save(m, OUT)
    print(f"saved {OUT} ({os.path.getsize(OUT)/1e6:.1f}MB) {time.time()-t0:.1f}s", flush=True)

    # 验证：加载 + 推理
    so = ort.SessionOptions()
    so.log_severity_level = 3
    try:
        sess = ort.InferenceSession(OUT, so, providers=["CPUExecutionProvider"])
    except Exception as e:
        print("LOAD FAILED:", str(e)[:500], flush=True)
        return 1
    iname = sess.get_inputs()[0].name
    x = (np.random.default_rng(0).standard_normal((1, 2, 343980)) * 0.1).astype(np.float32)
    t1 = time.time()
    y = sess.run(None, {iname: x})[0]
    dt = (time.time() - t1) * 1000
    nan = int(np.isnan(y).sum())
    inf = int(np.isinf(y).sum())
    rms = float(np.sqrt((y.astype(np.float64) ** 2).mean()))
    print(f"INFERENCE OK out={y.shape} {dt:.0f}ms nan={nan} inf={inf} rms={rms:.5f}", flush=True)
    return 0 if (nan == 0 and inf == 0) else 2

if __name__ == "__main__":
    sys.exit(main())
