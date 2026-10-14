# -*- coding: utf-8 -*-
"""离线验证：与真机 SpleeterProcessor 完全相同的处理流程
提取视频音轨 -> Spleeter 2-stems (vocals/accompaniment) -> 混音(BGM降90%) -> 输出对比
"""
import numpy as np
import wave, os, sys, time

MODEL_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\app\src\main\assets\spleeter"
AUDIO_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio"
SR = 44100
N_FFT = 1024
HOP = 256
FREQ_BINS = 512
FRAMES_PER_SPLIT = 1024

def read_wav(path):
    with wave.open(path, 'rb') as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr

def write_wav(path, data, sr=SR):
    d = np.clip(data, -1.0, 1.0)
    d = (d * 32767).astype(np.int16)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(d.tobytes())

def stft(signal):
    n_frames = (len(signal) - N_FFT) // HOP + 1
    win = np.hanning(N_FFT).astype(np.float32)
    real = np.zeros((FREQ_BINS, n_frames), np.float32)
    imag = np.zeros((FREQ_BINS, n_frames), np.float32)
    for f in range(n_frames):
        start = f * HOP
        frame = np.zeros(N_FFT, np.float32)
        end = min(start + N_FFT, len(signal))
        frame[:end-start] = signal[start:end]
        spec = np.fft.fft(frame * win)
        real[:, f] = spec[:FREQ_BINS].real
        imag[:, f] = spec[:FREQ_BINS].imag
    return real, imag

def istft(real, imag, out_len):
    out = np.zeros(out_len, np.float32)
    wsum = np.zeros(out_len, np.float32)
    win = np.hanning(N_FFT).astype(np.float32)
    n_frames = real.shape[1]
    for f in range(n_frames):
        spec = np.zeros(N_FFT, np.complex64)
        spec[:FREQ_BINS] = real[:, f] + 1j * imag[:, f]
        # Nyquist = 0, Hermitian symmetry
        spec[FREQ_BINS] = 0
        for k in range(FREQ_BINS+1, N_FFT):
            spec[k] = np.conj(spec[N_FFT-k])
        frame = np.fft.ifft(spec).real
        start = f * HOP
        end = min(start + N_FFT, out_len)
        n = end - start
        out[start:end] += frame[:n] * win[:n]
        wsum[start:end] += win[:n]**2
    mask = wsum > 1e-8
    out[mask] /= wsum[mask]
    return out

def run_model(sess, spec_real, spec_imag):
    n_frames = spec_real.shape[1]
    num_splits = (n_frames + FRAMES_PER_SPLIT - 1) // FRAMES_PER_SPLIT
    # 输入 [2, num_splits, 512, 1024] = [real/imag, split, freq, time]
    inp = np.zeros((2, num_splits, FREQ_BINS, FRAMES_PER_SPLIT), np.float32)
    for s in range(num_splits):
        start = s*FRAMES_PER_SPLIT
        end = min(start+FRAMES_PER_SPLIT, n_frames)
        n = end-start
        inp[0,s,:,:n] = spec_real[:,start:end]
        inp[1,s,:,:n] = spec_imag[:,start:end]
    res = sess.run(None, {'x': inp})[0]
    out_real = np.zeros((FREQ_BINS, n_frames), np.float32)
    out_imag = np.zeros((FREQ_BINS, n_frames), np.float32)
    for s in range(num_splits):
        start = s*FRAMES_PER_SPLIT
        end = min(start+FRAMES_PER_SPLIT, n_frames)
        n = end-start
        out_real[:, start:end] = res[0,s,:,:n]
        out_imag[:, start:end] = res[1,s,:,:n]
    return out_real, out_imag

def rms_db(x):
    r = np.sqrt(np.mean(x**2) + 1e-12)
    return 20*np.log10(r) if r > 0 else -96.0

def main():
    import onnxruntime as ort
    print("="*60)
    print("sherpa-onnx Spleeter 2-stems fp16 离线验证")
    print("="*60)
    signal, sr = read_wav(os.path.join(AUDIO_DIR, "original.wav"))
    print(f"输入: {len(signal)/sr:.1f}s mono {sr}Hz, RMS={rms_db(signal):.1f}dB")
    # 对齐到 N_FFT
    signal = signal[: (len(signal)//HOP)*HOP]

    # 模型加载
    v_sess = ort.InferenceSession(os.path.join(MODEL_DIR, "vocals.fp16.onnx"), providers=['CPUExecutionProvider'])
    a_sess = ort.InferenceSession(os.path.join(MODEL_DIR, "accompaniment.fp16.onnx"), providers=['CPUExecutionProvider'])

    t0 = time.time()
    sr_, si_ = stft(signal)
    v_real, v_imag = run_model(v_sess, sr_, si_)
    a_real, a_imag = run_model(a_sess, sr_, si_)
    vocals = istft(v_real, v_imag, len(signal))
    accomp = istft(a_real, a_imag, len(signal))
    elapsed = time.time() - t0
    print(f"分离耗时: {elapsed:.2f}s (实时因子 RTF={elapsed/(len(signal)/SR):.2f})")

    # 混音: 人声保留 + BGM 增益
    for gain in [0.0, 0.1, 0.3, 0.5, 1.0]:
        mixed = vocals + accomp * gain
        write_wav(os.path.join(AUDIO_DIR, f"processed_g{gain}.wav"), mixed)
        print(f"  gain={gain:.1f}: RMS={rms_db(mixed):.1f}dB")

    write_wav(os.path.join(AUDIO_DIR, "vocals.wav"), vocals)
    write_wav(os.path.join(AUDIO_DIR, "accompaniment.wav"), accomp)

    print("-"*60)
    print(f"原始完整混音 RMS: {rms_db(signal):.1f} dB")
    print(f"分离人声(vocals) RMS: {rms_db(vocals):.1f} dB")
    print(f"分离伴奏(accomp) RMS: {rms_db(accomp):.1f} dB")
    print(f"处理后 gain=0.1 RMS: {rms_db(vocals+accomp*0.1):.1f} dB")
    print(f"处理后 gain=0.0 RMS: {rms_db(vocals):.1f} dB")
    print("="*60)

if __name__ == "__main__":
    main()
