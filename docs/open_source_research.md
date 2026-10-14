# 开源项目调研报告 — BGM 人声分离与系统级音频处理

**调研日期**: 2026-09-02
**调研范围**: 实时人声分离算法、系统级音频处理框架、免 Root 输出替换方案
**项目性质**: 个人学习调研（非商用），可借鉴架构与思路

---

## 一、实时人声分离（BGM/Vocal Separation）项目

### 1. Transpose — 唯一真正实时流式人声消除

| 项目 | 详情 |
|---|---|
| **仓库** | github.com/zhuyuanxiang/transpose (推测) |
| **核心模型** | MDX-Net U-Net（Demucs 架构变体） |
| **推理引擎** | ONNX Runtime + XNNPACK（CPU 加速） |
| **STFT 参数** | N_FFT=6144, hop=1024（~128ms 帧长） |
| **端到端延迟** | ~708 ms |
| **架构亮点** | 双环形缓冲 + 状态机 + 自适应 prebuffer，真正的流式处理（非离线批处理） |
| **许可证** | GPL-3.0 |
| **借鉴价值** | ⭐⭐⭐⭐⭐ 流式架构设计最值得参考（环形缓冲、状态机、prebuffer 自适应） |
| **限制** | GPL-3.0 传染性，需 clean room 重新实现；708ms 延迟对实时对话场景不可接受 |

**架构分析**:
Transpose 的核心创新不在于模型（MDX-Net 是公开的），而在于**流式推理管线**：
- 输入环形缓冲：持续写入 PCM 数据，达到 hop 大小触发一次推理
- 输出环形缓冲：推理结果写入，按 hop 步进输出
- 状态机：IDLE → BUFFERING → PROCESSING → FLUSHING，处理启动/停止时的边界
- 自适应 prebuffer：根据推理耗时动态调整预缓冲大小，避免 underrun

### 2. sherpa-onnx Spleeter 2-stem — 实时性最好的神经方案

| 项目 | 详情 |
|---|---|
| **仓库** | github.com/k2-fsa/sherpa-onnx |
| **核心模型** | Spleeter 2-stem（人声+伴奏） |
| **推理引擎** | ONNX Runtime（支持 XNNPACK / NNAPI / CoreML） |
| **STFT 参数** | N_FFT=4096（比 MDX 轻 33%） |
| **RTF（实时因子）** | 0.079（桌面 1 线程）— 即处理速度是实时的 12.7 倍 |
| **许可证** | Apache-2.0（最宽松） |
| **借鉴价值** | ⭐⭐⭐⭐ 模型轻量化 + Apache-2.0 可直接使用 |
| **限制** | 只有离线 API（`offline_stream`），需自写流式封装；移动端 RTF 需实测（桌面 0.079 ≠ 移动端） |

**关键数据**:
- 桌面端（Intel CPU, 1 thread）: RTF=0.079，即 10 秒音频只需 0.79 秒处理
- 移动端（ARM64, XNNPACK NEON）: 预估 RTF=0.3~0.5（需实测），即 10 秒需 3~5 秒
- 即使移动端 RTF=0.5，加上 STFT 帧延迟（4096/44100=93ms），端到端延迟仍 > 200ms

### 3. Voela — DTTNet 引擎 + WOLA 重构

| 项目 | 详情 |
|---|---|
| **核心模型** | DTTNet（Deep Time-domain Transformer Network） |
| **推理引擎** | ONNX Runtime + KissFFT（自包含 FFT，无外部依赖） |
| **重构方法** | WOLA（Weighted Overlap-Add）最小二乘重构 — 比标准 OLA 音质更好 |
| **许可证** | 专有许可（非开源） |
| **借鉴价值** | ⭐⭐⭐ WOLA 重构方法和 KissFFT 自包含设计可参考 |
| **限制** | 专有许可，不可直接使用代码 |

### 4. Vocal-Remover / Spleeter-4stems-onnx — 离线工具

| 项目 | 详情 |
|---|---|
| **Vocal-Remover** | PyTorch 模型，ONNX 导出，离线批处理，非实时 |
| **Spleeter-4stems** | Deezer 官方，4  stems（人声/鼓/贝斯/其他），MIT 许可，离线 |
| **共性** | 均为离线批处理设计，无流式管线，延迟不可控 |

---

## 二、系统级音频处理框架

### 5. RootlessJamesDSP — 免 Root 感知级输出替换（关键发现）

| 项目 | 详情 |
|---|---|
| **仓库** | github.com/TheStaticBug/RootlessJamesDSP |
| **核心架构** | 捕获 → DSP → AudioTrack 回放 + per-session 静音 |
| **免 Root 原理** | 通过 `DynamicsProcessing` API 将目标 App 的音频会话（session）增益设为 -200dB（静音），同时用 AudioTrack 播放处理后的音频 |
| **权限要求** | DUMP 权限（通过 Shizuku 或 ADB 一次性授权），**无需 Root** |
| **效果** | 感知级输出替换 — 用户听到的是处理后的声音，原始声音被静音 |
| **许可证** | GPL-3.0 |
| **借鉴价值** | ⭐⭐⭐⭐⭐ **架构创新**：用公开 API 组合实现免 Root 输出替换 |
| **限制** | 真正的 HAL/output mix 级替换仍需 Root；per-session 静音依赖 App 使用独立音频会话 |

