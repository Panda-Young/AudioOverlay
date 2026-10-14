# -*- coding: utf-8 -*-
"""a15 后处理修复：补全 meta.txt + 创建别名 + 生成频谱图"""
import os, json, shutil
import numpy as np
import soundfile as sf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

OUT_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\uvr_mdx\vr_bve4b_a15"
INPUT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\yimeng\original.wav"
LOAD_TIME = 0.1
SEPARATE_TIME = 88.2
CONFIG_NAME = "vr_bve4b_a15"

# 中文字体
for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams['font.family'] = [font_manager.FontProperties(fname=fp).get_name(), 'DejaVu Sans']
        break
plt.rcParams['axes.unicode_minus'] = False

N_FFT = 1024
HOP = 256

def rms_db(x):
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return -120.0
    r = np.sqrt(np.mean(x ** 2))
    return float(20 * np.log10(max(r, 1e-7)))

def to_mono(x):
    if x.ndim > 1:
        return x.mean(axis=1)
    return x

def spectrogram(x, sr):
    n_frames = max(1, (len(x) - N_FFT) // HOP + 1)
    win = np.hanning(N_FFT)
    S = np.zeros((N_FFT // 2, n_frames))
    for f in range(n_frames):
        frame = np.zeros(N_FFT)
        start = f * HOP
        n = min(N_FFT, len(x) - start)
        frame[:n] = x[start:start + n]
        S[:, f] = np.abs(np.fft.rfft(frame * win)[:N_FFT // 2])
    return S

def spec_db(S):
    S = S + 1e-10
    return 20 * np.log10(S / S.max())

# 1. 读取文件
vocals_fn = "original_(Vocals)_UVR-BVE-4B_SN-44100-1.wav"
inst_fn = "original_(Instrumental)_UVR-BVE-4B_SN-44100-1.wav"

orig, sr = sf.read(INPUT)
orig = to_mono(orig)
vocals, _ = sf.read(os.path.join(OUT_DIR, vocals_fn))
vocals = to_mono(vocals)
inst, _ = sf.read(os.path.join(OUT_DIR, inst_fn))
inst = to_mono(inst)

n = min(len(orig), len(vocals), len(inst))
orig, vocals, inst = orig[:n], vocals[:n], inst[:n]
audio_sec = n / sr

print(f"orig: {len(orig)} samples, sr={sr}, rms={rms_db(orig):.2f}dB")
print(f"vocals: rms={rms_db(vocals):.2f}dB")
print(f"inst: rms={rms_db(inst):.2f}dB")

# 2. processed RMS（文件已存在，只算指标）
processed_rms = {}
for g in [0.0, 0.1, 0.3, 0.5, 1.0]:
    proc = vocals * 1.0 + inst * g
    processed_rms[f"processed_g{g}"] = round(rms_db(proc), 2)

# 3. 写 meta.txt
rms_orig = rms_db(orig)
rms_vocals = rms_db(vocals)
rms_inst = rms_db(inst)
bgm_supp = rms_inst - rms_orig
rtf = SEPARATE_TIME / audio_sec

meta = {
    "model": "UVR-BVE-4B_SN-44100-1.pth",
    "architecture": "VR",
    "aggression": 15,
    "load_time_s": round(LOAD_TIME, 2),
    "separate_time_s": round(SEPARATE_TIME, 2),
    "audio_sec": round(audio_sec, 2),
    "rtf": round(rtf, 3),
    "rms_orig_db": round(rms_orig, 2),
    "rms_vocals_db": round(rms_vocals, 2),
    "rms_inst_db": round(rms_inst, 2),
    "bgm_suppression_db": round(bgm_supp, 2),
    "processed": processed_rms,
    "output_files": [vocals_fn, inst_fn],
}
meta_path = os.path.join(OUT_DIR, "meta.txt")
with open(meta_path, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
print(f"meta.txt written: {meta_path}")
print(json.dumps(meta, ensure_ascii=False, indent=2))

# 4. 创建别名
shutil.copy(os.path.join(OUT_DIR, vocals_fn), os.path.join(OUT_DIR, "vocals.wav"))
shutil.copy(os.path.join(OUT_DIR, inst_fn), os.path.join(OUT_DIR, "instrumental.wav"))
print("vocals.wav / instrumental.wav aliases created")

# 5. 生成频谱图
proc_g01 = vocals * 1.0 + inst * 0.1
files_data = [
    ("原始混音 (人声+BGM)", orig),
    ("分离结果 人声轨", vocals),
    ("分离结果 伴奏BGM轨", inst),
    ("处理后 (人声×1 + BGM×0.1)", proc_g01),
]
fig, axes = plt.subplots(4, 1, figsize=(12, 9))
for ax, (title, x) in zip(axes, files_data):
    S = spec_db(spectrogram(x, sr))
    ax.imshow(S, aspect='auto', origin='lower', cmap='magma',
              extent=[0, len(x) / sr, 0, sr / 2 / 1000], vmin=-80, vmax=0)
    ax.set_title(title, fontsize=11, fontweight='bold')
    ax.set_ylabel("kHz", fontsize=9)
    ax.set_xticks([])
    ax.tick_params(labelsize=8)
axes[-1].set_xlabel("时间 (秒)", fontsize=10)
axes[-1].set_xticks(np.arange(0, len(orig) / sr + 1, 2))

fig.suptitle(f"UVR 分离效果频谱对比 - {CONFIG_NAME}", fontsize=13, fontweight='bold')
fig.tight_layout(rect=[0, 0, 1, 0.97])
out_png = os.path.join(OUT_DIR, f"spectrogram_{CONFIG_NAME}.png")
fig.savefig(out_png, dpi=130)
plt.close(fig)
print(f"spectrogram saved: {out_png} ({os.path.getsize(out_png)} bytes)")
print("A15 POSTPROCESS DONE")
