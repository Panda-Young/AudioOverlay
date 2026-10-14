# Android 系统级视频音频工具箱 —— 自动化技术验证任务书

## 1. 项目目标

我要研究并实现一种类似国产 Android 手机“视频工具箱 / 视频助手”的功能。

最终用户体验目标：

1. 用户打开第三方视频平台 App，例如抖音、B站、腾讯视频、YouTube 等。
2. 视频开始播放后，系统自动在屏幕边缘出现一个贴边的小悬浮按钮/悬浮窗。
3. 点击悬浮按钮后展开视频工具箱。
4. 工具箱中至少包含一个“BGM 音量”控制项。
5. 用户可以实时调整 BGM 音量，例如：
   - 100%：原始效果
   - 50%：BGM 降低
   - 20%：BGM 大幅降低
   - 0%：尽可能消除 BGM
6. 人声/对白等其他内容尽可能保持不变。
7. 音频处理必须实时进行，不能要求先下载视频或离线处理。
8. 我已经拥有成熟的 BGM 实时处理算法库，核心 DSP 算法无需重新研发。
9. 最终希望做到的理想状态是：
   
   第三方视频 App → Android 系统音频链路 → BGM 实时处理 → 最终扬声器/耳机/蓝牙输出

---

# 2. 当前硬件与开发环境

当前拥有：

### PC

- Windows 11 PC
- 可安装 Android Studio
- 可安装 Android SDK / NDK / CMake / JDK
- 可使用 ADB

### Android 设备

#### 设备 A：普通非 Root Android 手机

用途：

- 验证 Android 官方 API
- 验证第三方 App 的音频捕获能力
- 验证 Overlay
- 验证 MediaProjection / AudioPlaybackCapture
- 验证普通 Android App 能做到的边界

#### 设备 B：Root Android 手机

特殊限制：

- 已 Root
- 没有对应的可靠原厂 img 镜像
- 如果修改系统关键组件导致 bootloop / 挂死，可能存在变砖风险
- 因此禁止把该设备作为高风险刷写/系统修改实验机
- 当前阶段优先进行只读、低风险实验
- 暂时不要修改 AudioFlinger、Audio HAL、vendor 音频库或 system/framework 关键文件
- 暂时不要刷入未知音频模块
- 在没有可靠恢复方案之前，不进行不可逆系统改动

#### Android Studio Emulator

允许建立 Android Studio 虚拟机。

用途：

- 快速验证公开 Android API
- 验证 Kotlin/Java 代码
- 验证 Overlay
- 验证 MediaProjection
- 验证 AudioPlaybackCapture
- 验证 JNI
- 验证 C++ DSP
- 调试、Logcat、Profiler、Native Debugger
- 做高频率的安全实验

注意：

Emulator 不是最终真实硬件音频环境，尤其不能代替真实手机验证 Audio HAL、厂商 DSP、蓝牙/扬声器实际链路。

---

# 3. 核心技术问题

整个项目必须分成两个不同的问题研究。

## 问题 A：视频工具箱 UI / 自动出现

要求：

- 能识别当前是否进入指定的视频 App
- 视频播放时自动出现贴边小悬浮按钮
- 点击后展开控制面板
- 可以通过 Overlay 显示在第三方 App 上方
- 支持启动/停止工具箱
- 支持最基本的 BGM 音量滑杆

可能涉及：

- `SYSTEM_ALERT_WINDOW`
- `TYPE_APPLICATION_OVERLAY`
- `AccessibilityService`
- Usage / Activity / Window 相关能力
- MediaSession 等

需要验证普通非 Root Android 是否可以实现。

---

# 4. 核心技术问题 B：第三方 App 音频实时处理

需要重点验证 Android 的以下能力：

```text
第三方视频 App
    ↓
AudioPlaybackCapture
    ↓
AudioRecord
    ↓
PCM
    ↓
JNI
    ↓
已有 C/C++ BGM DSP
```

优先研究：

- `AudioPlaybackCaptureConfiguration`
- `AudioRecord`
- `MediaProjection`
- Foreground Service
- MediaProjection service type
- Audio capture policy
- Audio usage
- 第三方 App 是否允许 playback capture

