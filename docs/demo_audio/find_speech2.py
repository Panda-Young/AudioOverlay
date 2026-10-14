# -*- coding: utf-8 -*-
"""分析音频段落，找对白人声集中的段落 (参数化)
用法: python find_speech2.py <wav_path> [seg_seconds]
"""
import numpy as np
import wave, sys

wav_path = sys.argv[1]
SEG = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
SR = 44100
N_FFT = 2048
HOP = 1024

def read_wav(path):
    with wave.open(path, 'rb') as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data

x = read_wav(wav_path)
total_s = len(x) / SR
print(f"总时长 {total_s:.1f}s, 采样 {len(x)}")

seg_len = int(SEG * SR)
n_seg = len(x) // seg_len
win = np.hanning(N_FFT)
freqs = np.fft.rfftfreq(N_FFT, 1/SR)

def seg_features(xs):
    rms = np.sqrt(np.mean(xs**2) + 1e-12)
    n_frames = max(1, (len(xs)-N_FFT)//HOP + 1)
    speech_energy = 0.0
    total_energy = 0.0
    for f in range(0, n_frames, 3):
        start = f*HOP
        frame = np.zeros(N_FFT)
        n = min(N_FFT, len(xs)-start)
        frame[:n] = xs[start:start+n]
        spec = np.abs(np.fft.rfft(frame*win))**2
        total_energy += spec.sum()
        mask = (freqs >= 300) & (freqs <= 3400)
        speech_energy += spec[mask].sum()
    return rms, speech_energy / (total_energy + 1e-12)

print(f"\n{'段':>4} {'起止(s)':>12} {'RMS(dB)':>9} {'语音占比':>8}  判定")
print("-"*52)
for i in range(n_seg):
    xs = x[i*seg_len:(i+1)*seg_len]
    rms, ratio = seg_features(xs)
    db = 20*np.log10(rms) if rms > 1e-6 else -90
    judge = ""
    if db > -30:
        judge = "✓ 对白/人声" if ratio > 0.18 else ("~ 混合" if ratio > 0.10 else "低语音")
    print(f"{i*SEG:>4.0f} {i*SEG:>6.0f}-{(i+1)*SEG:>5.0f} {db:>9.1f} {ratio:>8.2f}  {judge}")
