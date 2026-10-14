# Android 音频捕获与系统级音频处理技术边界研究报告

> **研究目标**：为"第三方视频 App BGM 实时降低，并让用户只听到处理后的声音"这一目标，系统梳理 Android 技术体系下各层级的能力边界，为最终技术路线判断提供依据。
>
> **研究日期**：2026-09-02
>
> **覆盖版本**：Android 10 (API 29) ~ Android 17 (API 37)

---

## 1. 执行摘要（核心结论）

### 1.1 最关键结论

**普通 Android App（非 Root、公开 API）无法实现"用户只听到处理后的声音"。**

原因如下：

1. `AudioPlaybackCapture` 是**纯旁路捕获（tap）**，它只复制一份音频 PCM 数据给捕获方，**完全不会阻止或静音原始 App 的音频继续输出到扬声器**。
2. 公开 API **没有提供"静音/暂停特定第三方 App 音频输出"的能力**。`AudioManager.setStreamVolume()` 只能按流类型（如 STREAM_MUSIC）调节全局音量，无法针对单个 App。
3. 如果捕获后通过 `AudioTrack` 重新播放处理后的音频，用户将听到**原始声音 + 处理后声音的叠加**，产生回声、延迟、相位抵消等不可接受的问题。
4. `AudioFocus` 只能让其他 App **自愿**降低音量或暂停（依赖目标 App 正确实现焦点回调），且影响的是**所有**媒体 App 而非特定 App，不适合作为持续的 BGM 降低方案。

### 1.2 各路线能力总览

| 路线 | 能捕获第三方音频 | 能替换原始输出 | 所需权限 | 风险 | 推荐度 |
|---|---|---|---|---|---|
| 普通 App（公开 API） | 部分（需目标 App 允许） | **否** | RECORD_AUDIO + MediaProjection | 低 | 仅用于验证/采集 |
| Root + AudioEffect | 是 | 是（全局输出 mix） | Root + 修改 audio_effects 配置 | 中（配置错误可能无声音） | 可行，但需谨慎 |
| AudioFlinger Hook | 是 | 是 | Root + 修改 system 分区 | **极高（bootloop 风险）** | 不推荐 |
| Audio HAL / Vendor | 是 | 是 | 修改 vendor 分区 | **极高（变砖风险）** | 不推荐 |
| OEM / 系统签名 App | 是 | 是 | 系统签名 + 特权权限 | 低（OEM 环境内） | 仅 OEM 可走 |

### 1.3 最低可靠实现层级

> **要可靠实现"第三方视频 App BGM 实时降低，用户只听到处理后的声音"，最低需要 Root 权限 + 自定义 AudioEffect 挂载到全局输出 mix（session 0），或等效的系统级音频插入点。**

普通 App 路线只能做到"捕获 + 处理 + 可选重新播放"，无法解决双声叠加。Root + AudioEffect 路线（如 ViPER4Android / JamesDSP 的实现方式）是当前第三方开发者可触及的最低风险方案，但在 Android 14+ 的 AIDL Audio HAL 架构下兼容性挑战增大。

---

## 2. AudioPlaybackCapture API 详细分析

### 2.1 API 引入版本与基本要求

| 项目 | 详情 |
|---|---|
| **引入版本** | Android 10（API Level 29） |
| **核心类** | `AudioPlaybackCaptureConfiguration` |
| **最低 API level** | 29（Android 10） |
| **必需权限** | `android.permission.RECORD_AUDIO`（运行时权限） |
| **必需用户授权** | `MediaProjectionManager.createScreenCaptureIntent()` 弹出的系统授权对话框，用户必须点击"立即开始" |
| **用户配置文件** | 捕获 App 和播放 App 必须在同一用户配置文件（user profile）下 |

