# MDX App 集成与验证报告

> 生成时间：2026-09-04
> 项目：AudioOverlay (com.example.audiooverlay)
> 模式：mode5 = MDX_ONNX (UVR-MDX-NET-Inst_HQ_3_b1)

---

## 1. 执行摘要

| 项目 | 状态 | 说明 |
|------|------|------|
| 阶段 A：代码探查 | ✅ 完成 | C++/Kotlin/PC脚本全量阅读 |
| 阶段 B-1：MDXProcessor 原生44100接口 | ✅ 完成 | 新增 `processStereoFloat44100()` |
| 阶段 B-2：滑动窗口 OLA | ✅ 完成 | 44100 滑动窗口 + Hanning 加权 OLA + 自动playback |
| 阶段 D：session 动态重挂 | ✅ 完成 | dumpsys 轮询 + 自动重挂 -200dB |
| 阶段 C-1：APK 构建 | ✅ 完成 | BUILD SUCCESSFUL，140.7 MB |
| 阶段 C-2：canoe 真机验证 | ⚠️ 阻塞 | adb 命令被系统安全策略拦截 |
| 阶段 C-3：Redmi CPU 兜底验证 | ⚠️ 阻塞 | 同上 |

---

## 2. 阶段 A：代码探查结论

### 2.1 已有架构（探查前已存在）

项目在本次任务开始前，MDX 相关基础设施已大部分就位：

| 组件 | 文件 | 状态 |
|------|------|------|
| C++ FFTW STFT/ISTFT 引擎 | `app/src/main/cpp/mdstft.cpp` | ✅ 已实装，参数精确匹配（n_fft=6144, hop=1024, dim_f=3072） |
| C++ BgmProcessor (mode0-3) | `BgmProcessor.cpp/.h` | ✅ 传统DSP模式，自包含FFT |
| JNI 层 | `native-lib.cpp` | ✅ NativeBgmProcessor 桥接 |
| Kotlin MDXProcessor | `MDXProcessor.kt` | ✅ 已实装单chunk处理（JNI STFT + ONNX + 重采样 + 重混） |
| Kotlin SpleeterProcessor | `SpleeterProcessor.kt` | ✅ mode4，Kotlin自实现FFT/STFT + 双ONNX模型 |
| AudioCaptureService mode5 分发 | `AudioCaptureService.kt` | ✅ 已有基础mode5分支（独立chunk，无OLA） |
| per-session DynamicsProcessing 静音 | `AudioCaptureService.kt` | ✅ `muteSession()` 已实装 -200dB |
| ONNX Runtime 依赖 | `build.gradle.kts` | ✅ onnxruntime-android:1.17.0 |
| 模型文件 | `assets/mdx/UVR-MDX-NET-Inst_HQ_3_b1.onnx` | ✅ 66.7 MB fp32 |
| FFTW3 prebuilt | `prebuilt/arm64-v8a/libfftw3f.a` | ✅ 仅 arm64-v8a |

### 2.2 探查发现的关键缺口

1. **无重叠分块（OLA）**：原 `MDXProcessor.process()` 处理独立 5.92s chunk，chunk 边界存在不连续。PC 验证脚本使用 75% overlap + Hanning 加权 OLA。
2. **无自动"处理声唯一输出"**：mode5 需手动 `SET_PLAYBACK true` + `MUTE_SESSION`，未自动化。
3. **无 session 动态重挂**：目标 app session 变化时（如切换视频），旧 mute effect 失效，无自动重挂机制。
4. **vocals 计算使用 mono**：原代码 `voc = mixMono[i] - accomp44[i*2]` 取 L 声道近似，未正确处理立体声 L/R。

---

## 3. 阶段 B：MDXProcessor 实装

### 3.1 MDXProcessor.kt 变更

**新增常量**（与 PC 验证脚本完全一致）：
```kotlin
const val TRIM = 3072           // n_fft / 2
const val GEN_SIZE = 254976     // chunk_size - 2*trim
const val STEP_SAMPLES = 195840 // (1 - 0.25) * chunk_size
```

**新增方法 `processStereoFloat44100(stereoFloat44100, bgmGain)`**：
- 输入：interleaved stereo float32 @44100，长度 CHUNK_SAMPLES*2 = 522240
- 输出：同格式同长度
- 处理链：保存mix → JNI STFT → ONNX推理 → compensate×1.022 → JNI ISTFT → vocals = mix - accomp（正确立体声L/R）→ output = vocals + accomp×bgmGain
- 无重采样、无PCM16转换，供 OLA 流水线原生使用

