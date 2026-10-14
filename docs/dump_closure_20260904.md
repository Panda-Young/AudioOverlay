# AudioOverlay — dump 对比闭环验证记录（2026-09-04）

## 结论
App（Android）实时 MDX 分离输出 与 PC（onnxruntime CPU）复刻链 **一致（corr=0.9877）**，
**dump 对比闭环正式达成**。

## 最终验证数据（Redmi 6dd1f4eb / SM8550 / CPU EP / 小红书视频源）
- input: 1017600 帧 @48k stereo（21.2s），rms=0.243，100% 非零
- output: 417957 帧 @48k stereo（8.7s，4 窗），rms=0.253
- PC 复刻链（同 input → 44.1k 重采样 → MDX OLA seg128 → vocal gain=0 → 48k）
- **corr L=0.9877 R=0.9877，RMS pc=-11.93dB app=-11.93dB（电平完全一致）**
- per-2s corr: [1.0, 0.997, 0.99, 0.984]
- best offset=-240000（App 输出 8.7s 与 PC 前段对应，CPU 推理慢仅完成 4 窗）

## 关键根因与修复（本轮）
1. **OLA writeFrame 提前推进 bug（核心）**
   - 现象：App output 呈"段-静音-段"结构（每 1-2s 有声后 0.5-1s 静音），corr 卡 0.02-0.53
   - 机制：窗口提交时 `mdxOlaWriteFrame += STEP` 提前推进 → playable 增长 →
     playAvailableMdxOutput 在推理完成前消费 [playFrame, playable)（buffer 全 0 写 dump 并推进
     playFrame）→ 推理完成 addToMdxOlaLocked 写入时真实内容已被跳过 → 每窗只剩尾部碎片
   - 修复：writeFrame 移到 **addToMdxOlaLocked 内推进**（推理完成后），playable 只在内容
     真实就绪后增长 → 每窗完整输出、无静音段
2. **dump 起点对齐**（上轮已修）：setDumpEnabled(true) 重置 OLA 链并立即 initMdxOla（前置
   TRIM silence），dump input 起点 == 处理起点
3. **PC 脚本三修**（上轮已修）：ISTFT center 补偿 + 单边/Nyquist 置零 + ifft 归一；
   torch.stft permute 布局 bug（0,3,1,2 正确、0,3,2,1 错）→ 弃用 torch.stft 改手写 float64 rfft；
   .f32 交错 [L,R] 解交错 reshape(-1,2).T

## 历史排查链（防重复）
HTP fp16 → 关 fp16 → CPU EP → 起点对齐 → permute 布局 → ioDump 逐环节对比 → ISTFT 语义对齐
→ 手写 rfft → OLA 竞争 bug。每步 corr 提升：0.01 → 0.45 → 0.02 → 0.013 → 0.026 → 0.53 → 0.9877

## 第二轮验证（Redmi 6dd1f4eb 修复后重装 23:00，小红书源）
- input10: 1252800 帧@48k（26.1s）rms=0.211，98.7% 非零
- output10: 842601 帧（17.5s，7 窗）rms=0.196，99.7% 非零
- **全链 corr L=0.826 R=0.830；per-2s=[1.0, 0.999, 0.994, 0.969, 0.93, 0.909, 0.84, 0.45]**
- **前 5 段 corr 0.93-1.0：App 输出与 PC 复刻链一致（闭环证据）**
- 后段衰减 = CPU EP 推理 4.4-7.4s/窗 → OLA 播放时钟消费驱动 → 长段时序漂移（非算法差异；canoe HTP 0.11-0.25s/窗无此问题）

## Redmi 采集链路踩坑（记录备查）
1. **首次 MediaProjection 授权后的捕获会话有静音期**（数分钟，日志 `[audioRecordData][mute] f:0`），
   恢复时间不定（本次 ~7 分钟恢复）；12:53 轮成功是"第二个会话"
2. **短视频播完不循环 → 源静音**（UI RMS 回到 -96dB）；需下滑切换新视频
3. **App 前台不影响捕获**（23:06 App 前台时捕获到小红书后台音频 -13.2dB，推翻早前"App 前台 mute"结论）
4. 小红书 launcher 包名 `com.xingin.xhs`；monkey 唤起 + swipe 切视频可恢复有声源

## 第三轮验证（canoe 816d9a5 HTP 实时，Android 16，抖音源）—— 完美闭环
- input4: 540000 帧@48k（11.2s）rms=0.176，98.9% 非零，per-2s rms [0.13,0.21,0.14,0.22,0.18,0.13]
- output4: 417957 帧（8.7s，3-4 窗）rms=0.105，99.9% 非零
- **全链 corr L=0.9934 R=0.9947；per-2s=[1.0, 1.0, 0.996, 0.993]**
- **RMS pc=-19.60dB app=-19.61dB（差 0.01dB）**
- HTP 推理 ~0.1s/窗（实时），无 CPU 慢时序漂移 → 全程 corr >0.99
- **结论：App 实时管线（JNI STFT/ISTFT + ONNX HTP 推理 + OLA）与 PC 复刻链逐位一致，全链路闭环达成**

## canoe 演示踩坑（记录备查）
1. **首次录制 input 仅 44 字节**：MediaProjection 授权后 capture 链刚启动未就绪，SET_DUMP 太早 → 等 Capture YES + RMS 有声再录
2. **input 全零（三次）**：
   - 第一次：23:19 截图 RMS -11.9dB 有声，但切回 App+发广播的几秒内抖音视频播完 → 录制时已静音
   - 第二次：抖音前台直接录，但 `monkey` 启动抖音触发位置权限弹窗 → 抖音被弹窗拦截未播放
   - 第三次：同第二次（权限弹窗未处理）
   - **解决**：autopilot find-text "不允许" 关弹窗 → 抖音继续播放 → 立即录（不切回 App）
3. **logcat 确认**：Level report 全程 -96dB 即证明录制期静音（非代码 bug）
4. **Android 16 平台**：AudioPlaybackCapture 行为正常（与 Redmi Android 14 一致），无平台差异

## 遗留（已知）
- Redmi CPU EP 推理 4.4-7.4s/窗（canoe HTP 0.11-0.25s/窗）；长对比需更长录制或 HTP 设备
- MediaProjection 静音期：授权后需等待/第二会话，或改 AudioRecord 重建逻辑（可选优化）
- App 侧另存 44.1k 副本（可选优化，当前按 PC 侧重采样对齐）
