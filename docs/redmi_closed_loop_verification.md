# Redmi（vermeer）完整闭环验证报告：捕获→回放→静音

## 环境
- 设备: Redmi K70 (23113RKC6C / vermeer), Android16/SDK36, MIUI, 骁龙8Gen3(kalama)
- 连接: 无线 adb 192.168.0.109:37807
- App: com.example.audiooverlay (AudioRouteLab, uid 10302, PID 19718)
- Edge: com.microsoft.emmx (uid 10255)
- 测试页: http://127.0.0.1:8000/video_test.html（yimeng_60s.mp4）

## 1. MediaProjection 捕获（MIUI 可用性）✅

canoe(AOSP) 上定论不可用的 AudioPlaybackCapture，**在 Redmi MIUI 上完全可用**：

| 项 | 值 |
|---|---|
| App AudioRecord | Port 10771, Session 39625, **Source: REMOTE_SUBMIX**, 48kHz, 2ch, PCM16, Active |
| 捕获电平 | **RMS -9.3 ~ -13.9 dB**（Edge 视频播放中实时捕获）|
| 授权流程 | Start Capture → 系统弹窗"下一步" → 选 Edge（autopilot.py 全程自动）|

## 2. AudioTrack 回放 ✅

| 项 | 值 |
|---|---|
| App AudioTrack | Session 39633, 48kHz, 2ch, PCM16 |
| 回放输出 | **AudioOut_22D 持续 -10 ~ -14 dB**（处理声活跃输出）|

## 3. per-session 静音（消除原始声）✅ 铁证

Edge 重新播放视频（session 递增 **39649 → 39657**，证实"每次播放 session 变，需动态重挂"）：

| 阶段 | AudioOut_25 (Edge原始声) | AudioOut_22D (App回放声) | 说明 |
|---|---|---|---|
| 基线（播放中）| **-52.5 dB** | -12.7 dB | 双声叠加 |
| MUTE 39657 | **-119.0 dB** | -12.1 dB | **原始声静音 66dB+**，只剩回放声 |
| MUTE+3s | **-121.2 dB** | -13.9 dB | 稳定 |
| UNMUTE | effect 从 39657 卸载 | -10.8 dB | 可逆 |

- 静音 effect: DynamicsProcessing(UUID e0e6539b-1781-7261-676f-6d7573696340) -200dB，attach session 39657
- 广播: `am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 39657 --ez mute true|false`
- **用户最终听到的 = AudioOut_22D 的处理后回放声，Edge 原始声已消除** —— "只听到处理后声音"闭环成立！

## 4. 关键工程结论

1. **Redmi MIUI 全链路可行**：MediaProjection 捕获（remote_submix）✓ + per-session 静音 ✓ + AudioTrack 回放 ✓，**无需 Root/Shizuku/DUMP**
2. **canoe 差异**：canoe(AOSP userdebug) 的 remote_submix 无数据（vendor audio policy/HAL 限制），捕获不可用；Redmi(MIUI) 正常
3. **session 动态递增**：Edge 每次播放 session 39649→39657，旧 session 的 effect 成 Orphan，需**自动监听 + 动态重挂**（待实现）
4. **广播可靠性**：MUTE_SESSION 广播偶发未生效（原因待查：可能 receiver 竞争），需加执行确认机制（App 侧 Log + 查询 effect 列表核验）

## 5. 遗留
- 处理质量：当前闭环回放的是"捕获原声"（DSP OFF，BGM Gain 100%），需开 DSP 模式 4/5（Spleeter/Demucs）验证"人声保留+BGM抑制"效果
- session 自动发现/重挂机制未实现
- canoe 捕获不可用 → canoe 上闭环不可行，闭环验证以 Redmi 为准
