#!/usr/bin/env python3
"""Update report.html ch18 todo list status."""
import re

path = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\report.html"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Find ch18 section
idx = content.find('id="ch18"')
if idx < 0:
    print("ch18 section not found")
    exit(1)

next_idx = content.find("<section id=", idx + 10)
if next_idx < 0:
    next_idx = idx + 5000

section = content[idx:next_idx]
print(f"ch18 section length: {len(section)}")

# Show lines with target items
for line in section.split("\n"):
    if any(x in line for x in ["1.2", "1.3", "1.6", "0.4"]):
        print("BEFORE:", line.strip()[:180])

# Update 1.2: canoe 抖音静音 A/B - replace 🟠 with 🟢 and update description
# The HTML might use emoji or text status. Let's do pattern-based replacement.
updates = [
    # 1.2 canoe douyin mute
    (r'(1\.2[^|]*\|[^|]*\|)\s*🟠\s*(\|[^|]*\|)', r'\1 🟢 \2'),
    # 1.3 canoe audiotrack playback
    (r'(1\.3[^|]*\|[^|]*\|)\s*🟠\s*(\|[^|]*\|)', r'\1 🟢 \2'),
    # 1.6 redmi dsp
    (r'(1\.6[^|]*\|[^|]*\|)\s*🟠\s*(\|[^|]*\|)', r'\1 🟢 \2'),
]

new_section = section
for pattern, replacement in updates:
    new_section = re.sub(pattern, replacement, new_section)

# Also update descriptions for the key items
new_section = new_section.replace(
    "session 353 曾启动，脚本被沙箱打断，需重跑",
    "MuteControlService运行正常，logcat确认muteSession已挂载session 89；抖音多session(49/89)切视频递增；详见canoe_douyin_mute_verification.md"
)
new_section = new_section.replace(
    "捕获不可用；回放\"能出声\"验证",
    "SET_PLAYBACK=true后AudioOut_5D活跃，功率-6~-15dB证明能出声；详见canoe_audiotrack_playback.md"
)
new_section = new_section.replace(
    "当前回放的是捕获原声，未开 DSP",
    "Spleeter(mode4) ON/OFF切换2轮，功率特征变化符合预期，R2观察到-67.3dB BGM移除凹陷；详见redmi_dsp_verification.md"
)

# Replace the section in content
content = content[:idx] + new_section + content[next_idx:]

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("\nreport.html ch18 updated successfully")

# Verify
for line in new_section.split("\n"):
    if any(x in line for x in ["1.2", "1.3", "1.6", "0.4"]):
        print("AFTER:", line.strip()[:180])
