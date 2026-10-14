# -*- coding: utf-8 -*-
"""生成 Spleeter 分离效果的频谱对比图 (处理前 vs vocals vs accompaniment vs 处理后BGM降90%)"""
import numpy as np
import wave, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LogNorm

# 配置中文字体 (Windows 微软雅黑)
for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyhbd.ttc"]:
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        fname = font_manager.FontProperties(fname=fp).get_name()
        plt.rcParams['font.family'] = [fname, 'DejaVu Sans']
        print("using font:", fname)
        break
plt.rcParams['axes.unicode_minus'] = False

AUDIO_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio"
SR = 44100
N_FFT = 1024
HOP = 256

def read_wav(path):
    with wave.open(path, 'rb') as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data

def spectrogram(x):
    n_frames = (len(x) - N_FFT) // HOP + 1
    win = np.hanning(N_FFT)
    S = np.zeros((N_FFT//2, n_frames))
    for f in range(n_frames):
        frame = np.zeros(N_FFT)
        start = f*HOP
        n = min(N_FFT, len(x)-start)
        frame[:n] = x[start:start+n]
        S[:, f] = np.abs(np.fft.rfft(frame*win)[:N_FFT//2])
    return S

def db(S):
    S = S + 1e-10
    return 20*np.log10(S / S.max())

files = {
    "原始混音 (人声+BGM)": "original.wav",
    "Spleeter 分离 人声": "vocals.wav",
    "Spleeter 分离 伴奏BGM": "accompaniment.wav",
    "处理后 (人声×1 + BGM×0.1)": "processed_g0.1.wav",
}

fig, axes = plt.subplots(4, 1, figsize=(12, 9))
for ax, (title, fn) in zip(axes, files.items()):
    x = read_wav(os.path.join(AUDIO_DIR, fn))
    S = db(spectrogram(x))
    img = ax.imshow(S, aspect='auto', origin='lower', cmap='magma',
                    extent=[0, len(x)/SR, 0, SR/2/1000], vmin=-80, vmax=0)
    ax.set_title(title, fontsize=11, fontweight='bold')
    ax.set_ylabel("kHz", fontsize=9)
    ax.set_xticks([])
    # 频带标注
    ax.axhline(0.3/1000*SR/2/1000*1000/1000, color='w', lw=0.3, alpha=0.3)
axes[-1].set_xlabel("时间 (秒)", fontsize=10)
axes[-1].set_xticks(np.arange(0, 13, 2))
for ax in axes:
    ax.tick_params(labelsize=8)

fig.suptitle("sherpa-onnx Spleeter 2-stems fp16 分离效果 — 处处吻 (离线真模型推理)", fontsize=13, fontweight='bold')
fig.tight_layout(rect=[0,0,1,0.97])
out = os.path.join(AUDIO_DIR, "spectrogram_compare.png")
fig.savefig(out, dpi=130)
print("saved:", out, os.path.getsize(out), "bytes")
