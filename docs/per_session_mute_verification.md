# Per-Session 静音完整闭环验证报告

**日期**: 2026-09-02 22:42  
**设备**: Redmi 23113RKC6C (vermeer), Android 16 / SDK 36, 非 Root, MIUI, 骁龙8 Gen3  
**场景**: Edge 浏览器播放本地视频《以梦为马不负韶华》(60s 截取素材)，App 后台处理

---

## 一、本轮三大关键突破

### 1. MediaProjection 授权问题彻底解决（根因：坐标系统差异）

**问题**：`input tap` 点 Start Capture 不弹授权框，多轮卡死。

**根因**：设备物理分辨率 **1440x3200**，但系统 override 为 **1080x2400**（截图/OCR 是逻辑坐标）。`input tap` 用**物理像素**，逻辑坐标点击整体偏移 1.33 倍。

**解决**：`uiautomator dump` 拿真实物理坐标——Start Capture (283,240)，授权对话框"下一步" (765,2230)。

**结果**：授权流程完整走通（选 Edge），捕获线程启动，RMS 捕获到真实音频 **-12.6dB**。

### 2. MuteControlService 前台服务保活（绕过 MIUI Greezer）

**问题**：App 后台后 Greezer 拦截广播（`Greezer Denial ... need cached broadcast`），三种绕过手段全无效。

**解决**：`MuteControlService`（mediaPlayback 前台服务）保活 + 注册 MUTE_SESSION receiver。

**关键坑**：Android 14+ 需 `FOREGROUND_SERVICE_MEDIA_PLAYBACK` 权限，否则 SecurityException 崩溃。

**验证**：App 进后台后广播仍送达 → `MOUNTED DynamicsProcessing -200dB on session 38977 OK`。

### 3. Per-Session 静音完整闭环（核心目标达成）

对目标 App 的音频 session 挂 `DynamicsProcessing(-200dB)` 静音原始输出，用户只听到 AudioTrack 回放的处理声——**免 Root 消除双声叠加**。

**AudioFlinger 铁证**：
```
3 effects for session 38977  (Active tracks: 1)
Effect ID 97915:  Registered=y  Enabled=y  Suspended=n
  UUID: e0e6539b-...（Dynamics Processing，AOSP）
```

**AudioTrack 回放铁证**：
```
pid 23791 (App)  session 38945  maxAmplitude 32768  ← 处理声输出
pid 24987 (Edge) session 38977  maxAmplitude 1207959552  ← 原始播放
```

---

## 二、完整证据链（真机实测）

| 环节 | 状态 | 证据 |
|---|---|---|
| MediaProjection 授权 Edge | ✅ | 捕获线程启动 |
| 捕获 Edge 原始音频 | ✅ | RMS -12.6dB（pre-effect） |
| MuteControlService 保活 | ✅ | 后台广播送达，无 Greezer Denial |
| DynamicsProcessing 挂载 | ✅ | MOUNTED -200dB OK |
| Effect 挂活跃 track | ✅ | audio_flinger: Enabled=y, Active=1 |
| AudioTrack 回放处理声 | ✅ | maxAmplitude 32768 |
| 捕获不受静音影响 | ✅ | 静音后捕获保持 -12.5dB |

---

## 三、遗留问题

1. **Session 动态管理**：重播/音轨变化 session 递增（38961→38969→38977→39073），需自动监听并重挂。
2. **Spleeter 语音局限**：《以梦为马》《血色湘西》语音类人声遗漏；歌曲类（处处吻）正常。
3. **最终听感**：DSP 层已铁证，最终听感需耳朵验证。

---

## 四、补充修复：自捕获循环（"暂停后仍有杂音"）根因与解决

### 现象（用户反馈）
Edge 暂停视频后，仍有持续杂音播放（RMS -3.9dB，满量程）。

### 根因
`AudioPlaybackCaptureConfiguration` 用 `addMatchingUsage(USAGE_MEDIA)` **未限制 UID**，导致 MediaProjection 捕获到了 **App 自己 AudioTrack 回放声**（也是 USAGE_MEDIA），形成无限自循环：
```
AudioTrack 回放 → MediaProjection 捕获 → DSP 处理 → 再写 AudioTrack → 再捕获 → …
```
即使 Edge 暂停无输入，循环仍持续放大，产生刺耳杂音。

### 修复
捕获配置增加 `addMatchingUid(目标UID)`，只捕获目标应用（Edge uid=10255），排除自身 AudioTrack：
```kotlin
if (targetUid >= 0 && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
    builder.addMatchingUid(targetUid)
}
```
- targetUid 改为 companion object 静态变量（进程级共享，服务重启不丢）
- 新增 `SET_TARGET_UID` 广播（`--ei uid <uid>`），注意 IntentFilter 需注册该 action
- `setMatchingUid(int)` 是 API 34+，改用 API 29 就有的 `addMatchingUid(int)`

### A/B 验证（真机）
| 状态 | 修复前 | 修复后 |
|---|---|---|
| Edge 播放中 | RMS -12.3dB | RMS -12.3dB（正常捕获）|
| Edge 暂停后 | **RMS -3.9dB（持续杂音）** | **RMS -96dB（完全静音）** |

验证日志：
```
AudioPlaybackCaptureConfiguration limited to uid=10255   ← UID 限制生效
Edge 播放:  RMS=-12.3 dB（正常捕获）
Edge 暂停:  RMS=-96.0 dB（无自循环）
```

### 流程要点
1. 授权（选目标应用）→ 捕获启动（首次可用无限制）
2. 服务运行后发 `SET_TARGET_UID --ei uid <目标UID>`（receiver 已注册）
3. 停止捕获（UI Stop 按钮，STOP_CAPTURE 广播不生效）→ 重新授权 → 捕获用 UID 限制
4. 目标 UID 查询：`adb shell dumpsys package <pkg> | grep userId`

---

## 五、技术要点

- 静音：`DynamicsProcessing(Int.MAX_VALUE, sid, null).setInputGainAllChannelsTo(-200f).enabled=true`
- **无需 DUMP/system 权限**：直接按 session attach
- 捕获：`addMatchingUsage(USAGE_MEDIA/GAME/UNKNOWN)`，授权"共享一个应用"
- 物理坐标 = 逻辑 × 1.333
- 构建：`gradlew assembleDebug --no-daemon`（JAVA_HOME=Android Studio jbr）
