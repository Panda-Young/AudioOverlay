# canoe (SM8850) root 只读诊断报告

日期: 2026-09-04 02:15 | 设备: Canoe for arm64, Android16/SDK36, AOSP userdebug (serial 816d9a5)
方式: adb root (uid=0) 只读采集，未修改任何系统文件

## 1. Audio HAL

- **audio HAL 接口**: AIDL（`init.svc.vendor.audio-hal-aidl: running`，非 HIDL）
- vendor/lib64/hw 音频库:
  - audio.primary.default.so（主 HAL）
  - audio.r_submix.default.so（remote submix HAL —— **存在**）
  - audio.usb.default.so / audio.bluetooth*.so / android.hardware.bluetooth.audio*.so
  - libaudiocorehal.default.so / libaudiocorehal.qti.so / libaudioeffecthal.qti.so（Qualcomm 音频核心）
- system/lib64/hw: 无音频 HAL（全在 vendor）

## 2. SoundFX 效果库（vendor/lib64/soundfx）

- **libdynamicsprocessingaidl.so** —— AIDL 版 DynamicsProcessing（per-session 静音依赖，已实测可用）
- libvolumelistener.so —— Qualcomm 音量监听 effect
- libqcomvisualizer.so / libquasar.so / libqcompostprocbundle.so —— Qualcomm 音频栈
- libaudiopreprocessing / libbundle / libdownmix / libeffectproxy / libldnhncr / libloudnessenhancer / libreverb / libvisualizer 等标准 AOSP effect
- system/lib64/soundfx: 空（全在 vendor）

## 3. 关键 audio props

- `audio.deep_buffer.media=true` —— 媒体音频走 deep buffer（与 Chrome/抖音实测一致：AudioOut_25 deep buffer 为主输出）
- `audio.offload.video=true` —— 视频音频可能走 offload 路径（硬件解码直出，可能绕过部分 effect）
- `aaudio.mmap_policy=2` / `aaudio.mmap_exclusive_policy=2` —— MMAP 音频
- `vendor.audio-hal-aidl` + `vendor-qti-media-c2audio-hal-1-0` 运行中（Qualcomm C2 audio codec）

## 4. audio_policy r_submix

- Remote Submix Out (port 90) / Remote Submix In (port 85, 33) **配置存在**
- 但 AudioFlinger remote_submix 线程始终 Standby（实测 RMS=-96dB 无数据）→ 捕获链路不可用的根因在更底层（HAL/策略路由），MediaProjection 捕获音频在此设备不可行

## 5. 对项目的意义

1. **per-session 静音（DynamicsProcessing）在 AIDL effect 栈上完全可用**（Chrome 75.7dB / 抖音 26dB+ 实测）
2. 抖音多路径输出（AudioOut_D fast + AudioOut_25 deep）中，deep buffer 可被静音，fast/offload 路径待解
3. 捕获链路（remote_submix）在此设备不可用 → 完整闭环必须在 Redmi（MIUI kalama）上验证
4. 若后续要 vendor 级方案：Qualcomm effect 栈 + AIDL HAL 是挂载点（超出当前安全红线，仅记录）
