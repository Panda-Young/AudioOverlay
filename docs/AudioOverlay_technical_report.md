# AudioOverlay 技术总报告

> Android 系统级视频音频工具箱 — 人声/伴奏分离与原声静音技术方案调研

## 目录

1. 项目概述
2. STFT/ISTFT 实现方案调研与对比
3. ONNX 推理执行提供商 (EP) 对比
4. 模型分段 (seg) 大小对比
5. 全链路验证对比
6. per-session 静音方案调研与对比
7. OLA (Overlap-Add) 滑窗方案
8. 最终方案总结
9. 参考资料

---

## 1. 项目概述

### 1.1 项目目标

AudioOverlay 是一款 Android 系统级视频音频工具箱（个人学习、非商用），核心目标是在移动端实现对任意视频应用的实时人声/伴奏分离，并将分离后的音频回放给用户，同时将目标应用的原始音频静音，避免双重播放造成的回声与混叠。

### 1.2 核心链路

```
AudioPlaybackCapture 捕获 → MDX-Net (ONNX) 分离人声/伴奏 → AudioTrack 回放处理后音频
                                                          ↓
                                          DynamicsProcessing per-session 静音原声
```

**处理流程**：音频捕获 → STFT 变换（JNI FFTW3）→ 神经网络分离 → ISTFT 重建 → OLA 滑窗拼接 → AudioTrack 回放 → DynamicsProcessing 静音原声。

### 1.3 关键技术挑战

| 挑战 | 说明 |
|------|------|
| 移动端实时推理 | MDX-Net 模型较大，需在 NPU/DSP 上实现实时推理 |
| JNI 与 PC 数值对齐 | STFT/ISTFT 需与 PC 参考实现严格一致 |
| per-session 静音获取 | Android 权限限制下普通应用难以获取其他应用的 session ID |
| 流式处理稳定性 | 异步推理与 OLA 滑窗存在竞态，需防污染与时序漂移 |

---

## 2. STFT/ISTFT 实现方案调研与对比

### 2.1 实现方案

**JNI 层 mdstft.cpp（FFTW3）** — `app/src/main/cpp/mdstft.cpp`

- periodic Hann 窗，N=6144，center=True zero-pad，hop T=128
- 频域布局：`[((ch*2+reim)*3072+k)*128+t]`，k∈[0,3072) 不含 Nyquist
- ISTFT：FFTW_BACKWARD（无 1/N）+ 手动 invN=1/6144，k≥3072 置零（单边谱），hann/hann² WOLA

```cpp
// ISTFT 核心：单边谱负频置零 + 手动归一化 + WOLA
const double invN = 1.0 / 6144.0;
for (int k = 0; k < N; k++) {
    if (k < 3072) { /* 来自模型输出 */ }
    else { ifft_in[k][0] = 0.0; ifft_in[k][1] = 0.0; } // 负频置零
}
fftw_execute(plan_backward);
for (int n = 0; n < N; n++) {
    output[out_idx] += ifft_out[n] * invN * window[n];
}
// 除以 hann² 权重和完成 WOLA 归一化
```

**PC 侧 torch.stft/torch.istft 模拟** — `docs/demo_audio/mdx_pc_chain_sim.py`

作为金标准对照。需注意 `torch.istft` 默认 hermitian 模式（含 Nyquist 共 3073 bin，共轭对称填充负频），与 JNI 负频置零策略不同。PC 侧需手动构造完整 N 点频谱（负频置零）后执行 `torch.fft.ifft` 以对齐。

### 2.2 参数对比

| 参数项 | JNI (FFTW3) | PC (torch) |
|--------|------------|-----------|
| FFT 点数 | 6144 | 6144 |
| 窗函数 | periodic Hann | periodic Hann |
| 帧移 | 128 | 128 |
| 频率 bin | k∈[0,3072) 不含 Nyquist | k∈[0,3073) 含 Nyquist (hermitian) |
| ISTFT 归一化 | 手动 invN=1/6144 | 自动 |
| 负频处理 | 置零 | 共轭对称填充 |
| 时域重建 | hann/hann² WOLA | 内置 WOLA |

### 2.3 验证数据

| 对比环节 | corr | max diff | 结论 |
|---------|------|---------|------|
| JNI STFT vs PC torch | 1.00000000 | 3e-5 | 完全一致 |
| App out4 vs PC CPU 推理 | 1.000000 | 0.000251 | 全链路一致 |
| PC 自洽往返 | 0.999976 | — | 固有精度损失 |

### 2.4 踩坑记录

