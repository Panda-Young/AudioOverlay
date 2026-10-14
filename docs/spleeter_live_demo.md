# 真机演示记录 — sherpa-onnx Spleeter 2-stems fp16 实时处理

**演示时间**: 2026-09-02 21:46~21:48
**设备**: Redmi 23113RKC6C (vermeer) · Android 16 (SDK 36) · 骁龙 8 Gen 3 (kalama) · 非Root
**被捕获应用**: 抖音 (com.ss.android.ugc.aweme) — 实时播放含人声解说+BGM的视频
**App**: AudioRouteLab v1.0 (com.example.audiooverlay)

## 一、处理链路（当前已在真机实时运行）

```
抖音 (人声+BGM) → AudioPlaybackCapture (48kHz·立体声·PCM16)
  → SpleeterProcessor (mode 4):
      stereo→mono → 重采样 48000→44100 → STFT(N_FFT=1024,hop=256,Hann)
      → U-Net 推理 ×2 (vocals模型 + accompaniment模型, fp16, ONNX Runtime CPU EP)
      → iSTFT 重建 → 混音: 人声×1.0 + 伴奏×0.1 (BGM降90%)
      → 重采样 44100→48000 → mono→stereo → PCM16
  → AudioTrack 回放 → 扬声器
```

## 二、实时处理日志（真机采集）

```
09-02 21:47:21 SpleeterProcessor: Processed 2.00s audio in 743ms, RTF=0.37, bgmGain=0.10, nFrames=341
09-02 21:47:23 SpleeterProcessor: Processed 2.00s audio in 740ms, RTF=0.37, bgmGain=0.10, nFrames=341
09-02 21:47:25 SpleeterProcessor: Processed 2.00s audio in 815ms, RTF=0.41, bgmGain=0.10, nFrames=341
09-02 21:47:28 SpleeterProcessor: Processed 2.00s audio in 925ms, RTF=0.46, bgmGain=0.10, nFrames=341
09-02 21:47:29 SpleeterProcessor: Processed 2.00s audio in 751ms, RTF=0.38, bgmGain=0.10, nFrames=341
09-02 21:48:07 SpleeterProcessor: Processed 2.00s audio in 746ms, RTF=0.37, bgmGain=0.10, nFrames=341
09-02 21:48:09 SpleeterProcessor: Processed 2.00s audio in 785ms, RTF=0.39, bgmGain=0.10, nFrames=341
09-02 21:48:11 SpleeterProcessor: Processed 2.00s audio in 754ms, RTF=0.38, bgmGain=0.10, nFrames=341
09-02 21:48:13 SpleeterProcessor: Processed 2.00s audio in 791ms, RTF=0.40, bgmGain=0.10, nFrames=341
```

## 三、AudioFlinger 硬件级验证（dumpsys media.audio_flinger）

| 项 | 值 | 说明 |
|---|---|---|
| AudioRecord session | 38809 | **Active** (A), 48000Hz/2ch/PCM16, 从 REMOTE_SUBMIX 捕获抖音 |
| AudioTrack session | 38817 | **Active** (A), 48000Hz/2ch/PCM16, 已写入 **67,274 帧** → 扬声器 |
| session 38817 effects | 2 个 (Registered) | AudioOverlay 挂载的效果链 |
| 输出线程 | AudioOut_D (主输出·扬声器) | 处理后音频正在播放 |

> 关键证据：AudioTrack track 5402 (pid 27264, session 38817) 状态为 `A`(Active)，
> Server FrmCnt=67,274 且持续增长 —— 证明 Spleeter 处理后的音频正在被扬声器播放。

## 四、性能指标（骁龙 8 Gen 3）

| 指标 | 数值 | 说明 |
|---|---|---|
| 处理时长 | ~750-790ms / 2.0s 音频 | 比实时快 ~2.6 倍 |
| **RTF (实时因子)** | **0.37~0.40** | <1.0 即可实时处理 |
| CPU 占用 | **21.8%** | 约1个中核负载（双模型 U-Net 推理） |
| 内存占用 | 573MB | 含 ONNX Runtime + 2×18.8MB fp16 模型 |
| nFrames | 341 | 2秒@44100Hz, hop=256 |
| 模型 | vocals.fp16 + accompaniment.fp16 | sherpa-onnx Spleeter 2-stems, U-Net 架构 |

## 五、演示结论

1. **Spleeter 神经分离在非Root真机实时运行成功** — 双模型 U-Net 推理、STFT/iSTFT 全链路稳定
2. **BGM 增益 0.10** 正在生效 — 混音公式 `人声×1.0 + 伴奏×0.1`，抖音的 BGM 被降低 90%，人声解说保留
3. **处理后音频已回放到扬声器** — AudioFlinger 确认 session 38817 Active 且持续写入
4. **性能足够实时** — RTF=0.37，CPU 仅 21.8%，骁龙 8 Gen 3 可轻松承载
5. **已知现象**：当前原始抖音声 + 处理后声同时存在（双声叠加），这是 AudioPlaybackCapture 旁路方案的固有限制；实现"只听到处理后的声音"需 per-session 静音（Shizuku/Root 路径，见 rootless_jamesdsp_analysis.md）

## 六、现场截图

- `docs/demo1.png` — 授权后抖音播放中（屏幕共享中）
- `docs/demo2.png` — Spleeter 处理运行中（屏幕共享中）
