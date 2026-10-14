# -*- coding: utf-8 -*-
"""
QNN EP 兼容修复（Erf 版）：
htdemucs fp16 模型里 Gelu 以 Erf 序列存在（0.5x(1+erf(x/√2))）。
ORT 图优化会把该序列融合为 com.microsoft.Gelu，而 QNN CPU EP 的 Gelu
kernel 只支持 tensor(float) → fp16 模型加载失败。
解法：给每个 Erf 节点输入前插 Cast(fp32)、输出后插 Cast(fp16)。
这样无论优化器是否融合成 Gelu，Gelu/Erf 都在 fp32 域计算，QNN 兼容，
且保持精确 Erf（无 tanh 近似误差）。
输出 htdemucs_fp16_qnn.onnx。本地 ORT CPU EP 验证。
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

    cast_no = [0]
    fixes = 0
    for node in list(g.node):
        if node.op_type != "Erf":
            continue
        for i, inp in enumerate(node.input):
            if not inp:
                continue
            out_name = f"qnn_cast_in_{cast_no[0]}"
            cast_no[0] += 1
            c = helper.make_node("Cast", [inp], [out_name], to=TensorProto.FLOAT, name=out_name)
            g.node.append(c)
            node.input[i] = out_name
            set_type(m, out_name, TensorProto.FLOAT)
            fixes += 1
        for oi, out in enumerate(node.output):
            out_name = f"qnn_cast_out_{cast_no[0]}"
            cast_no[0] += 1
            c = helper.make_node("Cast", [out], [out_name], to=TensorProto.FLOAT16, name=out_name)
            g.node.append(c)
            set_type(m, out_name, TensorProto.FLOAT16)
            node.output[oi] = out_name
            fixes += 1
        print(f"  fixed Erf '{node.name}' (+2 casts)", flush=True)

    print(f"total Erf fixes: {fixes}", flush=True)
    onnx.save(m, OUT)
    print(f"saved {OUT} ({os.path.getsize(OUT)/1e6:.1f}MB) in {time.time()-t0:.1f}s", flush=True)

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
