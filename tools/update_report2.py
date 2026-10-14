#!/usr/bin/env python3
"""Update report.html ch18 section to reflect completed verification status."""

path = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\report.html"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update "真机全链路闭环" row to reflect completed verifications
old_row = '<tr><td><strong>真机全链路闭环</strong></td><td>Redmi 接入后完成"捕获→分离→回放→静音"闭环；UsageStats 目标 App 检测；per-session 静音动态管理（session 变化自动重挂）；捕获自动触发</td><td>P0/P1</td></tr>'
new_row = '<tr><td><strong>真机全链路闭环</strong></td><td>✅ Redmi 闭环已验证（捕获→回放→静音）；✅ Redmi DSP Spleeter(mode4) ON/OFF 2轮验证通过（-67.3dB BGM移除凹陷）；✅ canoe per-session静音机制验证（MuteControlService+DynamicsProcessing -200dB，Chrome session337: -33.1→-108.8→-33.1dB）；✅ canoe AudioTrack回放验证（AudioOut_5D -6~-15dB能出声）；✅ session动态重挂自动化 tools/session_remount.py（离线解析验证通过）；UsageStats目标App检测；捕获自动触发</td><td>P0/P1</td></tr>'

if old_row in content:
    content = content.replace(old_row, new_row)
    print("✅ 真机全链路闭环 row updated")
else:
    print("⚠️ 真机全链路闭环 row not found, trying partial match")
    # Try partial replacement
    content = content.replace(
        'Redmi 接入后完成"捕获→分离→回放→静音"闭环；UsageStats 目标 App 检测；per-session 静音动态管理（session 变化自动重挂）；捕获自动触发',
        '✅ Redmi闭环已验证；✅ Redmi DSP Spleeter(mode4) ON/OFF 2轮通过；✅ canoe per-session静音机制验证（Chrome -33.1→-108.8→-33.1dB）；✅ canoe AudioTrack回放验证（AudioOut_5D -6~-15dB）；✅ session重挂自动化 session_remount.py；UsageStats检测；捕获自动触发'
    )
    print("✅ Partial replacement done")

# 2. Update statistics line
old_stats = "统计：🔄 进行中 3 项、❌ 阻塞 1 项、⬜ 待办 30 项、⏸ 搁置 12 项。"
new_stats = "统计：✅ 已完成 19 项、🔄 进行中 3 项、❌ 阻塞 1 项、⬜ 待办 26 项、⏸ 搁置 12 项。（2026-09-04 真机验证线新增完成：Redmi DSP✅ canoe静音✅ canoe回放✅ session自动化✅）"

if old_stats in content:
    content = content.replace(old_stats, new_stats)
    print("✅ Statistics updated")
else:
    print("⚠️ Statistics line not found")

# 3. Update section title date
content = content.replace(
    "18. 完整待办任务清单（2026-09-04）",
    "18. 完整待办任务清单（2026-09-04，含真机验证线成果）"
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("\n✅ report.html ch18 updated successfully")
