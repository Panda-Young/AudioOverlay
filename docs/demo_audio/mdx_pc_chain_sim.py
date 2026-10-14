"""PC full-chain simulation of AudioOverlay mode5 (MDX OLA pipeline) to compare vs App dump output.
Replicates: capture 48k → resample 44.1k → OLA windows (TRIM silence, CHUNK/STEP) → STFT(JNI zero-pad style)
→ seg128_static ONNX → ISTFT(App one-sided style) → vocals = mix - accomp; out = vocals + accomp*gain
→ addToOla (symmetric hann) → play [playFrame, writeFrame-TRIM) /divider → resample 48k → PCM16.
"""
import numpy as np, soundfile as sf, torch, onnxruntime as ort, sys, os

BASE = r'docs\demo_audio\separation_results\dump_verify'
N_FFT, HOP, DIM_F = 6144, 1024, 3072
CHUNK, STEP, TRIM = 130048, 97536, 3072
MODEL_SR, OUT_SR = 44100, 48000
MODEL = r'UVR-MDX-NET-Inst_HQ_3_b1_seg128_static.onnx'

def resample_linear(x, src, dst):
    ratio = src / dst
    out_len = int(len(x) / ratio)
    out = np.empty(out_len, dtype=np.float32)
    for i in range(out_len):
        p = i * ratio
        i0 = int(np.floor(p)); frac = p - i0
        if i0 + 1 < len(x):
            out[i] = x[i0] * (1 - frac) + x[i0 + 1] * frac
        else:
            out[i] = x[i0]
    return out

