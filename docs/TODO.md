# AudioOverlay — 完整待办任务清单

> 更新时间：2026-09-04 05:47（**QNN HTP filesDir 自包含方案闭环：21 库 assets 打包→数据域解包→Enforcing 下 HTP 推理 0.24–0.6s，无 root 无 permissive**）| 项目：Android 系统级视频音频工具箱（个人学习非商用）
> 目标：看抖音/爱奇艺/Edge/Chrome 等视频时实时降低 BGM 音量、保留人声/对白，最终只听到处理后的声音

## 状态图例
🟢 完成 / 🔄 进行中 / ⛔ 阻塞 / 🟠 待执行 / ⚪ 待调研 / ⏸ 搁置

---

## 🔴 P0 — 当前主线（已委派子代理并行执行）

| # | 待办 | 状态 | 说明 |
|---|---|---|---|
| 0.1 | **App 接入 MDX-Net 频谱前后处理**（复用 SpleeterProcessor STFT 框架） | 🟢 | PC侧验证✅(corr=1.000)；App代码实装✅；构建✅(140.5MB)；**canoe真机闭环✅**：MDX chunk in=568320/out=568424 持续处理，捕获RMS -10~-15dB |
| 0.2 | **per-session 静音 + 分离回放 → "处理声唯一输出"** | 🟢 | **canoe抖音闭环✅**：DynamicsProcessing -200dB 挂 session 89(active track) Enabled=y，A/B 解除/重挂均生效（解除后 effect 从链移除）；App track(uid 10203/session 121) 在 AudioOut_25 输出 MDX 处理声 |
| 0.3 | QNN HTP 接入 App（onnxruntime QNN EP） | 🟢 | **canoe 双方案闭环✅**：①设备目录方案（/data/local/tmp/qnn_app 26库+stub，permissive，3.86s→0.41s）；②**filesDir 自包含方案✅**（21库 assets 打包→数据域解包→**Enforcing 下 HTP 0.24–0.6s，无 root 无 permissive**；根因=ORT 需 libQnnHtpPrepare.so 在 ADSP_LIBRARY_PATH）；docs/qnn_htp_integration.md；**待办**：Redmi v75 真机验证 |
| 0.4 | session 动态重挂自动化（重播/暂停递增） | 🟢 | 代码实装✅：3s轮询 dumpsys media.audio_flinger，正则解析session+uid，变化自动释放旧effect+挂新effect；canoe(root)可用，Redmi无root降级；**canoe实测✅**（session 89/49 均挂载成功） |

## 🟠 P1 — 真机验证（用户点名"一刻不能停"）

| # | 待办 | 状态 | 说明 |
|---|---|---|---|
| 1.1 | canoe per-session 静音实测 | 🟢 | Chrome session 337：-33.1→-108.8→-33.1dB（75.7dB 深度）docs/canoe_mute_verification.md |
| 1.2 | canoe 抖音静音 A/B | 🟢 | MuteControlService 运行正常，logcat 确认 muteSession 已挂载 session 89；抖音多 session(49/89)切视频递增；App AudioTrack 回放(-10dB)掩盖静音效果；详见 docs/canoe_douyin_mute_verification.md |
| 1.3 | canoe AudioTrack 回放验证 | 🟢 | SET_PLAYBACK=true 后 AudioOut_5D 线程活跃，功率 -6~-15dB（很响），证明能出声；存在回放过响和自捕获回环问题；详见 docs/canoe_audiotrack_playback.md |
| 1.4 | canoe root 诊断（18 项只读） | 🟢 | docs/canoe_root_diagnostic.md（AIDL HAL/Qti soundfx/r_submix） |
| 1.5 | Redmi 全链路闭环（捕获→分离→回放→静音） | 🟢 | docs/redmi_closed_loop_verification.md（捕获-9~-14dB、回放-10~-14dB、静音 66dB+） |
| 1.6 | Redmi 闭环 + DSP 处理效果验证（mode 4/5） | 🟢 | Spleeter(mode4) ON/OFF 切换 2 轮，功率特征变化符合预期：ON 状态方差增大，R2 观察到 -67.3dB BGM 移除凹陷；详见 docs/redmi_dsp_verification.md |
| 1.7 | B站/腾讯/YouTube 捕获测试 | 🟠 | VIP DRM 风险；Edge 网页已实测可捕获 |

## 🟡 P2 — BGM 算法（对白/影视场景）

