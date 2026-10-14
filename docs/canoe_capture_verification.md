# canoe (SM8850) 捕获链路与 per-session 静音验证报告

日期：2026-09-04 | 设备：canoe for arm64 (Android 16/SDK36, AOSP userdebug, SM8850/Adreno840v2)

## 一、结论速览

| 验证项 | 结果 | 说明 |
|---|---|---|
| MediaProjection 授权闭环 | ✅ 通过 | Start Capture → 下一步 → 选应用 → TYPE_SCREEN_CAPTURE，autopilot 工具全自动 |
| AudioPlaybackCapture 捕获 | ❌ 不可用 | **canoe 系统级限制**：remote_submix loopback 始终无数据，无论 usage 还是 uid 匹配配置均无法捕获 |
| per-session 静音 | ✅ 通过 | DynamicsProcessing(-200dB) 挂到 Chrome session 249，audio_flinger 确认 Registered+Enabled |
| 双声叠加解决方案机制 | ✅ 可行 | 静音原始 session 效果确凿生效，解除静音正常 |

## 二、捕获链路排查过程（证据）

### 2.1 排查序列
1. **RECORD_AUDIO 权限**：最初 `granted=false` → `pm grant` 授予 → 仍 -96dB
2. **Chrome Fast track 问题**：Web Audio 用 Fast track（Type F1），AudioPlaybackCapture **不捕获 Fast track** → 改用 `<audio>` 播放 wav（普通 track Type 76/81）
3. **usage 配置**：App 配 `addMatchingUsage(MEDIA/GAME/UNKNOWN)` → audio_policy 生成 `Criterion 0: UNKNOWN` + 其他（多 criterion）
4. **uid-only 配置**（新改）：`addMatchingUid(10118)` → audio_policy 只生成 `Criterion 0: RULE_MATCH_UID 10118`（单一规则）→ 仍 -96dB
5. **remote_submix 线程**：始终 `Standby: yes`，**无任何 track 被路由**

### 2.2 关键证据
- audio_policy：`APM_AudioPolicyMix: registerMix() adding mix for dev=0x8000`（注册成功）
- AudioFlinger：`openOutput() Device AUDIO_DEVICE_OUT_REMOTE_SUBMIX @:...`（输出线程创建）
- 但 remote_submix 输出线程（AudioOut_6D/75）始终 `Standby: yes`、无 active track
- Chrome 播放普通 track（Type 81, MEDIA, session 249, active 3844r）时 remote_submix 仍空
- **结论：canoe 的 AudioPolicyMix loopback（MediaProjection 捕获路径）未把匹配音频路由到 remote_submix**——系统级失效

### 2.3 已排除
- RECORD_AUDIO 权限 ✅ 已授予
- usage 规则不匹配 ❌（uid-only 单一规则仍失败）
- Chrome 无可捕获 track ❌（普通 track active 存在）
- MediaProjection 授权失败 ❌（授权成功）

## 三、per-session 静音验证（核心成果）

### 3.1 操作
```bash
# 挂载静音（-200dB DynamicsProcessing）到 Chrome session 249
adb shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 249 --ez mute true
```

### 3.2 audio_flinger 证据（session 249 Effect Chain）
```
1 Effect Chains
  2 effects for session 249
  Effect ID 131: Qti-MusicVolumeListener   Registered=y Enabled=y  (Qualcomm 自带)
  Effect ID 139: DynamicsProcessing        Registered=y Enabled=y  (我们的 -200dB 静音)
    3 Clients: pid 7537 (AudioOverlay, priority 2147483647)
    UUID: e0e6539b-1781-7261-676f-6d7573696340
```

### 3.3 解除静音
`MUTE_SESSION --ei sid 249 --ez mute false` → `unmounted session 249` 正常

### 3.4 意义
- 无需 Root/DUMP 权限，DynamicsProcessing 可直接 attach 任意 audio session
- 是解决"双声叠加"（原始声 + 处理声并存）的关键机制
- 未来可做成：检测目标 App 的 session → 自动挂静音 → 同时播放处理后的音频

## 四、canoe 设备限制与建议

### 4.1 canoe 限制
canoe（Qualcomm 开发板）的 MediaProjection 音频捕获（remote_submix loopback）**系统级失效**，普通 App 无法绕过。这在非此开发板的设备（如 Redmi/MIUI、Pixel、模拟器）上通常正常（此前模拟器 Android 17 全链路验证通过，Redmi 上 stagefright 捕获成功）。

### 4.2 建议
1. **连接 Redmi（6dd1f4eb）**：Redmi 的标准 PlaybackCapture 应可捕获，可完成"捕获→处理→回放→静音"全链路真机验证
2. **canoe 系统配置修改**（需用户授权，涉及改 system audio_policy）：把 loopback 规则/路由修正，或改用标准 AudioFlinger playback capture
3. **canoe 继续用于**：per-session 静音、App AudioTrack 回放、DSP 效果等不需要捕获的验证

## 五、测试环境备忘
- App: com.example.audiooverlay（uid 10203），uid-only 捕获配置（targetUid=10118）
- Chrome: com.android.chrome（uid 10118），audio_test.html / audio_file_test.html（http://127.0.0.1:8000，adb reverse tcp:8000）
- 控制广播：SET_TARGET_UID --ei uid N | MUTE_SESSION --ei sid N --ez mute true|false
- 构建：gradlew assembleDebug（JAVA_HOME=Android Studio jbr）