- **permute 轴交叉混叠**：`permute(0,3,1,2)` vs `(0,3,2,1)` 混淆导致频率轴与时间轴互换，模型输出完全失真。修复后 corr=1.00000000 验证。
- **ISTFT 单边谱 vs hermitian**：JNI 负频置零（3072 bin）与 torch.istft hermitian（3073 bin）本质不同。MDX-Net 输出为实数掩码/特征图，负频无物理意义，坚持置零策略，PC 侧对应调整后 corr=1.000000。

---

## 3. ONNX 推理执行提供商 (EP) 对比

### 3.1 方案对比

**QNN HTP EP** — HTP v81 (SM8850)，burst/O3/fp32-vtcm8 配置

- fp16：corr≈0.01，严重精度退化
- 关 fp16 后：corr≈0.45（仍低，后证实是 dump 起点不对齐）
- 修正对齐后 fp32：**corr=1.0**，推理 0.11–0.25s/窗

**CPU EP 对照** — corr≈0.0117，与 HTP fp16 同数量级，**推翻 fp16 假设**，根因是 dump 起点不对齐。

**结论**：HTP fp32 与 PC CPU 推理逐位一致 (corr=1.0)，EP 不是差异源。

### 3.2 对比表

| 方案 | corr | 推理耗时 | 结论 |
|------|------|---------|------|
| HTP fp16 | ≈0.01 | — | 精度退化，不可用 |
| HTP fp32 | 1.0（对齐后） | 0.11–0.25s/窗 | 最终方案 |
| CPU EP | ≈0.0117（对齐前） | — | 推翻 fp16 假设 |

### 3.3 死路记录

| 死路 | 原因 |
|------|------|
| ORT≤1.23 无 htp_arch "81" 分支 | 需升级 ORT 支持 SM8850 |
| 动态 shape + QNN + disable_cpu_ep_fallback=1 | QNN 对动态 shape 支持不完善 |
| "假静态"导出（只设 dim_value 没 ClearField） | 运行时仍被识别为动态 shape |

```kotlin
// MDXProcessor.kt — HTP EP 配置
val htpOptions = mapOf(
    "htp_arch" to "81",
    "htp_performance_mode" to "burst",
    "htp_graph_optimization_level" to "O3",
    "vtcm_size" to "8",
    "disable_cpu_ep_fallback" to "1"
)
```

---

## 4. 模型分段 (seg) 大小对比

### 4.1 PC 分段扫描

| seg | corr | 可行性 |
|-----|------|--------|
| seg64 | 0.9976 | 可行 |
| seg32 | 0.9948 | 可行（结构下限） |
| seg16/8 | — | 不可行（Conv Invalid input shape） |

### 4.2 真机采用 seg128

- 输入 shape `(1,4,3072,128)`，模型 66,764,970 B
- 选择理由：频率上下文最完整，避免音乐噪声；HTP 硬件加速抵消延迟；静态 shape 导出约束

### 4.3 流式处理参数

| 常量 | 值 | 关系 |
|------|-----|------|
| CHUNK_SAMPLES | 130,048 | 含两侧冗余边 |
| TRIM | 3,072 | 每侧裁剪 |
| GEN_SIZE | 123,904 | = CHUNK − 2×TRIM |
| STEP_SAMPLES | 97,536 | 输出重叠 = GEN − STEP = 26,368 |

---

## 5. 全链路验证对比

### 5.1 设备对比

| 维度 | canoe (SM8850) | Redmi (CPU EP) |
|------|---------------|----------------|
| AI 加速 | HTP v81 | 无 |
| Android | 16 | SDK 34 |
| Root | 有 | 无 |
| 推理耗时 | 0.11–0.25s/窗 | 4.4–7.4s/窗 |
| corr L / R | 0.9934 / 0.9947 | 0.826 / 0.830 |
| RMS 差 | 0.01 dB | — |

### 5.2 per-2s 分段 corr

**canoe**：[1.000, 1.000, 0.996, 0.993] — 稳定无漂移
**Redmi**：前 5 段 0.93–1.0，后段持续衰减 — 时序漂移

**核心结论**：初始段质量一致，整体差距完全由推理延迟引发的时序漂移造成。

### 5.3 差异根因

| 问题 | 影响 | 状态 |
|------|------|------|
| PC 脚本 permute 轴错误 + ISTFT 差异 | 对比基线不可靠 | 已修复 |
| App OLA 竞争 bug | 重叠区相位跳变 | 已修复 |
| SET_DUMP 起点不对齐 | dump 与连续流偏移 | 已明确（重置 OLA 链保证对齐） |

---

## 6. per-session 静音方案调研与对比

### 6.1 七方案逐一分析