| # | 待办 | 状态 | 说明 |
|---|---|---|---|
| 2.1 | MDX-Net 分离（部署算法） | 🟢 | UVR-MDX-NET-Inst_HQ_3：抑制 -12.44dB，PC RTF 1.18，canoe HTP 113.7ms |
| 2.2 | Demucs htdemucs（效果基准） | 🟢 | 抑制 -13.2dB / RTF 0.594，用户听感验收 |
| 2.3 | fp16 转换（QNN HTP 前置） | ⛔ | **已否决**：NaN 根因=长度常量 343980 fp16 溢出（多处）+ QNN 不支持 GRU |
| 2.4 | VAD 辅助：非语音段才降 BGM | 🟠 | 避免误伤对白 |
| 2.5 | 频谱门限法：300-3400Hz 语音频段保护 | 🟠 | 非神经兜底 |
| 2.6 | WOLA 最小二乘重构（iSTFT 音质） | ⚪ | 提升混叠质量 |
| 2.7 | 流式 chunk 优化（2s→0.5s，状态机） | 🟠 | 降延迟（用户暂不敏感，效果优先） |

## 🔵 P4 — 工程/App 功能

| # | 待办 | 状态 | 说明 |
|---|---|---|---|
| 4.1 | MDXProcessor（STFT 复用 SpleeterProcessor） | 🟢 | processStereoFloat44100原生44100接口+立体声vocals修复+常量对齐PC脚本 |
| 4.2 | AudioTrack 回放 + 静音合并输出链路 | 🟢 | mode5自动playback+OLA输出播放+per-session -200dB静音 |
| 4.3 | UsageStats 目标 App 检测 | 🟠 | |
| 4.4 | SYSTEM_ALERT_WINDOW 悬浮窗工具箱 | 🟠 | |
| 4.5 | Session 动态管理（重播自动重挂） | 🟢 | tools/session_remount.py 轮询+自动重挂，见P0.4；离线解析验证通过 |
| 4.6 | 缓冲管理避免 underrun | 🟠 | MDX OLA缓冲已实现，AudioTrack underrun待真机调优 |
| 4.7 | 效果冲突处理（Qti 共存） | 🟢 | canoe 已证共存 OK |
| 4.8 | BGM DSP 真实算法替换 stub | 🟢 | Spleeter/Demucs/MDX 均已验证 |

## ⚪ P5 — 系统级/Root 方案（长期）

| # | 待办 | 状态 | 说明 |
|---|---|---|---|
| 5.1 | 全局 AudioEffect 挂载 session 0 | ⚪ | JamesDSP 方式，HAL 级 |
| 5.2 | DirectOutput/Offload/MMAP 覆盖验证 | ⚪ | |
| 5.3 | 蓝牙 SCO 虚拟路由 | ⚪ | |
| 5.4 | MediaSession 音量/IPlayer.setVolume | ⚪ | MIUI 应用音量原理 |

## 🟣 基础设施/工具

| # | 待办 | 状态 | 说明 |
|---|---|---|---|
| 6.1 | 沙箱反复销毁 | ⛔ | 平台机制；产物即时落盘、checkpoint 化 |
| 6.2 | http server 被沙箱杀掉自动重启 | 🟠 | Python312 http.server 8000 + adb reverse |
| 6.3 | autopilot.py adb UI 自动化 | 🟢 | tools/adb_ui/（shot/dump/tap/find-text 等） |
| 6.4 | 「UVR实测进度监控」定时任务 | 🟠 | UVR 已完成，价值消失，可删 |

## ⏸ 已搁置（明确低优先级）

| # | 待办 | 原因 |
|---|---|---|
| 7.1 | Hexagon DSP offload / AudioFlinger Hook | 需 Root+vendor / bootloop 风险（红线外） |
| 7.2 | 全局增益/中置声道调优 | 效果上限低 |
| 7.3 | VR 系模型（BVE-4B） | 对朗诵/对白类轨互换无效（歌曲类可另测） |

---

## 汇总
🟢 完成 15 / 🔄 进行中 0 / ⛔ 阻塞 3 / 🟠 待执行 12 / ⚪ 待调研 5 / ⏸ 搁置 3
**最高优先路径**：用户手动执行真机验证（adb被系统策略拦截）→ 验证通过后集成 QNN HTP 加速 → 抖音/Redmi 全链路闭环
