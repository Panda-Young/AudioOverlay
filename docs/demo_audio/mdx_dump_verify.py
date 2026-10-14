# -*- coding: utf-8 -*-
"""
App 实时处理 vs PC 复刻对比验证（data dump 闭环）
App 端（canoe）：
  input_dump.wav  = 捕获的抖音音频 PCM16 @48000（原声）
  output_dump.wav = MDX 处理输出 PCM16 @48000（bgmGain=0 → 纯 vocals）
PC 端复刻：
  input_dump → 线性重采样 44100 → MDX OLA（seg128, chunk=130048, step=97536, trim=3072,
  Hanning + compensate=1.022，与 MDXProcessor.kt 完全一致）→ vocals（gain=0）
  → 线性重采样 48000 → 与 output_dump.wav 对比 corr/RMS
"""
import os, sys, time
import numpy as np
import soundfile as sf
import torch
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\dump_verify"
INPUT = os.path.join(BASE, sys.argv[1] if len(sys.argv) > 1 else "input_g0.wav")
OUTPUT = os.path.join(BASE, sys.argv[2] if len(sys.argv) > 2 else "output_g0.wav")
MODEL = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\UVR-MDX-NET-Inst_HQ_3_b1_seg128_static.onnx"

N_FFT = 6144
HOP = 1024
DIM_F = 3072
SEG = 128
CHUNK = HOP * (SEG - 1)   # 130048
STEP = int(0.75 * CHUNK)  # 97536
TRIM = N_FFT // 2         # 3072
COMPENSATE = 1.022
SR48 = 48000
SR44 = 44100

def linear_resample(x, src, dst):
    """复刻 App Resampler.resampleMono（线性插值）"""
    ratio = src / dst
    out_len = int(len(x) / ratio)
    out = np.zeros(out_len, dtype=np.float32)
    pos = np.arange(out_len) * ratio
    idx0 = np.floor(pos).astype(np.int64)
    frac = (pos - idx0).astype(np.float32)
    v0 = np.where(idx0 < len(x), x[np.minimum(idx0, len(x) - 1)], 0.0).astype(np.float32)
    idx1 = idx0 + 1
    v1 = np.where(idx1 < len(x), x[np.minimum(idx1, len(x) - 1)], 0.0).astype(np.float32)
    return (v0 * (1 - frac) + v1 * frac).astype(np.float32)

def rms_db(x):
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0: return -120.0
    return 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-7))

def corr(a, b):
    a = a.astype(np.float32); b = b.astype(np.float32)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if np.std(a) < 1e-6 or np.std(b) < 1e-6: return 0.0
    return float(np.corrcoef(a, b)[0, 1])

def istft_jni_style(spec4, n_frames, out_len, win_t):
    """App JNI mdxIstft 逐项对齐（mdstft.cpp:77-108，mdx_pc_istft_check.py 验证 corr=1.0）。
       spec4: (4, DIM_F, n_frames) 平面; 返回 (2, out_len) 平面。
       start=t*HOP-N_FFT/2; k>=DIM_F 置零; wave=ifft(1/N==FFTW_BACKWARD*invN); v=wave*hann;
       accum+=v; wsum+=hann^2; out=accum/wsum。向量化 scatter_add，与逐帧循环逐位等价。"""
    spec = torch.zeros(2, DIM_F, n_frames, dtype=torch.complex128)
    for ch in range(2):
        for reim in range(2):
            c = torch.from_numpy(spec4[ch * 2 + reim].reshape(DIM_F, n_frames).astype(np.float64)).double()
            spec[ch] += c * (1j if reim else 1.0)
    sf = torch.zeros(2, n_frames, N_FFT, dtype=torch.complex128)
    sf[:, :, :DIM_F] = spec.permute(0, 2, 1)
    wave = torch.fft.ifft(sf, n=N_FFT, dim=2).real
    v = wave * win_t[None, None, :]
    starts = torch.arange(n_frames) * HOP - N_FFT // 2
    idx = starts[:, None] + torch.arange(N_FFT)[None, :]
    mask = (idx >= 0) & (idx < out_len)
    idx_c = idx.masked_fill(~mask, 0)
    w2 = win_t * win_t
    accum = torch.zeros(2, out_len, dtype=torch.float64)
    wsum = torch.zeros(out_len, dtype=torch.float64)
    for ch in range(2):
        accum[ch].scatter_add_(0, idx_c.reshape(-1), (v[ch] * mask).reshape(-1))
    wsum.scatter_add_(0, idx_c.reshape(-1), (w2[None, :] * mask).repeat(n_frames, 1).reshape(-1))
    return (accum / wsum.clamp_min(1e-8)).numpy().astype(np.float32)