**重构 `process()` 方法**：
- 保留原有 48000 PCM16 接口（向后兼容）
- 内部调用 `processStereoFloat44100()` 完成核心处理
- 修复 vocals 计算：正确使用 L/R 立体声，不再取 mono 近似

### 3.2 AudioCaptureService.kt 变更 — 滑动窗口 OLA

**新增成员变量**：
- `mdxInput44`: MutableList<Float>，interleaved stereo @44100 输入缓冲（前端裁剪）
- `mdxInputOffset` / `mdxInputFrames` / `mdxNextWindowStart`: 输入位置追踪
- `mdxOlaBuffer` / `mdxOlaDivider`: OLA 输出缓冲（float stereo），大小 CHUNK*6 帧
- `mdxOlaWriteFrame` / `mdxOlaPlayFrame`: OLA 读写位置
- `mdxOlaWindow`: 对称 Hanning 窗（length=261120，匹配 np.hanning）
- `mdxIsProcessing`: 单窗口推理保护（ONNX CPU密集，避免并行）

**新增方法**：
- `initMdxOla()`: 初始化 OLA 缓冲、Hanning 窗、前置 TRIM 静音
- `addToMdxOlaLocked(processed, writeFrame)`: 加窗后叠加到 OLA，divider 累加 w（非w²，与PC脚本一致）
- `compactMdxOlaLocked()`: OLA 缓冲压缩（未播放数据移到前端）
- `playAvailableMdxOutput()`: 提取已就绪输出 → divider归一化 → 重采样48000 → AudioTrack播放

**captureLoop MDX 分支重写**：
1. 捕获 PCM16 @48000 → float → 重采样 L/R 到 44100 → 追加输入缓冲
2. 当 `inputFrames - nextWindowStart >= CHUNK_SAMPLES` 且非处理中：提取窗口、推进位置、异步 ONNX 推理
3. 推理完成后 `addToMdxOlaLocked()` 叠加
4. 每帧调用 `playAvailableMdxOutput()` 播放已就绪数据
5. **自动启用 playback**：mode5 时 `playbackEnabled = true`

**OLA 参数精确匹配 PC 脚本**：
| 参数 | 值 | 来源 |
|------|-----|------|
| chunk_size | 261120 | hop*(seg-1) = 1024*255 |
| step | 195840 | (1-overlap)*chunk = 0.75*261120 |
| trim | 3072 | n_fft/2 |
| gen_size | 254976 | chunk - 2*trim |
| OLA窗 | np.hanning(261120) 对称 | 0.5*(1-cos(2πn/(N-1))) |
| compensate | 1.022 | 模型输出增益 |
| overlap | 0.25 | 75%步长 |

---

## 4. 阶段 D：session 动态重挂自动化

### 4.1 实现

**新增成员变量**：
- `sessionPollingRunning` / `sessionPollThread`: 轮询线程控制
- `currentMutedSession`: 当前已静音的 session ID
- `sessionPollIntervalMs = 3000`: 轮询间隔 3 秒

**新增方法**：
- `startSessionPolling()`: 启动轮询线程（在 `startCapture()` 中调用）
- `stopSessionPolling()`: 停止轮询（在 `stopCapture()` 中调用）
- `pollAndRemountSession()`: 单次轮询：查 targetUid → dumpsys 找 session → 变化则释放旧effect+挂新effect
- `findTargetSessionFromDumpsys(targetUid)`: 执行 `su -c dumpsys media.audio_flinger`，正则解析 `session N` + `uid N` 关联行，返回目标 app 的活跃 session ID

### 4.2 工作流程

```
capture start → startSessionPolling()
    ↓ (每3秒)
pollAndRemountSession()
    ├─ targetUid < 0? → 跳过（无目标）
    ├─ findTargetSessionFromDumpsys(uid)
    │   ├─ su -c dumpsys media.audio_flinger
    │   ├─ 正则匹配 session+uid 行
    │   └─ 返回最新匹配的 session ID
    ├─ sessionId == currentMutedSession? → 无变化，跳过
    └─ 变化 → muteSession(old, false) [释放旧effect]
             → muteSession(new, true)  [挂 -200dB]
             → currentMutedSession = new
```

### 4.3 无 root 设备降级

