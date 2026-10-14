# -*- coding: utf-8 -*-
"""
MDX-Net seg 扫描验证：seg=64/32/16/8 的质量与可行性
每个 seg：导出静态模型 → 完整复刻处理链（chunk=hop*(seg-1)）→ 对比 seg256 基准 corr/RMS。
输出衰减曲线数据，供 App 选型（seg 越小首窗延迟越小）。
"""
import os, time, json
import numpy as np
import soundfile as sf
import torch
import onnx
import onnxruntime as ort

INPUT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\yimeng\original.wav"
BASE_MODEL = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\UVR-MDX-NET-Inst_HQ_3_b1.onnx"
OUT_ROOT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\mdx_pc_segsweep"
REF_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\uvr_mdx\mdx_inst_hq3"

N_FFT = 6144
HOP = 1024
DIM_F = 3072
OVERLAP = 0.25
COMPENSATE = 1.022

os.makedirs(OUT_ROOT, exist_ok=True)

def rms_db(x):
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0: return -120.0
    return 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-7))

def export_static(seg, out_path):
    m = onnx.load(BASE_MODEL)
    for g in (m.graph.input, m.graph.output):
        d = g[0].type.tensor_type.shape.dim
        d[0].dim_value = 1; d[0].dim_param = ''
        d[3].dim_value = seg; d[3].dim_param = ''
    onnx.checker.check_model(m)
    onnx.save(m, out_path)

def run_seg(seg, sess, mix, n_sample, sr):
    trim = N_FFT // 2
    chunk_size = HOP * (seg - 1)
    gen_size = chunk_size - 2 * trim
    pad = gen_size + trim - (n_sample % gen_size)
    mixture = np.concatenate((np.zeros((2, trim), dtype="float32"), mix,
                              np.zeros((2, pad), dtype="float32")), 1)
    step = int((1 - OVERLAP) * chunk_size)
    result = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    divider = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    win = torch.hann_window(N_FFT, periodic=True)
    t_inf = 0.0
    total_chunks = (mixture.shape[-1] + step - 1) // step
    for i, start in enumerate(range(0, mixture.shape[-1], step)):
        end = min(start + chunk_size, mixture.shape[-1])
        chunk = mixture[:, start:end]
        if chunk.shape[-1] < chunk_size:
            chunk = np.pad(chunk, ((0, 0), (0, chunk_size - chunk.shape[-1])))
        chunk_t = torch.from_numpy(chunk)
        spec = torch.stft(chunk_t, n_fft=N_FFT, hop_length=HOP, window=win,
                          center=True, return_complex=False)
        T = spec.shape[-2]
        spec = spec.permute(0, 3, 1, 2).reshape(1, 4, N_FFT // 2 + 1, T)
        spec = spec[..., :DIM_F, :].contiguous()
        t0 = time.time()
        out = sess.run(None, {"input": spec.numpy()})[0]
        t_inf += time.time() - t0
        out_t = torch.from_numpy(out)
        n_bins = N_FFT // 2 + 1
        pad_t = torch.zeros(4, n_bins - DIM_F, out_t.shape[-1], dtype=torch.float32)
        out_t = torch.cat([out_t[0], pad_t], dim=1)
        out_t = out_t.reshape(2, 2, n_bins, out_t.shape[-1])
        out_t = out_t.permute(0, 2, 3, 1)
        comp = torch.complex(out_t[..., 0], out_t[..., 1])
        wave = torch.istft(comp, n_fft=N_FFT, hop_length=HOP, window=win, center=True).numpy()
        L_actual = end - start
        w = np.hanning(chunk_size).astype(np.float32)
        w = np.tile(w[None, None, :], (1, 2, 1))
        result[:, :, start:end] += wave[None, :, :L_actual] * w[..., :L_actual]
        divider[:, :, start:end] += w[..., :L_actual]
    est = result / np.maximum(divider, 1e-8)
    est = est[0][:, trim:trim + n_sample]
    accomp = est * COMPENSATE
    vocals = mix - accomp
    return vocals, accomp, t_inf, total_chunks

def main():
    mix, sr = sf.read(INPUT, dtype="float32")
    if mix.ndim == 1:
        mix = np.stack([mix, mix], axis=0)
    else:
        mix = mix.T
    mix = np.ascontiguousarray(mix, dtype=np.float32)
    n_sample = mix.shape[-1]

    # seg256 基准 vocals（REF_DIR）
    ref_v, _ = sf.read(os.path.join(REF_DIR, "vocals.wav"), dtype="float32")
    if ref_v.ndim > 1: ref_v = ref_v.mean(axis=1)
    n_ref = min(len(ref_v), n_sample)
    ref_v = ref_v[:n_ref].astype(np.float32)
    ref_rms = rms_db(ref_v)

    results = []
    for seg in [64, 32, 16, 8]:
        model_path = os.path.join(OUT_ROOT, f"seg{seg}.onnx")
        out_dir = os.path.join(OUT_ROOT, f"seg{seg}")
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n=== seg={seg} (chunk={HOP*(seg-1)} samples, {HOP*(seg-1)/44100:.2f}s) ===", flush=True)
        try:
            export_static(seg, model_path)
            print("  exported", flush=True)
            sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            print("  input:", sess.get_inputs()[0].shape, flush=True)
            vocals, accomp, t_inf, nchunks = run_seg(seg, sess, mix, n_sample, sr)
            n = min(vocals.shape[-1], n_ref)
            a, b = vocals[:1, :n].ravel().astype(np.float32), ref_v[:n]
            corr = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else 0
            rv, ra = rms_db(vocals), rms_db(accomp)
            print(f"  OK vocals_rms={rv:.2f}dB accomp_rms={ra:.2f}dB corr_vs_256={corr:.4f} inf={t_inf:.1f}s chunks={nchunks}", flush=True)
            sf.write(os.path.join(out_dir, "vocals.wav"), vocals.T, sr, subtype="PCM_16")
            sf.write(os.path.join(out_dir, "accompaniment.wav"), accomp.T, sr, subtype="PCM_16")
            results.append(dict(seg=seg, ok=True, chunk=HOP*(seg-1), secs=round(HOP*(seg-1)/44100, 3),
                                rms_v=round(rv, 2), rms_a=round(ra, 2), corr=round(corr, 4),
                                inf_total_s=round(t_inf, 1), chunks=nchunks))
        except Exception as e:
            print(f"  FAIL: {str(e)[:300]}", flush=True)
            results.append(dict(seg=seg, ok=False, error=str(e)[:200]))

    print("\n=== SWEEP SUMMARY ===", flush=True)
    for r in results:
        print(json.dumps(r, ensure_ascii=False), flush=True)
    with open(os.path.join(OUT_ROOT, "sweep.json"), "w", encoding="utf-8") as f:
        json.dump(dict(ref_vocals_rms=round(ref_rms, 2), results=results), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
