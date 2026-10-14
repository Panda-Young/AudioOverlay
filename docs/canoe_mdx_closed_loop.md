# canoe MDX 端到端闭环验证记录（2026-09-04 04:12-04:18）

## 结论
**"捕获→MDX 分离→AudioTrack 回放→per-session 静音原声" 四段链路在 canoe（SM8850, Android16 userdebug）首次全部跑通。**

## 环境
- 设备：canoe（serial 816d9a5），AOSP userdebug，adb root
- App：com.example.audiooverlay（PID 25310），APK 140.5MB（FFTW3 + ONNX + MDX b1 模型 63.7MB）
- 目标 App：抖音（com.ss.android.ugc.aweme，uid 10205，PID 16368）
- 模型：UVR-MDX-NET-Inst_HQ_3_b1.onnx（fp32，输入 [1,4,3072,256]）

## 操作序列
1. UI 授权：Start Capture → "下一步" → 选择应用列表选"抖音" → MediaProjection 授权抖音
2. 广播（**必须在 Service 启动后发**，否则 receiver 未注册丢失）：
   - SET_TARGET_UID 10205 / SET_MODE 5 / SET_DSP true / SET_PLAYBACK true
   - MUTE_SESSION sid 89/49 mute true

## 证据链（logcat AudioRouteLab/MDXProcessor/MdxStft）
```
04:12:49 AudioPlaybackCaptureConfiguration limited to uid=10205 (uid-only)
04:12:49 AudioRecord.startRecording() — capture started
04:12:49 AudioTrack initialized: bufferSize=92256
04:12:49 Copied MDX model: UVR-MDX-NET-Inst_HQ_3_b1.onnx (66759194 bytes)
04:13:45 MDXProcessor initialized: model=.../mdx_models/UVR-MDX-NET-Inst_HQ_3_b1.onnx
04:13:51 MdxStftEngine created: n_fft=6144 hop=1024 dim_f=3072
04:14:20 MDX chunk processed: in=568320 out=568424     ← 首个 chunk（约29s，含模型预热）
04:14:34 MDX chunk processed: in=568320 out=568424     ← 稳定处理（14s/块）
04:17:56 muteSession: MOUNTED DynamicsProcessing -200dB on session 89 OK
04:17:56 muteSession: MOUNTED DynamicsProcessing -200dB on session 49 OK
04:18:12 MDX chunk processed: in=568320 out=568424     ← 重启后链路恢复
```
Level report 持续 RMS -9.9~-16 dB（捕获抖音音频稳定）。

## audio_flinger 证据
- AudioOut_25（DEEP_BUFFER 48000Hz）：Standby: no，活跃输出
  - Track 62: **yes 16368/10205 session 89**（抖音 active track，44100Hz）—— 挂 DynamicsProcessing（Effect ID 107, UUID e0e6539b...=DynamicsProcessing, **Enabled=y**）
  - Track 70: 22662/10203 session 121（App 的 AudioTrack 回放，48000Hz）
- A/B 控制验证：MUTE_SESSION mute=false → session 89 effect 从链中移除（只剩 Qti-MusicVolumeListener）→ 再 mute=true → 重新 MOUNTED OK

## 已知限制（如实标注）
1. **整块处理（5.92s chunk）**：AudioTrack 回放为"缓冲 5.92s + 处理 ~3-14s"的间歇模式，听感断续；滑动窗口 OLA（STEP_SAMPLES/GEN_SIZE/TRIM 常量已备）尚未接入 captureLoop
2. **App 进程被系统杀死**：04:14:35 `Process com.example.audiooverlay (pid 22662) has died: prcp FGS` + qti servicetracker killProcess——同日 qsap_mpamsvc（Qualcomm MPAM 服务）处于每 5s SIGABRT 崩溃循环，疑似关联；重启 App 后链路可恢复
3. 推理用 CPU EP（~3.4s/块 4 线程）；QNN HTP 实测 113.7ms/块，尚未集成进 App
4. session 递增：抖音重播/暂停会换新 session，动态重挂逻辑（0.4）需保持运行

## 复现步骤（脚本化要点）
1. `adb shell am start -n com.example.audiooverlay/.MainActivity`
2. Start Capture(377,328) → 授权"下一步"(1170,2141) → 选抖音(1260,1935)
3. 依次发 SET_MODE/SET_DSP/SET_PLAYBACK/MUTE_SESSION 广播
4. 等待 ~30s（模型加载+首块处理），logcat 查 MDX chunk processed