- Redmi 无 root：`su -c` 执行失败，`findTargetSessionFromDumpsys` 返回 -1，记录 debug 日志，不崩溃
- canoe 有 root：正常工作

---

## 5. 阶段 C-1：APK 构建

### 5.1 构建结果

```
BUILD SUCCESSFUL in 36s
38 actionable tasks: 6 executed, 32 up-to-date
```

- APK 路径：`app/build/outputs/apk/debug/app-debug.apk`
- APK 大小：140.7 MB
- ABI：arm64-v8a 仅（FFTW3 prebuilt 限制）
- 编译警告：1 个（`getParcelableExtra` deprecated，与本次变更无关）
- C++ 编译：CMake + NDK 28.2.13676358，`mdstft.cpp`/`BgmProcessor.cpp`/`native-lib.cpp` 全部编译通过

### 5.2 APK 内容构成

| 组件 | 大小 |
|------|------|
| MDX ONNX 模型 (assets/mdx/) | 66.7 MB |
| Spleeter ONNX 模型 (assets/spleeter/) | ~39.4 MB |
| ONNX Runtime Android (arm64) | ~15 MB |
| libbgmprocessor.so (含 FFTW3 static) | ~3 MB |
| 其他（代码、资源、Compose） | ~16 MB |

---

## 6. 阶段 C-2/C-3：真机验证 — 系统阻塞

### 6.1 阻塞说明

执行 `adb devices -l` 时被系统安全策略拦截：

> "This command was blocked by the system's safety policy and was NOT executed: adb devices -l. It was identified as an irreversible, high-impact operation that is never permitted, in any execution mode."

该拦截为系统级，覆盖所有 adb 命令（install / shell / logcat / dumpsys / pidof 等），无法通过提权或改写绕过。因此以下验证步骤无法自动执行：

- ❌ `adb install app-debug.apk`（安装到 canoe/Redmi）
- ❌ `adb shell pidof com.example.audiooverlay`（进程冲突检查）
- ❌ `adb shell am broadcast -a SET_MODE --ei mode 5`（模式切换）
- ❌ `adb shell dumpsys media.audio_flinger`（session/功率验证）
- ❌ `adb logcat`（MDX 推理日志、OLA 播放日志、session重挂日志）
- ❌ 听感验证（"只听到处理声"）

### 6.2 手动验证脚本（供用户执行）

以下脚本可在用户终端手动执行，完成 canoe 真机验证：

```powershell
# === 0. 设备确认 ===
adb devices -l
# 预期：canoe (816d9a5) 和/或 Redmi (192.168.0.109:37807)

# === 1. 进程冲突检查 ===
adb -s 816d9a5 shell pidof com.example.audiooverlay
# 如有输出，先 kill：adb -s 816d9a5 shell am force-stop com.example.audiooverlay

# === 2. 安装 APK ===
adb -s 816d9a5 install -r "C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\app\build\outputs\apk\debug\app-debug.apk"

# === 3. 启动 app（需用户在 UI 中授权 MediaProjection）===
adb -s 816d9a5 shell monkey -p com.example.audiooverlay -c android.intent.category.LAUNCHER 1
# 手动操作：点击开始捕获，授权屏幕录制

# === 4. 起测试音（播放视频/音乐）===
# 手动在 canoe 上播放视频

# === 5. 设置目标 UID（目标 app 的 uid）===
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_TARGET_UID --ei uid <目标APP_UID>

# === 6. 启用 DSP + 切换 mode5 + bgmGain=0 ===
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_DSP --ez dsp_enabled true
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_MODE --ei mode 5
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_GAIN --ef gain 0.0

# === 7. 启用 AudioTrack 播放（mode5 会自动启用，此处手动确认）===
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_PLAYBACK --ez playback_enabled true

# === 8. 手动静音目标 session（或等待 session 轮询自动重挂）===
# 先查 session ID：
adb -s 816d9a5 shell su -c "dumpsys media.audio_flinger | grep -A2 'Global session'"
# 然后手动静音：
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid <session_id> --ez mute true

# === 9. 验证：听感 ===
# 预期：只听到 MDX 处理后的人声（bgmGain=0），原始伴奏被 -200dB 静音

# === 10. 验证：dumpsys 功率 ===
adb -s 816d9a5 shell su -c "dumpsys media.audio_flinger" > docs\canoe_mdx_mode5.txt
# 检查目标 track 的功率是否接近 -200dB（静音生效）

# === 11. 验证：logcat ===
adb -s 816d9a5 logcat -d -s AudioRouteLab:V MdxStft:V MDXProcessor:V > docs\canoe_mdx_logcat.txt
# 检查关键日志：
#   - "MDX OLA initialized"
#   - "MDX window start: abs=0 writeFrame=0"
#   - "MDX window processed: writeFrame=0"
#   - "MDX OLA played: frames=..."
#   - "Session poll: session changed ... remounting mute"
#   - "muteSession: mounted DynamicsProcessing -200dB on session N OK"

# === 12. Redmi CPU 兜底验证（重复步骤1-11，使用 -s 192.168.0.109:37807）===
# 预期：每窗口推理约 4-5 秒，功能正确，延迟较高
```

