# -*- coding: utf-8 -*-
"""静态 seg128 vs 动态 seg128 质量守恒验证：同一素材前 5 窗，vocals corr。"""
import os
import numpy as np
import soundfile as sf
import torch
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay"
INPUT_WAV = os.path.join(BASE, "docs", "demo_audio", "yimeng", "original.wav")
DYN = os.path.join(BASE, "UVR-MDX-NET-Inst_HQ_3_b1_seg128.onnx")
STATIC = os.path.join(BASE, "UVR-MDX-NET-Inst_HQ_3_b1_seg128_static.onnx")
N_FFT, HOP, DIM_F = 6144, 1024, 3072
SEG, CHUNK, STEP, TRIM = 128, 130048, 97536, 3072
COMP = 1.022

def process(model, mix, n_windows):
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    win = torch.hann_window(N_FFT, periodic=True)
    total = mix.shape[-1]
    out = np.zeros_like(mix)
    cnt = 0
    for start in range(0, total, STEP):
        if cnt >= n_windows: break
        end = min(start + CHUNK, total)
        chunk = mix[:, start:end]
        if chunk.shape[-1] < CHUNK:
            chunk = np.pad(chunk, ((0,0),(0,CHUNK-chunk.shape[-1])))
        spec = torch.stft(torch.from_numpy(chunk), n_fft=N_FFT, hop_length=HOP, window=win,
                          center=True, return_complex=False)
        T = spec.shape[-2]
        spec = spec.permute(0,3,1,2).reshape(1,4,N_FFT//2+1,T)[..., :DIM_F, :].contiguous()
        o = sess.run(None, {"input": spec.numpy()})[0]
        ot = torch.from_numpy(o)
        nb = N_FFT//2+1
        pad = torch.zeros(4, nb-DIM_F, ot.shape[-1], dtype=torch.float32)
        ot = torch.cat([ot[0], pad], dim=1).reshape(2,2,nb,ot.shape[-1]).permute(0,2,3,1)
        comp = torch.complex(ot[...,0], ot[...,1])
        w = torch.istft(comp, n_fft=N_FFT, hop_length=HOP, window=win, center=True).numpy()
        hn = np.hanning(CHUNK).astype(np.float32)[None, :]
        L = end - start
        out[:, start:end] += w[:, :L] * hn[:, :L]
        cnt += 1
    # trim 后用原始 mix 算 accomp（只取前 n_windows*STEP 有效区，简化比较）
    vocals = mix - out * COMP
    return vocals

mix, sr = sf.read(INPUT_WAV, dtype="float32")
if mix.ndim == 1: mix = np.stack([mix, mix], axis=0)
else: mix = mix.T
mix = np.ascontiguousarray(mix, dtype=np.float32)[:, :5*STEP]
print("processing dynamic...")
vd = process(DYN, mix, 5)
print("processing static...")
vs = process(STATIC, mix, 5)
# corr（取前 4 窗有效区，避开边缘）
a = vd[0].ravel().astype(np.float32); b = vs[0].ravel().astype(np.float32)
n = min(len(a), len(b))
c = float(np.corrcoef(a[:n], b[:n])[0,1])
print(f"static vs dynamic vocals corr = {c:.6f}")
print(f"rms dyn={20*np.log10(max(np.sqrt(np.mean(a**2)),1e-9)):.2f} stat={20*np.log10(max(np.sqrt(np.mean(b**2)),1e-9)):.2f}")
