# AudioOverlay 项目交接文档

> 交接时间：2026-09-05
> 项目状态：核心算法闭环已验证，真机播放存在未解决 Bug

---

## 一、项目概述

**AudioOverlay** 是一个 Android 系统级视频音频工具箱，个人学习非商用项目。核心功能是实时捕获系统播放的音频，经过 AI 音源分离（去除 BGM 保留人声）后通过 AudioTrack 回放，同时静音原应用的音频，实现"只听人声不听背景音乐"的效果。

### 技术栈

| 模块 | 技术 |
|------|------|
| 音频捕获 | `AudioPlaybackCapture` + `MediaProjection`（Android 10+） |
| 音源分离 | UVR-MDX-NET-Inst_HQ_3_b1 ONNX 模型 + ONNX Runtime QNN HTP EP |
| STFT/ISTFT | JNI C++ FFTW3（N_FFT=6144, HOP=1024） |
| 音频回放 | `AudioTrack`（48kHz stereo PCM16） |
| 原声静音 | per-session `DynamicsProcessing` effect（-200dB） |
| UI | Jetpack Compose Material3 |
| NDK | CMake + FFTW3 prebuilt（arm64-v8a only） |

### 全链路流程

```
系统音频(VLC/抖音) → AudioPlaybackCapture(AudioRecord 48k) → 重采样44.1k → STFT
→ MDX-Net推理(HTP) → ISTFT → OLA叠加 → 重采样48k → AudioTrack回放
                                                          ↓
                                        辅助脚本(PC端adb) → DynamicsProcessing静音原session
```

---

## 二、已完成验证

### 2.1 全链路真机闭环（canoe，已验证 ✅）

- **设备**：canoe（SM8850/Adreno840v2/Hexagon v81，Android16/SDK36，userdebug root，1440x3200）
- **结果**：corr L=0.9934 R=0.9947，per-2s=[1.0,1.0,0.996,0.993]，RMS 差 0.01dB
- **输入**：input_20260904_232539.wav（540000帧@48k=11.2s，rms=0.176）
- **输出**：417957帧=8.7s（3-4窗，HTP ~0.1s/窗实时）
- **逐环节验证**：JNI STFT vs PC torch corr=1.0；App out4 vs PC CPU 推理 corr=1.0；HTP fp32 与 PC CPU 逐位一致

### 2.2 Redmi 验证（已验证 ✅，有时序漂移）

- **设备**：Redmi（SM8550/Hexagon v73，无 root，SDK34，MIUI）
- **结果**：corr L=0.826 R=0.830，前5段 0.93-1.0，后段衰减
- **根因**：CPU EP 推理 4.4-7.4s/窗时序漂移（非算法差异）

### 2.3 双声叠加问题（已修复并验证 ✅）

- **根因1**：`targetUid` 从未自动设置（MediaProjection 选了应用但没把 UID 写入静音逻辑）
- **根因2**：session 自动发现被 Android 系统权限三重封死（AudioManager.activePlaybackConfigurations 需 MEDIA_CONTENT_CONTROL；dumpsys 需 DUMP 权限；su 需 root）
- **根因3**：抖音刷视频时预创建下一个视频的 AudioTrack，同时存在多个活跃 session，旧版辅助脚本只静音第一个
- **解决方案**：PC 端辅助脚本 `tools/session_mute_helper.py`，通过 adb 轮询 `dumpsys media.audio_flinger`，解析目标 UID 的所有 session ID，通过 `am broadcast MUTE_SESSION` 发给 App 挂载 DynamicsProcessing(-200dB)
- **验证**：10 个视频连续刷，10/10 全部活跃 session（始终2个）都被静音，双声叠加彻底消除

### 2.4 关键发现：Session ID 复用

- 应用停止播放后 AudioTrack session 被系统释放，session ID 可被后续创建的 AudioTrack 复用（包括其他应用）
- 若旧 session 上的 DynamicsProcessing effect 未被清理，复用该 ID 的新 AudioTrack 会被意外静音
- **必须保证辅助脚本持续运行**，session 消失时立即 unmute 清理 effect

---

## 三、当前未解决 Bug（高优先级）

### Bug：VLC 播放时 AudioTrack 不输出声音

**现象**：
- AudioPlaybackCapture 成功捕获到 VLC 音频（RMS -10~-16dB）
- MDX 处理器初始化成功（QNN HTP EP enabled）
- 但 AudioTrack 状态始终为 `no`（inactive），gain=-inf，state=f（flushed），underruns 持续累积
- 用户听不到任何声音