**架构详解**:
```
┌─────────────────────────────────────────────────────┐
│              RootlessJamesDSP 架构                    │
├─────────────────────────────────────────────────────┤
│                                                     │
│  第三方App ──→ AudioTrack (session=X)              │
│                     │                               │
│                     ▼                               │
│              AudioFlinger mix                       │
│                     │                               │
│         ┌───────────┴───────────┐                  │
│         ▼                       ▼                  │
│   扬声器(原始声音)        AudioPlaybackCapture     │
│                              │                      │
│                     DynamicsProcessing              │
│                     (session X → -200dB)            │
│                              │                      │
│                              ▼                      │
│                         JNI/C++ DSP                  │
│                              │                      │
│                              ▼                      │
│                    AudioTrack 回放(处理后)           │
│                              │                      │
│                              ▼                      │
│                          扬声器                       │
│                                                     │
│  结果：用户只听到处理后的声音（原始被-200dB静音）    │
└─────────────────────────────────────────────────────┘
```

**关键修正**: 第一轮结论"最低需要 Root"需修正为：
- **感知级输出替换**：可免 Root（RootlessJamesDSP 方案，DUMP 权限）
- **HAL/output mix 级真正替换**：仍需 Root（全局 AudioEffect 挂载 session 0）

### 6. JamesDSP — Root 全局音频效果器

| 项目 | 详情 |
|---|---|
| **仓库** | github.com/james34602/JamesDSPManager |
| **核心架构** | Root + AudioEffect 挂载到全局输出 mix（session 0） |
| **功能** | 均衡器、压缩器、卷积混响、限幅器、交叉馈送等 |
| **许可证** | GPL-3.0 |
| **借鉴价值** | ⭐⭐⭐⭐ 全局 AudioEffect 挂载方式可参考 |
| **限制** | 需 Root；跨 OEM 兼容性需验证（不同厂商 AudioFlinger 实现有差异） |

### 7. ViPER4Android — 最知名的 Root 音频效果器

| 项目 | 详情 |
|---|---|
| **核心架构** | Root + AudioEffect + SELinux 权限修改 |
| **功能** | 均衡器、环绕声、低音增强、清晰度、混响等 |
| **许可证** | 专有（部分开源） |
| **借鉴价值** | ⭐⭐⭐ 证明全局 AudioEffect 方案在量产设备上可行 |
| **限制** | 需 Root + 刷入 Magisk 模块；有 bootloop 风险（用户安全红线禁止） |

---

## 三、技术路线总结与推荐

### 移动端实时人声分离可行性判断

| 方案 | 延迟 | CPU | 可行性 | 推荐度 |
|---|---|---|---|---|
| **模式1 中置声道消除** | ~1ms | 低 | ✅ 完全可行 | ⭐⭐⭐ 零延迟首选 |
| **模式2 自适应谱滤波** | ~28ms | 55% | ⚠️ 旗舰机勉强可行 | ⭐⭐⭐⭐ 效果最佳 |
| **Spleeter ONNX (sherpa)** | >200ms | 高 | ⚠️ 非实时，可做准实时 | ⭐⭐ 延迟不可接受 |
| **MDX-Net (Transpose)** | ~708ms | 很高 | ❌ 不可行 | ⭐ 仅参考架构 |
| **Hexagon DSP offload** | 未知 | 极低 | ❓ 需 Root+vendor 开发 | ⭐⭐⭐ 长期方向 |

### 输出替换方案选择

| 方案 | Root? | 效果 | 风险 | 推荐度 |
|---|---|---|---|---|
| **RootlessJamesDSP 式** | 免 | 感知级替换 | 低（Shizuku/ADB） | ⭐⭐⭐⭐ **当前最优** |
| **Root + 全局 AudioEffect** | 需 | HAL 级真正替换 | 中（兼容性） | ⭐⭐⭐ 长期方向 |
| **AudioFlinger Hook** | 需 | 完全控制 | 高（bootloop） | ⭐ 不推荐 |
| **Audio HAL 修改** | 需 | 最底层 | 极高（vendor） | ⭐ 不推荐 |

### 对本项目的具体建议

1. **短期（免 Root）**: 实现 RootlessJamesDSP 式架构 — AudioPlaybackCapture + C++ DSP + AudioTrack 回放 + DynamicsProcessing per-session 静音。这是当前唯一能让用户"只听到处理后声音"的免 Root 方案。
2. **DSP 算法**: 以模式2（自适应谱滤波）为主，模式1（中置声道）为零延迟备选；参考 Transpose 的流式缓冲架构优化模式2的延迟和稳定性。
3. **神经分离**: 暂不投入移动端实时推理；可做离线处理（非实时）或等待 Hexagon DSP offload 方案成熟。
4. **长期**: Root 设备上验证全局 AudioEffect 方案（JamesDSP 方式），实现真正的 HAL 级输出替换。

---

## 四、参考链接

- Transpose (MDX-Net 实时流式): GitHub 搜索 "transpose vocal remover realtime"
- sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx
- RootlessJamesDSP: https://github.com/TheStaticBug/RootlessJamesDSP
- JamesDSP: https://github.com/james34602/JamesDSPManager
- Spleeter (Deezer): https://github.com/deezer/spleeter
- Android AudioPlaybackCapture: https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration
- Android DynamicsProcessing: https://developer.android.com/reference/android/media/audiofx/DynamicsProcessing
