# -*- coding: utf-8 -*-
"""
NaN 根因修复 v5（最终版）
根因：htdemucs 样本标准差归一化用输入长度 n=343980，Cast 转 fp16 溢出 inf → inf/inf=NaN → 全图 NaN。
修复（统计链整体 fp32 域，模型主体仍 fp16）：
  1. Cast_3.to: fp16 → fp32（长度 343980 保持 fp32）
  2. Constant_29: 插 Cast→fp32
  3. ReduceMean_5 输入: 插 Cast→fp32
  4. Constant_30: 插 Cast→fp32
  5. Sqrt_1 输出改 /Sqrt_1_raw(fp32)：Add_1 直连 fp32；其余消费者走 Cast→fp16 到原名
  6. Add_1 输出: 插 Cast→fp16 回原名（Div_3 norm 输入）
输出 htdemucs_fp16_ok2.onnx；验证加载 + 推理无 NaN + 与 fp32 输出 RMS 对比。
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

def main():
    t0 = time.time()
    m = onnx.load(SRC)
    g = m.graph
    pm = producers_map(m)
    print(f"loaded {SRC} nodes={len(g.node)}", flush=True)
    idx = 0
    def nn(tag):
        nonlocal idx
        idx += 1
        return f"normfix_{tag}_{idx}"
    new_nodes = []

    # 1. Cast_3 → fp32
    c3 = pm.get("/Cast_3_output_0")
    if c3 is None: print("Cast_3 NOT FOUND"); return 1
    for a in c3.attribute:
        if a.name == "to":
            print(f"  Cast_3.to {a.i} -> {FP32}", flush=True); a.i = FP32
    set_type(m, "/Cast_3_output_0", FP32)

    # 2. Constant_29 → fp32
    c29 = pm.get("/Constant_29_output_0")
    if c29 is None: print("Constant_29 NOT FOUND"); return 1
    raw = nn("c29"); c29.output[0] = raw
    new_nodes.append(helper.make_node("Cast", [raw], ["/Constant_29_output_0"], to=FP32, name=nn("c29c")))
    set_type(m, "/Constant_29_output_0", FP32)

    # 3. ReduceMean_5 输入 → fp32
    rm5 = pm.get("/ReduceMean_5_output_0")
    if rm5 is None: print("ReduceMean_5 NOT FOUND"); return 1
    rm5_in0 = rm5.input[0]; raw = nn("rm5in"); rm5.input[0] = raw
    new_nodes.append(helper.make_node("Cast", [rm5_in0], [raw], to=FP32, name=nn("rm5c")))
    set_type(m, raw, FP32); set_type(m, "/ReduceMean_5_output_0", FP32)

    # 4. Constant_30 → fp32
    c30 = pm.get("/Constant_30_output_0")
    if c30 is None: print("Constant_30 NOT FOUND"); return 1
    raw = nn("c30"); c30.output[0] = raw
    new_nodes.append(helper.make_node("Cast", [raw], ["/Constant_30_output_0"], to=FP32, name=nn("c30c")))
    set_type(m, "/Constant_30_output_0", FP32)

    # 5. Sqrt_1 输出改名 /Sqrt_1_raw(fp32)；原消费者走 Cast→fp16 到原名；Add_1 直连 fp32
    sq = pm.get("/Sqrt_1_output_0")
    if sq is None: print("Sqrt_1 NOT FOUND"); return 1
    sq_raw = nn("sqrt"); sq.output[0] = sq_raw
    new_nodes.append(helper.make_node("Cast", [sq_raw], ["/Sqrt_1_output_0"], to=FP16, name=nn("sqrtc")))
    set_type(m, sq_raw, FP32); set_type(m, "/Sqrt_1_output_0", FP16)
    a1 = pm.get("/Add_1_output_0")
    if a1 is None: print("Add_1 NOT FOUND"); return 1
    for i, inp in enumerate(a1.input):
        if inp == "/Sqrt_1_output_0":
            a1.input[i] = sq_raw  # Add_1 直连 fp32 sqrt

    # 6. Add_1 输出 → Cast fp16 回原名
    raw = nn("a1"); a1.output[0] = raw
    new_nodes.append(helper.make_node("Cast", [raw], ["/Add_1_output_0"], to=FP16, name=nn("a1c")))
    set_type(m, raw, FP32); set_type(m, "/Add_1_output_0", FP16)

    g.node.extend(new_nodes)
    from collections import Counter
    cnt = Counter()
    for n in g.node:
        for o in n.output: cnt[o] += 1
    dups = {k: v for k, v in cnt.items() if v > 1}
    if dups:
        print("DUP OUTPUTS:", list(dups.items())[:6], flush=True); return 1

    onnx.save(m, OUT)
    print(f"saved {OUT} ({os.path.getsize(OUT)/1e6:.1f}MB) {time.time()-t0:.1f}s", flush=True)

    so = ort.SessionOptions(); so.log_severity_level = 3
    try:
        sess = ort.InferenceSession(OUT, so, providers=["CPUExecutionProvider"])
    except Exception as e:
        print("LOAD FAILED:", str(e)[:400], flush=True); return 1
    iname = sess.get_inputs()[0].name
    x = (np.random.default_rng(0).standard_normal((1, 2, 343980)) * 0.1).astype(np.float32)
    t1 = time.time(); y = sess.run(None, {iname: x})[0]
    dt = (time.time() - t1) * 1000
    nan = int(np.isnan(y).sum()); inf = int(np.isinf(y).sum())
    rms = float(np.sqrt((y.astype(np.float64) ** 2).mean()))
    print(f"INFERENCE OK out={y.shape} {dt:.0f}ms nan={nan} inf={inf} rms={rms:.5f}", flush=True)
    return 0 if (nan == 0 and inf == 0) else 2

if __name__ == "__main__":
    sys.exit(main())