def stft_app_style(mix, win_t):
    """App JNI mdxStft 逐项对齐（mdstft.cpp:53-74，mdx_pc_istft_check.py 验证 corr=1.0）：
       center=True（前后补 N_FFT/2 零）、periodic hann、rfft 无归一、布局 [ch*2+reim, k, t] 展平。
       返回 float32 展平 (4*DIM_F*T,)。mix: (2, N) float32。"""
    x = torch.from_numpy(mix).double()
    pad = N_FFT // 2
    xp = torch.zeros(2, x.shape[1] + 2 * pad, dtype=torch.float64)
    xp[:, pad:pad + x.shape[1]] = x
    T = (xp.shape[1] - N_FFT) // HOP + 1
    spec = torch.zeros(2, T, N_FFT // 2 + 1, 2, dtype=torch.float64)
    for t in range(T):
        seg = xp[:, t * HOP: t * HOP + N_FFT] * win_t
        f = torch.fft.rfft(seg, n=N_FFT, dim=1)
        spec[:, t, :, 0] = f.real
        spec[:, t, :, 1] = f.imag
    spec4 = torch.zeros(4, DIM_F, T, dtype=torch.float64)
    for ch in range(2):
        for reim in range(2):
            spec4[ch * 2 + reim] = spec[ch, :, :DIM_F, reim].T
    return spec4.reshape(-1).numpy().astype(np.float32)

def main():
    mix48, _ = sf.read(INPUT, dtype="float32")  # (N,2) @48000
    print("input:", mix48.shape, flush=True)
    if mix48.ndim == 1:
        mix48 = np.stack([mix48, mix48], axis=1)
    mixL = linear_resample(mix48[:, 0], SR48, SR44)
    mixR = linear_resample(mix48[:, 1], SR48, SR44)
    n = min(len(mixL), len(mixR))
    mix = np.stack([mixL[:n], mixR[:n]], axis=0)  # (2, N) @44100
    print("resampled 44.1k:", mix.shape, flush=True)

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    print("model:", sess.get_inputs()[0].shape, flush=True)

    # OLA（与 App 相同：trim 前置填充 + Hanning + compensate）
    n_sample = mix.shape[-1]
    gen_size = CHUNK - 2 * TRIM
    pad = gen_size + TRIM - (n_sample % gen_size)
    mixture = np.concatenate((np.zeros((2, TRIM), dtype="float32"), mix,
                              np.zeros((2, pad), dtype="float32")), 1)
    result = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    divider = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    win = torch.hann_window(N_FFT, periodic=True)
    hn = np.hanning(CHUNK).astype(np.float32)
    hn = np.tile(hn[None, None, :], (1, 2, 1))
    t0 = time.time()
    for start in range(0, mixture.shape[-1], STEP):
        end = min(start + CHUNK, mixture.shape[-1])
        chunk = mixture[:, start:end]
        if chunk.shape[-1] < CHUNK:
            chunk = np.pad(chunk, ((0, 0), (0, CHUNK - chunk.shape[-1])))
        # 手写 rfft（float64）替代 torch.stft：布局/精度与 JNI 完全一致（torch.stft 布局转换与 float32 精度曾致全链 corr≈0）
        spec = stft_app_style(chunk, win.double())
        T = spec.size // (4 * DIM_F)
        out = sess.run(None, {"input": spec.reshape(1, 4, DIM_F, T)})[0]
        # JNI 对齐 ISTFT（单边置零 + hann WOLA；torch.istft 的 Hermitian 假设与 JNI 不等价，弃用）
        wave = istft_jni_style(out[0], T, CHUNK, win.double())
        L_actual = end - start
        result[:, :, start:end] += wave[None, :, :L_actual] * hn[..., :L_actual]
        divider[:, :, start:end] += hn[..., :L_actual]
    print(f"MDX OLA done in {time.time()-t0:.1f}s", flush=True)

    est = result / np.maximum(divider, 1e-8)
    est = est[0][:, TRIM:TRIM + n_sample]
    accomp = est * COMPENSATE
    vocals = mix - accomp  # gain=0 → 输出纯 vocals

    # 落盘候选（48k）供与 App 输出对比：accomp / vocals
    def _to48(x):
        l = linear_resample(x[0], SR44, SR48); r = linear_resample(x[1], SR44, SR48)
        m = min(len(l), len(r)); return np.stack([l[:m], r[:m]], axis=1)
    sf.write(os.path.join(BASE, "pc_accomp.wav"), _to48(accomp), SR48)
    sf.write(os.path.join(BASE, "pc_vocals.wav"), _to48(vocals), SR48)
    print("saved pc_accomp.wav / pc_vocals.wav", flush=True)

    # 重采样回 48000
    outL = linear_resample(vocals[0], SR44, SR48)
    outR = linear_resample(vocals[1], SR44, SR48)
    m = min(len(outL), len(outR))
    pc_out = np.stack([outL[:m], outR[:m]], axis=1)  # (M,2)

    # 读 App dump output
    app_out, _ = sf.read(OUTPUT, dtype="float32")
    if app_out.ndim == 1:
        app_out = np.stack([app_out, app_out], axis=1)
    print("app output:", app_out.shape, "pc output:", pc_out.shape, flush=True)

    # 对齐：找最佳偏移（±5s 内滑动，最大化 corr，逐通道均值；App 输出 dump 起点滞后首窗采集+处理+播放启动约 3s）
    n_c = min(len(pc_out), len(app_out))
    best_corr, best_off = -1.0, 0
    for off in range(-240000, 240001, 9600):
        a = pc_out[:n_c]
        b = app_out[max(0, off):max(0, off) + n_c]
        c = corr((a[:, 0] + a[:, 1]) / 2, (b[:, 0] + b[:, 1]) / 2)
        if c > best_corr:
            best_corr, best_off = c, off
    # 细对齐（±9600 内步进 960）
    for off in range(max(-9600, best_off - 9600), min(9600, best_off + 9600) + 1, 960):
        b = app_out[max(0, off):max(0, off) + n_c]
        c = corr((pc_out[:n_c, 0] + pc_out[:n_c, 1]) / 2, (b[:, 0] + b[:, 1]) / 2)
        if c > best_corr:
            best_corr, best_off = c, off
    print(f"best offset={best_off} samples corr={best_corr:.4f}", flush=True)

    # 最终逐通道 corr + RMS
    a = pc_out[:n_c]
    b = app_out[max(0, best_off):max(0, best_off) + n_c]
    cl = corr(a[:, 0], b[:, 0]); cr = corr(a[:, 1], b[:, 1])
    print(f"corr L={cl:.4f} R={cr:.4f}", flush=True)
    print(f"RMS pc={rms_db(a):.2f}dB app={rms_db(b):.2f}dB", flush=True)

    # 分段 corr（每 2s 一段，观察稳定性）
    seg_n = SR48 * 2
    segs = []
    for s in range(0, n_c - seg_n, seg_n):
        e = s + seg_n
        segs.append(round(corr((a[s:e, 0] + a[s:e, 1]) / 2, (b[s:e, 0] + b[s:e, 1]) / 2), 3))
    print("per-2s corr:", segs, flush=True)

    with open(os.path.join(BASE, "dump_verify_result.txt"), "w", encoding="utf-8") as f:
        f.write(f"offset={best_off} corr_L={cl:.4f} corr_R={cr:.4f} rms_pc={rms_db(a):.2f} rms_app={rms_db(b):.2f}\n")
        f.write(f"per2s={segs}\n")

if __name__ == "__main__":
    main()
