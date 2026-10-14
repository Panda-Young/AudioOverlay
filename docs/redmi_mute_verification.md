# Redmi（vermeer）per-session 静音验证报告

## 环境
- 设备: Redmi K70 (23113RKC6C / vermeer), Android16/SDK36, MIUI, 骁龙8Gen3(kalama)
- 连接: 无线 adb 192.168.0.109:38911（后变 37807）
- App: com.example.audiooverlay (AudioRouteLab, uid 未知), Edge: com.microsoft.emmx (uid 102xx)
- 素材: docs/yimeng_60s.mp4（以梦为马朗诵 60s）
- 测试页: docs/video_test.html（<video> 标签 + TAP ANYWHERE TO PLAY）

## 测试链路
1. Python312 http.server 8000（docs 目录）+ `adb reverse tcp:8000 tcp:8000`
2. Edge 打开 `http://127.0.0.1:8000/video_test.html`，点击任意处触发播放
3. Edge 音频 session 39577（Global session refs 表）
4. 静音广播: `am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid 39577 --ez mute true|false`

## A/B 结果（AudioOut_25, Signal power history）

| 阶段 | 操作 | Signal power | 说明 |
|---|---|---|---|
| 基线 | Edge 播放 yimeng_60s.mp4 | **-20.9 dB** | 视频播放中 |
| MUTE | sid=39577, mute=true | **-43.0 dB** | 抑制 22.1 dB，~1s 内生效 |
| UNMUTE | sid=39577, mute=false | **-16.9 dB** | 恢复播放 |

- Effect 挂载确认: session 39577 存在 "2 Effect Chains, 2 effects"，Effect ID 98（DynamicsProcessing，UUID e0e6539b-1781-7261-676f-6d7573696340）
- 结论: **MIUI (Redmi) 上 per-session 静音同样有效**（与 canoe/AOSP 一致），无需 Root/Shizuku/DUMP

## 备注
- 抑制深度 22dB < canoe Chrome 的 75.7dB：Edge 视频音频走 deep buffer（AudioOut_25），但可能存在其它路径或音量波动，需进一步确认
- 无线 adb 断开（设备休眠）后 Edge/App 进程被系统回收，session 丢失；恢复连接后需重启 App/Edge 与 MediaProjection 授权
- 下一步: MediaProjection 授权后验证 AudioPlaybackCapture 在 MIUI 的可用性（"捕获→处理→回放"闭环）

## 结论
per-session 静音机制在 Redmi MIUI 上验证通过，为"只听到处理后声音"方案提供了跨厂商一致性证据。