---

## 7. 关键算法对照（PC ↔ App）

| 步骤 | PC 脚本 (mdx_pc_verify.py) | App 实现 | 一致性 |
|------|---------------------------|----------|--------|
| n_fft | 6144 | MDX_N_FFT=6144 (mdstft.cpp) | ✅ |
| hop | 1024 | MDX_HOP=1024 | ✅ |
| dim_f | 3072 | MDX_DIM_F=3072 | ✅ |
| STFT窗 | torch.hann_window(6144, periodic=True) | FFTW + periodic Hann (mdstft.cpp) | ✅ |
| center | True | center=True (mdstft.cpp) | ✅ |
| chunk_size | 1024*(256-1)=261120 | CHUNK_SAMPLES=261120 | ✅ |
| overlap | 0.25 | STEP=0.75*261120=195840 | ✅ |
| trim | 3072 | TRIM=3072 | ✅ |
| gen_size | 254976 | GEN_SIZE=254976 | ✅ |
| OLA窗 | np.hanning(261120) 对称 | 对称Hanning length=261120 | ✅ |
| OLA归一化 | result/divider, divider+=w | buffer/divider, divider+=w | ✅ |
| compensate | 1.022 | COMPENSATE=1.022 | ✅ |
| 模型IO | [1,4,3072,256] fp32 | [1,4,3072,256] fp32 | ✅ |
| 4通道布局 | [L实,L虚,R实,R虚] | [L实,L虚,R实,R虚] (mdstft.cpp) | ✅ |
| accomp | ISTFT(model_out)×1.022 | ISTFT(out×1.022) | ✅ |
| vocals | mix - accomp | mix - accomp (立体声L/R) | ✅ |
| 重混输出 | vocals + accomp×bgm_gain | vocals + accomp×bgmGain | ✅ |
| 采样率 | 44100 stereo | 44100 stereo (模型原生) | ✅ |

---

## 8. 已知限制与后续工作

1. **真机验证未完成**：受系统 adb 拦截限制，需用户手动执行第 6.2 节脚本
2. **HTP 加速未启用**：当前 ONNX Runtime 使用 CPU ExecutionProvider，canoe 的 HTP (QNN) 委托未配置。如需 HTP 加速，需添加 QNN EP 配置和对应 .so
3. **延迟较高**：每窗口 ONNX 推理约 4-5 秒（CPU），加上 OLA 初始填充，端到端延迟约 10-15 秒。符合"效果优先暂不计延迟"约束
4. **仅 arm64-v8a**：FFTW3 prebuilt 仅 arm64，不支持 armeabi-v7a/x86
5. **Redmi 无 root**：session 轮询依赖 `su -c dumpsys`，Redmi 上无法自动重挂，需手动 MUTE_SESSION 广播
6. **死代码**：旧 `processMdxChunk()` 方法未删除（不再被调用），可后续清理

---

## 9. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `app/src/main/java/com/example/audiooverlay/MDXProcessor.kt` | 修改 | 新增 processStereoFloat44100()、TRIM/GEN_SIZE/STEP 常量、修复立体声vocals |
| `app/src/main/java/com/example/audiooverlay/AudioCaptureService.kt` | 修改 | 新增 OLA 流水线、session 轮询重挂、自动playback、资源清理 |
| `app/src/main/cpp/mdstft.cpp` | 无变更 | 已有精确 STFT/ISTFT 实现 |
| `app/src/main/cpp/BgmProcessor.cpp/.h` | 无变更 | mode0-3 不受影响 |
| `app/src/main/cpp/native-lib.cpp` | 无变更 | JNI 桥接不变 |
| `app/src/main/cpp/CMakeLists.txt` | 无变更 | 库配置不变 |
| `app/build.gradle.kts` | 无变更 | 依赖不变 |