**已排查**：
1. ✅ AudioPlaybackCapture 配置：已修复为按 usage 匹配所有应用（不按 UID 过滤），RMS 有值
2. ✅ VLC 允许被捕获：按 usage 匹配时 RMS -13.9dB
3. ✅ Session 冲突：已确认 AudioOverlay 的 AudioTrack session 与 VLC session 不同
4. ✅ 辅助脚本静音：只静音 VLC session，不误伤 AudioOverlay
5. ✅ SET_PLAYBACK 广播：代码中已调用 `audioTrack.play()`，日志显示 "playback enabled=true"
6. ❓ 但日志中没有 "play() called" 或 "not initialized"，怀疑 `audioTrack` 为 null 或 state 异常
7. ❓ captureLoop 可能被阻塞：Baseline 模式下也不播放，说明问题不在 MDX
8. ❓ AudioTrack gain=-inf：可能是被残留的 DynamicsProcessing effect 静音，或处于 flushed 状态

**可疑方向**：
- `audioTrack` 可能为 null（initAudioTrack() 未被调用或失败）
- AudioTrack 被 idle 检测暂停后无法恢复（`playbackPausedForIdle=true` 但 `resumePlaybackAfterIdle()` 未被调用）
- MDX 初始化阻塞 captureLoop 48 秒，期间 idle 检测触发 pause，之后 source 恢复但 resume 逻辑未执行
- 残留的 DynamicsProcessing effect 挂载到了 AudioOverlay 的 session 上

**复现步骤**：
1. 安装最新 APK，启动 App
2. Start Capture → 授权"共享一个应用→VLC"
3. 用 VLC 播放 `/sdcard/Movies/yimeng_full.mp4`
4. 设置 mode=5, gain=0, dsp=true, playback=true
5. 启动辅助脚本 `python tools/session_mute_helper.py --serial 816d9a5 --uid 10206 --interval 2`
6. 观察：RMS 有值但 AudioTrack inactive，无声

**相关文件**：
- `app/src/main/java/com/example/audiooverlay/AudioCaptureService.kt`（核心，71KB）
  - `initAudioTrack()` 第 832 行
  - `ACTION_SET_PLAYBACK` 处理第 484-512 行
  - `captureLoop()` idle 检测逻辑
  - `pausePlaybackForIdle()` / `resumePlaybackAfterIdle()`
- `tools/session_mute_helper.py`（辅助脚本）

---

## 四、关键技术细节

### 4.1 MDX 模型常量

```
N_FFT=6144, HOP=1024, DIM_F=3072, MODEL_SR=44100
COMPENSATE=1.022f, CHUNK_SAMPLES=130048
MODEL_TIME=128（当前 seg128）, TRIM=3072
GEN_SIZE=123904, STEP_SAMPLES=97536
enableQnn=true（canoe HTP）
```

### 4.2 当前模型文件

- `UVR-MDX-NET-Inst_HQ_3_b1_seg128_static.onnx`（66,764,970B，真静态 shape (1,4,3072,128)）
- 设备端路径：`/data/user/0/com.example.audiooverlay/files/mdx_models/`
- 根目录已有 seg32/seg64 模型：`UVR-MDX-NET-Inst_HQ_3_b1_seg32.onnx`、`UVR-MDX-NET-Inst_HQ_3_b1_seg64.onnx`

### 4.3 JNI STFT/ISTFT（mdstft.cpp）

- FFTW3；periodic hann N=6144；stft center=True zero-pad
- 布局 `[((ch*2+reim)*3072+k)*128+t]`，k∈[0,3072) 不含 Nyquist
- istft FFTW_BACKWARD（无1/N）+手动 invN=1/6144；k≥3072置零（单边）
- hann/hann² WOLA；输出交错 [L,R,...]

### 4.4 广播控制接口

| Action | Extra | 说明 |
|--------|-------|------|
| `SET_MODE` | `mode`(int) | 0=Baseline, 5=MDX-Net |
| `SET_GAIN` | `gain`(float) | BGM 增益 0.0-1.0 |
| `SET_DSP` | `dsp_enabled`(bool) | DSP 开关 |
| `SET_PLAYBACK` | `playback_enabled`(bool) | AudioTrack 回放开关 |
| `MUTE_SESSION` | `sid`(int), `mute`(bool) | 静音指定 session |
| `SET_TARGET_UID` | `uid`(int) | 目标应用 UID（仅用于静音，不用于捕获过滤） |
| `SET_DUMP` | `dump_enabled`(bool) | dump 输入输出 PCM |

### 4.5 设备信息

| 设备 | serial | SoC | Android | root | 用途 |
|------|--------|-----|---------|------|------|
| canoe | 816d9a5 | SM8850/HTP v81 | 16/SDK36 | userdebug | 主力验证机 |
| Redmi | 6dd1f4eb | SM8550/HTP v73 | 14/SDK34 | 无 | 兼容性验证 |