**方案 A — DynamicsProcessing(session, -200dB)**：已验证可行，需 session ID。AudioPlaybackCapture 在效果器上游捕获，不受静音影响。

**方案 B — getActivePlaybackConfigurations + 反射 mSessionId**：失败。Android 12+ 需 MEDIA_CONTENT_CONTROL 权限，普通 App 返回空列表。

**方案 C — dumpsys media.audio_flinger 直接执行**：失败。Permission Denial，需 DUMP 权限 (signature|privileged)。

**方案 D — dumpsys + su**：失败。userdebug 无 Magisk，Permission denied。

**方案 E — MediaRouter2.getRoutes() + getClientPackageName()**：部分可行。可自动检测目标 UID，但拿不到 session ID。

**方案 F — AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE**：不可行。目标应用被暂停，AudioPlaybackCapture 捕获中断。

**方案 G — PC adb 辅助脚本 (session_mute_helper.py)**：已验证可行。PC 端 `adb shell dumpsys media.audio_flinger` 解析 session ID → `am broadcast MUTE_SESSION` → App 挂载 DynamicsProcessing。支持多 session 同时跟踪（抖音刷视频时会预创建多个 AudioTrack），2 秒轮询自动切换。

```python
# tools/session_mute_helper.py 核心逻辑
def parse_sessions_from_dumpsys(output, target_uid):
    """返回目标 UID 的所有活跃 session ID 列表（抖音可能同时有多个）"""
    sessions = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+\d+\s+\d+\s+(\d+)\s+(\S+)", line)
        if match and int(match.group(2)) == target_uid:
            sessions.append(int(match.group(1)))
    return sessions

# 每 2 秒轮询：新 session 静音，消失 session 取消静音
muted_sessions = set()
while True:
    active = set(parse_sessions_from_dumpsys(dumpsys(), target_uid))
    for sid in active - muted_sessions: mute(sid)
    for sid in muted_sessions - active: unmute(sid)
    muted_sessions = active
```

### 6.2 七方案对比矩阵

| 方案 | 需 root | 需系统权限 | 自动跟踪 session | 多session支持 | 体验 | 可靠性 | 结论 |
|------|:-------:|:---------:|:---------------:|:------------:|------|--------|------|
| A: DynamicsProcessing | 否 | 否(需session ID) | 否 | 是 | 无 | 高 | **采用**(执行器) |
| B: getActivePlaybackConfigurations | 否 | 是 | — | — | — | 不可用 | 放弃 |
| C: dumpsys 直接执行 | 否 | 是 | — | — | — | 不可用 | 放弃 |
| D: dumpsys + su | 是 | 否 | 可轮询 | 是 | 无 | 低 | 放弃 |
| E: MediaRouter2 | 否 | 否 | 自动检测UID | — | 无 | 中 | **采用**(辅助) |
| F: 音频焦点独占 | 否 | 否 | — | — | 差 | 低 | 放弃 |
| G: PC adb 辅助 | 否 | 否 | 是(2s轮询) | 是 | 极小 | 高 | **采用**(获取ID) |

### 6.3 最终方案：G + A + E 三层组合

1. **E（UID 检测层）**：MediaRouter2 自动识别目标应用 UID
2. **G（session 获取层）**：PC 脚本轮询 dumpsys，2s 内跟踪所有活跃 session 变化，广播通知 App
3. **A（静音执行层）**：App 挂载 DynamicsProcessing(-200dB) 静音目标 session（支持多 session 同时静音）

零 root、零系统权限、自动化、多 session 支持、体验影响最小。

### 6.4 双声叠加问题与修复

**现象**：刷视频时出现原声 + 处理后声音的双重播放。

**根因**：抖音刷视频时会预创建下一个视频的 AudioTrack session，导致同时存在多个活跃 session。旧版辅助脚本只静音了第一个匹配的 session（`return session_id`），其余 session 的原声未被抑制。

**修复**：辅助脚本改为返回所有匹配 session 的列表（`parse_sessions_from_dumpsys`），维护 `muted_sessions` 集合，新出现的 session 立即静音，消失的 session 自动取消静音。轮询间隔从 5s 缩短到 2s，减少新 session 的无静音窗口期。

**验证**：10 个视频连续刷，每个视频的所有活跃 session（始终 2 个）全部被静音，双声叠加彻底消除。

---

## 7. OLA (Overlap-Add) 滑窗方案

### 7.1 数据结构

```kotlin
// AudioCaptureService.kt
private val mdxInput44 = mutableListOf<Float>()   // append-only + front trimming
private val mdxOlaBuffer = FloatArray(OLA_SIZE)   // 加权输出累加
private val mdxOlaDivider = FloatArray(OLA_SIZE)  // 权重计数(归一化除数)
private var mdxOlaEpoch = 0L                       // 世代计数
```

