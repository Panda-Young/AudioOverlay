# 本机资源补充说明（重要，供任务执行者使用）

## 1. 本机视频资源（用于"本地视频播放器"捕获测试）

目录：`C:\Users\16411\OneDrive\Videos\`（已确认存在，均为 mp4）

| 文件 | 大小 |
|---|---|
| 范进中举.mp4 | 10 MB |
| 桃园三结义.mp4 | 17 MB |
| 改革春风吹满地.mp4 | 32 MB |
| 处处吻.mp4 | 42 MB |
| 康熙说唱王朝-怒斥群臣,.mp4 | 40 MB |
| 【血色湘西】华夷之辩.mp4 | 38 MB |
| 以梦为马不负韶华.mp4 | 101 MB |
| 罢工：无产阶级的重拳！.mp4 | 108 MB |

使用方式：裁剪成 10-30 秒短视频后 `adb -s 6dd1f4eb push <本地> /sdcard/Movies/`，再用本地播放器（VLC APK 位于 `C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\vlc.apk`）播放并启动 AudioRouteLab 捕获测试。

## 2. ffmpeg 说明（已核实存在）

ffmpeg 位于：`C:\Users\16411\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\app\ffmpeg\`

该目录下已存在：
- `ffmpeg.exe`（约 213 MB，2026/8/21）
- `ffplay.exe`、`ffprobe.exe` 同目录

注意：该路径**未加入系统 PATH**，调用时需用全路径，或将该目录加入 PATH 后再用。

裁剪命令示例：
```
ffmpeg -ss 0 -t 20 -i 输入.mp4 -c copy 输出_20s.mp4
```
同编码直接截取很快；若需兼容手机播放可用 `-c:v libx264 -c:a aac` 重编码。

## 3. 已确认可用的抖音 APK（沿用上一条说明）

`C:\Users\16411\Downloads\APK\抖音+-+记录美好生活_38.7.0_APKPure.apk`

## 4. 目标设备

Redmi 真机 serial=6dd1f4eb（Android 16 / SDK 36，屏幕已解锁，装有 Edge com.microsoft.emmx）
