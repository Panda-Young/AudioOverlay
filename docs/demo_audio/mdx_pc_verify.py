# -*- coding: utf-8 -*-
"""
MDX-Net (UVR-MDX-NET-Inst_HQ_3 b1) PC 复刻验证
完全复刻 audio-separator MDXSeparator 的预处理/推理/后处理链：
  n_fft=6144, hop=1024, dim_f=3072, segment_size=256, overlap=0.25, compensate=1.022
输出 vocals / accompaniment 并与 audio-separator 基准 (mdx_inst_hq3) 对比 RMS。
基准: vocals -14.18dB, accompaniment -25.52dB (yimeng 60s)
"""
import os, sys, time, json
import numpy as np
import soundfile as sf
import torch
import onnxruntime as ort

INPUT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\yimeng\original.wav"
MODEL = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\UVR-MDX-NET-Inst_HQ_3_b1.onnx"
OUT_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\mdx_pc"
REF_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\uvr_mdx\mdx_inst_hq3"

N_FFT = 6144
HOP = 1024
DIM_F = 3072
SEG = 256
OVERLAP = 0.25
COMPENSATE = 1.022

os.makedirs(OUT_DIR, exist_ok=True)

def rms_db(x):
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0: return -120.0
    return 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-7))

def main():
    print("loading model:", MODEL, flush=True)
    t0 = time.time()
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    print(f"  load {time.time()-t0:.1f}s, input={sess.get_inputs()[0].shape}, output={sess.get_outputs()[0].shape}", flush=True)

    mix, sr = sf.read(INPUT, dtype="float32")
    print("mix:", mix.shape, "sr:", sr, flush=True)
    if mix.ndim == 1:
        mix = np.stack([mix, mix], axis=0)
    else:
        mix = mix.T  # (2, N)
    mix = np.ascontiguousarray(mix, dtype=np.float32)
    n_sample = mix.shape[-1]

    trim = N_FFT // 2
    chunk_size = HOP * (SEG - 1)
    gen_size = chunk_size - 2 * trim
    pad = gen_size + trim - (n_sample % gen_size)
    mixture = np.concatenate((np.zeros((2, trim), dtype="float32"), mix,
                              np.zeros((2, pad), dtype="float32")), 1)
    print(f"trim={trim} chunk_size={chunk_size} gen_size={gen_size} pad={pad} mixture={mixture.shape}", flush=True)

    step = int((1 - OVERLAP) * chunk_size)
    result = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    divider = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    win = torch.hann_window(N_FFT, periodic=True)
    total_chunks = (mixture.shape[-1] + step - 1) // step
    t_inf = 0.0

    for i, start in enumerate(range(0, mixture.shape[-1], step)):
        end = min(i_start := start + chunk_size, mixture.shape[-1])
        chunk = mixture[:, start:end]
        if chunk.shape[-1] < chunk_size:
            chunk = np.pad(chunk, ((0, 0), (0, chunk_size - chunk.shape[-1])))
        chunk_t = torch.from_numpy(chunk)  # (2,L)

        # STFT: (2, 3073, T, 2) -> permute -> (2,2,3073,T) -> reshape (1,4,3073,T) -> [...,:3072,:]
        spec = torch.stft(chunk_t, n_fft=N_FFT, hop_length=HOP, window=win,
                          center=True, return_complex=False)  # (2, 3073, T, 2)
        T = spec.shape[-2]
        spec = spec.permute(0, 3, 1, 2).reshape(1, 4, N_FFT // 2 + 1, T)
        spec = spec[..., :DIM_F, :].contiguous()  # (1,4,3072,T)

        t0 = time.time()
        out = sess.run(None, {"input": spec.numpy()})[0]  # (1,4,3072,T)
        t_inf += time.time() - t0

        out_t = torch.from_numpy(out)
        # ISTFT: pad freq -> 3073, reshape (2,2,3073,T) -> complex -> istft -> (2,L)
        n_bins = N_FFT // 2 + 1
        pad_t = torch.zeros(4, n_bins - DIM_F, out_t.shape[-1], dtype=torch.float32)
        out_t = torch.cat([out_t[0], pad_t], dim=1)              # (4,3073,T)
        out_t = out_t.reshape(2, 2, n_bins, out_t.shape[-1])     # (2,2,3073,T) L/R, real/imag
        out_t = out_t.permute(0, 2, 3, 1)                        # (2,3073,T,2)
        comp = torch.complex(out_t[..., 0], out_t[..., 1])
        wave = torch.istft(comp, n_fft=N_FFT, hop_length=HOP, window=win, center=True)  # (2,L)
        wave = wave.numpy()

        L_actual = end - start
        w = np.hanning(chunk_size).astype(np.float32)
        w = np.tile(w[None, None, :], (1, 2, 1))
        result[:, :, start:end] += wave[None, :, :L_actual] * w[..., :L_actual]
        divider[:, :, start:end] += w[..., :L_actual]
        print(f"  chunk {i+1}/{total_chunks} start={start} end={end} inf={time.time()-t0:.2f}s", flush=True)

    est = result / np.maximum(divider, 1e-8)
    est = est[0]  # (2, L)
    # 去掉前 trim 与尾部 pad
    est = est[:, trim:trim + n_sample]
    accomp = est * COMPENSATE
    vocals = mix - accomp

    sf.write(os.path.join(OUT_DIR, "vocals.wav"), vocals.T, sr, subtype="PCM_16")
    sf.write(os.path.join(OUT_DIR, "accompaniment.wav"), accomp.T, sr, subtype="PCM_16")

    rv, ra, rm = rms_db(vocals), rms_db(accomp), rms_db(mix)
    print(f"\nRMS: mix={rm:.2f}dB vocals={rv:.2f}dB accomp={ra:.2f}dB", flush=True)

    # 与基准对比
    ref = {}
    for name, key in [("vocals.wav", "vocals"), ("accompaniment.wav", "accomp")]:
        p = os.path.join(REF_DIR, name)
        if os.path.exists(p):
            x, _ = sf.read(p, dtype="float32")
            if x.ndim > 1: x = x.mean(axis=1)
            n = min(len(x), vocals.shape[-1]) if key == "vocals" else min(len(x), accomp.shape[-1])
            ref[key] = (rms_db(x), x[:n])
            print(f"REF {key}: RMS={ref[key][0]:.2f}dB", flush=True)
    if "vocals" in ref:
        n = ref["vocals"][1].shape[0]
        a, b = vocals[:1, :n].ravel().astype(np.float32), ref["vocals"][1].astype(np.float32)
        corr = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else 0
        print(f"vocals corr vs ref: {corr:.3f}", flush=True)

    meta = dict(input=INPUT, model=MODEL, sr=sr, n_fft=N_FFT, hop=HOP, dim_f=DIM_F,
                seg=SEG, overlap=OVERLAP, compensate=COMPENSATE, inf_total_s=t_inf,
                chunks=total_chunks, rms_db=dict(mix=round(rm,2), vocals=round(rv,2), accomp=round(ra,2)),
                ref_rms=dict(vocals=round(ref['vocals'][0],2) if 'vocals' in ref else None,
                             accomp=round(ref['accomp'][0],2) if 'accomp' in ref else None))
    with open(os.path.join(OUT_DIR, "meta.txt"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("META:", json.dumps(meta, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