必须验证：

1. Android 版本要求
2. MediaProjection 权限流程
3. AudioPlaybackCapture 实际能否工作
4. 不同第三方视频 App 是否允许被捕获
5. 捕获出来的 PCM：
   - sample rate
   - channel count
   - sample format
   - buffer size
6. 延迟
7. 丢帧
8. CPU 占用
9. 内存占用
10. 稳定性
11. 后台运行能力

---

# 5. 特别重要：不要默认“捕获后重新播放”就是最终方案

这是本项目的关键技术判断。

普通 App 很可能可以做到：

```text
第三方视频 App
       ↓
AudioPlaybackCapture
       ↓
PCM
       ↓
BGM DSP
       ↓
处理后的 PCM
```

但如果第三方 App 原始音频仍然直接输出：

```text
第三方 App ─────────→ Speaker
       │
       ↓
    Capture
       ↓
     DSP
       ↓
   AudioTrack
       ↓
    Speaker
```

那么用户可能听到：

```text
原始声音 + 处理声音
```

导致双声、回声、延迟等问题。

因此必须明确验证：

> Android 官方公开 API 是否能够实现“捕获 → 修改 → 替换原始第三方音频输出”，而不仅仅是“捕获 + 重新播放”。

这是本项目最关键的技术边界。

---

# 6. 最终目标与系统级方案

如果普通 Android API 无法完成“替换第三方 App 原始音频”，则研究系统级方案。

目标架构：

```text
第三方视频 App
       ↓
AudioTrack
       ↓
AudioFlinger
       ↓
Audio Effect / 其他系统级处理点
       ↓
Mixer
       ↓
Audio HAL
       ↓
Speaker / Bluetooth
```

重点研究：

- AudioFlinger
- AudioTrack
- AudioEffect
- Audio Policy
- Audio HAL
- Vendor Audio HAL
- Vendor DSP
- 系统级 Audio Effect
- Android 不同版本之间的架构变化

目标是判断：

1. 是否存在适合插入自定义 DSP 的系统级音频处理位置
2. 是否能够针对特定 App / Audio Session 处理
3. 是否能够针对 Media Audio 处理
4. 是否能够修改最终系统输出
5. 是否可以实现类似国产手机视频工具箱的效果
6. Root 权限是否足够
7. 是否还需要 Framework / System 权限
8. 是否必须修改系统镜像或 vendor
9. 是否必须与特定 OEM ROM 深度绑定

---

# 7. Root 设备的实验原则

Root 设备 B 存在变砖风险，因此：

## 允许

- ADB
- `dumpsys`
- 读取系统属性
- 读取音频相关信息
- Logcat
- 查看 AudioFlinger / AudioPolicy 状态
- 查看系统目录和音频模块结构
- 只读分析
- 尽量不改变系统状态的实验

重点可以关注：

```bash
adb shell dumpsys media.audio_flinger
adb shell dumpsys media.audio_policy
adb shell dumpsys media_session
```

以及：

- Android version
- ABI
- SoC
- vendor
- audio HAL
- audio policy
- audio effect
- DSP 相关模块

## 禁止/暂缓

未经可靠恢复方案前：

- 不刷 system
- 不刷 vendor
- 不替换 AudioFlinger
- 不替换 Audio HAL
- 不替换 vendor audio libraries
- 不修改关键 framework native library
- 不刷未知 Magisk 音频模块
- 不做高风险 bootloop 实验

---

# 8. 建议的软件工程结构

算法与 Android 音频入口必须彻底解耦。

建议结构：

```text
Android App
│
├── UI
│   └── VideoToolbox UI
│
├── Overlay
│
├── App Detection
│
├── Audio Capture / System Audio Backend
│
├── JNI
│
└── Native
    └── BgmProcessor
         └── 已有 BGM DSP 算法库
```

BGM 算法层尽量只依赖：

```cpp
sampleRate
channels
numFrames
input PCM
output PCM
```

不要让 BGM DSP 依赖 Android Framework。