def stft_app_style(mix, win_t, use_torch=True):
    """App JNI mdxStft: center=True zero-pad, symmetric? No - periodic hann window (matches verified corr=1.0)."""
    x = torch.from_numpy(mix).double()
    pad = N_FFT // 2
    xp = torch.zeros(2, x.shape[1] + 2 * pad, dtype=torch.float64)
    xp[:, pad:pad + x.shape[1]] = x
    T = (xp.shape[1] - N_FFT) // HOP + 1
    spec = torch.zeros(2, T, N_FFT // 2 + 1, 2, dtype=torch.float64)
    for t in range(T):
        seg = xp[:, t * HOP: t * HOP + N_FFT] * win_t
        f = torch.fft.rfft(seg, n=N_FFT, dim=1)
        spec[:, t, :, 0] = f.real; spec[:, t, :, 1] = f.imag
    spec4 = torch.zeros(4, DIM_F, T, dtype=torch.float64)
    for ch in range(2):
        for reim in range(2):
            spec4[ch * 2 + reim] = spec[ch, :, :DIM_F, reim].T
    return spec4.reshape(-1).numpy().astype(np.float32)

def istft_app_style(spec4, n_frames, out_len, win_t):
    """App JNI mdxIstft 逐项对齐（mdstft.cpp:77-108，已由 mdx_pc_istft_check.py 验证 corr=1.0）：
       start = t*HOP - N_FFT/2（center=True 补偿）; 单边（k>=DIM_F 含 Nyquist 置零）;
       wave = ifft（torch 自带 1/N == FFTW_BACKWARD*invN）; v = wave*hann;
       accum += v; wsum += hann^2; out = accum/wsum（wsum>1e-8）。返回 (2, out_len) 平面。
       向量化 scatter_add 版，与逐帧循环逐位等价。"""
    spec = torch.zeros(2, DIM_F, n_frames, dtype=torch.complex128)
    for ch in range(2):
        for reim in range(2):
            c = torch.from_numpy(spec4[ch * 2 + reim].reshape(DIM_F, n_frames).astype(np.float64)).double()
            spec[ch] += c * (1j if reim else 1.0)
    # full spectrum: k in [0,DIM_F) 填 spec, [DIM_F,N_FFT) 置零（含 Nyquist）
    sf = torch.zeros(2, n_frames, N_FFT, dtype=torch.complex128)
    sf[:, :, :DIM_F] = spec.permute(0, 2, 1)
    wave = torch.fft.ifft(sf, n=N_FFT, dim=2).real  # (2, T, N_FFT)
    v = wave * win_t[None, None, :]                  # (2, T, N_FFT)
    # 样本位置矩阵 idx[t,i] = t*HOP - N_FFT/2 + i
    starts = torch.arange(n_frames) * HOP - N_FFT // 2
    idx = starts[:, None] + torch.arange(N_FFT)[None, :]  # (T, N_FFT)
    mask = (idx >= 0) & (idx < out_len)
    idx_c = idx.masked_fill(~mask, 0)
    w2 = win_t * win_t
    accum = torch.zeros(2, out_len, dtype=torch.float64)
    wsum = torch.zeros(out_len, dtype=torch.float64)
    for ch in range(2):
        accum[ch].scatter_add_(0, idx_c.reshape(-1), (v[ch] * mask).reshape(-1))
    wsum.scatter_add_(0, idx_c.reshape(-1), (w2[None, :] * mask).repeat(n_frames, 1).reshape(-1))
    out = accum / wsum.clamp_min(1e-8)
    return out.numpy().astype(np.float32)

def main():
    gain = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    tag = sys.argv[2] if len(sys.argv) > 2 else 'g0'
    inp, _ = sf.read(os.path.join(BASE, 'input_g0.wav'), dtype='float32')   # (N,2) 48k
    out_app, _ = sf.read(os.path.join(BASE, 'output_g0.wav'), dtype='float32')  # (M,2) 48k
    mixL = resample_linear(inp[:, 0], OUT_SR, MODEL_SR)
    mixR = resample_linear(inp[:, 1], OUT_SR, MODEL_SR)
    print(f'input {inp.shape} app_out {out_app.shape} mix44 {mixL.shape[0]}')

    win_t = torch.hann_window(N_FFT, periodic=True).double()
    hann_ola = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(CHUNK) / (CHUNK - 1))).astype(np.float64)  # symmetric

    sess = ort.InferenceSession(MODEL, providers=['CPUExecutionProvider'])
    iname = sess.get_inputs()[0].name
    oname = sess.get_outputs()[0].name

    # App stream coordinates: stream[f] = mix44[f - TRIM] for f >= TRIM else 0
    n_mix = len(mixL)
    ola_size = CHUNK * 6
    ola_buf = np.zeros(ola_size * 2)
    ola_div = np.zeros(ola_size * 2)
    play_frame = TRIM
    write_frame = 0
    out44_all = []
    k = 0
    while k * STEP + CHUNK <= TRIM + n_mix + 0:
        abs_start = k * STEP
        # window input in stream coords
        wL = np.zeros(CHUNK, dtype=np.float32); wR = np.zeros(CHUNK, dtype=np.float32)
        for i in range(CHUNK):
            f = abs_start + i - TRIM
            if 0 <= f < n_mix:
                wL[i] = mixL[f]; wR[i] = mixR[f]
        mix = np.stack([wL, wR])  # (2, CHUNK)
        spec4 = stft_app_style(mix, win_t)
        T = CHUNK // HOP + 1
        inp_t = torch.from_numpy(spec4).reshape(1, 4, DIM_F, T)
        out4 = sess.run([oname], {iname: inp_t.numpy()})[0].reshape(4, DIM_F, T).astype(np.float64)
        accomp = istft_app_style(out4, T, CHUNK, win_t)  # (2, CHUNK)
        # vocals = mix - accomp ; output = vocals + accomp*gain = mix - accomp*(1-gain)
        out = mix - accomp * (1.0 - gain)
        # addToOla with symmetric hann
        for i in range(CHUNK):
            w = hann_ola[i]
            ola_buf[write_frame * 2 + i * 2] += out[0, i] * w
            ola_buf[write_frame * 2 + i * 2 + 1] += out[1, i] * w
            ola_div[write_frame * 2 + i * 2] += w
            ola_div[write_frame * 2 + i * 2 + 1] += w
        write_frame += STEP
        # play available
        playable = write_frame - TRIM
        if playable > play_frame:
            n = playable - play_frame
            off = play_frame * 2
            seg = np.zeros(n * 2)
            for i in range(n * 2):
                d = ola_div[off + i]
                seg[i] = ola_buf[off + i] / d if d > 1e-8 else 0.0
                ola_buf[off + i] = 0.0
                ola_div[off + i] = 0.0
            play_frame = playable
            out44_all.append(seg)
        k += 1
        if k > 12:
            break
    out44 = np.concatenate(out44_all).reshape(-1, 2)
    print(f'pc out44 frames {out44.shape[0]}')
    # resample 44.1 -> 48 + pcm16 (same as App)
    L = resample_linear(out44[:, 0], MODEL_SR, OUT_SR)
    R = resample_linear(out44[:, 1], MODEL_SR, OUT_SR)
    n48 = min(len(L), len(R))
    pcm = np.stack([np.clip(L[:n48] * 32767, -32768, 32767).astype(np.int16),
                    np.clip(R[:n48] * 32767, -32768, 32767).astype(np.int16)], axis=1)
    print(f'pc pcm16 frames {n48} app frames {len(out_app)}')
    sf.write(os.path.join(BASE, f'pc_chain_{tag}.wav'), pcm / 32767.0, OUT_SR)
    # compare vs app output (same length)
    n = min(n48, len(out_app))
    a = pcm[:n].reshape(-1).astype(np.float32)
    b = (out_app[:n] * 32767).reshape(-1).astype(np.float32)
    c = float(np.corrcoef(a, b)[0, 1])
    print(f'PC_chain[{tag}] vs APP output corr={c:.6f} (n={n})')
    print(f'rms pc={np.sqrt(np.mean(a ** 2)):.1f} app={np.sqrt(np.mean(b ** 2)):.1f}')

if __name__ == '__main__':
    main()
