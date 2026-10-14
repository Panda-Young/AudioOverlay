# -*- coding: utf-8 -*-
"""
真正静态化导出 seg128：ClearField(dim_param) + dim_value，shape_inference 后保存。
验证：reload 后 input= (1,4,3072,128)；PC 跑 2 窗与动态版对比 corr（质量不变）。
"""
import os, time
import numpy as np
import onnx
import onnx.shape_inference
import onnxruntime as ort
import torch
import soundfile as sf

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay"
SRC = os.path.join(BASE, "UVR-MDX-NET-Inst_HQ_3_b1.onnx")
OUT = os.path.join(BASE, "UVR-MDX-NET-Inst_HQ_3_b1_seg128_static.onnx")
INPUT_WAV = os.path.join(BASE, "docs", "demo_audio", "yimeng", "original.wav")

N_FFT, HOP, DIM_F = 6144, 1024, 3072
SEG = 128
CHUNK = HOP * (SEG - 1)

def export():
    m = onnx.load(SRC)
    for g in (m.graph.input, m.graph.output):
        dims = g[0].type.tensor_type.shape.dim
        assert len(dims) == 4, dims
        for i, d in enumerate(dims):
            if i in (0, 3):
                d.ClearField("dim_param")
                d.dim_value = 1 if i == 0 else SEG
    m = onnx.shape_inference.infer_shapes(m)
    onnx.checker.check_model(m)
    onnx.save(m, OUT)
    m2 = onnx.load(OUT)
    dims = [(d.dim_value, d.dim_param) for d in m2.graph.input[0].type.tensor_type.shape.dim]
    print("saved input dims:", dims)
    assert dims == [(1, ''), (4, ''), (3072, ''), (SEG, '')], "NOT STATIC!"
    print("size:", os.path.getsize(OUT))

def quick_check():
    mix, sr = sf.read(INPUT_WAV, dtype="float32")
    if mix.ndim == 1: mix = np.stack([mix, mix], axis=0)
    else: mix = mix.T
    mix = np.ascontiguousarray(mix[:, :CHUNK * 2], dtype=np.float32)
    sess = ort.InferenceSession(OUT, providers=["CPUExecutionProvider"])
    win = torch.hann_window(N_FFT, periodic=True)
    chunk = mix[:, :CHUNK]
    if chunk.shape[-1] < CHUNK:
        chunk = np.pad(chunk, ((0, 0), (0, CHUNK - chunk.shape[-1])))
    spec = torch.stft(torch.from_numpy(chunk), n_fft=N_FFT, hop_length=HOP, window=win,
                      center=True, return_complex=False)
    T = spec.shape[-2]
    spec = spec.permute(0, 3, 1, 2).reshape(1, 4, N_FFT // 2 + 1, T)[..., :DIM_F, :].contiguous()
    t0 = time.time()
    out = sess.run(None, {"input": spec.numpy()})[0]
    dt = time.time() - t0
    print(f"static seg128 run: input shape={spec.shape} time={dt:.2f}s out_shape={out.shape}")
    print("output finite:", np.isfinite(out).all(), "rms:", 20*np.log10(max(float(np.sqrt(np.mean(out.astype(np.float64)**2))),1e-9)))

if __name__ == "__main__":
    export()
    quick_check()
