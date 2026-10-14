# -*- coding: utf-8 -*-
"""
QNN EP 兼容修复 v2（Erf 包 Cast，保留原始 tensor 名）：
对每个 Erf 节点：
  orig_in --Cast(fp32)--> erf_in --Erf--> erf_out --Cast(fp16)--> orig_out
保留 orig_out 原名，下游引用不变；Erf 在 fp32 域计算，
ORT 融合成 Gelu 后输入也是 fp32，QNN CPU EP 的 com.microsoft.Gelu(float) 兼容。
"""
import os, time, sys
import numpy as np
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
SRC = os.path.join(BASE, "htdemucs_fp16_ok.onnx")
OUT = os.path.join(BASE, "htdemucs_fp16_qnn.onnx")

def set_type(m, name, t):
    g = m.graph
    for vi in g.value_info:
        if vi.name == name:
            vi.type.tensor_type.elem_type = t
            return
    g.value_info.append(helper.make_tensor_value_info(name, t, None))

def main():
    t0 = time.time()
    m = onnx.load(SRC)
    g = m.graph
    print(f"loaded {SRC}, nodes={len(g.node)}", flush=True)

    erf_nodes = [n for n in g.node if n.op_type == "Erf"]
    print(f"Erf count: {len(erf_nodes)}", flush=True)

    # 重建节点列表：Erf 替换为 Cast_in + Erf + Cast_out（保留 orig_out 名）
    idx = 0
    new_nodes = []
    replaced = 0
    for node in g.node:
        if node.op_type != "Erf":
            new_nodes.append(node)
            continue
        if len(node.input) < 1 or not node.input[0] or len(node.output) < 1 or not node.output[0]:
            print(f"  skip Erf '{node.name}' (empty io)", flush=True)
            new_nodes.append(node)
            continue
        orig_in = node.input[0]
        orig_out = node.output[0]
        cin = f"qnn_erf_in_{idx}"
        cout = f"qnn_erf_out_{idx}"
        idx += 1

        cast_in = helper.make_node("Cast", [orig_in], [cin], to=TensorProto.FLOAT, name=cin + "_c")
        new_erf = helper.make_node("Erf", [cin], [cout], name=node.name)
        cast_out = helper.make_node("Cast", [cout], [orig_out], to=TensorProto.FLOAT16, name=cout + "_c")
        set_type(m, cin, TensorProto.FLOAT)
        set_type(m, cout, TensorProto.FLOAT)

        new_nodes.extend([cast_in, new_erf, cast_out])
        replaced += 1
    del g.node[:]
    g.node.extend(new_nodes)

    print(f"replaced {replaced} Erf nodes", flush=True)
    onnx.save(m, OUT)
    print(f"saved {OUT} ({os.path.getsize(OUT)/1e6:.1f}MB) in {time.time()-t0:.1f}s", flush=True)

    # 校验无重名输出
    from collections import Counter
    c = Counter()
    for n in g.node:
        for o in n.output:
            c[o] += 1
    dups = {k: v for k, v in c.items() if v > 1}
    if dups:
        print("DUP OUTPUTS:", dups, flush=True)
        return 1

    so = ort.SessionOptions()
    so.log_severity_level = 3
    try:
        sess = ort.InferenceSession(OUT, so, providers=["CPUExecutionProvider"])
    except Exception as e:
        print("LOAD FAILED:", e, flush=True)
        return 1
    inp = sess.get_inputs()[0]
    print(f"input name='{inp.name}' shape={inp.shape} type={inp.type}", flush=True)
    x = (np.random.default_rng(1).standard_normal((1, 2, 343980)) * 0.05).astype(np.float32)
    t1 = time.time()
    y = sess.run(None, {inp.name: x})[0]
    dt = (time.time() - t1) * 1000
    print(f"INFERENCE OK: out={y.shape} dtype={y.dtype} time={dt:.1f}ms", flush=True)
    print("DONE", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