这样未来可以同时支持：

```text
AudioPlaybackCapture
AudioRecord
AudioEffect
AudioFlinger
Audio HAL
```

而算法代码保持不变。

---

# 9. 第一阶段：绝对优先完成的实验

建立一个最小 Android Studio 工程：

项目名建议：

`AudioRouteLab`

第一版不要做漂亮 UI，不做复杂视频工具箱。

只验证：

```text
第三方视频 App
      ↓
AudioPlaybackCapture
      ↓
AudioRecord
      ↓
PCM
      ↓
JNI
      ↓
C++ BGM DSP
      ↓
实时处理
```

提供最基本界面：

```text
AudioRouteLab

[Start Capture]

Target App:
XXXX

Capture:
YES / NO

Sample Rate:
48000

Channels:
2

RMS:
XX dB

Peak:
XX dB

DSP:
ON / OFF

BGM Gain:
0~100%
```

第一阶段不强制输出处理后的音频。

重点只收集实验数据。

---

# 10. 必须建立测试矩阵

至少测试：

- 抖音
- B站
- 腾讯视频
- YouTube
- Chrome / 浏览器网页视频
- 一个普通本地视频播放器

在：

- Android Emulator
- 非 Root 真机
- Root 真机（仅低风险测试）

分别测试。

记录：

| 项目 | 结果 |
|---|---|
| 是否能捕获 | YES/NO |
| 是否要求 MediaProjection | YES/NO |
| 是否需要用户确认 | YES/NO |
| sample rate | 数值 |
| channels | 数值 |
| PCM format | 数值 |
| latency | ms |
| CPU | % |
| 是否稳定 | YES/NO |
| 后台是否可持续 | YES/NO |
| App 切换后是否继续 | YES/NO |
| App 是否禁止 capture | YES/NO |
| 是否能检测目标 App | YES/NO |

---

# 11. 第二阶段

完成：

```text
AudioPlaybackCapture
       ↓
AudioRecord
       ↓
JNI
       ↓
BGM DSP
       ↓
实时处理
```

验证：

- BGM 参数实时改变
- 无明显音频断裂
- 无明显爆音
- latency 可接受
- CPU 可接受
- 48kHz / stereo 等常见格式
- DSP 在 Android ARM64 上稳定运行

---

# 12. 第三阶段

加入 Overlay：

```text
第三方视频 App
       +
贴边悬浮按钮
       ↓
点击
       ↓
展开 BGM 控制面板
```

验证：

- Overlay 权限
- 自动启动
- 前台 App 检测
- 视频 App 切换
- 横竖屏
- 全屏视频
- 后台 Service
- 前台服务
- Overlay 生命周期

---

# 13. 第四阶段

验证“真正输出处理后的音频”。

必须研究以下两条路线：

## 路线 A：公开 API

```text
Capture
 ↓
DSP
 ↓
Output
```

确认是否存在原始声音叠加的问题。

最终明确：

> 是否能仅依靠普通 Android App，实现第三方 App 的实时音频替换。

## 路线 B：Root / 系统级

```text
Third-party App
 ↓
AudioTrack
 ↓
AudioFlinger
 ↓
AudioEffect / 系统级插入点
 ↓
DSP
 ↓
Output
```

只在完成前面的公开 API 验证后，再开始深入。

---

# 14. 技术研究时的关键问题

AI agent 必须特别回答：

### Android 官方 API

1. AudioPlaybackCapture 到底能捕获什么？
2. 哪些 AudioUsage 可以捕获？
3. 第三方 App 如何禁止 capture？
4. MediaProjection 在当前 Android 版本上的限制？
5. Android 14+ / 15+ / 16+ 是否存在额外限制？
6. 后台运行的约束？
7. 是否可以捕获第三方视频 App？
8. 是否可以将捕获的音频重新送入系统输出？
9. 为什么/为什么不能替换原始音频？

### 系统级

