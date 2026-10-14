# -*- coding: utf-8 -*-
"""
UVR5 系（MDX-Net + VR Architecture）yimeng 素材实测对比
模型（2026-09-04 修正：Kim_Vocal_2 实为 MDX-Net，VR 改用 UVR-BVE-4B_SN-44100-1）：
  1. UVR-MDX-NET-Inst_HQ_3.onnx (MDX-Net)
  2. UVR-BVE-4B_SN-44100-1.pth (VR Architecture, aggression=5)
  3. UVR-BVE-4B_SN-44100-1.pth (VR Architecture, aggression=15)  # 用户要求BGM尽可能减小
"""
import os, sys, time, json, shutil
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
import numpy as np
import soundfile as sf
from audio_separator.separator import Separator

PY = sys.executable
INPUT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\yimeng\original.wav"
MODEL_DIR = r"C:\Users\16411\.cache\audio-separator\models"
OUT_ROOT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\uvr_mdx"

os.makedirs(MODEL_DIR, exist_ok=True)

def rms_db(x):
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0: return -120.0
    r = np.sqrt(np.mean(x ** 2))
    return 20 * np.log10(max(r, 1e-7))

CONFIGS = [
    ("mdx_inst_hq3", "UVR-MDX-NET-Inst_HQ_3.onnx", None),
    ("vr_bve4b_a5", "UVR-BVE-4B_SN-44100-1.pth", 5),
    ("vr_bve4b_a15", "UVR-BVE-4B_SN-44100-1.pth", 15),
]

def run(config):
    name, model, aggression = config
    out_dir = os.path.join(OUT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)

    kwargs = dict(
        log_level=30,
        model_file_dir=MODEL_DIR,
        output_dir=out_dir,
        output_format="WAV",
        use_soundfile=True,
    )
    if model.endswith(".pth"):
        kwargs["vr_params"] = {"batch_size": 1, "window_size": 512, "aggression": aggression, "enable_tta": False}

    print(f"\n=== {name} (model={model}, aggression={aggression}) ===", flush=True)
    sep = Separator(**kwargs)
    t0 = time.time()
    sep.load_model(model_filename=model)
    t_load = time.time() - t0
    print(f"load_model: {t_load:.1f}s", flush=True)

    t0 = time.time()
    outs = sep.separate(INPUT)
    t_sep = time.time() - t0
    print(f"separate: {t_sep:.1f}s, outputs={outs}", flush=True)

    # 读取原始和分离结果
    orig, sr = sf.read(INPUT)
    if orig.ndim > 1:
        orig = orig.mean(axis=1)
    # 找出 vocals 和 instrumentals
    vocals_path = None
    inst_path = None
    for f in outs:
        fl = f.lower()
        if "voc" in fl:
            vocals_path = f
        elif "inst" in fl or "accom" in fl or "no_voc" in fl:
            inst_path = f

    vocals, _ = sf.read(os.path.join(out_dir, vocals_path))
    inst, _ = sf.read(os.path.join(out_dir, inst_path))
    if vocals.ndim > 1: vocals = vocals.mean(axis=1)
    if inst.ndim > 1: inst = inst.mean(axis=1)

    # 长度对齐
    n = min(len(orig), len(vocals), len(inst))
    orig, vocals, inst = orig[:n], vocals[:n], inst[:n]

    # 生成多档 processed
    results = {}
    for g in [0.0, 0.1, 0.3, 0.5, 1.0]:
        proc = vocals * 1.0 + inst * g
        p = os.path.join(out_dir, f"processed_g{g:.1f}.wav")
        sf.write(p, proc, sr)
        results[f"processed_g{g}"] = rms_db(proc)

    # 拷贝原始
    shutil.copy(INPUT, os.path.join(out_dir, "original.wav"))

    meta = {
        "model": model,
        "architecture": "MDX-Net" if model.endswith(".onnx") else "VR",
        "aggression": aggression,
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
    with open(os.path.join(out_dir, "meta.txt"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)

for cfg in CONFIGS:
    try:
        run(cfg)
    except Exception as e:
        import traceback
        print(f"!!! {cfg[0]} FAILED: {e}", flush=True)
        traceback.print_exc()

print("\nALL DONE", flush=True)
