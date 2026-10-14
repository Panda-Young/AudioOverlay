# -*- coding: utf-8 -*-
"""生成 Spleeter 分离效果频谱对比图 (参数化)
用法: python spectrogram_compare.py <audio_dir> <out_png> <title>
"""
import numpy as np
import wave, os, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

AUDIO_DIR = sys.argv[1]
OUT_PNG = sys.argv[2]
TITLE = sys.argv[3]
SR = 44100
N_FFT = 1024
HOP = 256

for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyhbd.ttc"]:
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams['font.family'] = [font_manager.FontProperties(fname=fp).get_name(), 'DejaVu Sans']
        break
plt.rcParams['axes.unicode_minus'] = False

def read_wav(path):
    with wave.open(path, 'rb') as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

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

files = [
    ("原始混音 (人声+BGM)", "original.wav"),
    ("分离结果 人声轨", "vocals.wav"),
    ("分离结果 伴奏BGM轨", "accompaniment.wav"),
    ("处理后 (人声×1 + BGM×0.1)", "processed_g0.1.wav"),
]

fig, axes = plt.subplots(4, 1, figsize=(12, 9))
for ax, (title, fn) in zip(axes, files):
    x = read_wav(os.path.join(AUDIO_DIR, fn))
    S = db(spectrogram(x))
    ax.imshow(S, aspect='auto', origin='lower', cmap='magma',
              extent=[0, len(x)/SR, 0, SR/2/1000], vmin=-80, vmax=0)
    ax.set_title(title, fontsize=11, fontweight='bold')
    ax.set_ylabel("kHz", fontsize=9)
    ax.set_xticks([])
    ax.tick_params(labelsize=8)
axes[-1].set_xlabel("时间 (秒)", fontsize=10)
axes[-1].set_xticks(np.arange(0, len(read_wav(os.path.join(AUDIO_DIR, "original.wav")))/SR+1, 2))

fig.suptitle(TITLE, fontsize=13, fontweight='bold')
fig.tight_layout(rect=[0,0,1,0.97])
fig.savefig(OUT_PNG, dpi=130)
print("saved:", OUT_PNG, os.path.getsize(OUT_PNG), "bytes")
