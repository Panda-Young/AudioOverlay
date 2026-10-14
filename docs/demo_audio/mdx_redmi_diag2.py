# -*- coding: utf-8 -*-
"""修正版：精细 offset 扫描（-260000..-200000, step 960）+ 匹配段 per-2s corr"""
import numpy as np, soundfile as sf

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\dump_verify"
app, _ = sf.read(BASE + r"\redmi_output2.wav", dtype="float32")
pc, _ = sf.read(BASE + r"\pc_vocals.wav", dtype="float32")

def corr(a, b):
    a = a - a.mean(); b = b - b.mean()
    den = np.sqrt((a*a).sum() * (b*b).sum())
    return float((a*b).sum() / den) if den > 0 else 0.0

# 假设 App 输出整体对应 PC 的 [O:O+len(app)]（O 可负）
print("fine scan around -240000 (step 960):")
best = []
for O in range(-260000, -200001, 960):
    n = min(len(app), len(pc) - O)
    c = corr(app[:n, 0], pc[O:O+n, 0])
    best.append((c, O))
best.sort(reverse=True)
for c, O in best[:6]:
    print(f"  offset={O} corr={c:.4f}")

O = best[0][1]
print(f"\nper-2s corr at offset={O} (app t -> pc t+{O}):")
sr = 48000; seg2 = sr * 2
n = min(len(app), len(pc) - O)
for i in range(0, n - seg2, seg2):
    a = app[i:i+seg2, 0]; b = pc[O+i:O+i+seg2, 0]
    print(f"  app_t={i/sr:6.2f}s corr={corr(a,b):.4f}")

# 也扫正偏移（App 对应 PC 前段）
print("\npositive offsets scan (step 960):")
best2 = []
for O in range(0, 80001, 960):
    n = min(len(app), len(pc) - O)
    c = corr(app[:n, 0], pc[O:O+n, 0])
    best2.append((c, O))
best2.sort(reverse=True)
for c, O in best2[:4]:
    print(f"  offset={O} corr={c:.4f}")
