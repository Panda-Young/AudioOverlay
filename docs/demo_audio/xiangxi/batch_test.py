# -*- coding: utf-8 -*-
"""批量测试：对全视频每10秒段跑 Spleeter，统计 vocals/accomp 能量，定位人声保留最好的段落"""
import numpy as np
import wave, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio")
from spleeter_separate import read_wav, write_wav, stft, istft, run_model, rms_db

MODEL_DIR = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\app\src\main\assets\spleeter"
FULL = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\xiangxi\full.wav"
SR = 44100

def load_seg(x, start_s, dur_s):
    s = int(start_s * SR); e = s + int(dur_s * SR)
    return x[s:e]

def main():
    import onnxruntime as ort
    x = read_wav(FULL)[0]
    v_sess = ort.InferenceSession(os.path.join(MODEL_DIR, "vocals.fp16.onnx"), providers=['CPUExecutionProvider'])
    a_sess = ort.InferenceSession(os.path.join(MODEL_DIR, "accompaniment.fp16.onnx"), providers=['CPUExecutionProvider'])
    print(f"{'段':>6} {'RMS原':>7} {'RMS人声':>8} {'RMS伴奏':>8} {'人声占比':>8}  判定")
    print("-"*58)
    best = []
    for start in range(0, 165, 10):
        seg = load_seg(x, start, 10)
        seg = seg[: (len(seg)//256)*256]
        sr_, si_ = stft(seg)
        v = istft(*run_model(v_sess, sr_, si_), len(seg))
        a = istft(*run_model(a_sess, sr_, si_), len(seg))
        r_o, r_v, r_a = rms_db(seg), rms_db(v), rms_db(a)
        v_ratio = 10**(r_v/20) / (10**(r_v/20) + 10**(r_a/20) + 1e-12)
        judge = "✓ 人声保留好" if r_v > r_a - 3 else ("~ 混合" if r_v > r_a - 8 else "✗ 人声被遗漏")
        print(f"{start:>5}-{start+10:>3}s {r_o:>7.1f} {r_v:>8.1f} {r_a:>8.1f} {v_ratio*100:>7.1f}%  {judge}")
        if r_v > r_a - 3:
            best.append((start, r_v, r_a))
    if best:
        best.sort(key=lambda t: -t[1])
        print(f"\n最佳段: {best[0][0]}s, vocals={best[0][1]:.1f}dB, accomp={best[0][2]:.1f}dB")

if __name__ == "__main__":
    main()
