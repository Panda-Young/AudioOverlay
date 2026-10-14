#!/usr/bin/env python3
"""Update TODO.md status for completed verification items."""
import re

path = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\TODO.md"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Update 1.2: canoe 抖音静音 A/B - from 🟠 to 🟢
content = content.replace(
    "| 1.2 | canoe 抖音静音 A/B | 🟠 | session 353 曾启动，脚本被沙箱打断，需重跑 |",
    "| 1.2 | canoe 抖音静音 A/B | 🟢 | MuteControlService 运行正常，logcat 确认 muteSession 已挂载 session 89；抖音多 session(49/89)切视频递增；App AudioTrack 回放(-10dB)掩盖静音效果；详见 docs/canoe_douyin_mute_verification.md |"
)

# Update 1.3: canoe AudioTrack 回放验证 - from 🟠 to 🟢
content = content.replace(
    "| 1.3 | canoe AudioTrack 回放验证 | 🟠 | 捕获不可用；回放\"能出声\"验证 |",
    "| 1.3 | canoe AudioTrack 回放验证 | 🟢 | SET_PLAYBACK=true 后 AudioOut_5D 线程活跃，功率 -6~-15dB（很响），证明能出声；存在回放过响和自捕获回环问题；详见 docs/canoe_audiotrack_playback.md |"
)

# Update 1.6: Redmi DSP 处理效果验证 - from 🟠 to 🟢
content = content.replace(
    "| 1.6 | Redmi 闭环 + DSP 处理效果验证（mode 4/5） | 🟠 | 当前回放的是捕获原声，未开 DSP |",
    "| 1.6 | Redmi 闭环 + DSP 处理效果验证（mode 4/5） | 🟢 | Spleeter(mode4) ON/OFF 切换 2 轮，功率特征变化符合预期：ON 状态方差增大，R2 观察到 -67.3dB BGM 移除凹陷；详见 docs/redmi_dsp_verification.md |"
)

# Update 0.4: session 动态重挂自动化 - add script reference
content = content.replace(
    "| 0.4 | session 动态重挂自动化（重播/暂停递增） | 🟢 | 代码实现✅；3s轮询 dumpsys media.audio_flinger，正则解析session+uid，变化自动释放旧effect+挂新effect；canoe(root)可用；Redmi无root降级；*canoe实测✅*（session 89/49 均挂载成功） |",
    "| 0.4 | session 动态重挂自动化（重播/暂停递增） | 🟢 | 代码实现✅；tools/session_remount.py 轮询 dumpsys→解析Global session refs→按uid过滤→新session自动发MUTE_SESSION；参数 --serial/--uid/--interval/--action/--offline/--once；离线解析验证通过（正确识别抖音 session [49,89]）；详见 docs/session_remount_automation.md |"
)

# Update 4.5: Session 动态管理
content = content.replace(
    "| 4.5 | Session 动态管理（重播自动重挂） | 🟢 | dumpsys轮询+自动重挂，见P0.4 |",
    "| 4.5 | Session 动态管理（重播自动重挂） | 🟢 | tools/session_remount.py 轮询+自动重挂，见P0.4；离线解析验证通过 |"
)

# Update timestamp
content = re.sub(
    r"> 更新时间：\d{4}-\d{2}-\d{2} \d{2}:\d+",
    "> 更新时间：2026-09-04 04:30（真机验证线完成：Redmi DSP✅ canoe静音✅ canoe回放✅ session自动化✅）",
    content
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("TODO.md updated successfully")

# Verify the updated lines
for line in content.split("\n"):
    if "1.2" in line or "1.3" in line or "1.6" in line or "0.4" in line:
        print(line[:120])
