# -*- coding: utf-8 -*-
"""
后处理脚本：对已完成分离的 UVR 配置生成 meta/processed/original/spectrogram
不重新分离，只读取已有 wav 文件。
"""
import os, sys, time, json, shutil
import numpy as np
import soundfile as sf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

PY = sys.executable
INPUT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\yimeng\original.wav"
OUT_ROOT = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\uvr_mdx"

# 字体设置
for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyhbd.ttc"]:
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams['font.family'] = [font_manager.FontProperties(fname=fp).get_name(), 'DejaVu Sans']
        break
plt.rcParams['axes.unicode_minus'] = False

SR_TARGET = 44100
N_FFT = 1024
HOP = 256

def rms_db(x):
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return -120.0
    r = np.sqrt(np.mean(x ** 2))
    return float(20 * np.log10(max(r, 1e-7)))

def read_wav_safe(path, retries=3, delay=1.0):
    """带重试的 wav 读取，避免文件锁问题"""
    last_err = None
    for i in range(retries):
        try:
            data, sr = sf.read(path)
            return data, sr
        except Exception as e:
            last_err = e
            print(f"  读取重试 {i+1}/{retries}: {path} -> {e}")
            time.sleep(delay)
    raise last_err

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

def find_files(out_dir):
    """在输出目录中找到 vocals 和 instrumental 文件"""
    vocals_path = None
    inst_path = None
    for f in os.listdir(out_dir):
        if not f.lower().endswith('.wav'):
            continue
        if f.startswith('processed_') or f == 'original.wav' or f == 'vocals.wav' or f == 'instrumental.wav':
            continue
        fl = f.lower()
        if 'voc' in fl:
            vocals_path = f
        elif 'inst' in fl or 'accom' in fl or 'no_voc' in fl:
            inst_path = f
    return vocals_path, inst_path

