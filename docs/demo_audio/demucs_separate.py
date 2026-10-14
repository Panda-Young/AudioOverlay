# -*- coding: utf-8 -*-
"""Demucs htdemucs 4-stem 分离脚本 (参数化)：输入 wav + 输出目录
用法: python demucs_separate.py <in_wav> <out_dir>
输出: 每个stem wav + vocals/accompaniment + processed_g{0.0,0.1,0.3,0.5,1.0}.wav + meta.txt
"""
import os, sys, time
import numpy as np
import torch
import soundfile as sf

def rms_db(x):
    x = np.asarray(x, dtype=np.float32)
    r = np.sqrt(np.mean(x**2) + 1e-12)
    return 20*np.log10(r) if r > 0 else -96.0

def write_wav(path, data, sr):
    d = np.clip(data, -1.0, 1.0)
    sf.write(path, d, sr, subtype='PCM_16')

def main():
    in_wav, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    data, sr = sf.read(in_wav, dtype='float32', always_2d=True)  # [n, ch]
    sr = int(sr)
    if data.shape[1] > 1:
        data = data.mean(axis=1, keepdims=True)
    n = data.shape[0]
    # htdemucs 需要 stereo 输入：mono 复制为双通道
    mono = np.ascontiguousarray(data[:, 0], dtype=np.float32)
    stereo_in = np.stack([mono, mono], axis=0)  # [2, n]
    x_t = torch.from_numpy(stereo_in).unsqueeze(0)  # [1, 2, n]

    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    t_dl = time.time()
    model = get_model('htdemucs')
    model.eval()
    dl_time = time.time() - t_dl

    t0 = time.time()
    with torch.no_grad():
        stems = apply_model(model, x_t, device='cpu')[0]  # [stems, 1, n] (mono in -> ch out)
    dt = time.time() - t0

    names = list(model.sources)  # e.g. ['drums','bass','other','vocals']
    stem_maps = {nm: i for i, nm in enumerate(names)}
    # 每stem转numpy mono
    stem_arrays = {}
    for nm, i in stem_maps.items():
        arr = stems[i, 0].numpy()  # [n]
        if arr.ndim > 1:
            arr = arr.mean(axis=0)
        stem_arrays[nm] = arr.astype(np.float32)

    # 保存每个stem
    for nm, arr in stem_arrays.items():
        write_wav(os.path.join(out_dir, f"stem_{nm}.wav"), arr, sr)

    # vocals 与 accompaniment(其余stem和)
    vocals = stem_arrays.get('vocals', np.zeros(n, np.float32))
    accomp = np.zeros(n, np.float32)
    for nm in names:
        if nm != 'vocals':
            accomp = accomp + stem_arrays[nm]
    write_wav(os.path.join(out_dir, "vocals.wav"), vocals, sr)
    write_wav(os.path.join(out_dir, "accompaniment.wav"), accomp, sr)

    # 处理混合 (vocals + accomp*gain)
    gains = [0.0, 0.1, 0.3, 0.5, 1.0]
    for g in gains:
        write_wav(os.path.join(out_dir, f"processed_g{g}.wav"), vocals + accomp*g, sr)

    # 量化
    orig_rms = rms_db(data[:, 0])
    voc_rms = rms_db(vocals)
    acc_rms = rms_db(accomp)
    # BGM残余: 处理后(processed_g0.1)相比原始的能量抑制 (dB差)
    mix01 = vocals + accomp*0.1
    mix01_rms = rms_db(mix01)

    meta = (f"input={os.path.basename(in_wav)}\n"
            f"duration_s={n/sr:.1f}\nsr={sr}\n"
            f"model=htdemucs (4-stem: {','.join(names)})\n"
            f"model_download_s={dl_time:.1f}\n"
            f"separation_s={dt:.2f}\nrtf={dt/(n/sr):.3f}\n"
            f"original_rms={orig_rms:.1f}\n"
            f"vocals_rms={voc_rms:.1f}\n"
            f"accomp_rms={acc_rms:.1f}\n"
            f"bgm_suppression_g0.1={(orig_rms - mix01_rms):.1f}dB\n"
            f"processed_g0.1_rms={mix01_rms:.1f}\n")
    for nm, arr in stem_arrays.items():
        meta += f"stem_{nm}_rms={rms_db(arr):.1f}\n"
    with open(os.path.join(out_dir, "meta.txt"), "w", encoding="utf-8") as f:
        f.write(meta)
    print(meta)
    print(f"stems: {names}")
    print(f"模型下载 {dl_time:.1f}s, 分离 {dt:.2f}s, RTF={dt/(n/sr):.3f}")
    print(f"原始 {orig_rms:.1f} | 人声 {voc_rms:.1f} | 伴奏 {acc_rms:.1f} | g0.1混音 {mix01_rms:.1f} (抑制 {(orig_rms-mix01_rms):.1f}dB)")

if __name__ == "__main__":
    main()