### 4.6 应用 UID（canoe）

| 应用 | 包名 | UID |
|------|------|-----|
| AudioOverlay | com.example.audiooverlay | 10203 |
| 抖音 | com.ss.android.ugc.aweme | 10205 |
| VLC | org.videolan.vlc | 10206 |
| 图库 | com.android.gallery3d | 10142 |

---

## 五、构建与测试指南

### 5.1 构建（PowerShell）

```powershell
cd <项目根目录>
$env:JAVA_HOME="C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat assembleDebug --no-daemon
```

- 构建时间约 27-36 秒
- APK 路径：`app\build\outputs\apk\debug\app-debug.apk`
- 安装：`adb -s <serial> install -r app\build\outputs\apk\debug\app-debug.apk`

### 5.2 环境依赖

- Python 3.12：numpy / torch / soundfile / onnxruntime（CPU EP，用于 PC 端对照验证）
- ffmpeg：自行安装并加入 PATH（dump 后转码 / 抽轨 / 音视频对照用）
- NDK：28.2.13676358
- CMake：3.22.1
- ONNX Runtime：com.microsoft.onnxruntime:onnxruntime-android-qnn:1.26.0

### 5.3 辅助脚本启动（稳定版）

```powershell
Start-Process -FilePath "python" -ArgumentList "tools\session_mute_helper.py","--serial","816d9a5","--uid","10206","--interval","2" -WindowStyle Hidden -RedirectStandardOutput "helper.log" -RedirectStandardError "helper_err.log"
```

- 不要用 agent run_in_background（易 Native execution failed）
- 停止：`taskkill /F /IM python.exe`

### 5.4 Dump 拉取验证

```powershell
# 设备路径：/data/user/0/com.example.audiooverlay/files/dump/
cmd /c "adb -s 816d9a5 exec-out run-as com.example.audiooverlay cat files/dump/<file> > <local>"
# PowerShell 重定向会毁二进制，必须 cmd /c
```

PC 端全链对比：
```powershell
python docs\demo_audio\mdx_dump_verify.py <input.wav> <output.wav>
```

### 5.5 测试素材

- 自行准备一段含人声 + BGM 的视频（项目仓库不附带素材文件）
- 设备端推送路径：`/sdcard/Movies/yimeng_full.mp4`

---

## 六、待办事项

### P0：修复 AudioTrack 不播放 Bug

见第三节详细描述。这是阻断性问题，必须先解决。

### P1：切换到 seg32 模型降低时延

**目标**：将 MDX 推理从 seg128 切换到 seg32，降低单窗推理时延。

**背景**：
- PC 端 seg 扫描结果：seg64 corr=0.9976, seg32 corr=0.9948（`docs\demo_audio\separation_results\mdx_pc_segsweep\sweep.json`）
- seg32 质量损失可接受（corr 0.9948 vs seg128 0.999+）
- 当前 seg128 单窗推理 ~0.1-0.25s（HTP），seg32 预计进一步降低

**需要修改**：
1. 模型文件：使用 `UVR-MDX-NET-Inst_HQ_3_b1_seg32.onnx`（根目录已有）
2. 可能需要导出静态版本：参考 `tools/export_seg128_static.py` 导出 seg32 静态模型
3. `AudioCaptureService.kt` 中的常量：`MODEL_TIME=128` → `32`，以及相关的 CHUNK_SAMPLES/GEN_SIZE/STEP_SAMPLES/TRIM 需要重新计算
4. 验证：PC 端 corr 对比 + 真机闭环 dump 验证
5. 注意：seg 变化会影响 OLA 叠加逻辑和延迟补偿

**参考文件**：
- `tools/export_seg128_static.py`（静态导出脚本）
- `tools/verify_static_seg128.py`（静态验证脚本）
- `docs/demo_audio/mdx_pc_seg_sweep.py`（seg 扫描脚本）

### P2：提交到 GitHub

**目标**：将整个工程提交到 GitHub，创建规范的开源项目结构。

**需要创建/完善**：

1. **README.md**（项目根目录）
   - 项目简介、功能截图
   - 技术架构图
   - 构建指南、环境要求
   - 使用说明（授权、播放、静音）
   - 已知限制（Android 版本、root 要求、仅 arm64-v8a）
   - 致谢（UVR模型、ONNX Runtime、FFTW3）

2. **.gitignore**（已有，需完善）
   - 当前已有基础 Android .gitignore
   - 需要补充：`*.onnx`（模型文件太大，用 Git LFS 或 release 附件）、`docs/素材/`、`docs/demo_audio/separation_results/`（大文件）、`.preview/`、`*.apk`

