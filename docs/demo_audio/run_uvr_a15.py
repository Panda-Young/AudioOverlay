# -*- coding: utf-8 -*-
"""只跑 vr_bve4b_a15（重跑被中断的档位）"""
import os, sys, time, json, shutil
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
import numpy as np
import soundfile as sf
from audio_separator.separator import Separator

INPUT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\yimeng\original.wav"
MODEL_DIR = r"C:\Users\16411\.cache\audio-separator\models"
OUT_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\uvr_mdx\vr_bve4b_a15"

def rms_db(x):
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0: return -120.0
    r = np.sqrt(np.mean(x ** 2))
    return 20 * np.log10(max(r, 1e-7))

os.makedirs(OUT_DIR, exist_ok=True)
print("=== vr_bve4b_a15 (model=UVR-BVE-4B_SN-44100-1.pth, aggression=15) RETRY ===", flush=True)
sep = Separator(
    log_level=30,
    model_file_dir=MODEL_DIR,
    output_dir=OUT_DIR,
    output_format="WAV",
    use_soundfile=True,
    vr_params={"batch_size": 1, "window_size": 512, "aggression": 15, "enable_tta": False},
)
t0 = time.time()
sep.load_model(model_filename="UVR-BVE-4B_SN-44100-1.pth")
t_load = time.time() - t0
print(f"load_model: {t_load:.1f}s", flush=True)

t0 = time.time()
outs = sep.separate(INPUT)
t_sep = time.time() - t0
print(f"separate: {t_sep:.1f}s, outputs={outs}", flush=True)

orig, sr = sf.read(INPUT)
if orig.ndim > 1: orig = orig.mean(axis=1)
vocals_path = inst_path = None
for f in outs:
    fl = f.lower()
    if "voc" in fl: vocals_path = f
    elif "inst" in fl or "accom" in fl or "no_voc" in fl: inst_path = f
vocals, _ = sf.read(os.path.join(OUT_DIR, vocals_path))
inst, _ = sf.read(os.path.join(OUT_DIR, inst_path))
if vocals.ndim > 1: vocals = vocals.mean(axis=1)
if inst.ndim > 1: inst = inst.mean(axis=1)
n = min(len(orig), len(vocals), len(inst))
orig, vocals, inst = orig[:n], vocals[:n], inst[:n]

results = {}
for g in [0.0, 0.1, 0.3, 0.5, 1.0]:
    proc = vocals * 1.0 + inst * g
    p = os.path.join(OUT_DIR, f"processed_g{g:.1f}.wav")
    sf.write(p, proc, sr)
    results[f"processed_g{g}"] = rms_db(proc)
shutil.copy(INPUT, os.path.join(OUT_DIR, "original.wav"))

meta = {
    "model": "UVR-BVE-4B_SN-44100-1.pth",
    "architecture": "VR",
    "aggression": 15,
    "load_time_s": round(t_load, 2),
    "separate_time_s": round(t_sep, 2),
    "audio_sec": round(n / sr, 2),
    "rtf": round(t_sep / (n / sr), 3),
    "rms_orig_db": round(rms_db(orig), 2),
    "rms_vocals_db": round(rms_db(vocals), 2),
    "rms_inst_db": round(rms_db(inst), 2),
    "bgm_suppression_db": round(rms_db(inst) - rms_db(orig), 2),
    "processed": results,
    "output_files": outs,
}
with open(os.path.join(OUT_DIR, "meta.txt"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
print("A15 DONE", flush=True)