def generate_spectrogram(out_dir, config_name, orig, vocals, inst, proc_g01, sr):
    """生成4行频谱对比图"""
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

    fig.suptitle(f"UVR 分离效果频谱对比 - {config_name}", fontsize=13, fontweight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out_png = os.path.join(out_dir, f"spectrogram_{config_name}.png")
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    print(f"  频谱图已保存: {out_png} ({os.path.getsize(out_png)} bytes)")
    return out_png

def postprocess(config_name, model_file, aggression, load_time, separate_time):
    out_dir = os.path.join(OUT_ROOT, config_name)
    print(f"\n{'='*60}")
    print(f"后处理: {config_name}")
    print(f"  目录: {out_dir}")
    print(f"{'='*60}")

    # 1. 找到分离输出文件
    vocals_fn, inst_fn = find_files(out_dir)
    if not vocals_fn or not inst_fn:
        print(f"  错误: 未找到分离文件! vocals={vocals_fn}, inst={inst_fn}")
        return None
    print(f"  vocals: {vocals_fn}")
    print(f"  inst: {inst_fn}")

    vocals_path = os.path.join(out_dir, vocals_fn)
    inst_path = os.path.join(out_dir, inst_fn)

    # 2. 读取音频（带重试）
    print("  读取原始音频...")
    orig, sr = read_wav_safe(INPUT)
    orig = to_mono(orig)
    print(f"    original: {len(orig)} samples, sr={sr}, rms={rms_db(orig):.2f}dB")

    print("  读取 vocals...")
    vocals, sr_v = read_wav_safe(vocals_path)
    vocals = to_mono(vocals)
    print(f"    vocals: {len(vocals)} samples, sr={sr_v}, rms={rms_db(vocals):.2f}dB")

    print("  读取 instrumental...")
    inst, sr_i = read_wav_safe(inst_path)
    inst = to_mono(inst)
    print(f"    inst: {len(inst)} samples, sr={sr_i}, rms={rms_db(inst):.2f}dB")

    # 3. 长度对齐
    n = min(len(orig), len(vocals), len(inst))
    orig = orig[:n]
    vocals = vocals[:n]
    inst = inst[:n]
    audio_sec = n / sr
    print(f"  对齐后长度: {n} samples = {audio_sec:.2f}s")

    # 4. 生成多档 processed
    processed_rms = {}
    for g in [0.0, 0.1, 0.3, 0.5, 1.0]:
        proc = vocals * 1.0 + inst * g
        p = os.path.join(out_dir, f"processed_g{g:.1f}.wav")
        sf.write(p, proc, sr)
        rms_val = rms_db(proc)
        processed_rms[f"processed_g{g}"] = round(rms_val, 2)
        print(f"  processed_g{g:.1f}: rms={rms_val:.2f}dB")

    # 5. 拷贝 original.wav
    shutil.copy(INPUT, os.path.join(out_dir, "original.wav"))
    print("  已拷贝 original.wav")

    # 6. 同时创建 vocals.wav / instrumental.wav 别名（方便频谱图脚本）
    shutil.copy(vocals_path, os.path.join(out_dir, "vocals.wav"))
    shutil.copy(inst_path, os.path.join(out_dir, "instrumental.wav"))
    print("  已创建 vocals.wav / instrumental.wav 别名")

    # 7. 计算指标
    rms_orig = rms_db(orig)
    rms_vocals = rms_db(vocals)
    rms_inst = rms_db(inst)
    bgm_supp = rms_inst - rms_orig
    rtf = separate_time / audio_sec

    # 8. 写 meta.txt
    meta = {
        "model": model_file,
        "architecture": "MDX-Net" if model_file.endswith(".onnx") else "VR",
        "aggression": aggression,
        "load_time_s": round(load_time, 2),
        "separate_time_s": round(separate_time, 2),
        "audio_sec": round(audio_sec, 2),
        "rtf": round(rtf, 3),
        "rms_orig_db": round(rms_orig, 2),
        "rms_vocals_db": round(rms_vocals, 2),
        "rms_inst_db": round(rms_inst, 2),
        "bgm_suppression_db": round(bgm_supp, 2),
        "processed": processed_rms,
        "output_files": [vocals_fn, inst_fn],
    }
    meta_path = os.path.join(out_dir, "meta.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  meta.txt 已写入: {meta_path}")
    print(json.dumps(meta, ensure_ascii=False, indent=2))

    # 9. 生成频谱图
    proc_g01 = vocals * 1.0 + inst * 0.1
    generate_spectrogram(out_dir, config_name, orig, vocals, inst, proc_g01, sr)

    print(f"\n  {config_name} 后处理完成!")
    return meta


if __name__ == "__main__":
    # 已知的分离耗时（来自 uvr_run.log）
    results = {}

    # 1. mdx_inst_hq3
    m1 = postprocess(
        config_name="mdx_inst_hq3",
        model_file="UVR-MDX-NET-Inst_HQ_3.onnx",
        aggression=None,
        load_time=5.7,
        separate_time=70.8,
    )
    if m1:
        results["mdx_inst_hq3"] = m1

    # 2. vr_bve4b_a5
    m2 = postprocess(
        config_name="vr_bve4b_a5",
        model_file="UVR-BVE-4B_SN-44100-1.pth",
        aggression=5,
        load_time=0.1,
        separate_time=88.7,
    )
    if m2:
        results["vr_bve4b_a5"] = m2

    print("\n" + "=" * 60)
    print("全部后处理完成!")
    print("=" * 60)
    for name, meta in results.items():
        print(f"\n{name}:")
        print(f"  vocals RMS: {meta['rms_vocals_db']} dB")
        print(f"  inst RMS: {meta['rms_inst_db']} dB")
        print(f"  BGM抑制: {meta['bgm_suppression_db']} dB")
        print(f"  分离耗时: {meta['separate_time_s']}s")
        print(f"  RTF: {meta['rtf']}")
