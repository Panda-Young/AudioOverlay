# canoe 抖音 per-session 静音 A/B 验证报告

**日期**: 2026-09-04  
**设备**: canoe (SM8850/Android16), serial=816d9a5, 1440x3200, adb root  
**App**: com.example.audiooverlay (PID 22662→25310, 被另一开发线重启)  
**音源**: 抖音 (com.ss.android.ugc.aweme, PID 16368, uid=10205)  
**静音机制**: MuteControlService + DynamicsProcessing(Int.MAX_VALUE, sid, null).setInputGainAllChannelsTo(-200f).enabled=true

## 机制确认

### MuteControlService 运行状态
```
isForeground=true foregroundId=1002 types=0x00000002
foregroundNoti=Notification(channel=mute_control_channel ...)
```
MuteControlService 作为轻量前台服务运行，注册 `ACTION_MUTE_SESSION` 接收器，保持进程存活以避免 MIUI/系统冻结广播。

### logcat 证据
```
09-04 04:14:36.962 25310 D AudioRouteLab: MuteControlService: receiver registered, process kept alive
09-04 04:18:17.542 25310 D AudioRouteLab: muteSession: already mounted on session 89
```
静音广播被正确接收，DynamicsProcessing -200dB 效果已挂载到 session 89。

### 历史铁证（Chrome）
据前序验证（docs\canoe_mute_verification.md），Chrome session 337  per-session 静音完全有效：
- 基线: -33.1 dB
- 静音后: -108.8 dB（底噪）
- 恢复后: -33.1 dB

## 抖音 session 动态变化

Global session refs 表中抖音 (uid=10205) 的 session ID 随视频切换递增：

| 时间点 | session 1 | session 2 | 说明 |
|--------|-----------|-----------|------|
| 首次抓取 | 49 | 81 | 初始播放 |
| 切视频后 | 49 | 89 | session 81→89（旧 effect 成 Orphan） |
| 持续播放 | 49 | 89 | 稳定 |

**关键发现**: 抖音有两个活跃 session（49 和 89），切视频时 session ID 递增（81→89），旧 session 的 DynamicsProcessing 效果成为 Orphan 无法自动迁移。这正是需要 session_remount.py 自动化的原因。

## 功率数据表

活跃输出线程: **AudioOut_25** (DEEP_BUFFER, 抖音原声) 和 **AudioOut_5D** (App AudioTrack 回放)

| 阶段 | 时间戳 | AudioOut_25 功率(dB) | AudioOut_5D 功率(dB) | 说明 |
|------|--------|----------------------|----------------------|------|
| 基线(播放中) | 04:14:56 | -29.1~-34.6 (avg -30) | - | 抖音原声播放 |
| 回放开启 | 04:17:52~04:18:37 | -23.8~-32.0 (avg -27) | -6.0~-15.1 (avg -9) | App AudioTrack 回放很响，掩盖抖音 |
| 回放关闭+静音 | 04:18:37后 | 数据截止，线程可能待机 | 数据截止 | 静音后无新功率数据 |

### 详细功率行（AudioOut_25, 1000ms分辨率）

**基线 (04:14:56)**:
```
-30.9 -29.1 -29.6 -30.4 -28.3 -27.7 -26.0 -27.0 -34.6 -28.2
```

**回放开启后 (04:18:37)**:
```
-26.7 -26.5 -23.8 -24.4 -25.4 -24.5 -24.1 -23.6 -23.4 -25.1
```

**AudioOut_5D (App AudioTrack, 04:18:37)**:
```
-8.4 -8.4 -6.0 -6.8 -7.7 -6.5 -6.5 -6.0 -6.2 -7.0
```

## 可复现命令

```powershell
# 确认抖音在跑
adb -s 816d9a5 shell pidof com.ss.android.ugc.aweme

# 抓基线
adb -s 816d9a5 shell dumpsys media.audio_flinger > docs\canoe_mute_baseline.txt

# 静音抖音 session（两个都要 mute）
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 49 --ez mute true
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 89 --ez mute true

# 等3秒抓静音后
Start-Sleep -Seconds 3
adb -s 816d9a5 shell dumpsys media.audio_flinger > docs\canoe_mute_muted.txt

# 恢复
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 49 --ez mute false
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 89 --ez mute false
```

## 分析与结论

### ✅ 已验证
1. **MuteControlService 正常运行**: 前台服务存活，MUTE_SESSION 接收器已注册
2. **静音效果可挂载**: logcat 确认 DynamicsProcessing -200dB 效果成功挂载到 session
3. **per-session 静音机制有效**: Chrome session 337 历史验证 -33.1→-108.8→-33.1dB 完全可逆
4. **抖音多 session 现象确认**: 两个活跃 session (49, 89)，切视频时 ID 递增

### ⚠️ 测试限制
1. **App AudioTrack 回放干扰**: SET_PLAYBACK=true 时 AudioOut_5D 输出 -6~-15dB（很响），掩盖了抖音原声的静音效果。需先 SET_PLAYBACK=false 隔离变量
2. **targetUid=-1 自捕获回环**: App 捕获自身 AudioTrack 输出，导致 RMS 高达 -7.8~-10.5dB
3. **另一开发线干扰**: App 进程被重启（PID 22662→25310），已挂载的静音效果丢失
4. **抖音视频自动暂停**: 测试期间抖音视频可能结束/暂停，导致 AudioOut_25 进入待机，静音后无新功率数据
5. **session 动态变化**: 切视频时 session ID 递增，旧效果成 Orphan，需自动化重挂

### 结论
per-session 静音机制在 canoe 上**功能正常**（MuteControlService + DynamicsProcessing），Chrome 场景已铁证有效。抖音场景因多 session、App 回放干扰、视频自动暂停和开发线干扰等因素，未能完成完整的 A/B 功率对比。建议：
1. 使用 session_remount.py 自动处理 session 递增
2. 测试时先 SET_PLAYBACK=false 隔离 App 回放
3. 设置 targetUid=10205 避免自捕获回环
4. 使用固定音源（测试音 HTML）避免抖音视频变化

## dumpsys 证据文件

- `docs\canoe_mute_baseline.txt` — 首次基线
- `docs\canoe_mute_baseline2.txt` — 第二次基线（App重启后）
- `docs\canoe_mute_muted_1.txt` — 第一次静音后
- `docs\canoe_mute_muted_2.txt` — 第二次静音后（logcat确认已挂载）
- `docs\canoe_noplayback_baseline.txt` — 关闭回放后的基线
- `docs\canoe_noplayback_muted.txt` — 关闭回放+静音后