### 7.2 工作原理

输入累积 → 取帧(含重叠) → MDX-Net 异步推理 → 加权叠加 mdxOlaBuffer → 归一化 (buffer/divider) → AudioTrack 回放 → front trimming 循环。

### 7.3 世代计数防污染

异步推理在途时若 OLA 链重置，旧结果会污染新流。`mdxOlaEpoch++` 使所有在途结果失效，回调时检查 epoch 不匹配则丢弃。O(1) 开销彻底解决竞态。

### 7.4 Dump 对齐

`setDumpEnabled(true)` 时调用 `resetOla()`，保证 dump 起点 == 处理起点。

### 7.5 Idle 检测

源暂停时（超过空闲阈值），`resetOla()` 清空缓冲 + AudioTrack.pause/flush，避免陈旧音频残留输出。

---

## 8. 最终方案总结

| 技术领域 | 最终方案 | 关键参数 | 验证指标 |
|---------|---------|---------|---------|
| STFT/ISTFT | JNI FFTW3 | N=6144, hop=128, 单边谱负频置零 | corr=1.00000000 |
| ONNX 推理 | QNN HTP EP fp32 | HTP v81, burst/O3, vtcm8 | corr=1.0, 0.11–0.25s/窗 |
| 模型分段 | seg128 静态 | (1,4,3072,128), 66.8MB | 全链路 corr≥0.993 |
| 流式处理 | OLA + epoch 防污染 | CHUNK=130048, STEP=97536 | 无时序漂移 |
| 原声静音 | G+A+E 组合 | PC adb 辅助 + DynamicsProcessing | 多session 2s 内切换 |
| 全链路验证 | canoe (SM8850) | HTP v81 / Android 16 | corr L=0.9934 R=0.9947 |

**核心结论**：JNI STFT/ISTFT 与 PC 数值等价；HTP fp32 推理可靠且实时；seg128 是质量与延迟平衡点；高端设备全链路已闭合（corr≥0.993, RMS 差 0.01dB）；per-session 静音需 PC adb 辅助（零 root 零系统权限，支持多 session 同时跟踪）；OLA 稳定性依赖 epoch 机制。

---

## 9. 参考资料

### 项目源代码

| 文件 | 说明 |
|------|------|
| `app/src/main/cpp/mdstft.cpp` | JNI STFT/ISTFT 实现（FFTW3） |
| `app/src/main/java/com/example/audiooverlay/MDXProcessor.kt` | MDX 推理配置、流式常量、EP 配置 |
| `app/src/main/java/com/example/audiooverlay/AudioCaptureService.kt` | 捕获/回放/静音/OLA |
| `tools/session_mute_helper.py` | PC adb 辅助脚本（多 session 跟踪） |

### 验证脚本与数据

| 文件 | 说明 |
|------|------|
| `docs/demo_audio/mdx_dump_verify.py` | PC 对比脚本（corr/RMS/分段分析） |
| `docs/demo_audio/mdx_pc_chain_sim.py` | PC 全链路模拟 |
| `docs/demo_audio/mdx_pc_seg_sweep.py` | seg 扫描脚本 |
| `docs/demo_audio/separation_results/mdx_pc_segsweep/sweep.json` | seg 扫描结果 |
| `docs/dump_closure_20260904.md` | 三轮验证记录 |

### 外部文档与 API

- [AudioPlaybackCapture](https://developer.android.com/reference/android/media/audioplaybackcapture/AudioPlaybackCaptureConfiguration)
- [DynamicsProcessing](https://developer.android.com/reference/android/media/audiofx/DynamicsProcessing)
- [AudioTrack](https://developer.android.com/reference/android/media/AudioTrack)
- [AudioManager.getActivePlaybackConfigurations()](https://developer.android.com/reference/android/media/AudioManager#getActivePlaybackConfigurations())
- [MediaRouter2](https://developer.android.com/reference/android/media/MediaRouter2)
- [ONNX Runtime QNN EP](https://onnxruntime.ai/docs/execution-providers/QNN-ExecutionProvider.html)
- [FFTW3](https://www.fftw.org/)
- [torch.stft](https://pytorch.org/docs/stable/generated/torch.stft.html) / [torch.istft](https://pytorch.org/docs/stable/generated/torch.istft.html)
- [UVR-MDX-NET-Inst_HQ_3](https://github.com/Anjok07/ultimatevocalremovergui)
- `adb shell dumpsys media.audio_flinger` — AudioFlinger 调试命令
