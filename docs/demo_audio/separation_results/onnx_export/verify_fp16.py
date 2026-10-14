# -*- coding: utf-8 -*-
"""htdemucs fp16 ONNX 推理验证：加载 + 精度对比 + 计时"""
import time, numpy as np, onnxruntime as ort

O = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
F32 = O + r"\htdemucs.onnx"
F16 = O + r"\htdemucs_fp16.onnx"
FIX = O + r"\htdemucs_fp16_fixed.onnx"

rng = np.random.default_rng(0)
x = (rng.standard_normal((1, 2, 343980)) * 0.1).astype(np.float32)

def run(path, n=1, label=""):
    try:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        t0 = time.time()
        y = sess.run(None, {"mix": x})[0] if "mix" in [i.name for i in sess.get_inputs()] else sess.run(None, {sess.get_inputs()[0].name: x})[0]
        dt = (time.time() - t0) * 1000
        if n > 1:
            t0 = time.time()
            for _ in range(n):
                sess.run(None, {sess.get_inputs()[0].name: x})
            dt = (time.time() - t0) * 1000 / n
        print(f"[{label}] load OK, out={y.shape}, avg={dt:.1f}ms, out dtype={y.dtype}")
        return y
    except Exception as e:
        print(f"[{label}] FAILED: {type(e).__name__}: {e}")
        return None

y32 = run(F32, 1, "fp32")
y16 = run(F16, 3, "fp16")
yfix = run(FIX, 3, "fp16_fixed")

if y32 is not None and y16 is not None:
    d = np.abs(y32.astype(np.float64) - y16.astype(np.float64))
    print(f"fp16 vs fp32: max_abs_diff={d.max():.6f} mean={d.mean():.2e} "
          f"rms32={np.sqrt((y32**2).mean()):.4f} rms16={np.sqrt((y16**2).mean()):.4f}")
    # 相对误差
    denom = np.sqrt((y32.astype(np.float64)**2).mean())
    print(f"relative_err={d.mean()/denom:.2e}")
if yfix is not None and y16 is not None:
    d2 = np.abs(y16.astype(np.float64) - yfix.astype(np.float64))
    print(f"fixed vs fp16: max_abs_diff={d2.max():.6f} (0=相同)")
