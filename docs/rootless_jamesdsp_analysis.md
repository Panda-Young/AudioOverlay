# RootlessJamesDSP 免 Root 系统级音频处理技术分析

> 研究对象：[timschneeb/RootlessJamesDSP](https://github.com/timschneeb/RootlessJamesDSP)
> 分析日期：2026-09-02
> 分析方法：源码级阅读（git clone 后逐文件分析）+ README/Wiki/Issues 交叉验证

---

## 1. RootlessJamesDSP 架构概述

RootlessJamesDSP 是一个面向非 Root Android 设备的系统级 JamesDSP 音频处理引擎。它存在两种运行模式：

| 模式 | 触发条件 | 核心机制 | 限制 |
|------|----------|----------|------|
| **Rootless 模式** | 无 Root，通过 Shizuku/ADB 初始授权 | AudioPlaybackCapture 捕获 → DSP 处理 → AudioTrack 回放 + **per-session 静音效果** | 见第 5 节 |
| **Root 模式** | 有 Root，配合 Magisk 模块 | 直接访问系统音频流（标准 JamesDSP Magisk 模块路径） | 无 Rootless 模式的限制 |

本分析聚焦 **Rootless 模式**，因为这是对本项目最有借鉴价值的部分。

### 1.1 核心模块组成

```
RootlessAudioProcessorService (前台服务, MediaProjection 类型)
├── AudioRecord (AudioPlaybackCaptureConfiguration)  ← 捕获系统音频
├── JamesDspLocalEngine (JNI → libjamesdsp native)  ← DSP 处理
├── AudioTrack (ALLOW_CAPTURE_BY_NONE)               ← 回放处理后音频
└── RootlessSessionManager
    ├── DumpManager (DUMP 权限 → dumpsys media.audio_policy/audio)  ← 会话发现
    └── RootlessSessionDatabase
        └── MutedAudioEffectFactory  ← per-session 静音效果（关键！）
            ├── DynamicsProcessing (inputGain = -200dB)
            └── 隐藏 Volume AudioEffect (MUTE=1, LEVEL=-96)
```

### 1.2 关键源码文件定位

| 功能 | 文件路径 |
|------|----------|
| 主音频服务（捕获+处理+回放） | `app/.../service/RootlessAudioProcessorService.kt` |
| 本地 DSP 引擎（JNI 封装） | `app/.../interop/JamesDspLocalEngine.kt` |
| 静音效果工厂（核心技巧） | `app/.../utils/MutedAudioEffectFactory.kt` |
| 静音会话数据模型 | `app/.../model/rootless/MutedEffectSession.kt` |
| 会话数据库（效果挂载/卸载） | `app/.../session/rootless/RootlessSessionDatabase.kt` |
| 会话管理器（轮询+事件） | `app/.../session/rootless/RootlessSessionManager.kt` |
| Dump 工具（DUMP 权限读系统服务） | `app/.../session/dump/utils/DumpUtils.kt` |
| 初始化引导（Shizuku/ADB 授权） | `app/.../fragment/OnboardingFragment.kt` |
| 隐藏 API 实现（Shizuku 系统服务调用） | `hidden-api-impl/.../ShizukuSystemServerApi.kt` |

---

## 2. 免 Root 技术路径详解（逐步）

RootlessJamesDSP 的免 Root 路径可以拆解为 **5 个串行步骤**，其中第 4 步是避免"双声叠加"的核心技巧。

### 步骤 1：获取 DUMP 权限（初始设置，一次性）

普通应用无法获取 `android.permission.DUMP`（这是 signature|privileged 级别权限），必须通过 Shizuku 或 ADB 授予：

```bash
# ADB 方式
adb shell pm grant me.timschneeberger.rootlessjamesdsp android.permission.DUMP

# Shizuku 方式（应用内自动调用）
ShizukuSystemServerApi.PermissionManager_grantRuntimePermission(
    pkg, Manifest.permission.DUMP, UserHandle.USER_SYSTEM)
```

**DUMP 权限的作用**：允许应用通过 `IBinder.dumpAsync()` 直接调用系统服务的 dump 接口，获取 `media.audio_policy`、`audio`、`media.audio_flinger` 服务的内部状态，从而解析出当前活跃的音频会话 ID（session ID）、UID、包名、usage 等信息。

> 没有 DUMP 权限，应用无法知道其他 app 的音频会话 ID，也就无法挂载 per-session 效果。这是整个方案的前提条件。

### 步骤 2：获取 MediaProjection 授权（用户交互，每次启动）

通过 `MediaProjectionManager.createScreenCaptureIntent()` 弹出系统录屏授权对话框，用户同意后获得 `MediaProjection` token。

**用途**：作为 `AudioPlaybackCaptureConfiguration.Builder(mediaProjection)` 的参数，构建可以捕获其他应用播放音频的 `AudioRecord`。

```kotlin
// RootlessAudioProcessorService.kt 关键代码
val configBuilder = AudioPlaybackCaptureConfiguration.Builder(mediaProjection!!)
    .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
    .addMatchingUsage(AudioAttributes.USAGE_GAME)
    .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
// 排除自身 UID、黑名单应用、受限应用
excluded.forEach { configBuilder.excludeUid(it) }

val recorder = AudioRecord.Builder()
    .setAudioFormat(format)
    .setAudioPlaybackCaptureConfig(configBuilder.build())
    .build()
```

### 步骤 3：捕获 → DSP 处理 → 回放（核心音频管线）

在一个独立线程中循环执行：

```
AudioRecord.read()  →  JamesDspLocalEngine.processFloat/processInt16()  →  AudioTrack.write()
```

**关键细节**：
- 采样率：读取 `AudioManager.PROPERTY_OUTPUT_SAMPLE_RATE`（HAL 原生采样率，通常 48000），clamp 到 44100-48000
- 编码格式：支持 PCM_16BIT 和 PCM_FLOAT（可配置）
- 缓冲大小：用户可配置，影响延迟
- **AudioTrack 设置 `ALLOW_CAPTURE_BY_NONE`**：防止回放的处理后音频被自身的 AudioRecord 再次捕获，避免反馈循环
- AudioTrack 使用 `USAGE_UNKNOWN` / `CONTENT_TYPE_UNKNOWN`

### 步骤 4：per-session 静音效果（避免双声叠加的核心技巧）

这是整个方案最关键、最巧妙的部分。如果只做步骤 3（捕获→处理→回放），用户会同时听到**原始音频**和**处理后音频**的叠加（双声）。RootlessJamesDSP 通过**给每个活跃音频会话挂载一个静音效果**来消除原始声音。

#### 4.1 会话发现

通过 `DumpManager` 轮询系统服务 dump，解析活跃会话：

```kotlin
// DumpUtils.kt — 通过 DUMP 权限直接调用系统服务的 dump 接口
val serviceBinder = SystemServiceHelper.getSystemService("media.audio_policy")
serviceBinder.dumpAsync(writePipe.fileDescriptor, args)
// 解析输出中的 "Session Id: X  UID: Y  ... Usage: Z"
```

支持三种 dump 源（带自动 fallback）：
1. `media.audio_policy`（默认，API 29+ 格式，包含 capture 策略信息）
2. `audio`（AudioService，API 31+ 直接含 sessionId）
3. `media.audio_flinger`（调试用，通过 PID→SID 映射表补全）

#### 4.2 挂载静音效果

对每个新发现的会话，`MutedAudioEffectFactory.make(sid, packageName)` 尝试挂载以下两种效果之一（按优先级 fallback）：

**方案 A：DynamicsProcessing 效果（API 28+，首选）**

```kotlin
DynamicsProcessing(Int.MAX_VALUE, sid, null).apply {
    setInputGainAllChannelsTo(-200f)  // -200dB，本质静音
    enabled = true
}
```

- `priority = Int.MAX_VALUE`：争取最高优先级，防止被其他效果抢占
- `setInputGainAllChannelsTo(-200f)`：将所有通道输入增益设为 -200dB，人耳不可闻
- 挂载在指定 session ID 上，只影响该 app 的输出

**方案 B：隐藏 Volume AudioEffect（fallback，部分设备）**

```kotlin
// 使用隐藏的 Volume effect UUID
val volumeHiddenTypeUuid = UUID.fromString("09e8ede0-ddde-11db-b4f6-0002a5d5c51b")
AudioEffectHidden(volumeHiddenTypeUuid, EFFECT_TYPE_NULL, Int.MAX_VALUE, sid).apply {
    setParameter(VolumeParams.MUTE.ordinal, 1)   // 静音
    setParameter(VolumeParams.LEVEL.ordinal, -96)  // -96dB
}
```

- 通过 `hidden-api-refine` + `HiddenApiBypass` 访问 Android 隐藏 API
- UUID 是 Android 内部 Volume effect 的类型标识

#### 4.3 效果保活与冲突处理

```kotlin
muteEffect.setEnableStatusListener { effect, enabled ->
    if (!enabled) {
        effect.enabled = true  // 被系统/其他应用禁用时自动重新启用
    }
}
muteEffect.setControlStatusListener { effect, controlGranted ->
    if (!controlGranted) {
        sessionLossListener?.invoke(sid, packageName)  // 控制权被夺走 → 会话丢失
    }
}
```

- 监听效果启用状态，被禁用时自动重新启用
- 监听控制权状态，被其他应用（如 Wavelet）夺走时触发"会话丢失"流程（重试 + 用户通知）

#### 4.4 效果卸载

应用停止播放或会话关闭时，`RootlessSessionDatabase.onSessionRemoved()` 禁用并释放效果：

```kotlin
audioMuteEffect?.enabled = false
audioMuteEffect?.release()
```

### 步骤 5：会话管理与动态更新

- **事件驱动**：注册 `AudioManager.AudioPlaybackCallback`，播放配置变化时触发 `pollOnce()`
- **轮询兜底**：可选持续轮询（默认 3 秒间隔），防止事件丢失
- **通知监听**：通过 `NotificationListenerService` 辅助检测媒体会话
- **MediaSession 监听**：`MediaSessionManager.OnActiveSessionsChangedListener`
- 综合以上信号，动态挂载/卸载静音效果，重建 AudioRecord（更新排除 UID 列表）

---

## 3. Shizuku/ADB 初始化分析

### 3.1 初始化流程总览

```
用户启动应用
  → 欢迎页
  → 限制说明页
  → 方式选择页（Shizuku / ADB / Root）
  → 权限授予页
      ├── Shizuku 路径：安装 Shizuku → 启动 Shizuku → 应用内请求 Shizuku 权限 → 自动授予 DUMP
      └── ADB 路径：开启 USB 调试 → WebADB 连接 → 手动执行 pm grant / appops set 命令
  → 运行时权限页（RECORD_AUDIO + POST_NOTIFICATIONS）
  → 完成
```

### 3.2 Shizuku 路径具体操作

当用户选择 Shizuku 方式且 Shizuku 服务已运行、应用已获得 Shizuku 权限时，应用**自动**执行以下操作（`OnboardingFragment.ensureAdbPermissions()`）：

| 操作 | 调用方式 | 目的 |
|------|----------|------|
| 授予 DUMP 权限 | `PermissionManager_grantRuntimePermission(pkg, "android.permission.DUMP", USER_SYSTEM)` | 允许读取系统服务 dump，发现音频会话 |
| 授予 SYSTEM_ALERT_WINDOW 权限 | `PermissionManager_grantRuntimePermission(pkg, "android.permission.SYSTEM_ALERT_WINDOW", USER_SYSTEM)` | 可选，用于自动启动/悬浮窗 |
| 设置 SYSTEM_ALERT_WINDOW AppOps | `AppOpsService_setMode(OP_SYSTEM_ALERT_WINDOW, uid, pkg, MODE_ALLOW)` | 持久化悬浮窗权限 |
| 设置 PROJECT_MEDIA AppOps | `AppOpsService_setMode(OP_PROJECT_MEDIA, uid, pkg, MODE_ALLOW)` | Android 15+ 必需，绕过屏幕共享保护 |

以上调用通过 `rikka.shizuku:api` 库以 **shell 身份（uid=2000）** 执行系统服务调用，等价于在 ADB shell 中执行对应命令。

### 3.3 ADB 路径具体命令

用户需在电脑端（WebADB 或本地 adb）手动执行：

```bash
# 必需：授予 DUMP 权限
adb shell pm grant me.timschneeberger.rootlessjamesdsp android.permission.DUMP

# 必需（Android 15+）：授予 PROJECT_MEDIA appops
adb shell appops set me.timschneeberger.rootlessjamesdsp PROJECT_MEDIA allow

# 可选：授予 SYSTEM_ALERT_WINDOW（自动启动）
adb shell appops set me.timschneeberger.rootlessjamesdsp SYSTEM_ALERT_WINDOW allow
```

### 3.4 关键结论：Shizuku/ADB 做了什么、没做什么

**做了什么**：
- 授予 `DUMP` 权限 → 使应用能读取系统音频服务的内部状态（会话 ID、UID、包名）
- 授予 `PROJECT_MEDIA` appops → Android 15+ 绕过屏幕共享隐私保护
- 可选授予 `SYSTEM_ALERT_WINDOW` → 自动启动

**没做什么**：
- **没有 app_process 注入**：不注入系统进程，不修改系统文件
- **没有修改 audio_policy 配置**：不替换 HAL，不改 mixer 路径
- **没有设置系统级属性**（setprop）：不修改 `ro.audio` 或 `persist.audio` 属性
- **没有安装系统级 AudioEffect**：所有效果都是 per-session 挂载，非全局 output mix 效果

> Shizuku/ADB 的唯一核心作用是**授予 DUMP 权限**，使应用能"看到"其他 app 的音频会话 ID。真正的音频处理和替换完全在应用自身进程内完成，不依赖系统进程注入。

### 3.5 Shizuku 是否需要常驻？

- **Shizuku 服务本身**：只需在初始授权时运行。授权完成后（DUMP 权限已写入系统），Shizuku 可以关闭，不影响后续使用。
- **DUMP 权限持久性**：`pm grant` 授予的权限在重启后仍然有效（除非应用被重新安装或权限被手动撤销）。
- **MediaProjection**：每次启动服务都需要用户重新授权（系统限制，MediaProjection token 不能持久化）。

---

## 4. 与本项目当前路径的对比

### 4.1 路径对比表

| 维度 | 本项目当前路径<br>(Capture → Process → 未回放) | RootlessJamesDSP 路径<br>(Capture → Process → Playback + Mute) |
|------|------|------|
| **捕获** | AudioPlaybackCapture + AudioRecord | 相同 |
| **DSP 处理** | 应用内处理 | 相同（libjamesdsp native） |
| **回放** | ❌ 未回放 | ✅ AudioTrack 回放处理后音频 |
| **原始音频处理** | ❌ 未处理（原始声音照常播放） | ✅ per-session 静音效果（DynamicsProcessing -200dB / Volume MUTE） |
| **用户听到的声音** | 原始音频（处理后音频未输出） | 仅处理后音频（原始被静音） |
| **双声叠加风险** | N/A（不回放就没有叠加） | 已通过静音效果消除 |
| **需要 DUMP 权限** | 否 | **是**（发现会话 ID 以挂载静音效果） |
| **需要 Shizuku/ADB** | 否 | **是**（初始授权 DUMP） |
| **需要 MediaProjection** | 是 | 是 |
| **效果范围** | 仅捕获，不影响系统输出 | 全局（所有允许捕获的 app） |
| **延迟** | 无输出延迟 | 捕获+处理+回放链路延迟（缓冲可配） |
| **Android 版本要求** | API 29+ | API 29+（DynamicsProcessing 需 API 28+） |

### 4.2 本质差异

本项目当前处于"**只听不说**"状态：捕获了系统音频并做了处理，但没有把处理结果输出回去，也没有抑制原始音频。用户听到的仍然是原始系统声音。

RootlessJamesDSP 则是"**先说后捂**"：
1. 把处理后的音频通过 AudioTrack 播放出去（"说"）
2. 同时把原始 app 的音频会话静音（"捂"）
3. 用户只听到处理后的声音

### 4.3 本项目缺失的两个关键环节

要从当前路径进化到 RootlessJamesDSP 的"感知级输出替换"，需要补上：

1. **AudioTrack 回放**：将 DSP 处理后的音频写回系统输出
   - 必须设置 `ALLOW_CAPTURE_BY_NONE` 防止自捕获反馈
   - 需要精确的缓冲管理避免 underrun
2. **per-session 静音效果**：给每个活跃播放会话挂载静音效果
   - 需要 DUMP 权限发现会话 ID
   - 需要处理效果冲突（与其他音效 app 共存问题）
   - 需要动态管理（app 启动/停止时挂载/卸载）

---

## 5. "输出替换"可行性评估

### 5.1 核心结论

> **有条件可行。** RootlessJamesDSP 证明了在免 Root 条件下可以实现"**感知级输出替换**"——用户只听到处理后的音频，原始音频被 per-session 效果静音。但这不是"**真正的系统级输出替换**"（HAL/output mix 级别的流替换），后者仍然需要 Root。

### 5.2 "感知级输出替换" vs "真正的系统级输出替换"

| 维度 | 感知级输出替换<br>(RootlessJamesDSP 方案) | 真正的系统级输出替换<br>(Root/Magisk 方案) |
|------|------|------|
| **替换位置** | app 输出 session 级（静音原始 + 独立 AudioTrack 回放） | HAL/output mix 级（直接替换流向硬件的音频流） |
| **原始音频是否流经系统** | 是（被静音效果衰减到 -200dB，但仍在音频管线中） | 否（在到达硬件前被替换/拦截） |
| **处理后音频来源** | 独立 AudioTrack（USAGE_UNKNOWN），与原始 app 无关 | 原始流的直接替换 |
| **音频路由** | 受 AudioTrack 路由规则约束（可能走不同的 output thread） | 完全跟随原始路由 |
| **低延迟应用** | 不适用（捕获+处理+回放引入显著延迟） | 适用（可在低延迟路径上处理） |
| **AAudio 原生 app** | 可能不受静音效果影响（取决于实现） | 全部受影响 |
| **DRM 保护内容** | 无法捕获（ALLOW_CAPTURE_BY_NONE 的 app 被排除） | 可处理（Magisk 模块直接在 HAL 层操作） |
| **系统声音/通知** | 不处理（只捕获 USAGE_MEDIA/GAME/UNKNOWN） | 可配置处理范围 |

### 5.3 免 Root 实现"感知级输出替换"的条件

#### 必需条件

1. **Android 10+ (API 29)**：AudioPlaybackCapture API 从 API 29 引入
2. **DUMP 权限**：通过 Shizuku 或 ADB 授予 `android.permission.DUMP`
   - 用途：读取 `media.audio_policy` / `audio` 系统服务 dump，发现活跃音频会话 ID
   - 没有 DUMP 权限 → 无法知道其他 app 的 session ID → 无法挂载静音效果 → 会双声叠加
3. **MediaProjection 用户授权**：每次启动服务需用户同意屏幕录制（系统强制弹窗）
4. **RECORD_AUDIO 权限**：运行时权限
5. **目标 app 允许音频捕获**：目标 app 未设置 `ALLOW_CAPTURE_BY_NONE`（即未启用 DRM 捕获保护）
6. **设备支持 DynamicsProcessing 或隐藏 Volume effect**：`MutedAudioEffectFactory.isDeviceCompatible()` 检查
   - 绝大多数 Android 9+ 设备支持 DynamicsProcessing
7. **无其他 app 抢占 DynamicsProcessing 控制权**：如 Wavelet 等使用 DynamicsProcessing 的音效 app 会冲突

#### Android 15+ 额外条件

8. **PROJECT_MEDIA appops 授权**：`appops set <pkg> PROJECT_MEDIA allow`
   - Android 15 引入屏幕共享隐私保护，通知等内容会被系统隐藏
   - 或在开发者选项中开启"Disable screen share protection"

### 5.4 不可行/受限的场景

| 场景 | 原因 |
|------|------|
| Spotify（未 patch）、Google Chrome、SoundCloud | app 设置了 `ALLOW_CAPTURE_BY_NONE`，AudioPlaybackCapture 无法捕获 |
| 使用原生 AAudio C++ API 的 app | 可能不经过 Java AudioEffect 框架，静音效果可能不生效 |
| 低延迟应用（模拟器、游戏语音） | 捕获+处理+回放链路延迟过高（通常 100ms+） |
| 系统通知、铃声、闹钟 | 只捕获 USAGE_MEDIA/GAME/UNKNOWN，不包含 USAGE_NOTIFICATION/RINGTONE/ALARM |
| 通话音频 | 不在 AudioPlaybackCapture 范围内 |
| 与 Wavelet 等 DynamicsProcessing app 共存 | 同一 session 只能有一个 DynamicsProcessing 控制者 |
| Android 9 及以下 | AudioPlaybackCapture API 不存在 |
| 设备重启后 | DUMP 权限保留，但 MediaProjection 需重新授权 |

### 5.5 对本项目的具体建议

如果本项目的目标是实现"处理后音频替换系统输出"（而非仅仅叠加 overlay 音频），可以借鉴 RootlessJamesDSP 的方案：

1. **补全 AudioTrack 回放**：将 DSP 输出通过 AudioTrack 写回，设置 `ALLOW_CAPTURE_BY_NONE`
2. **引入 DUMP 权限 + 会话发现**：通过 Shizuku/ADB 初始授权，轮询系统服务 dump 获取活跃会话
3. **实现 per-session 静音效果**：优先使用 DynamicsProcessing(-200dB)，fallback 到隐藏 Volume effect
4. **动态会话管理**：监听 AudioPlaybackCallback + 轮询，自动挂载/卸载静音效果
5. **处理冲突**：检测其他音效 app，提示用户关闭或选择共存策略

**如果本项目的目标只是"在系统音频上叠加一层 overlay 音频"**（如旁白、音效叠加），则不需要静音原始音频，只需 AudioTrack 回放即可，也不需要 DUMP 权限和 Shizuku/ADB。这种场景下当前路径只需补上 AudioTrack 回放即可。

---

## 6. 对"最低权限"结论的修正建议

### 6.1 前一轮结论回顾

> "真正的系统级输出替换最低需要 Root。"

### 6.2 修正后的结论

RootlessJamesDSP 的存在**部分推翻**了这个结论，但需要精确区分两种"输出替换"：

| 结论 | 是否成立 | 说明 |
|------|----------|------|
| **真正的 HAL/output mix 级输出替换最低需要 Root** | ✅ 仍然成立 | RootlessJamesDSP 没有做到这一级别，它只是在 session 级静音 + 独立回放 |
| **感知级输出替换（用户只听到处理后声音）最低需要 Root** | ❌ 被推翻 | RootlessJamesDSP 证明免 Root 可行，条件是 DUMP 权限 + MediaProjection + per-session 静音效果 |
| **任何形式的系统级音频处理最低需要 Root** | ❌ 被推翻 | 免 Root 的 AudioPlaybackCapture + AudioEffect 路径已被多个应用验证 |

### 6.3 修正后的"最低权限"分级

```
Level 0：纯应用内音频处理
  权限：无（普通应用权限）
  能力：只能处理自己 app 的音频
  代表：普通音乐 app 的内置 EQ

Level 1：系统音频捕获（本项目当前状态）
  权限：RECORD_AUDIO + MediaProjection 用户授权
  能力：能捕获其他 app 的音频并处理，但不能影响系统输出
  代表：屏幕录制 app 的音频捕获

Level 2：感知级输出替换（RootlessJamesDSP 方案）
  权限：Level 1 + DUMP 权限（Shizuku/ADB 授予）
  能力：捕获→处理→回放 + per-session 静音，用户只听到处理后音频
  限制：DRM 保护 app 不可用、延迟增加、DynamicsProcessing 冲突
  代表：RootlessJamesDSP、（借鉴后）本项目

Level 3：真正的系统级输出替换
  权限：Root（Magisk 模块）
  能力：HAL/output mix 级直接替换音频流，无捕获/回放延迟，覆盖所有 app（含 DRM）
  代表：JamesDSP Magisk 模块、ViPER4Android FX
```

### 6.4 对本项目的决策建议

- 如果项目目标是 **Level 2（感知级输出替换）**：免 Root 可行，需引入 Shizuku/ADB 初始设置 + DUMP 权限 + per-session 静音效果管理。开发复杂度中等偏高。
- 如果项目目标是 **Level 3（真正的系统级输出替换）**：仍然需要 Root，免 Root 无法实现。
- 如果项目目标只是 **音频叠加（overlay）**：Level 1 补 AudioTrack 回放即可，无需 Shizuku/ADB。

---

## 7. 许可证与借鉴可行性

### 7.1 许可证

RootlessJamesDSP 使用 **GNU General Public License v3.0 (GPL-3.0)**。

- 仓库根目录 `LICENSE` 文件明确为 GPL-3.0
- 其依赖的核心 DSP 库 `libjamesdsp`（James Fung 开发）也为 GPL 系列
- `hidden-api-impl`、`hidden-api-refined`、`hidden-api-stubs` 等子模块随主项目同样受 GPL-3.0 约束

### 7.2 GPL-3.0 对本项目的影响

| 借鉴方式 | GPL-3.0 要求 | 风险 |
|----------|---------------|------|
| **直接复制源码**（拷贝 Kotlin/Java 文件到本项目） | 本项目整体必须以 GPL-3.0 开源，且需保留版权声明、提供对应源码 | 高 — 传染性强，可能与本项目的许可证策略冲突 |
| **参考思路后独立实现**（不复制代码，只借鉴架构设计） | 不构成衍生作品，无需开源，但建议在文档中致谢 | 低 — 思想不受版权保护，但需确保不复制具体表达 |
| **调用其 API/IPC**（如通过绑定服务调用 RootlessJamesDSP 的处理能力） | 通常不构成衍生作品（GPL 关于系统调用/IPC 的边界有争议，但一般认为独立进程调用不受传染） | 中低 — 但依赖第三方 app 安装，用户体验差 |
| **使用其 native libjamesdsp .so** | libjamesdsp 本身为 GPL，动态链接也可能触发传染（LGPL 才允许动态链接闭源） | 高 — 除非本项目也 GPL |

### 7.3 借鉴建议

1. **推荐方式：参考架构，独立实现**
   - 可以借鉴的"思想"（不受 GPL 约束）：
     - AudioPlaybackCapture + AudioTrack 回放的管线结构
     - `ALLOW_CAPTURE_BY_NONE` 防止自捕获的技巧
     - per-session DynamicsProcessing(-200dB) 静音的思路
     - DUMP 权限 + dumpsys 解析发现会话的方法
     - 会话动态管理（事件 + 轮询混合策略）
   - 必须独立编写的"表达"（受 GPL 约束，不能复制）：
     - 具体的 Kotlin/Java 源码实现
     - 正则表达式、参数配置、错误处理逻辑
     - JNI 接口定义和 native 代码
     - UI 布局和资源文件

2. **DSP 引擎的替代方案**
   - 本项目已有自己的 DSP 处理逻辑，无需引入 libjamesdsp
   - 如果需要特定 DSP 算法（如卷积、FIR EQ），可寻找 MIT/Apache 许可的替代库，或自行实现

3. **Shizuku 集成**
   - Shizuku API 库本身是 MIT 许可（`dev.rikka.shizuku:api`），可以自由使用
   - 但通过 Shizuku 调用系统服务的具体代码逻辑如果从 RootlessJamesDSP 复制，仍受 GPL 传染
   - 建议参考 Android 官方文档和 Shizuku 官方示例独立编写

4. **致谢要求**
   - 即使独立实现，建议在项目文档/关于页面中致谢 RootlessJamesDSP 作为架构参考
   - 这是开源社区的良好实践，也不构成法律义务

### 7.4 总结

> RootlessJamesDSP 的 **架构思路和技术方案可以自由借鉴**（思想不受版权保护），但 **具体源码不能直接复制**到非 GPL 项目中。本项目应采用"参考架构、独立实现"的策略，重点借鉴 per-session 静音 + DUMP 权限会话发现的核心思路，同时避免直接复制其 GPL 代码。

---

## 附录 A：关键源码引用索引

### A.1 音频管线核心（RootlessAudioProcessorService.kt）

```kotlin
// 捕获配置：匹配 MEDIA/GAME/UNKNOWN，排除自身和黑名单
val configBuilder = AudioPlaybackCaptureConfiguration.Builder(mediaProjection!!)
    .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
    .addMatchingUsage(AudioAttributes.USAGE_GAME)
    .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
excluded.forEach { configBuilder.excludeUid(it) }

// 回放：ALLOW_CAPTURE_BY_NONE 防止自捕获
attributesBuilder.setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE)

// 处理循环：read → process → write
recorder.read(floatBuffer, 0, size, AudioRecord.READ_BLOCKING)
engine.processFloat(floatBuffer, floatOutBuffer)
track.write(floatOutBuffer, 0, size, AudioTrack.WRITE_BLOCKING)
```

### A.2 静音效果核心（MutedAudioEffectFactory.kt）

```kotlin
// DynamicsProcessing 静音
DynamicsProcessing(Int.MAX_VALUE, sid, null).apply {
    setInputGainAllChannelsTo(-200f)
    enabled = true
}

// 隐藏 Volume effect 静音（fallback）
AudioEffectHidden(volumeHiddenTypeUuid, EFFECT_TYPE_NULL, Int.MAX_VALUE, sid).apply {
    setParameter(VolumeParams.MUTE.ordinal, 1)
    setParameter(VolumeParams.LEVEL.ordinal, -96)
}
```

### A.3 DUMP 权限会话发现（DumpUtils.kt）

```kotlin
val serviceBinder = SystemServiceHelper.getSystemService("media.audio_policy")
serviceBinder.dumpAsync(writePipe.fileDescriptor, args)
// 解析 "Session Id: X  UID: Y  ... Usage: Z"
```

### A.4 Shizuku 授权（OnboardingFragment.kt）

```kotlin
ShizukuSystemServerApi.PermissionManager_grantRuntimePermission(
    pkg, Manifest.permission.DUMP, UserHandle.USER_SYSTEM)
ShizukuSystemServerApi.AppOpsService_setMode(
    APP_OPS_OP_PROJECT_MEDIA, uid, pkg, APP_OPS_MODE_ALLOW)
```

---

## 附录 B：术语表

| 术语 | 说明 |
|------|------|
| AudioPlaybackCapture | Android 10+ 引入的 API，允许应用捕获其他应用的播放音频（需 MediaProjection） |
| MediaProjection | Android 屏幕录制/投射 API，AudioPlaybackCapture 依赖其授权 token |
| AudioEffect | Android 音频效果框架，可挂载到指定音频 session 上 |
| DynamicsProcessing | Android 9+ 内置的多频段动态处理效果，RootlessJamesDSP 用其 -200dB 输入增益实现静音 |
| DUMP 权限 | `android.permission.DUMP`，signature|privileged 级别，允许读取系统服务 dump 输出 |
| Shizuku | 一个通过 ADB 或 Root 启动的服务，允许普通应用以 shell 身份调用系统 API |
| per-session 效果 | 挂载在特定音频会话 ID 上的 AudioEffect，只影响该会话的音频输出 |
| 感知级输出替换 | 用户感知上原始音频被替换，但技术上仍是"原始静音 + 独立回放"的组合 |
| 真正的系统级输出替换 | 在 HAL/output mix 级别直接替换音频流，需要 Root |
