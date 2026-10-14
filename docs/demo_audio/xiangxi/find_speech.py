# -*- coding: utf-8 -*-
"""分析整段音频，找出对白人声集中的段落（基于语音频段能量占比）"""
import numpy as np
import wave, sys

wav_path = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\xiangxi\full.wav"
SR = 44100
N_FFT = 2048
HOP = 1024
SEG = 5.0  # 每段5秒

def read_wav(path):
    with wave.open(path, 'rb') as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data

x = read_wav(wav_path)
total_s = len(x) / SR
print(f"总时长 {total_s:.1f}s, 采样 {len(x)}")

# 每段计算: 整体RMS, 语音频段(300-3400Hz)能量占比, 谐波性
seg_len = int(SEG * SR)
n_seg = len(x) // seg_len
win = np.hanning(N_FFT)
freqs = np.fft.rfftfreq(N_FFT, 1/SR)

def seg_features(xs):
    rms = np.sqrt(np.mean(xs**2) + 1e-12)
    n_frames = max(1, (len(xs)-N_FFT)//HOP + 1)
    speech_energy = 0.0
    total_energy = 0.0
    for f in range(0, n_frames, 3):  # 抽样加速
        start = f*HOP
        frame = np.zeros(N_FFT)
        n = min(N_FFT, len(xs)-start)
        frame[:n] = xs[start:start+n]
        spec = np.abs(np.fft.rfft(frame*win))**2
        total_energy += spec.sum()
        mask = (freqs >= 300) & (freqs <= 3400)
        speech_energy += spec[mask].sum()
    speech_ratio = speech_energy / (total_energy + 1e-12)
    return rms, speech_ratio

print(f"\n{'段':>4} {'起止(s)':>12} {'RMS(dB)':>9} {'语音占比':>8}  判定")
print("-"*50)
candidates = []
for i in range(n_seg):
    xs = x[i*seg_len:(i+1)*seg_len]
    rms, ratio = seg_features(xs)
    db = 20*np.log10(rms) if rms > 1e-6 else -90
    # 语音判定：语音占比 > 0.15 且 RMS 高于 -30dB
    judge = ""
    if db > -30:
        if ratio > 0.18:
            judge = "✓ 对白/人声"
            candidates.append((i, db, ratio))
        elif ratio > 0.10:
            judge = "~ 混合"
    print(f"{i*SEG:>4.0f} {i*SEG:>6.0f}-{(i+1)*SEG:>5.0f} {db:>9.1f} {ratio:>8.2f}  {judge}")

print("\n对白候选段 (start_sec):", [c[0]*SEG for c in candidates])