1. AudioFlinger 当前架构
2. AudioTrack 到 Mixer 的路径
3. AudioEffect 插入点
4. Output Mix / Session 的关系
5. 全局输出 effect 是否仍然适合当前 Android 版本
6. Audio HAL 如何参与
7. Vendor DSP 是否参与
8. OEM ROM 是否做了特殊修改
9. Root 是否足够
10. 是否必须修改 Framework/HAL/vendor

---

# 15. 研究 Android 版本要求

以当前 Android 最新公开版本为背景，但实际测试以实验设备版本为准。

不能只依赖旧博客/旧教程。

涉及 Android Framework、MediaProjection、AudioPlaybackCapture、Foreground Service、AudioEffect 等技术时：

- 优先使用 Android Developers 官方文档
- 对架构变化进行版本对比
- 不确定的技术结论必须通过真实设备实验验证
- 涉及当前版本行为时必须查最新资料

---

# 16. 最终验收标准

第一阶段成功：

```text
Android Studio Emulator / 真机
可以捕获第三方 App 的播放 PCM
```

第二阶段成功：

```text
第三方 App PCM
→ JNI
→ 已有 BGM DSP
→ 实时处理
```

第三阶段成功：

```text
第三方视频 App
→ 自动出现贴边悬浮工具箱
→ 可修改 BGM 参数
```

第四阶段目标：

```text
第三方视频 App
→ 系统级音频链路
→ BGM DSP
→ 最终输出
```

理想最终体验：

```text
                    ┌───────────────┐
                    │ Video Toolbox │
                    │               │
                    │ BGM ━━━●━━━━ │
                    │ 35%           │
                    └───────┬───────┘
                            │
第三方视频 App → System Audio → DSP → Speaker/BT
```

---

# 17. Agent 工作原则

1. 不要一开始就修改 Root 手机。
2. 优先 Emulator。
3. 然后普通非 Root 真机。
4. 最后才使用 Root 真机做系统级验证。
5. Root 真机默认只读。
6. 所有高风险修改必须先证明技术路线确实需要。
7. 不要把“音频捕获”和“音频替换”混为一谈。
8. 明确区分：
   - Capture
   - Process
   - Playback
   - System-level interception
   - Final output replacement
9. 每一个关键结论都需要给出：
   - Android 版本
   - API
   - 官方文档依据或实验依据
10. 不允许因为某个旧教程成功，就直接认为当前 Android 一定可用。
11. 任何可能导致 Root 设备 bootloop/变砖的操作必须标记为 HIGH RISK，并默认禁止执行。
12. 优先通过实验确定能力边界，而不是先写大量代码。

---

# 18. 第一轮任务

请从零开始执行以下工作：

### Task 1
确认开发环境需要的软件及版本。

### Task 2
创建 `AudioRouteLab` Android Studio 最小工程。

### Task 3
实现 AudioPlaybackCapture + AudioRecord。

### Task 4
输出实时 PCM 参数和音频电平。

### Task 5
实现 JNI → C++。

### Task 6
接入预留的 BGM DSP 接口。

### Task 7
验证 Emulator。

### Task 8
验证普通非 Root Android 真机。

### Task 9
建立第三方视频 App 测试矩阵。

### Task 10
总结公开 Android API 的能力边界。

### Task 11
在不修改系统文件的前提下，对 Root 真机执行只读音频架构探测。

### Task 12
根据上述结果判断下一步应该进入：

- 普通 Android App 路线
- Root + AudioEffect 路线
- AudioFlinger 路线
- Audio HAL 路线
- OEM/System App 路线

不要跳过前面的验证直接做高风险系统修改。

---

# 最终要求

请把整个任务当成一个**技术可行性验证项目**，而不仅仅是写一个 Demo。

最终输出应明确回答：

> 在当前 Android 技术体系下，使用普通 App、非 Root、Root、Framework/HAL 各自能做到什么程度？

尤其需要最终明确：

> **“第三方视频 App 的 BGM 实时降低，并让用户最终只听到处理后的声音”这一目标，最低需要什么权限/系统层级才能可靠实现？**

同时给出：

- 推荐架构
- 实验结果
- Android 版本差异
- 风险点
- 下一阶段路线
- 不建议采用的方案
- Root 设备安全注意事项