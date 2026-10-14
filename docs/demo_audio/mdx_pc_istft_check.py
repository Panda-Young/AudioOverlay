# -*- coding: utf-8 -*-
"""对齐 JNI mdstft.cpp 的 PC ISTFT 逐环节验证（正确解交错）

JNI mdxIstft 权威语义（mdstft.cpp:77-108）：
  for t in [0,nFrames):
      start = t*HOP - N_FFT/2                       # center=True 补偿
      for ch in [0,2):
          m_in[k] = in4[((ch*2+re)*DIM_F+k)*nFrames+t], k<DIM_F
          m_in[k] = 0, k in [DIM_F, N_FFT)          # 单边：负频+Nyquist 置零
          fftwf_execute(planInv)                    # FFTW_BACKWARD = N*ifft（无 1/N）
          v = re[i]*invN*hann[i]                    # = ifft[i]*hann[i]
          accum[idx*2+ch] += v; wsum[idx*2+ch] += hann[i]^2
  out[i] = accum[i]/wsum[i] if wsum>1e-8 else 0     # 交错存储 [L,R,...]
"""
import numpy as np, torch, os, sys

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\dump_verify"
N_FFT, HOP, DIM_F = 6144, 1024, 3072
OUTLEN = 130048  # CHUNK_SAMPLES

def corr(a, b):
    a = np.asarray(a).reshape(-1).astype(np.float64)
    b = np.asarray(b).reshape(-1).astype(np.float64)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])

def istft_jni_style(spec4, n_frames, out_len, dtype=torch.float64):
    """逐项对齐 JNI：返回 (2, out_len) 平面数组。spec4: (4, DIM_F, n_frames)。"""
    hann = torch.hann_window(N_FFT, periodic=True).to(dtype)
    sp = torch.zeros(2, DIM_F, n_frames, dtype=torch.complex128)
    for ch in range(2):
        for reim in range(2):
            c = torch.from_numpy(spec4[ch * 2 + reim].astype(np.float64)).to(dtype)
            sp[ch] += c * (1j if reim else 1.0)
    accum = torch.zeros(2, out_len, dtype=torch.float64)
    wsum = torch.zeros(2, out_len, dtype=torch.float64)
    for t in range(n_frames):
        start = t * HOP - N_FFT // 2
        for ch in range(2):
            sf = torch.zeros(N_FFT, dtype=torch.complex128)
            sf[:DIM_F] = sp[ch, :, t]
            wave = torch.fft.ifft(sf, n=N_FFT).real  # torch ifft 自带 1/N == FFTW_BACKWARD*invN
            for i in range(N_FFT):
                idx = start + i
                if 0 <= idx < out_len:
                    v = wave[i] * hann[i]
                    accum[ch, idx] += v
                    wsum[ch, idx] += hann[i] * hann[i]
    out = accum / wsum.clamp_min(1e-8)
    return out.numpy()

def main():
    base = BASE
    mix = np.fromfile(os.path.join(base, "app_mix44.f32"), np.float32).reshape(-1, 2).T      # (2,N) 交错->平面
    spec4 = np.fromfile(os.path.join(base, "app_spec4.f32"), np.float32).reshape(4, DIM_F, 128)
    accomp = np.fromfile(os.path.join(base, "app_accomp44.f32"), np.float32).reshape(-1, 2).T
    recon = np.fromfile(os.path.join(base, "app_recon_spec.f32"), np.float32).reshape(-1, 2).T  # JNI istft(spec4)
    out4_post = np.fromfile(os.path.join(base, "app_out4_post.f32"), np.float32).reshape(4, DIM_F, 128)

    print("shapes: mix", mix.shape, "spec4", spec4.shape, "accomp", accomp.shape,
          "recon", recon.shape, "out4_post", out4_post.shape, flush=True)

    # A. PC 对齐 ISTFT(spec4) vs JNI recon（同输入同算法 → 应 ≈ 1.0，若 <1 则是 float32/float64 差异或布局错）
    sim_recon = istft_jni_style(spec4, 128, OUTLEN)
    print(f"[A] PC-ISTFT(spec4) vs JNI recon      corr={corr(sim_recon, recon):.6f} "
          f"rms_pc={np.sqrt(np.mean(sim_recon**2)):.4f} rms_ji={np.sqrt(np.mean(recon**2)):.4f}", flush=True)

    # B. JNI recon vs mix（黄金：istft(stft(x)) ≈ x）——正确解交错后应 ≈ 1.0
    print(f"[B] JNI recon(spec4) vs mix           corr={corr(recon, mix):.6f} "
          f"rms_re={np.sqrt(np.mean(recon**2)):.4f} rms_mx={np.sqrt(np.mean(mix**2)):.4f}", flush=True)

    # C. PC 对齐 ISTFT(spec4) vs mix（A+B 的传递：应 ≈ 1.0）
    print(f"[C] PC-ISTFT(spec4) vs mix            corr={corr(sim_recon, mix):.6f}", flush=True)

    # D. PC 对齐 ISTFT(out4_post) vs App accomp（App accomp = ISTFT(out4*1.022)）
    sim_accomp = istft_jni_style(out4_post, 128, OUTLEN)
    print(f"[D] PC-ISTFT(out4_post) vs app_accomp corr={corr(sim_accomp, accomp):.6f} "
          f"rms_pc={np.sqrt(np.mean(sim_accomp**2)):.4f} rms_ap={np.sqrt(np.mean(accomp**2)):.4f}", flush=True)

    # E. app_accomp vs mix 相关（诊断：分离输出与输入应弱相关）
    print(f"[E] app_accomp vs mix                 corr={corr(accomp, mix):.6f}", flush=True)

if __name__ == "__main__":
    main()