3. **CI 配置**（`.github/workflows/`）
   - Android Build CI：assembleDebug，缓存 gradle
   - 可选：代码检查（ktlint/detekt）

4. **LICENSE**
   - 个人学习非商用，建议 MIT 或 CC BY-NC 4.0
   - 注意：UVR 模型有自己的 license，需声明

5. **大文件处理**
   - ONNX 模型文件（60MB+）：建议用 Git LFS 或放在 Release 附件
   - 测试视频（100MB）：不提交到仓库
   - `docs/demo_audio/` 下的 wav dump 文件：不提交

6. **目录清理**
   - 根目录的 `*.onnx` 文件应移到 `models/` 或不提交
   - `docs/logs/` 下的临时截图/XML 可清理
   - `_APK来源说明.md`、`_本机资源_Videos与ffmpeg.md` 等个人笔记不提交

---

## 七、已验证的死路（避免重复踩坑）

| 死路 | 原因 |
|------|------|
| "共享整个屏幕"模式 | AudioPlaybackCapture 不工作，RMS 恒 -96dB |
| `addMatchingUid(targetUid)` 过滤 | targetUid 自动检测偏差或应用不允许捕获，导致 RMS -96dB |
| 图库（com.android.gallery3d） | 可能设置了 ALLOW_CAPTURE_BY_NONE，单独授权捕获不到 |
| AudioManager.activePlaybackConfigurations | Android16 对普通 App 返回空（需 MEDIA_CONTENT_CONTROL） |
| App 进程执行 dumpsys | Permission Denial（需 DUMP 权限） |
| App 进程执行 su | Permission denied（userdebug 无 Magisk） |
| 反射 AudioPlaybackConfiguration.mSessionId | Android16 被 hiddenapi 拒绝（NoSuchFieldException） |
| 应用选择器点击预览卡片 | 无效，需点下方应用列表图标 |
| agent run_in_background 跑 python | 多次 Native execution failed，改用 Start-Process 独立进程 |
| MIUI uiautomator | 拿不到控件，fallback 用坐标 |
| Redmi 首次 MediaProjection 授权 | AudioRecord mute 5-7 分钟 |
| ORT≤1.23 htp_arch | 无 "81"，需升级到 1.26.0 |
| 动态 shape+QNN+disable_cpu_ep_fallback=1 | 失败，需用静态 shape |
| PowerShell 重定向二进制 | 会毁文件，必须 `cmd /c` |

---

## 八、关键文件索引

| 文件 | 说明 | 大小 |
|------|------|------|
| `app/src/main/java/.../AudioCaptureService.kt` | 核心服务：捕获、MDX推理、OLA、AudioTrack、per-session静音 | 71KB |
| `app/src/main/java/.../MDXProcessor.kt` | ONNX Runtime QNN HTP 封装 | 14KB |
| `app/src/main/java/.../MainActivity.kt` | Compose UI | 13KB |
| `app/src/main/cpp/mdstft.cpp` | JNI STFT/ISTFT（FFTW3） | 7KB |
| `app/src/main/cpp/BgmProcessor.cpp` | 旧版 BGM 处理器（可能已废弃） | 20KB |
| `tools/session_mute_helper.py` | PC端辅助脚本：轮询dumpsys静音目标session | 6KB |
| `tools/export_seg128_static.py` | ONNX 静态导出脚本 | 2.5KB |
| `docs/AudioOverlay_technical_report.md` | 技术总报告（9章） | 10KB |
| `docs/dump_closure_20260904.md` | 三轮验证完整记录 | - |
| `docs/demo_audio/mdx_dump_verify.py` | PC全链对比脚本 | - |
| `docs/demo_audio/mdx_pc_chain_sim.py` | PC全链路模拟 | - |

---

## 九、交接建议

1. **先修 Bug**：AudioTrack 不播放是阻断性问题，建议从 `audioTrack` 是否为 null、`playbackPausedForIdle` 状态、captureLoop 是否被阻塞这三个方向排查
2. **再切 seg32**：Bug 修复后，先在 PC 端验证 seg32 静态导出和 corr，再上真机
3. **最后提交 GitHub**：功能稳定后再整理提交，避免半成品
4. **保留辅助脚本**：per-session 静音方案依赖 PC 端辅助脚本，这是 Android 权限限制下的最优解，不要尝试在 App 内自动发现 session
5. **注意 session 复用**：辅助脚本必须持续运行，否则残留 effect 会误伤后续 AudioTrack

---

*本文档由项目历史上下文自动整理，如有疑问请参考 `docs/AudioOverlay_technical_report.md` 和 `docs/dump_closure_20260904.md`*
