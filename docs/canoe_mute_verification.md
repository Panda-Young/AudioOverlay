# canoe(SM8850) Per-Session 静音验证报告

**日期**: 2026-09-04 01:29
**设备**: Canoe for arm64 (serial 816d9a5), Android 16 / SDK 36, AOSP userdebug, **adb root (uid=0)**
**场景**: Chrome 播放本地 Web 测试音 `test_440_880.wav`（普通 AudioTrack，连续播放），AudioOverlay App 挂载 per-session 静音

---

## 一、结论

**canoe 上 per-session 静音机制验证通过** —— DynamicsProcessing(-200dB) 成功挂载到 Chrome 的活跃音频 session (313)，与 Qualcomm 自带 effect 无冲突。这是解决"双声叠加"（原始声+处理声并存）的关键机制，且**无需 Root/Shizuku/DUMP 权限**（DynamicsProcessing 按 session attach 即可）。

> 注：canoe 上 MediaProjection 捕获系统级不可用（remote_submix Standby），因此"捕获→分离→回放→静音"全链路闭环需在 Redmi 上完成；canoe 用于静音机制 + AudioTrack 回放 + root 诊断。

## 二、验证证据链

### 1. Chrome 播放测试音（普通 AudioTrack，可被捕获策略识别）
- Chrome UID = 10118
- 测试页: `http://127.0.0.1:8000/audio_file_test.html`（HTTP server port 8000 + adb reverse tcp:8000）
- 播放状态: `PLAYING: file test_440_880.wav (normal AudioTrack)`，进度 0:01/0:10

### 2. 活跃 session 发现（audio_flinger Global session refs）
```
session  cnt     pid    uid  name
    313    2   18234  10118  com.android.chrome   ← Chrome 播放
    313    1    1833   1041  audioserver          ← 活跃播放引用
```

### 3. MUTE_SESSION 广播挂载
```
adb shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 313 --ez mute true
```
App 日志（PID 7537）:
```
AudioRouteLab: muteSession: MOUNTED DynamicsProcessing -200dB on session 313 OK
AudioRouteLab: muteSession: mounted DynamicsProcessing -200dB on session 313 OK
```

### 4. AudioFlinger Effect Chain 铁证（session 313, Active tracks: 1）
```
2 effects for session 313
  Effect ID 187: Qti-MusicVolumeListener (Qualcomm, 自带, 无冲突共存)
  Effect ID 195: DynamicsProcessing
    Session State Registered Internal Enabled Suspended:
    00313   003   y          n        y       n     ← Registered + Enabled
    UUID: e0e6539b-1781-7261-676f-6d7573696340      ← AOSP DynamicsProcessing
    implementor: The Android Open Source Project
    2 Clients: Pid 7537 Priority 2147483647          ← AudioOverlay App, MAX 优先级
```

## 三、关键点

| 项 | 值 |
|---|---|
| 静音实现 | `DynamicsProcessing(Int.MAX_VALUE, sid, null).setInputGainAllChannelsTo(-200f).enabled=true` |
| 所需权限 | 无（普通 App，无需 Root/Shizuku/DUMP） |
| Session 发现 | `dumpsys media.audio_flinger` 的 Global session refs 表 |
| 广播控制 | `MUTE_SESSION --ei sid N --ez mute true\|false` |
| 效果 UUID | e0e6539b-1781-7261-676f-6d7573696340 |
| 共存 | 与 Qualcomm Qti-MusicVolumeListener 无冲突 |

## 四、A/B 验证（听感）
- **Mute=true**：Chrome 440+880Hz 测试音从扬声器静音（原始输出被 -200dB 压制）
- **Mute=false**：恢复正常播放
- 听感验证需用户在场确认；机制层面 Registered+Enabled 已铁证

## 五、与 Shizuku 的关系
- **canoe 已 adb root（uid=0），Shizuku 非必需**。Shizuku 价值是"免 root 借 adb 权限"，root 可直接做更多
- 用户此前 `adb shell libshizuku.so` 报错根因：① APK 重装后 `/data/app/~~xxx==` 随机目录已变（旧路径失效）；② libshizuku.so 是共享库非可执行程序，需 app_process 加载

## 六、产物
- 截图: `docs/canoe_chrome_now.png`（播放前, autoplay blocked）、`docs/canoe_chrome_playing.png`（播放中, PLAYING test_440_880.wav）

## 2026-09-04 02:00 Chrome A/B 输出功率铁证（补充）

**场景**: Chrome 播放 test_440_880.wav（loop），session 337，AudioOut_25（DEEP_BUFFER, PCM_32BIT）

**Signal power history（AudioOut_25, HAL 输出功率, dB）**:

| 阶段 | 1000ms 采样 | 功率 | 说明 |
|---|---|---|---|
| MUTE前（播放中） | 01:49:03~01:49:33 | **-33.1 dB 持续** | 440+880Hz 测试音正常输出 |
| MUTE后 | 01:49:36.9 过渡 → 01:49:37~40 | **-108.8 dB 稳定** | ~100ms 内从 -33 跌到 -108.8，静音深度 **75.7 dB** |
| UNMUTE后 | 01:49:40.9 过渡 → 01:49:41+ | **-33.1 dB 恢复** | A/B 完全可逆 |

**结论**: DynamicsProcessing(-200dB) per-session 静音对 Chrome 真实音频输出完整生效：
- 挂载方式: m broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 337 --ez mute true
- 静音深度 75.7 dB = 听感完全静音（背景噪声底 -108.8 dB）
- 恢复即时、可逆、无残留
- 实证了 session 动态变化（329→337）：每次播放/重播 session 递增，需要动态监听重挂

## 2026-09-04 02:10 抖音 per-session 静音 A/B（补充）

**场景**: 抖音 feed 视频自动播放（com.ss.android.ugc.aweme pid 17349），session 361+369

**Signal power（AudioOut_25, deep buffer 输出线程, dB）**:

| 阶段 | AudioOut_25 | 说明 |
|---|---|---|
| 基线（抖音播放中） | **-25.7 dB** | 视频音频活跃 |
| MUTE 361+369 | **-60.1 dB** | 抑制 34.4 dB |
| UNMUTE | **-27.9 dB** | 恢复 |

**重要发现**:
- session 369 成功挂载 effect（Effect ID 355，DynamicsProcessing），361 无 chain（旧 session 已失效）→ 印证 session 动态变化
- **抖音音频走多输出路径**：AudioOut_25（deep buffer）被静音生效；AudioOut_D（primary/fast, -12.8dB）未被静音——抖音部分音频（可能是 fast track 或 UI 音效）绕过 per-session effect。视频主体音频（deep buffer）可被静音
- 对比 Chrome（-33.1→-108.8，75.7dB 完全静音）：Chrome 纯深缓冲输出，抖音多路径混合

**结论**: per-session 静音对抖音视频主体音频有效；"只听到处理声"的静音侧覆盖大部分场景。剩余 AudioOut_D 路径需进一步分析（可能是 fast track 需单独处理或用更底层方案）。
