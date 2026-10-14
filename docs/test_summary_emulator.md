# Android 模拟器多 App 音频捕获测试矩阵 — 测试总结报告

**测试日期**: 2026-09-01/02  
**设备**: Android Emulator (emulator-5554)  
**系统**: Android 17 (SDK 37), x86_64, google_apis_playstore_ps16k  
**分辨率**: 1280x2856  
**测试工具**: AudioRouteLab v1.0 (AudioPlaybackCapture + AudioRecord + JNI BGM DSP)

---

## 一、核心结论

### ✅ AudioPlaybackCapture 管道在 Android 17 上完全正常工作

通过 **stagefright 系统命令**播放 440Hz 正弦波测试音频，成功捕获到：
- **RMS = -9.0 dB**, **Peak = -6.0 dB**（50% 音量的正弦波，理论值吻合）
- 捕获延迟 < 1 秒
- 捕获稳定，每秒持续输出 Level report

### ⚠️ 关键发现：该 Android 17 模拟器上普通 App 不输出音频

所有测试的普通 App（YouTube、Chrome、VLC）均出现**视频可播放但 AudioFlinger 显示 0 活跃音频轨道**的现象：

| 验证项 | 结果 |
|--------|------|
| 视频画面播放 | ✅ 正常（截图确认播放控件、进度条） |
| AudioFlinger 活跃轨道 | ❌ 0 active |
| 媒体音量 | ✅ STREAM_MUSIC = 7/15（非静音） |
| 停止捕获后仍 0 active | ✅ 确认非 AudioPlaybackCapture 导致 |
| stagefright（shell uid）输出音频 | ✅ 正常 |

**结论**：这是该 Android 17 模拟器的**音频配置/兼容性问题**，非 Android 17 系统级限制，也非 AudioPlaybackCapture 的问题。普通 App 的 AudioTrack 数据未到达 AudioFlinger HAL 层。

---

## 二、捕获技术参数（所有测试一致）

| 参数 | 值 |
|------|-----|
| Sample Rate | 48000 Hz |
| Channels | 2 (stereo) |
| PCM Format | PCM_16BIT (format=2) |
| Buffer Size | 3072 frames (24576 bytes) |
| Min Buffer Size | 6144 bytes |
| AudioPlaybackCapture 匹配 | USAGE_MEDIA, USAGE_GAME, USAGE_UNKNOWN |
| MediaProjection 模式 | **Share entire screen**（必须，非 Share one app） |
| Foreground Service Type | mediaProjection |
| Logcat Tag | AudioRouteLab |
| Target App 检测 | 未指定（未实现 UsageStats 检测） |

---

## 三、各 App 测试结果详表

| App | 包名 | 能捕获 | 视频播放 | 音频输出 | RMS | 备注 |
|-----|------|--------|----------|----------|-----|------|
| **基线(stagefright)** | N/A (shell) | ✅ YES | N/A | ✅ YES | -9.0 dB | 系统命令，证明管道正常 |
| **YouTube** | com.google.android.youtube | ❓ 未验证 | ✅ YES | ❌ NO | -96 dB | 视频播放但无音频输出（模拟器问题） |
| **Chrome** | com.android.chrome | ❓ 未验证 | ✅ YES | ❌ NO | -96 dB | HTML5视频播放但无音频输出 |
| **VLC** | org.videolan.vlc | ❓ 未验证 | ❌ 异常 | ❌ NO | -96 dB | 音频播放模块反复初始化/停止，测试视频可能无音轨 |
| **抖音** | com.ss.android.ugc.aweme | ❓ 未验证 | ❌ 需登录 | ❌ NO | -96 dB | 需手机号登录，无游客模式 |
| **Bilibili** | tv.danmaku.bili | ➖ 未测试 | N/A | N/A | N/A | APK下载失败，未安装 |
| **腾讯视频** | com.tencent.qqlive | ➖ 未测试 | N/A | N/A | N/A | 无APK源，跳过 |

---

## 四、MediaProjection 授权流程（Android 17）

Android 17 的 MediaProjection 授权对话框与旧版本不同：

1. **对话框标题**: "Share your screen with AudioOverlay?"
2. **模式选择下拉框**: 
   - "Share one app"（默认）
   - "Share entire screen"（**必须选择此项**才能捕获系统音频）
3. **按钮**: Cancel / Next
4. **选择 Share entire screen 后点 Next** → 捕获启动

**注意**: 如果选择 "Share one app"，AudioPlaybackCapture 可能只能捕获指定 App 的音频，且需要额外配置。本测试统一使用 "Share entire screen"。

---

## 五、后台持续与 App 切换验证

| 测试项 | 结果 |
|--------|------|
| 捕获运行中按 Home 回桌面 | ✅ 捕获继续（Level report 持续输出） |
| 切换到其他 App | ✅ 捕获继续 |
| Foreground Service 通知 | ✅ 显示 "AudioRouteLab 正在捕获系统音频..." |
| 进程被杀后自动恢复 | ❌ START_NOT_STICKY（不自动重启） |

---

## 六、App 禁止 Capture 检测

由于普通 App 在该模拟器上不输出音频，**无法通过 RMS=-96 dB 判断 App 是否设置了 ALLOW_CAPTURE_BY_NONE**。判断标准应为：
- 如果 App 正常输出音频（AudioFlinger 有 active track）但 RMS=-96 dB → App 禁止了 capture
- 如果 App 本身不输出音频（AudioFlinger 0 active track）→ 无法判断

本测试中所有 App 均为后者（不输出音频），因此 **App 是否禁止 capture 字段标记为"未知"**。

---

## 七、问题与建议

### 已发现问题
1. **模拟器 App 音频输出异常**: 普通 App 的 AudioTrack 数据未到达 AudioFlinger，建议在真实设备上复测
2. **VLC 音频播放异常**: VideoPlayerActivity 对纯音频文件处理有问题，StartActivity 不自动播放
3. **抖音需登录**: 无游客模式，无法在模拟器上测试
4. **Bilibili/腾讯视频 APK 下载困难**: 国内 App 的 APK 源在海外网络环境下不易获取

### 下一步建议
1. **在真实非 Root Android 手机上复测**：验证普通 App 音频输出和捕获是否正常
2. **解决模拟器音频问题**：检查 AVD 音频配置（`hw.audioOutput=yes`），或换用不同系统镜像
3. **安装 Bilibili/腾讯视频**：通过国内 APK 镜像站或 Play Store 登录后安装
4. **实现 UsageStats 目标 App 检测**：当前 Target App 字段显示"未指定"
5. **验证 AudioTrack 回放路径**：第一阶段仅验证了 Capture，未验证 "捕获→DSP→回放" 的完整链路

---

## 八、输出文件清单

```
docs/
├── test_matrix_emulator.csv          # 测试矩阵 CSV
├── test_summary_emulator.md           # 本总结报告
├── test_video.mp4                     # 测试视频 (BigBuckBunny)
├── test_tone.wav                      # 测试音频 (440Hz 正弦波)
├── vlc.apk                            # VLC 安装包
└── logs/
    ├── 00_baseline_stagefright.txt    # 基线测试完整 logcat (含YouTube/VLC第一次测试)
    ├── 01_youtube.txt                 # YouTube 测试摘要
    ├── 02_chrome.txt                  # Chrome 测试摘要
    ├── 03_vlc.txt                     # VLC 测试摘要
    ├── 04_bilibili.txt                # Bilibili 未安装说明
    └── 05_douyin.txt                  # 抖音测试完整 logcat
```