> **来源**：[Android Developers - 录制视频和音频播放内容](https://developer.android.com/media/platform/av-capture?hl=zh-cn)

### 2.2 能捕获什么 — AudioUsage 限制

**只有以下三种 AudioUsage 可以被捕获：**

| AudioUsage | 说明 | 可捕获 |
|---|---|---|
| `USAGE_MEDIA` | 音乐、视频播放等媒体音频 | ✅ |
| `USAGE_GAME` | 游戏音频 | ✅ |
| `USAGE_UNKNOWN` | 未指定用途的音频 | ✅ |
| `USAGE_VOICE_CALL` | 语音通话 | ❌ |
| `USAGE_ALARM` | 闹钟 | ❌ |
| `USAGE_NOTIFICATION` | 通知 | ❌ |
| `USAGE_ASSISTANCE_*` | 辅助音（导航、无障碍等） | ❌ |
| `USAGE_ASSISTANT` | 助手 | ❌ |

**过滤方式**（二选一，不可混用）：

- **按用途过滤**：`addMatchingUsage(int usage)` 白名单 / `excludeUsage(int usage)` 黑名单。两者不能同时使用。
- **按 UID 过滤**：`addMatchingUid(int uid)` 白名单 / `excludeUid(int uid)` 黑名单。两者不能同时使用。

> 注意：`addMatchingUsage()` 和 `excludeUsage()` 不能同时调用；`addMatchingUid()` 和 `excludeUid()` 也不能同时调用。但 usage 过滤和 UID 过滤可以组合使用。

> **来源**：[Android Developers - Capture video and audio playback](https://developer.android.com/media/platform/av-capture)

### 2.3 捕获的是混合流还是单 App 流

**捕获的是混合流（output mix），不是单 App 独立流。**

`AudioPlaybackCapture` 的工作原理是在 AudioFlinger 的输出混音器（output mix）处做一个旁路 tap。即使通过 `addMatchingUid()` 指定了只捕获某个 UID 的 App，系统内部仍然是从输出 mix 中筛选匹配的 track 后重新混音，最终交付给捕获方的是一个混合后的 PCM 流。

这意味着：
- 如果同时有多个匹配的 App 在播放，捕获到的是它们的混音。
- 即使只匹配一个 App，该 App 内部的多个 AudioTrack 也已经被混音。
- **无法从捕获流中分离出单个 App 的独立音轨**。

> **来源**：[GenericJam/mob_audio_capture - GitHub](https://github.com/GenericJam/mob_audio_capture)，AOSP AudioFlinger 架构

### 2.4 捕获的 PCM 参数

| 参数 | 说明 |
|---|---|
| **Sample Rate** | 通常跟随设备的主输出采样率，现代 Android 设备多为 48000 Hz。部分高解析度音频设备可能输出 96000/192000 Hz（Android 16 Pixel 设备已出现 192kHz 输出导致捕获不匹配的问题） |
| **Channel Count** | 通常为 2（立体声），跟随输出设备配置 |
| **Encoding Format** | `AudioFormat.ENCODING_PCM_16BIT` 最常见；部分设备/配置可能支持 `ENCODING_PCM_FLOAT` |
| **参数决定权** | 由系统 AudioFlinger 的输出配置决定，捕获 App 可以在 `AudioRecord.Builder` 中指定期望参数，但实际生效的是系统协商后的结果 |
| **Buffer Size** | 通过 `AudioRecord.getMinBufferSize()` 获取最小值，实际使用通常设为最小值的 2~4 倍 |

> **注意**：Android 16 上已出现高解析度音频输出（192kHz）与硬编码 48kHz 捕获不匹配导致 AudioPatch 创建失败的问题，实际开发中应动态查询设备输出采样率而非硬编码。

> **来源**：[scrcpy issue #6218 - Android 16 sample rate mismatch](https://github.com/Genymobile/scrcpy/issues/6218)，[Android FastMixer 实例](https://blog.csdn.net/kangear/article/details/78552351)

### 2.5 延迟

`AudioPlaybackCapture` API **不会影响被捕获 App 的音频延迟**（官方明确说明）。

捕获端到端延迟构成：
- AudioFlinger 输出混音缓冲：通常 ~20ms（HAL frame count 960 frames @ 48kHz = 20ms）
- AudioRecord 读取缓冲：取决于 buffer size 配置，通常 20~80ms
- DSP 处理延迟：取决于算法，通常 <10ms
- **总捕获+处理延迟**：通常 50~150ms 量级

> 这个延迟对于"捕获后重新播放"方案是致命的——原始声音（0ms 延迟）和处理后声音（50~150ms 延迟）叠加会产生明显的回声/梳状滤波效应。

> **来源**：[Android Developers - av-capture](https://developer.android.com/media/platform/av-capture)，[Android audio latency design](https://source.android.com/docs/core/audio/latency/design)

### 2.6 对原始 App 的影响

- **不影响原始 App 的音频输出**：原始 App 的音频照常播放到扬声器/耳机。
- **不影响原始 App 的延迟**：官方明确说明 API 不增加被捕获 App 的延迟。
- **不消耗原始 App 的 CPU**：捕获和处理在捕获 App 的进程中进行。
- **DRM 保护内容**：使用 DRM（如 Widevine）解密的音频输出可能被系统排除在捕获之外，或被静音。

---

## 3. MediaProjection 授权与限制

### 3.1 完整授权流程

```
App 调用 MediaProjectionManager.createScreenCaptureIntent()
    ↓
系统弹出授权对话框（"App 将开始捕获屏幕上显示的所有内容"）
    ↓
用户点击"立即开始"（Start now）
    ↓
onActivityResult 返回 RESULT_OK 和 Intent data
    ↓
MediaProjectionManager.getMediaProjection(resultCode, data) 获取 MediaProjection 实例
    ↓
创建 AudioPlaybackCaptureConfiguration.Builder(mediaProjection)
    ↓
配置 usage/UID 过滤
    ↓
AudioRecord.Builder.setAudioPlaybackCaptureConfig(config) 构建 AudioRecord
    ↓
AudioRecord.startRecording() 开始捕获
```

### 3.2 Android 各版本变化

#### Android 10 (API 29)
- AudioPlaybackCapture 首次引入。
- 需要 MediaProjection 授权。
- 每次启动捕获需要用户确认。

#### Android 11 (API 30)
- 新增并发音频捕获 API（`AudioRecord`、`MediaRecorder`、`AAudioStream` 中启用/停用并发捕获的方法）。
- 共享音频输入策略完善。
- AudioPlaybackCapture 本身无重大变化。

> **来源**：[Android Developers - 共享音频输入](https://developer.android.com/media/platform/sharing-audio-input?hl=zh-cn)

#### Android 12 (API 31)
- 新增麦克风使用指示器（隐私保护），AudioPlaybackCapture 作为录音行为会触发麦克风指示器吗？**需实验验证**——AudioPlaybackCapture 捕获的是播放音频而非麦克风，但使用了 RECORD_AUDIO 权限，可能触发系统的录音指示器。
- Foreground service 限制开始收紧（从后台启动 FGS 受限）。
- Audio HAL v7 重大更新，为 AIDL HAL 迁移做准备。
- 组合音频设备路由（Combined audio device routing）。

#### Android 13 (API 33)
- 新增 `READ_MEDIA_AUDIO` 运行时权限（替代旧的 `READ_EXTERNAL_STORAGE` 对音频文件的访问）。
- 空间音频（Spatial Audio）和头部追踪。
- LE Audio（蓝牙低功耗音频）。
- MIDI 2.0。
- **AIDL Audio HAL 成为所有新 HAL 变更的强制要求**（HIDL HAL 不再新增 API）。

#### Android 14 (API 34) — 重大变化
- **Foreground service 类型强制声明**：targetSdk ≥ 34 的 App 必须为每个前台服务声明类型。
- **MediaProjection 必须使用 `mediaProjection` 类型的前台服务**：
  - Manifest 声明 `<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION" />`
  - Service 声明 `android:foregroundServiceType="mediaProjection"`
  - 调用 `startForeground()` 时传入 `ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION`
- **必须在启动前台服务之前调用 `createScreenCaptureIntent()`** 并获得用户授权。
- Audio HAL 接口从 HIDL 迁移到 **AIDL**（Android 14+ 官方推荐，新功能仅 AIDL 支持）。

> **来源**：[Android Developers - 必须提供前景服务类型](https://developer.android.com/about/versions/14/changes/fgs-types-required)，[Android Developers - 前景服务类型](https://developer.android.com/develop/background-work/services/fg-service-types)

#### Android 15 (API 35)
- **BOOT_COMPLETED 广播接收器无法启动 mediaProjection 类型前台服务**：targetSdk ≥ 35 的 App 不能在 `BOOT_COMPLETED` 接收器中启动 `mediaProjection`、`camera`、`microphone` 等类型的 FGS，否则抛出 `ForegroundServiceStartNotAllowedException`。
- **直接/分流音轨资源限制**：当 App 请求直接播放（direct）或分流播放（offload）且达到资源上限时，系统会使当前已打开的 `AudioTrack` 对象失效，以腾出资源给新请求。
- 新增 `LoudnessCodecController` API（基于 CTA-2075 标准的响度管理）。
- AIDL Audio HAL 迁移继续推进，部分厂商在 Android 15 设备上已完全切换到 AIDL Effects Factory。

> **来源**：[Android Developers - Android 15 行为变更](https://developer.android.com/about/versions/15/behavior-changes-all)，[Zebra DevCon 2025 - Android 15 changes](https://www.zebra.com/content/dam/zebra_dam/en/presentation/customer-facing/cfw2-presentation-customer-facing-devcon2025-en-us.pdf)

#### Android 16 (API 36)
- 高解析度音频输出成为更多设备的默认配置（Pixel 9 Pro 等设备出现 192kHz 输出）。
- AudioPlaybackCapture 在高采样率输出下可能出现采样率不匹配问题（需动态查询而非硬编码 48kHz）。
- AIDL Audio HAL 成为主流，HIDL 兼容层逐步废弃。
- **需持续关注官方行为变更文档**。

#### Android 17 (API 37) — 重大变化
- **后台音频强化（Background Audio Hardening）**：
  - 从 Android 17 开始，音频框架对**后台音频交互**强制执行限制，包括音频播放、音频焦点请求和音量更改 API。
  - **所有 App**（无论 targetSdk）在后台进行音频交互时，必须有可见的 Activity 或运行一个**非 SHORT_SERVICE 类型**的前台服务。
  - **targetSdk ≥ 37 的 App** 额外要求：后台运行时必须使用具有 **while-in-use (WIU) 能力**的前台服务（WIU 能力在用户主动操作或 App 可见时启动 FGS 才会被授予）。
  - 不满足条件时，音频播放和音量更改 API **静默失败**（不抛异常），音频焦点请求返回失败码。
  - 这对 AudioPlaybackCapture + AudioTrack 重新播放方案影响巨大：如果捕获 App 在后台（用户切到视频 App），其 AudioTrack 播放可能被系统阻止。

> **来源**：[Android Developers - Background audio hardening](https://developer.android.com/about/versions/17/changes/bg-audio)，[Android Authority - Android 17 background audio](https://www.androidauthority.com/android-17-background-audio-hardening-3669109/)

### 3.3 是否需要用户每次确认

- **每次创建新的 MediaProjection 会话都需要用户确认**。授权对话框不能跳过。
- MediaProjection 实例在前台服务的生命周期内有效，服务运行期间不需要重复授权。
- 如果服务被杀死或用户撤销授权，需要重新走授权流程。
- **没有"永久授权"选项**——用户无法勾选"始终允许"，每次启动新会话都必须手动确认。

### 3.4 能否后台持续

| 场景 | 能否持续 | 条件 |
|---|---|---|
| App 切换到后台（用户切到其他 App） | ✅ | 必须运行 `mediaProjection` 类型前台服务，显示常驻通知 |
| 屏幕关闭 | ✅ | 同上，前台服务保持运行 |
| Android 12+ 后台 FGS 启动限制 | ⚠️ | 不能从后台广播/后台状态启动 FGS，必须在 App 可见或有用户交互时启动 |
| Android 15+ BOOT_COMPLETED 启动 | ❌ | targetSdk ≥ 35 不能从 BOOT_COMPLETED 启动 mediaProjection FGS |
| Android 17+ 后台音频播放 | ⚠️ | 捕获本身不受影响，但通过 AudioTrack 重新播放处理后的音频在后台可能被阻止 |

---

## 4. 第三方 App 捕获策略分析

### 4.1 捕获策略机制

第三方 App 通过两种方式控制自身音频是否可被捕获：

#### 方式一：Manifest 属性
```xml
<application android:allowAudioPlaybackCapture="true">
```
- 默认值：targetSdk ≥ 29 (Android 10+) 的 App 默认为 `true`；targetSdk ≤ 28 的 App 默认为 `false`。
- 设为 `false` 后，该 App 的音频**只能被系统应用捕获**，普通第三方 App 无法捕获。

#### 方式二：AudioAttributes 运行时策略
```java
AudioAttributes.Builder.setAllowedCapturePolicy(int policy)
```
三个取值：

| 策略值 | 含义 |
|---|---|
| `ALLOW_CAPTURE_BY_ALL` | 任何应用都可以捕获（需同时满足 usage 条件） |
| `ALLOW_CAPTURE_BY_SYSTEM` | 仅系统应用可以捕获（普通第三方 App 无法捕获） |
| `ALLOW_CAPTURE_BY_NONE` | 任何应用都无法捕获（包括系统应用） |

也可以通过 `AudioManager.setAllowedCapturePolicy()` 全局设置 App 内所有播放器的策略。

### 4.2 策略组合效果矩阵

| Manifest `allowAudioPlaybackCapture` | `ALLOW_CAPTURE_BY_ALL` | `ALLOW_CAPTURE_BY_SYSTEM` | `ALLOW_CAPTURE_BY_NONE` |
|---|---|---|---|
| `true` | 任何 App 可捕获 | 仅系统可捕获 | 无法捕获 |
| `false` | 仅系统可捕获 | 仅系统可捕获 | 无法捕获 |

> 关键：如果 Manifest 设为 `false`，即使运行时设为 `ALLOW_CAPTURE_BY_ALL`，也降级为"仅系统可捕获"。

> **来源**：[Android Developers - av-capture 捕获策略表](https://developer.android.com/media/platform/av-capture?hl=zh-cn)

### 4.3 主流视频 App 的实际捕获策略

> **注意**：以下信息基于公开资料和社区报告，具体策略可能随 App 版本更新而变化。**标注"需实验验证"的结论必须通过真实设备测试确认。**

| App | 推测策略 | 依据 | 可信度 |
|---|---|---|---|
| **抖音** | `ALLOW_CAPTURE_BY_ALL`（允许） | 多个 Android 内录 Demo 报告称抖音开放了音频捕获配置；抖音录屏功能本身需要支持内录 | 中（需实验验证） |
| **B站** | `ALLOW_CAPTURE_BY_ALL`（允许） | 社区报告称 B 站允许音频捕获；部分 DRM 内容可能排除 | 中（需实验验证） |
| **腾讯视频** | 不确定，部分内容可能 `ALLOW_CAPTURE_BY_SYSTEM` 或 DRM 排除 | VIP 版权内容可能使用 DRM 保护，系统录屏时可能被静音 | 低（需实验验证） |
| **YouTube** | `ALLOW_CAPTURE_BY_ALL`（允许） | YouTube App 通常允许音频捕获；但 YouTube Music 可能有不同策略 | 中（需实验验证） |
| **Chrome / 浏览器网页视频** | `ALLOW_CAPTURE_BY_ALL`（允许） | 浏览器通常使用 USAGE_MEDIA 且允许捕获；但网页内的 DRM 内容（如 Netflix 网页版）可能被排除 | 中（需实验验证） |
| **Spotify** | `ALLOW_CAPTURE_BY_NONE` 或等效阻止 | RootlessJamesDSP 文档明确指出 Spotify 阻止内部音频捕获 | 高 |
| **Netflix** | DRM 保护，无法捕获 | Widevine DRM 保护的音频输出被系统排除 | 高 |

> **重要**：DRM（Widevine L1）保护的内容，即使 App 设置了 `ALLOW_CAPTURE_BY_ALL`，系统也可能在 AudioFlinger 层将受保护的音频流排除在捕获之外，或输出静音。这是 DRM 框架的硬性要求，不受 capture policy 控制。

### 4.4 ALLOW_CAPTURE_BY_SYSTEM 对普通 App 的意义

`ALLOW_CAPTURE_BY_SYSTEM` 意味着：
- **普通第三方 App（包括你的 App）无法捕获该 App 的音频**。
- 只有**系统应用**（platform signature 或 privileged 权限的 App）可以捕获。
- 系统的屏幕录制功能（如 Android 原生录屏、OEM 录屏）属于系统应用，可以捕获。
- 这是很多流媒体 App 的默认选择——它们允许系统录屏，但不允许第三方 App 录制。

---

## 5. "双声叠加"问题深度分析

### 5.1 问题本质

```
第三方视频 App ──────────────────────→ Speaker（原始声音，0ms 延迟）
       │
       ↓（AudioPlaybackCapture 旁路 tap，复制一份）
    PCM 数据
       ↓
    BGM DSP 处理
       ↓
    AudioTrack 播放 ─────────────────→ Speaker（处理后声音，~50-150ms 延迟）
```

用户最终听到的是：**原始声音 + 延迟的处理后声音**。

### 5.2 为什么公开 API 无法解决

#### 5.2.1 无法静音特定 App 的原始输出

Android 公开 API 中**没有任何方法可以静音或暂停特定第三方 App 的音频输出**：

| 尝试的方法 | 能否针对特定 App | 说明 |
|---|---|---|
| `AudioManager.setStreamVolume()` | ❌ 否 | 只能按流类型（STREAM_MUSIC 等）调节全局音量，会影响所有媒体 App |
| `AudioManager.adjustStreamVolume()` | ❌ 否 | 同上 |
| `AudioManager.setStreamMute()` | ❌ 否 | 同上，且已废弃 |
| `AudioFocus` | ❌ 否（影响所有媒体 App） | 见 5.2.2 分析 |
| `AudioTrack.setVolume()` | ❌ 否 | 只能控制自己 App 的 AudioTrack，无法控制其他 App |
| `MediaSession` 音量控制 | ⚠️ 有限 | 可以向拥有 MediaSession 的 App 发送音量命令，但需要 App 支持，且不是所有视频 App 都暴露 MediaSession |

> **隐藏 API**：`IPlayer.setVolume()`（通过 `AudioManager.getPlayers()` 获取 `IPlayer` 列表）可以针对特定 App 的播放器设置音量。这是 MIUI"应用音量单独调节"功能使用的 API。但它是 **@SystemApi / 隐藏 API**，普通第三方 App 调用需要 `android.permission.MODIFY_AUDIO_ROUTING` 特权权限，且在 Android 12+ 上对隐藏 API 的访问限制更严格。

> **来源**：[farizanjum/mixer-1 - GitHub](https://github.com/farizanjum/mixer-1)（MIUI per-app volume 实现分析）

#### 5.2.2 AudioFocus 能否用来让其他 App 降低音量

`AudioFocus` 机制可以让其他 App 在失去焦点时降低音量（duck）或暂停，但存在以下根本局限：

1. **自愿性**：AudioFocus 是合作式机制。目标 App 必须实现 `OnAudioFocusChangeListener` 并在收到 `AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK` 时主动降低音量。如果 App 没有正确实现（很多视频 App 确实实现了），则完全无效。

2. **Android 8.0+ 自动 ducking**：当另一个 App 请求 `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK` 时，系统会**自动**对持有焦点的媒体 App 进行 ducking（降低音量），不需要目标 App 手动实现。但：
   - 自动 ducking 的降低幅度是系统决定的（通常降低到 ~20% 音量），**不可自定义**。
   - 它影响的是**所有**持有媒体焦点的 App，不是特定 App。
   - 它设计用于**短暂**的音频插入（如导航语音），不是持续的 BGM 降低。

3. **焦点竞争**：如果你的 App 请求了音频焦点来播放处理后的音频，那么视频 App 会失去焦点并 duck/pause。但你的 App 播放的是处理后的音频（包含视频的人声+降低后的 BGM），这实际上形成了一个闭环：
   - 视频 App 被 duck → 原始声音降低
   - 你的 App 播放处理后的声音 → 用户听到处理后的声音
   - **但这要求你的 App 持续持有音频焦点**，而 `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK` 是暂时性焦点，设计上不应该长期持有。
   - 长期持有 `AUDIOFOCUS_GAIN` 会导致视频 App 完全暂停（而非 duck），这不是期望的行为。

4. **无法实现 BGM 分离降低**：AudioFocus 只能整体降低或暂停目标 App 的音量，**无法只降低 BGM 而保留人声**。这正是需要 DSP 处理的原因，但 DSP 处理需要先捕获音频，而捕获后又面临双声叠加。

#### 5.2.3 其他可能的绕过方案及局限

| 方案 | 原理 | 局限 |
|---|---|---|
| **蓝牙 SCO 虚拟路由** | 创建虚拟蓝牙 SCO 设备，将音频路由到虚拟设备再处理 | 需要系统级权限修改音频路由；普通 App 无法创建虚拟音频设备 |
| **ALSA 回环（snd-aloop）** | 通过内核模块创建虚拟声卡回环 | 需要 Root + 内核模块支持；修改 vendor/boot 分区；高风险 |
| **Xposed / LSPosed Hook** | Hook AudioTrack/AudioFlinger 来拦截和修改音频 | 需要 Root + LSPosed 框架；Hook 系统服务有稳定性风险；Android 14+ 兼容性差 |
| **USB 音频回环** | 通过 USB 声卡物理回环 | 需要额外硬件；不切实际 |

### 5.3 结论

> **在公开 API 范围内，"双声叠加"问题无法解决。** 任何"捕获 → DSP → AudioTrack 重新播放"的方案都会导致用户听到原始声音和处理后声音的叠加。

唯一可能在普通 App 层面缓解的方式是：
1. 通过 AudioFocus 让视频 App duck（降低原始音量），同时播放处理后的音频。
2. 但这只能做到"原始音量降低 + 处理后音频播放"，无法做到"原始完全静音 + 只听处理后"。
3. 且 AudioFocus ducking 的降低幅度不可控，长期持有暂时性焦点不符合系统设计。

**因此，要实现"用户只听到处理后的声音"，必须进入系统级音频处理层级。**

---

## 6. 系统级音频架构分析

### 6.1 完整音频链路

```
第三方 App 进程                          audioserver 进程                     HAL / 硬件
─────────────                          ────────────────                     ──────────
AudioTrack (Java/Kotlin)
    │ (binder IPC + shared memory)
    ↓
AudioFlinger::PlaybackThread
    ├── MixerThread（普通混音输出）
    │     ├── Track 1 (App A 的 AudioTrack)
    │     ├── Track 2 (App B 的 AudioTrack)
    │     ├── ...
    │     ├── AudioEffect 链（session 0 全局 effect 挂在这里）
    │     └── threadLoop_mix() → 混音 → 写入 HAL
    │
    ├── DirectOutputThread（直通输出，不混音）
    ├── OffloadThread（硬件解码分流）
    └── MmapThread（AAudio MMAP 低延迟路径）
              │
              ↓
        Audio HAL (AIDL / HIDL)
              │
              ↓
        Vendor DSP / 硬件 Codec
              │
              ↓
        Speaker / 耳机 / 蓝牙
```

### 6.2 AudioFlinger 线程类型

| 线程类型 | 角色 | 说明 |
|---|---|---|
| **MixerThread** | 普通混音输出 | 最常见的播放线程，支持多个 AudioTrack 混音到一个输出流。全局 AudioEffect（session 0）挂在此线程的 effect 链上 |
| **DirectOutputThread** | 直通输出 | 不进行混音，单个 AudioTrack 直接输出到 HAL。用于低延迟或高采样率音频。**不经过全局 effect 链** |
| **OffloadThread** | 硬件解码分流 | 将压缩音频（MP3/AAC等）直接交给硬件 DSP 解码，不经过 ARM CPU 混音。**全局 effect 可能不生效** |
| **RecordThread** | 录音输入 | 处理麦克风/音频输入数据。AudioPlaybackCapture 的远端读取在此线程的对端 |
| **MmapThread** | MMAP 低延迟 | AAudio MMAP 路径，App 直接映射硬件缓冲区，绕过 AudioFlinger 混音。**全局 effect 不生效** |

> **关键洞察**：如果视频 App 使用了 DirectOutput、Offload 或 MMAP 路径，全局 AudioEffect 可能无法处理其音频。现代视频 App 通常使用标准 MixerThread 路径，但需要实验验证。

> **来源**：[掘金 - AudioFlinger 笔记](https://juejin.cn/post/7624851801591300131)，[CSDN - AudioFlinger Threads 分析](https://blog.csdn.net/Ciellee/article/details/102602201)

### 6.3 AudioEffect 框架

#### 6.3.1 工作原理

AudioEffect 框架允许在音频链路中插入信号处理模块：

```
AudioTrack → [Effect 1] → [Effect 2] → ... → Mixer → HAL
```

Effect 有两种连接模式：
- **Insert（插入式）**：Effect 串联在音频流中，处理所有经过的音频数据。可以挂在特定 session（单 App）或 session 0（全局输出 mix）。
- **Auxiliary（辅助式）**：Effect 并联在音频流中，通过 send level 控制发送量，处理后的音频与原始音频混合。**必须挂在 session 0（全局输出 mix）**。

#### 6.3.2 全局输出 Effect（session 0）

挂在 session 0 的 Effect 会处理**所有经过 MixerThread 的音频**（即所有 App 的混音输出）。这是实现"系统级音频处理"的标准插入点。

**创建全局 AudioEffect 的条件：**

| 条件 | 普通 App | 系统/特权 App | Root |
|---|---|---|---|
| 创建 session 0 的 Insert Effect | ❌ | ✅ | ⚠️（通过修改配置间接实现） |
| 创建 Auxiliary Effect | ❌（必须 session 0） | ✅ | ⚠️ |
| 使用 `AudioEffect(Context, UUID, ...)` 标准构造函数 | ⚠️（只能挂自己的 session） | ✅ | ⚠️ |
| 使用 `@SystemApi` `AudioEffect(UUID, AudioDeviceAttributes)` 构造函数 | ❌ | ✅ | ❌（需要系统签名） |
| 修改 `audio_effects.xml` 注册自定义 effect | ❌ | ✅ | ✅（可写入 vendor/etc） |
| 加载自定义 effect 库（.so） | ❌ | ✅ | ✅ |

> **普通 App 的局限**：普通第三方 App 只能为**自己的 AudioTrack session** 创建 AudioEffect，无法创建 session 0 的全局 effect。这是 Android 安全模型的设计——防止任意 App 篡改系统全局音频输出。

> **来源**：[AOSP - Audio effects](https://source.android.com/docs/core/audio/audio-effects)，[AudioEffect.java 源码](https://github.com/commaai/android_frameworks_base/blob/master/media/java/android/media/audiofx/AudioEffect.java)

#### 6.3.3 Root 如何实现全局 AudioEffect（ViPER4Android / JamesDSP 方式）

Root 方案不直接通过 Java API 创建全局 effect，而是通过以下步骤：

1. **编写自定义 AudioEffect 原生库**（`.so` 文件），实现 AOSP 定义的 effect 接口（`effect_interface_t` / `IEffect` AIDL）。
2. **将库文件复制到系统库目录**（如 `/vendor/lib64/soundfx/` 或 `/system/lib64/soundfx/`）。
3. **修改 `audio_effects.xml` 配置文件**（位于 `/vendor/etc/` 或 `/system/etc/`），注册自定义 effect 的 UUID、库名和效果描述。
4. **重启 audioserver**（或重启设备），让 AudioFlinger 加载新的 effect 配置。
5. **通过 App 控制 effect 参数**：App 使用标准 `AudioEffect` API，通过 UUID 找到已注册的自定义 effect，将其 attach 到 session 0（需要特权或通过 root 辅助获取 session）。

**Android 14+ AIDL HAL 的影响：**
- AIDL Audio Effects HAL 将 effectProxy 逻辑从 XML 配置移到了 audio framework。
- `IFactory.queryEffects()` 和 `IFactory.queryProcessing()` 由 framework 查询所有 effect 实例。
- 传统的修改 `audio_effects.xml` 方式在 AIDL HAL 上可能不生效或需要额外的 proxy 配置。
- ViPER4Android 在 Android 15 上的兼容性问题部分源于此——需要 AIDL 模式的驱动适配。
- XDA 社区报告：部分 Android 15 设备（Pixel、OnePlus、Samsung）已切换到新的 AIDL audio engine，旧的 HIDL effect 模块不再工作。

> **来源**：[ViPER4Android GitHub](https://github.com/likelikeslike/ViPER4Android)，[XDA - ViPER4Android Android 15 兼容性](https://xdaforums.com/t/anyone-get-viper4android-working-on-their-13-with-android-15.4714926/)，[AOSP - AIDL and HIDL Audio HAL comparison](https://source.android.com/docs/core/audio/aidl-hidl-comparison)

### 6.4 AudioPolicyService

| 方面 | 说明 |
|---|---|
| **角色** | 音频策略的制定者。决定音频路由（哪个流输出到哪个设备）、设备连接状态管理、音量策略、音频焦点管理 |
| **与 AudioFlinger 的关系** | AudioPolicyService 是"大脑"（决策），AudioFlinger 是"手"（执行）。Policy 决定打开哪个输出设备、使用哪个线程，Flinger 负责实际混音和数据传输 |
| **核心组件** | Engine（策略引擎，解析 XML 配置、执行路由算法）、HwModuleCollection（管理音频硬件模块）、DeviceManager（设备管理） |
| **配置文件** | `/vendor/etc/audio_policy_configuration.xml`（或 `/system/etc/`），定义 module、devicePort、mixPort、route 等 |
| **能否拦截特定 App** | AudioPolicy 本身不处理音频数据，只做路由决策。理论上可以通过自定义 AudioPolicy 模块将特定 App 的音频路由到特殊设备，但这需要修改 framework 或 vendor，普通 App/Root 都难以实现 |

> **来源**：[掘金 - AudioPolicyService 笔记](https://juejin.cn/post/7624449484358647834)，[CSDN - AudioPolicyService 深入剖析](https://blog.csdn.net/u010164190/article/details/89367664)

### 6.5 Audio HAL / Vendor DSP

#### 6.5.1 HAL 版本演进

| 版本 | Android 版本 | 接口类型 | 说明 |
|---|---|---|---|
| Audio HAL v5 | Android 8-9 | HIDL | 传统 HIDL HAL |
| Audio HAL v6 | Android 10-11 | HIDL | AudioPlaybackCapture 引入 |
| Audio HAL v7 | Android 12-13 | HIDL | 为 AIDL 迁移做准备，组合路由 |
| **Audio HAL v8 (AIDL)** | **Android 14+** | **AIDL** | **官方推荐，新功能仅 AIDL 支持** |

- Android 14 起，Audio HAL 接口使用 **AIDL** 定义。
- 旧版使用 HIDL 定义。
- 合作伙伴和 SoC 供应商被鼓励重新实现 Audio HAL 以提供 AIDL 接口。
- **Android 14+ 不再向 HIDL HAL 添加新 API**。

> **来源**：[AOSP - Audio HAL](https://source.android.com/docs/core/audio/implement)

#### 6.5.2 HAL 组成

Audio HAL 包含两部分：
1. **Core HAL**：AudioFlinger 用来读写音频数据和控制路由的主 API。
2. **Effects HAL**：AudioEffect 框架用来控制音频效果的 API。自定义 effect 库通过 Effects HAL 注册和加载。

#### 6.5.3 Vendor DSP 的位置

```
AudioFlinger → Audio HAL → Vendor Audio HAL 实现 → Vendor DSP / 硬件 Codec → 物理输出
```

- Vendor DSP（如 Qualcomm Hexagon DSP、联发科 APU）可以承担音频解码、后处理、效果处理等计算任务。
- Offload 模式下，压缩音频直接发送到 Vendor DSP 解码，绕过 ARM CPU 的 AudioFlinger 混音。
- OEM 可以在 Vendor DSP 中实现自定义音频效果（如小米的音质音效、华为的 Histen 音效）。

#### 6.5.4 OEM 厂商的自定义音频处理

| 厂商 | 音频技术 | 推测实现层级 |
|---|---|---|
| **小米/红米 (MIUI/HyperOS)** | 音质音效、视频工具箱、游戏空间音效 | Framework 定制 + Vendor DSP + 系统签名 App |
| **华为/荣耀 (EMUI/HarmonyOS)** | Histen 音效、音频分散 | Framework 定制 + Vendor DSP |
| **OPPO/一加/realme (ColorOS)** | Real 原声技术、游戏音效 | Framework 定制 + Vendor DSP |
| **vivo/iQOO (OriginOS)** | DeepField 深空音效、游戏音效 | Framework 定制 + Vendor DSP |
| **三星 (One UI)** | SoundAlive、杜比全景声 | Framework 定制 + Vendor DSP |

> **推断（非官方文档）**：国产手机的"视频工具箱"/"游戏空间"中的音频处理功能，最可能的实现方式是：
> 1. OEM 在 framework 层定制了 AudioEffect 或 AudioFlinger 的扩展，允许系统签名 App 创建全局音频处理。
> 2. 部分功能可能在 Vendor DSP 或 vendor audio_effects.xml 中实现。
> 3. 视频工具箱 App 本身是系统签名的特权 App，可以调用普通 App 无法访问的隐藏 API（如 per-app volume、全局 effect 控制）。
> 4. **普通第三方开发者无法复制这一路线**，因为它依赖 OEM 的 framework 定制和系统签名。

---

## 7. Android 版本差异对照表

| 维度 | Android 10 | Android 11 | Android 12 | Android 13 | Android 14 | Android 15 | Android 16 | Android 17 |
|---|---|---|---|---|---|---|---|---|
| **API Level** | 29 | 30 | 31 | 33 | 34 | 35 | 36 | 37 |
| **AudioPlaybackCapture** | ✅ 首次引入 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **可捕获 Usage** | MEDIA/GAME/UNKNOWN | 同左 | 同左 | 同左 | 同左 | 同左 | 同左 | 同左 |
| **MediaProjection 授权** | 每次需确认 | 同左 | 同左 | 同左 | 同左 | 同左 | 同左 | 同左 |
| **FGS 类型声明** | 不需要 | 不需要 | 不需要 | 不需要 | **强制** | 强制 | 强制 | 强制 |
| **mediaProjection FGS** | 不需要 | 不需要 | 不需要 | 不需要 | **必须** | 必须 | 必须 | 必须 |
| **BOOT_COMPLETED 启动 FGS** | 允许 | 允许 | 受限 | 受限 | 受限 | **禁止 mediaProjection 类型** | 禁止 | 禁止 |
| **后台音频播放限制** | 无 | 无 | 无 | 无 | 无 | 无 | 无 | **后台音频强化** |
| **Audio HAL 类型** | HIDL v6 | HIDL v6 | HIDL v7 | HIDL v7 | **AIDL v8** | AIDL v8 | AIDL v8 | AIDL v8 |
| **HIDL HAL 新 API** | ✅ | ✅ | ✅ | ✅ | ❌ 停止 | ❌ | ❌ | ❌ |
| **全局 AudioEffect (Root)** | ✅ 容易 | ✅ 容易 | ✅ | ⚠️ | ⚠️ AIDL 适配 | ⚠️ 兼容性问题 | ⚠️ | ⚠️ |
| **高解析度音频** | 少见 | 少见 | 少见 | 少见 | 增多 | 增多 | **192kHz 常见** | 常见 |
| **麦克风/录音指示器** | 无 | 无 | **新增** | 有 | 有 | 有 | 有 | 有 |
| **空间音频** | 无 | 无 | 无 | **新增** | 完善 | 完善 | 完善 | 完善 |
| **LE Audio** | 无 | 无 | 无 | **新增** | 完善 | 完善 | 完善 | 完善 |

### 7.1 各版本关键音频变更详解

#### Android 10 (API 29)
- **AudioPlaybackCapture 引入**：首次允许第三方 App 捕获其他 App 的播放音频。
- 引入 `AudioPlaybackCaptureConfiguration`、`allowAudioPlaybackCapture` manifest 属性。
- `ALLOW_CAPTURE_BY_ALL/SYSTEM/NONE` 策略引入。

#### Android 11 (API 30)
- 并发音频捕获 API：`AudioRecord`、`MediaRecorder`、`AAudioStream` 新增启用/停用并发捕获的方法。
- 共享音频输入策略完善。
- 包可见性限制（影响查询其他 App 信息）。

#### Android 12 (API 31)
- **麦克风使用指示器**：系统状态栏显示麦克风使用图标，AudioPlaybackCapture 使用 RECORD_AUDIO 权限可能触发。
- **前台服务启动限制**：从后台状态启动 FGS 受到更严格限制。
- **Audio HAL v7**：重大更新，为 AIDL 迁移做准备。
- **组合音频设备路由**：支持同时输出到多个设备。
- **SoundTrigger 增强**：改进麦克风使用归因和权限执行。

#### Android 13 (API 33)
- **`READ_MEDIA_AUDIO` 运行时权限**：细分媒体文件访问权限。
- **空间音频 + 头部追踪**：新的音频管线架构。
- **LE Audio**：蓝牙低功耗音频支持。
- **MIDI 2.0**。
- **AIDL HAL 强制**：所有新的 HAL 变更必须使用 AIDL，HIDL 不再新增 API。

#### Android 14 (API 34)
- **FGS 类型强制声明**：targetSdk ≥ 34 必须声明前台服务类型。
- **`mediaProjection` FGS 类型**：使用 MediaProjection 必须运行 `mediaProjection` 类型的前台服务，声明 `FOREGROUND_SERVICE_MEDIA_PROJECTION` 权限。
- **授权时序**：必须在启动 FGS 之前调用 `createScreenCaptureIntent()` 并获得用户授权。
- **AIDL Audio HAL**：官方推荐使用 AIDL 定义 Audio HAL 接口，新功能仅 AIDL 支持。

#### Android 15 (API 35)
- **BOOT_COMPLETED FGS 限制**：targetSdk ≥ 35 不能从 BOOT_COMPLETED 接收器启动 `mediaProjection`、`camera`、`microphone` 等类型 FGS。
- **直接/分流音轨失效**：达到资源上限时，系统使已打开的 direct/offload AudioTrack 失效。
- **`LoudnessCodecController`**：基于 CTA-2075 标准的响度管理 API。
- **AIDL Effects Factory 普及**：更多厂商切换到 AIDL audio engine，旧 HIDL effect 模块兼容性下降。

#### Android 16 (API 36)
- 高解析度音频输出普及（192kHz），AudioPlaybackCapture 硬编码 48kHz 可能失败。
- AIDL Audio HAL 成为绝对主流。
- 需持续关注官方行为变更。

#### Android 17 (API 37)
- **后台音频强化（Background Audio Hardening）**：
  - 所有 App 后台音频交互需可见 Activity 或非 SHORT_SERVICE FGS。
  - targetSdk ≥ 37 需 while-in-use (WIU) 能力的 FGS。
  - 不满足条件时音频播放/音量更改静默失败，音频焦点请求失败。
  - **对"捕获+AudioTrack 重新播放"方案影响致命**：后台时 AudioTrack 播放可能被阻止。
- **音频管理的 SCO 架构**：蓝牙 SCO 路由从 BT framework 移到 audio framework。

---

## 8. 四条技术路线边界对比表

### 8.1 路线一：普通 Android App（公开 API）

```
第三方 App → AudioPlaybackCapture → AudioRecord → PCM → JNI → BGM DSP → AudioTrack → Speaker
```

| 维度 | 分析 |
|---|---|
| **能做到什么** | 捕获允许捕获的第三方 App 音频（USAGE_MEDIA/GAME/UNKNOWN + ALLOW_CAPTURE_BY_ALL）；对 PCM 进行任意 DSP 处理；可选通过 AudioTrack 播放处理后的音频 |
| **不能做到什么** | **无法静音原始 App 的音频输出**；无法避免双声叠加；无法捕获 ALLOW_CAPTURE_BY_SYSTEM/NONE 的 App；无法捕获 DRM 内容；无法针对单 App 独立处理（捕获的是混合流） |
| **所需权限** | `RECORD_AUDIO`（运行时）；MediaProjection 用户授权；`FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_MEDIA_PROJECTION`（Android 14+）；`SYSTEM_ALERT_WINDOW`（悬浮窗） |
| **Android 版本** | Android 10+ |
| **后台运行** | 可通过 mediaProjection FGS 后台持续捕获；但 Android 17+ 后台 AudioTrack 播放可能被阻止 |
| **风险** | 低。不修改系统，不影响稳定性。最坏情况是捕获失败或双声叠加体验差 |
| **适用场景** | 技术验证、PCM 数据采集、DSP 算法验证、录屏类应用 |
| **是否推荐** | **作为第一阶段验证推荐**，但作为最终方案**不推荐**——无法解决双声叠加 |

### 8.2 路线二：Root + AudioEffect

```
第三方 App → AudioTrack → AudioFlinger MixerThread → [自定义 AudioEffect (BGM DSP)] → HAL → Speaker
```

| 维度 | 分析 |
|---|---|
| **能做到什么** | 在全局输出 mix（session 0）插入自定义 AudioEffect，处理所有经过 MixerThread 的音频；BGM DSP 直接作用于最终输出，**不存在双声叠加**；可以通过 per-app session effect 实现针对特定 App 的处理（需获取真实 session ID） |
| **不能做到什么** | 无法处理 DirectOutput/Offload/MMAP 路径的音频（需实验验证视频 App 是否走这些路径）；Android 14+ AIDL HAL 下自定义 effect 注册更复杂；无法针对 DRM 保护内容 |
| **所需权限** | Root（Magisk/KernelSU）；修改 `audio_effects.xml`；写入自定义 effect .so 到系统库目录；控制 App 可能需要 Shizuku 或辅助手段获取 session 0 |
| **Android 版本** | 理论上全版本，但 Android 14+ AIDL HAL 兼容性需验证；Android 15 上部分设备已出现兼容性问题 |
| **后台运行** | 效果在 audioserver 进程中运行，不受 App 后台限制。控制 App 可以被杀死，效果仍在运行（直到重启） |
| **风险** | **中**。配置错误可能导致无声或 bootloop（如果修改 system 分区）。使用 Magisk 模块方式（systemless）风险较低——不修改实际 system 分区，卸载模块即可恢复。但 `audio_effects.xml` 配置错误可能导致 audioserver 崩溃循环 |
| **稳定性** | 取决于 effect 库质量。原生 effect 崩溃可能导致 audioserver 重启，影响整个系统音频。需要健壮的错误处理 |
| **代表项目** | ViPER4Android、JamesDSP Manager、AudioFreedom、RootlessJamesDSP（Shizuku 变体） |
| **是否推荐** | **作为最终方案推荐**（在 Root 设备上），但需注意：①使用 Magisk systemless 模块降低风险；②充分测试 AIDL HAL 兼容性；③effect 库必须健壮，避免崩溃影响系统 |

### 8.3 路线三：AudioFlinger Hook / 替换

```
第三方 App → AudioTrack → [Hook/替换后的 AudioFlinger] → 自定义 DSP → HAL → Speaker
```

| 维度 | 分析 |
|---|---|
| **能做到什么** | 完全控制音频混音过程，可以在任意位置插入自定义处理；可以针对特定 App/Track 处理；可以修改路由和混音逻辑 |
| **不能做到什么** | 几乎没有技术上的不能，但工程复杂度极高 |
| **所需权限** | Root + 修改 system 分区（替换 `libaudioflinger.so` 或 `audioserver` 可执行文件）；或通过 PLT Hook / Inline Hook 在运行时篡改 AudioFlinger（需 Root 注入到 audioserver 进程） |
| **Android 版本** | 每个版本的 AudioFlinger 实现不同，需要逐版本适配。AIDL HAL 迁移后架构变化更大 |
| **风险** | **极高**。替换系统核心库可能导致 bootloop、系统不稳定、所有音频功能失效。audioserver 是系统关键进程，崩溃会导致系统服务重启循环。注入到系统进程有 SELinux 限制和安全检测 |
| **是否需要修改 system 分区** | **是**（替换库文件）或运行时注入（仍需 Root 且高风险） |
| **bootloop 风险** | **高**。任何错误都可能导致系统无法启动音频服务，进而影响系统启动 |
| **是否推荐** | **强烈不推荐**。风险收益比极差。AudioEffect 路线已经能实现相同的目标，且风险低得多。只有在 AudioEffect 路线无法满足需求（如需要处理 Offload/MMAP 路径）时才考虑，且应在有完整恢复方案的设备上进行 |

### 8.4 路线四：Audio HAL / Vendor 替换

```
第三方 App → AudioFlinger → [替换/Hook 的 Audio HAL] → 自定义 DSP → Vendor DSP / 硬件 → Speaker
```

| 维度 | 分析 |
|---|---|
| **能做到什么** | 在 HAL 层拦截所有音频数据（包括 Offload 路径的解码后数据）；可以利用 Vendor DSP 进行硬件加速处理；完全控制最终输出 |
| **不能做到什么** | 无法区分具体是哪个 App 的音频（HAL 层已经是混音后的最终流，除非在 HAL 实现中添加 per-stream 追踪）；OEM 绑定程度高 |
| **所需权限** | Root + 修改 vendor 分区（替换 `audio.primary.*.so` 等 HAL 实现库）；或修改 audio_policy_configuration.xml 指向自定义 HAL |
| **Android 版本** | AIDL HAL（Android 14+）和 HIDL HAL（Android 13-）的接口完全不同，需要分别实现 |
| **风险** | **极高**。vendor 分区修改可能导致设备变砖（特别是 A/B 分区设备，如果两个槽位都损坏）。HAL 实现错误会导致所有音频功能失效，包括通话、闹钟、通知。Vendor DSP 固件修改风险更高 |
| **OEM 绑定程度** | **极高**。每个厂商的 HAL 实现、DSP 固件、配置文件都不同。为小米写的 HAL 替换无法在华为上使用。甚至同一厂商不同机型都不同 |
| **是否需要修改 vendor 分区** | **是** |
| **是否推荐** | **强烈不推荐**。这是 OEM 厂商才能走的路线。第三方开发者没有足够的硬件文档和测试资源。变砖风险不可接受。如果需要 HAL 层处理，应通过自定义 AudioEffect（路线二）间接实现，而非替换 HAL |

### 8.5 路线五：OEM / 系统签名 App

```
第三方 App → AudioTrack → AudioFlinger → [OEM 定制全局 AudioEffect / Framework 扩展] → HAL → Speaker
                                        ↑
                              系统签名 App 控制（视频工具箱）
```

| 维度 | 分析 |
|---|---|
| **能做到什么** | 作为系统特权 App，可以：创建全局 AudioEffect（session 0）；调用隐藏 API（per-app volume `IPlayer.setVolume()`）；修改音频路由；访问 `@SystemApi`；作为 privileged app 获得 `MODIFY_AUDIO_ROUTING` 等权限 |
| **不能做到什么** | 仍然受 OEM framework 定制的限制；无法处理 OEM 未开放的音频路径 |
| **所需权限** | **平台签名（platform signature）**——必须用 OEM 的平台密钥签名；或作为 privileged app 安装在 `/system/priv-app/`；需要 OEM 合作或自行编译 ROM |
| **普通开发者能否走这条路** | **不能**。平台密钥由 OEM 掌握，第三方开发者无法获取。除非：①与 OEM 合作预装；②使用自定义 ROM（如 LineageOS）并自行签名；③Root 后将 App 安装为 privileged app（但没有平台签名，部分 @SystemApi 仍无法调用） |
| **国产厂商如何实现** | 视频工具箱 App 是系统预装的特权 App，调用 OEM 在 framework 层定制的音频处理扩展。BGM 降低/人声分离等功能可能在 framework 或 vendor DSP 中实现，由工具箱 App 控制开关和参数 |
| **风险** | 低（在 OEM 环境内）。但对第三方开发者来说，这条路不可行 |
| **是否推荐** | **仅作为目标参考**，了解 OEM 如何实现。第三方开发者无法走这条路 |

### 8.6 路线综合对比

| 评估维度 | 普通 App | Root+Effect | AudioFlinger | HAL/Vendor | OEM/System |
|---|---|---|---|---|---|
| **捕获第三方音频** | 部分 | ✅ | ✅ | ✅ | ✅ |
| **替换原始输出（无双声）** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **BGM 分离处理** | ✅（DSP） | ✅（DSP） | ✅ | ✅ | ✅ |
| **针对特定 App** | ⚠️（UID 过滤但混合流） | ✅（per-session effect） | ✅ | ⚠️（HAL 层难区分） | ✅ |
| **实现难度** | 低 | 中高 | 极高 | 极高 | 不可行（第三方） |
| **系统修改风险** | 无 | 中 | 极高 | 极高 | 无（OEM 内） |
| **Android 14+ 兼容性** | ✅ | ⚠️（AIDL 适配） | ⚠️ | ⚠️ | ✅（OEM 适配） |
| **Android 17 后台限制** | ⚠️（播放受限） | ✅（effect 在系统进程） | ✅ | ✅ | ✅ |
| **第三方开发者可及性** | ✅ | ✅（需 Root 设备） | ⚠️（需 Root+高风险） | ❌ | ❌ |
| **推荐度** | ⭐⭐（验证用） | ⭐⭐⭐⭐（最终方案） | ⭐（不推荐） | ⭐（不推荐） | ⭐（仅参考） |

---

## 9. 推荐架构与下一步路线

### 9.1 推荐技术路线

基于以上研究，推荐分阶段推进：

#### 第一阶段（当前）：普通 App 验证
- 使用 AudioPlaybackCapture + AudioRecord 验证第三方视频 App 的可捕获性。
- 建立测试矩阵（抖音/B站/腾讯视频/YouTube/Chrome/本地播放器 × Emulator/非Root/Root）。
- 验证 PCM 参数、延迟、CPU 占用、稳定性。
- 接入 BGM DSP 算法，验证实时处理能力。
- **目的**：确认哪些 App 可以被捕获，DSP 算法在 Android 上的性能表现。

#### 第二阶段：Root + AudioEffect 方案预研
- 在 Root 设备上进行**只读探测**：`dumpsys media.audio_flinger`、`dumpsys media.audio_policy`，了解当前设备的音频线程、effect 配置、HAL 类型。
- 研究 ViPER4Android / JamesDSP 的开源实现，理解自定义 AudioEffect 的注册和控制流程。
- 确认目标设备的 Audio HAL 类型（AIDL vs HIDL），评估兼容性。
- **暂不修改系统文件**，仅做架构分析和方案设计。

#### 第三阶段：Root + AudioEffect 实现（需可靠恢复方案后）
- 编写自定义 AudioEffect 原生库（.so），接入 BGM DSP。
- 以 Magisk 模块方式（systemless）注册 effect，降低 bootloop 风险。
- 编写控制 App，通过标准 AudioEffect API 控制全局 effect 的参数（BGM 降低比例）。
- 充分测试稳定性，确保 effect 库崩溃不会导致系统音频不可用。
- 验证 DirectOutput/Offload/MMAP 路径的视频 App 是否被 effect 覆盖。

### 9.2 不建议采用的方案

1. **AudioFlinger 替换/Hook**：风险极高，bootloop 风险不可接受，且 AudioEffect 路线已能实现相同目标。
2. **Audio HAL / Vendor 替换**：OEM 绑定程度极高，变砖风险大，第三方开发者无硬件文档。
3. **普通 App + AudioTrack 重新播放作为最终方案**：双声叠加问题无法解决，用户体验不可接受。
4. **AudioFocus 长期 ducking 作为替代方案**：降低幅度不可控，影响所有媒体 App，不符合系统设计规范。

### 9.3 Root 设备安全注意事项

1. **当前阶段严格只读**：不修改 system/vendor 分区，不替换任何系统库，不刷入未知模块。
2. **Magisk systemless 优先**：未来修改系统配置时，使用 Magisk 模块的 systemless 方式，不直接修改实际分区。卸载模块即可恢复。
3. **备份优先**：在进行任何系统修改前，确保有可靠的恢复方案（如 TWRP 备份、原厂镜像、Magisk 卸载包）。
4. **audioserver 崩溃处理**：如果自定义 effect 导致 audioserver 崩溃循环，通过 ADB 或安全模式卸载模块。
5. **不在无恢复方案的设备上做高风险实验**：设备 B 没有可靠原厂镜像，禁止作为高风险实验机。

---

## 10. 参考资料

### 官方文档

1. [Android Developers - 录制视频和音频播放内容 (AudioPlaybackCapture)](https://developer.android.com/media/platform/av-capture?hl=zh-cn)
2. [Android Developers - AudioPlaybackCaptureConfiguration API 参考](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration)
3. [Android Developers - MediaProjection API 参考](https://developer.android.com/reference/android/media/projection/MediaProjection)
4. [Android Developers - AudioAttributes.Builder.setAllowedCapturePolicy()](https://developer.android.com/reference/android/media/AudioAttributes.Builder#setAllowedCapturePolicy(int))
5. [Android Developers - 管理音频焦点 (AudioFocus)](https://developer.android.com/media/optimize/audio-focus?hl=zh-cn)
6. [Android Developers - 共享音频输入 (Android 11)](https://developer.android.com/media/platform/sharing-audio-input?hl=zh-cn)
7. [Android Developers - 必须提供前景服务类型 (Android 14)](https://developer.android.com/about/versions/14/changes/fgs-types-required)
8. [Android Developers - 前景服务类型](https://developer.android.com/develop/background-work/services/fg-service-types)
9. [Android Developers - Android 15 行为变更：所有应用](https://developer.android.com/about/versions/15/behavior-changes-all?hl=zh-cn)
10. [Android Developers - Android 17 后台音频强化 (Background Audio Hardening)](https://developer.android.com/about/versions/17/changes/bg-audio)
11. [AOSP - Audio HAL 实现](https://source.android.com/docs/core/audio/implement)
12. [AOSP - Audio Effects (音效)](https://source.android.com/docs/core/audio/audio-effects?hl=zh-cn)
13. [AOSP - AIDL and HIDL Audio HAL comparison](https://source.android.com/docs/core/audio/aidl-hidl-comparison)
14. [AOSP - 组合音频设备路由](https://source.android.com/docs/core/audio/combined-audio-routing)
15. [AOSP - 低延迟音频设计](https://source.android.com/docs/core/audio/latency/design)
16. [AOSP - Android 17 发布说明](https://source.android.com/docs/whatsnew/android-17-release)

### 开源项目与社区资源

17. [ViPER4Android (GitHub) - Root 全局 AudioEffect 实现参考](https://github.com/likelikeslike/ViPER4Android)
18. [JamesDSPManager - 跨平台 Audio Effect DSP](https://james34602.github.io/JamesDSPManager/)
19. [RootlessJamesDSP - 非 Root 全局音频处理（Shizuku/ADB）](https://voice.thegeckoproject.org/en/packages/me.timschneeberger.rootlessjamesdsp/)
20. [AudioFreedom - Root 全局音频模块（AIDL 适配）](https://github.com/unicastbg/AudioFreedom)
21. [GenericJam/mob_audio_capture - AudioPlaybackCapture 技术分析](https://github.com/GenericJam/mob_audio_capture)
22. [farizanjum/mixer-1 - MIUI per-app volume 实现分析（IPlayer.setVolume 隐藏 API）](https://github.com/farizanjum/mixer-1)
23. [scrcpy issue #6218 - Android 16 高采样率音频捕获不匹配](https://github.com/Genymobile/scrcpy/issues/6218)
24. [XDA Forums - ViPER4Android Android 15 兼容性讨论](https://xdaforums.com/t/anyone-get-viper4android-working-on-their-13-with-android-15.4714926/)

### 技术分析文章

25. [掘金 - AudioFlinger 笔记（架构分析）](https://juejin.cn/post/7624851801591300131)
26. [掘金 - AudioPolicyService 笔记](https://juejin.cn/post/7624449484358647834)
27. [CSDN - 理解 Audio 音频系统三之 AudioFlinger Threads](https://blog.csdn.net/Ciellee/article/details/102602201)
28. [CSDN - Android 12 dump 系统音频流各阶段数据](https://blog.csdn.net/zhangboyu1986/article/details/149198214)
29. [CSDN - Android 录屏内部音频录制完全指南](https://blog.csdn.net/liushuidehao/article/details/163896072)
30. [Esper - Why Many Android Equalizer Apps Don't Work with All Media Players](https://www.esper.io/blog/android-equalizer-apps-inconsistent)
31. [Android Authority - Android 17 cracks down on background audio](https://www.androidauthority.com/android-17-background-audio-hardening-3669109/)
32. [Zebra DevCon 2025 - Android 15 行为变更（含 FGS 限制）](https://www.zebra.com/content/dam/zebra_dam/en/presentation/customer-facing/cfw2-presentation-customer-facing-devcon2025-en-us.pdf)
33. [CSDN - RootlessJamesDSP 无需 Root 全局音频 DSP 原理解析](https://blog.csdn.net/weixin_42547335/article/details/160607568)

---

> **报告完成日期**：2026-09-02
>
> **免责声明**：本报告基于公开文档、开源项目和社区资料整理。标注"需实验验证"的结论必须通过真实设备测试确认。Android 版本更新频繁，部分结论可能随新版本发布而变化，建议持续关注官方行为变更文档。
