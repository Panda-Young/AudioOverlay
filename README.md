# AudioOverlay

Android 系统级实时音频分离 App：捕获系统播放的音频（短视频 / 播放器），用 **MDX-Net** 神经网络实时分离人声与 BGM，通过 AudioTrack 回放纯人声，同时静音原声——实现「只听人声、不听背景音乐」。

> 个人学习项目，非商用。

## 功能

- `AudioPlaybackCapture` 系统音频捕获（Android 10+，无需 root）
- MDX-Net（UVR-MDX-NET-Inst_HQ_3）ONNX 实时人声分离
- Hexagon HTP NSP 硬件加速（SM8850）+ CPU EP 回退（SM8550 等）
- per-session 原声静音（DynamicsProcessing -200 dB）
- Jetpack Compose Material3 UI

## 架构

```
系统音频 (抖音 / 视频播放器)
        │
        ▼
AudioPlaybackCapture (AudioRecord 48 kHz)
        │
        ▼  重采样 44.1 kHz
STFT (N_FFT=6144, HOP=1024, JNI/FFTW3)
        │
        ▼
MDX-Net U-Net 推理 (ONNX Runtime: HTP EP / CPU EP)
        │
        ▼  ISTFT + Hanning OLA
重采样 48 kHz
        │
        ├──► AudioTrack 回放纯人声
        │
        └──► tools/session_mute_helper.py (PC / adb 辅助)
                 └─► 轮询 dumpsys，给原 App 的 AudioTrack session
                      挂 DynamicsProcessing(-200 dB)，静音原声
```

## 当前验证状态

| 验证项 | 设备 | 结果 |
|--------|------|------|
| seg128 全链路闭环 | SM8850 / HTP v81 | 波形相关性 L=0.9934，R=0.9947 |
| seg128 兼容性 | SM8550 / MIUI / CPU EP | L=0.826，R=0.830（CPU 推理时序漂移所致） |
| seg32 模型切换 | 代码 + APK 就绪 | PC corr=0.9948；单窗推理 1.46 s → 0.72 s |
| Redmi 真机闭环 | SM8550 / MIUI | 受阻：MIUI 对非系统 App 的 AudioRecord 做系统级 mute |

完整调研、死路列表与技术报告见 [`docs/PROJECT_HANDOFF.md`](docs/PROJECT_HANDOFF.md) 与 [`docs/AudioOverlay_technical_report.md`](docs/AudioOverlay_technical_report.md)。

## 构建

要求：Android Studio（JDK 17+）、NDK 28.x、CMake 3.22、仅 arm64-v8a。

```powershell
$env:JAVA_HOME="C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat assembleDebug
```

APK 输出：`app/build/outputs/apk/debug/app-debug.apk`

安装到设备：

```powershell
adb -s <serial> install -r app\build\outputs\apk\debug\app-debug.apk
```

## 模型文件（不随仓库分发）

仓库**不包含** ONNX 模型权重（每个约 64 MB）。构建前需要：

1. 从 [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) / [KUIELab-MDX-Net](https://github.com/kuielab/mdx-net) 获取 `UVR-MDX-NET-Inst_HQ_3` 权重；
2. 用 `tools/export_seg128_static.py` 或 `tools/export_seg32_static.py` 导出静态 shape ONNX；
3. 放到 `app/src/main/assets/mdx/`。

QNN / HTP 专有运行时库（`app/src/main/assets/qnn/`、`app/src/main/jniLibs/`）是从支持 Hexagon NSP 的设备上提取的 Qualcomm 闭源二进制，不随仓库分发；走 CPU EP 的设备不需要它们。

## 目录结构

```
app/src/main/
  java/com/example/audiooverlay/
    AudioCaptureService.kt   # 捕获→STFT→MDX→OLA→AudioTrack 主链路
    MDXProcessor.kt          # ONNX Runtime QNN HTP 封装
    Resampler.kt             # 48k ↔ 44.1k
    STFT.kt / FFT.kt
  cpp/
    mdstft.cpp               # JNI STFT/ISTFT (FFTW3)
tools/
  session_mute_helper.py     # PC 辅助：轮询 dumpsys 静音目标 session
  export_seg32_static.py     # ONNX 静态导出
  adb_ui/autopilot.py        # adb UI 自动化
docs/
  PROJECT_HANDOFF.md          # 交接文档（状态/死路/构建指南）
  AudioOverlay_technical_report.md
```

## 已知限制

- 仅 arm64-v8a（FFTW3 prebuilt + QNN 运行时库均为 arm64）。
- **MIUI 设备**存在系统级 AudioRecord mute：普通 App 通过 AudioPlaybackCapture 拿到的 PCM 被框架层清零，已验证的所有绕过方案（`pm grant`、Shizuku、修改系统 settings）均无效。
- per-session 原声静音依赖 PC 端 `tools/session_mute_helper.py` 辅助脚本：Android 不允许普通 App 枚举其他 App 的 AudioTrack session（需 `MEDIA_CONTENT_CONTROL` / `DUMP` / root），App 内无法自动完成。
- 最低 Android 10（API 29）。

## 致谢

- [KUIELab-MDX-Net](https://github.com/kuielab/mdx-net) / [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) — 人声分离模型
- [ONNX Runtime](https://github.com/microsoft/onnxruntime) — 推理引擎
- [FFTW3](https://www.fftw.org/) — FFT

模型权重遵循其原始仓库的 License；本项目代码按 MIT License 发布。
