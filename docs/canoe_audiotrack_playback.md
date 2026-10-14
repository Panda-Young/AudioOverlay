# canoe AudioTrack 回放验证报告

**日期**: 2026-09-04  
**设备**: canoe (SM8850/Android16), serial=816d9a5  
**App**: com.example.audiooverlay (PID 25310)  
**验证目标**: SET_PLAYBACK true 后 AudioTrack 是否能出声（功率上升）

## 测试方法

1. 确认 App 捕获服务运行（AudioCaptureService + MuteControlService）
2. 抖音播放视频作为捕获音源
3. 发送 SET_PLAYBACK --ez playback_enabled true
4. 抓 dumpsys media.audio_flinger，观察 AudioOut 线程功率变化
5. 发送 SET_PLAYBACK false，对比

## 可复现命令

```powershell
# 开启回放
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_PLAYBACK --ez playback_enabled true

# 抓功率
Start-Sleep -Seconds 3
adb -s 816d9a5 shell dumpsys media.audio_flinger > docs\canoe_playback_on.txt

# 关闭回放
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_PLAYBACK --ez playback_enabled false
```

## 功率数据表

| 状态 | 活跃线程 | 功率范围(dB) | 平均(dB) | 说明 |
|------|---------|-------------|---------|------|
| 回放 OFF | AudioOut_25 | -26~-35 | -30 | 仅抖音原声 |
| 回放 ON | AudioOut_25 | -23~-32 | -27 | 抖音原声（略有上升） |
| 回放 ON | AudioOut_5D (新) | -6.0~-15.1 | -9.0 | **App AudioTrack 回放，很响！** |

### AudioOut_5D 详细功率行（回放 ON, 1000ms分辨率）

```
09-04 04:17:52: [-12.9 -12.0 -15.7 -15.5 -10.8 -14.9 -14.1 -13.6 -13.2 -13.3]
09-04 04:18:02: [-8.4 -11.8 -9.9 -10.5 -10.9 -10.6 -10.6 -10.1 -11.4 -10.7]
09-04 04:18:12: [-9.8 -7.6 -10.0 -8.9 -8.7 -8.9 -9.3 -8.6 -8.8 -10.0]
09-04 04:18:22: [-9.6 -7.0 -7.1 -9.0 -6.9 -6.8 -6.7 -6.3 -8.3 -7.4]
09-04 04:18:32: [-8.4 -8.4 -6.0 -6.8 -7.7 -6.5 -6.5 -6.0 -6.2 -7.0]
```

## AudioTrack 配置（源码）

```kotlin
// AudioCaptureService.initAudioTrack()
audioTrack = AudioTrack.Builder()
    .setAudioAttributes(
        AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
            .build()
    )
    .setAudioFormat(
        AudioFormat.Builder()
            .setSampleRate(48000)
            .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .build()
    )
    .setBufferSizeInBytes(bufferSize)
    .setTransferMode(AudioTrack.MODE_STREAM)
    .build()
audioTrack!!.play()
```

## 分析与结论

### ✅ 已验证
1. **AudioTrack 能出声**: SET_PLAYBACK=true 后，新的输出线程 AudioOut_5D 出现，功率高达 -6~-15dB（很响），证明 AudioTrack 回放正常工作
2. **回放可控制**: SET_PLAYBACK 广播正确切换 playbackEnabled 状态
3. **AudioTrack 配置正确**: 48000Hz, 立体声, PCM_16BIT, USAGE_MEDIA, MODE_STREAM

### ⚠️ 已知问题
1. **回放过响**: AudioOut_5D 功率 -6~-15dB 远高于抖音原声 (-27~-30dB)，可能存在增益过高问题
2. **自捕获回环**: targetUid=-1 时 App 捕获自身 AudioTrack 输出，形成回环，导致 RMS 高达 -7.8~-10.5dB。源码注释明确提到此问题："prevents self-capture of our own AudioTrack output (which caused an infinite echo / noise loop)"
3. **remote_submix Standby**: 已知定论（前序文档），在 canoe 上 remote_submix 输出可能进入 Standby
4. **掩盖静音效果**: AudioTrack 回放很响，会掩盖 per-session 静音的效果（抖音被静音但 App 回放仍在出声）

### 结论
✅ **AudioTrack 回放功能验证通过**。SET_PLAYBACK=true 后 AudioOut_5D 线程活跃，功率 -6~-15dB，证明能出声。但存在回放过响和自捕获回环问题，建议：
1. 设置 targetUid=10205（抖音 uid）避免自捕获
2. 降低 AudioTrack 增益或添加音量控制
3. per-session 静音测试时先关闭回放（SET_PLAYBACK=false）

## dumpsys 证据文件

- `docs\canoe_mute_baseline2.txt` — 包含回放 ON 状态的 AudioOut_5D 数据
- `docs\canoe_noplayback_baseline.txt` — 回放 OFF 状态
