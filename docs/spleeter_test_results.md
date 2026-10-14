# Spleeter ONNX 神经分离真机测试结果

## 测试环境
- 设备: Redmi 23113RKC6C (vermeer), Android 16 / SDK 36
- 芯片: 骁龙 8 Gen 3 (kalama)
- 模型: sherpa-onnx Spleeter 2-stems fp16 (vocals + accompaniment)
- ONNX Runtime: 1.17.0 (Maven Central, CPU EP)
- 捕获源: 抖音 (AudioPlaybackCapture, 48000Hz/stereo/PCM16)
- 回放: AudioTrack (USAGE_MEDIA, 48000Hz/stereo/PCM16, MODE_STREAM)

## 模型格式 (已验证)
- 输入: `x [2, num_splits, 512, 1024]` float32
  - dim0: real/imag (复数频谱)
  - dim1: num_splits (每块1024帧)
  - dim2: 512 频率bin (N_FFT/2, 排除Nyquist)
  - dim3: 1024 时间帧/块
- 输出: `y [2, num_splits, 512, 1024]` float32 (masked复数频谱)
- 架构: U-Net + Sigmoid mask + Mul (输出=输入×mask)
- STFT参数: N_FFT=1024, hop=256, Hann窗, 75%重叠, COLA重构

## 处理流程
1. stereo PCM16 (48000Hz) → mono float32
2. 重采样 48000 → 44100
3. STFT → 复数频谱 [512, n_frames]
4. reshape → [2, num_splits, 512, 1024]
5. ONNX推理 (vocals + accompaniment双模型)
6. reshape回 → [512, total_frames]
7. iSTFT (Nyquist=0, Hermitian对称) → vocals/accompaniment波形
8. 混音: output = vocals×1.0 + accompaniment×bgmGain
9. 重采样 44100 → 48000
10. mono float32 → stereo PCM16
11. AudioTrack回放

## 多增益水平测试结果

| BGM增益 | 处理时间(2s音频) | RTF | 状态 | 说明 |
|---------|-----------------|-----|------|------|
| 0.0 | ~716ms | 0.36 | ✅ | 完全移除BGM，仅保留人声 |
| 0.1 | ~610ms | 0.31 | ✅ | BGM降低90% |
| 0.3 | ~708ms | 0.35 | ✅ | BGM降低70% |
| 0.5 | ~704ms | 0.35 | ✅ | BGM降低50% |
| 1.0 | ~702ms | 0.35 | ✅ | 无降低（基线，vocals+accompaniment完整混音） |

**关键发现**: 处理时间与BGM增益无关（~700ms/2s, RTF=0.35），因为增益只是神经分离后的简单乘法，推理才是瓶颈。

## AudioFlinger 验证
- AudioRecord session 38769: 活跃，从抖音捕获 (48000Hz/stereo)
- AudioTrack session 38777: 活跃，已写入65502帧，通过扬声器播放 (48000Hz/stereo/PCM16)
- 输出线程: AudioOut_D (主输出，扬声器)
- 整条链路: 抖音 → AudioPlaybackCapture → Spleeter分离 → BGM增益 → AudioTrack → 扬声器 ✅

## 稳定性
- 连续运行超过10分钟，无崩溃、无OOM、无推理错误
- 每次处理2秒chunk，协程异步处理(Dispatchers.Default)
- 内存: App占用~200MB (含ONNX模型~37MB + 运行库)
- nFrames=341 (2秒@44100Hz, hop=256 → 341帧)

## 与其他模式对比 (前序测试)

| 模式 | 原理 | 延迟 | BGM抑制效果 | 人声保留 | 所需权限 |
|------|------|------|------------|---------|---------|
| 0: 全局增益 | 整体音量缩放 | ~0ms | 差(人声也降) | 差 | 普通 |
| 1: 中置声道消除 | mid-side零延迟 | ~0ms | 中(仅中置BGM) | 中(人声在中置) | 普通 |
| 2: 自适应谱滤波 | per-bin自适应增益 | ~16ms | 中 | 中 | 普通 |
| 3: 神经分离(降级) | 全局增益(stub) | ~0ms | 差 | 差 | 普通 |
| **4: Spleeter ONNX** | **U-Net神经分离** | **~700ms/2s** | **优** | **优** | **普通** |

## 结论
1. **Spleeter神经分离在非Root真机上完全可行** — 普通App权限即可实现"降低BGM保留人声"
2. **RTF=0.35** — 骁龙8 Gen3上比实时快3倍，2秒音频仅需700ms
3. **全链路打通** — 从捕获到分离到增益到回放，无Root、无系统API、无特殊权限
4. **延迟可接受** — 2秒chunk处理+缓冲，总延迟约2-3秒（用户明确表示暂不考虑延迟）
5. **多增益水平稳定** — 0.0~1.0全范围无错误

## 后续优化方向
1. **QNN HTP加速** — 使用Qualcomm QNN SDK的HTP EP，预计可将推理时间降低50-70%
2. **4-stem模型** — vocals/drums/bass/other四轨分离，更精细控制
3. **流式处理** — 减小chunk大小(如0.5秒)，降低延迟
4. **模型量化** — INT8量化进一步减小模型体积和推理时间
5. **per-session静音** — 结合RootlessJamesDSP的DynamicsProcessing -200dB方案，实现真正的输出替换（避免双声叠加）
