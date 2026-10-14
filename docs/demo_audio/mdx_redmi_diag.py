# -*- coding: utf-8 -*-
"""Redmi 全链 corr=0.53 诊断：精细 offset 扫描 + App output 段 RMS + input 静音分布"""
import numpy as np, soundfile as sf

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\dump_verify"
app, _ = sf.read(BASE + r"\redmi_output2.wav", dtype="float32")
pc, _ = sf.read(BASE + r"\pc_vocals.wav", dtype="float32")
inp, _ = sf.read(BASE + r"\redmi_input2.wav", dtype="float32")
print("app", app.shape, "pc", pc.shape, "input", inp.shape)

def corr(a, b):
    a = a - a.mean(); b = b - b.mean()
    den = np.sqrt((a*a).sum() * (b*b).sum())
    return float((a*b).sum() / den) if den > 0 else 0.0

# 1) App output 每 0.5s 段 RMS（找中途跳跃/静音）
sr = 48000
seg = sr // 2
print("\nApp output RMS per 0.5s:")
for i in range(0, app.shape[0] - seg, seg):
    a = app[i:i+seg]
    print(f"  t={i/sr:6.2f}s rms={np.sqrt((a*a).mean()):.4f}")

# 2) input 每 0.5s 段 RMS（找静音段）
print("\ninput RMS per 0.5s:")
for i in range(0, inp.shape[0] - seg, seg):
    a = inp[i:i+seg]
    print(f"  t={i/sr:6.2f}s rms={np.sqrt((a*a).mean()):.4f}")

# 3) 精细 offset 扫描（对 L 声道），步进 960，范围 -320000..+80000
print("\nfine offset scan (step 960):")
best = []
for off in range(-320000, 80001, 960):
    if off < 0:
        a = app[-off:, 0]; b = pc[:off, 0] if off < 0 else pc[:len(a), 0]
        n = min(len(a), len(b))
        c = corr(a[:n], b[:n])
    else:
        a = app[:, 0]; b = pc[off:off+len(a), 0]
        n = min(len(a), len(b))
        c = corr(a[:n], b[:n])
    best.append((c, off))
best.sort(reverse=True)
for c, o in best[:8]:
    print(f"  offset={o} corr={c:.4f}")

# 4) 分段对齐检查：假设 offset=O，检查每 2s 段 corr
O = best[0][1]
print(f"\nper-2s corr at best offset={O}:")
seg2 = sr * 2
if O < 0:
    a0 = app[-O:]; b0 = pc[:len(app)-(-O)] if False else pc[:len(a0)]
    # App 从 -O 开始对应 PC 开头
    start_app, start_pc = -O, 0
else:
    start_app, start_pc = 0, O
n = min(len(app)-start_app, len(pc)-start_pc)
for i in range(0, n - seg2, seg2):
    a = app[start_app+i:start_app+i+seg2, 0]; b = pc[start_pc+i:start_pc+i+seg2, 0]
    print(f"  t={i/sr:6.2f}s corr={corr(a,b):.4f}")
