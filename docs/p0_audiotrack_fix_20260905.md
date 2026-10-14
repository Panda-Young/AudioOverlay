# P0：AudioTrack 不播放 Bug — 定位与修复

**日期**：2026-09-05
**设备**：canoe SM8850 / Android 16，serial `816d9a5`
**音源**：VLC 播放 `/sdcard/Movies/yimeng_full.mp4`（uid 10206）
**结论**：✅ 已修复。回放连续、实时、无自捕获反馈。

---

## 一、结论先行

AudioTrack 一直是"能出声"的。真正的问题是**回放链路被饿死**，叠加一个**自捕获反馈环**，
使得听感上"没有任何声音"。三个独立缺陷：

| # | 缺陷 | 实测证据 |
|---|------|---------|
| 1 | 捕获线程阻塞在 `AudioTrack.write()` 上 | 单个 MDX 窗口产出 2.2s 音频，一次 `write()` 阻塞 ~2s；期间捕获量从 96000 帧/秒跌到 **9600 帧/秒（丢 90%）** |
| 2 | MDX 初始化阻塞捕获线程 49s | `QNN libs ready 03:35:36.978` → `MDXProcessor initialized 03:36:26.068`；期间 `playbackHeadPosition` 完全冻结在 756000 |
| 3 | 自捕获反馈环：我们自己的 AudioTrack 被 AudioPlaybackCapture 录回去 | 回放 OFF → 捕获 RMS `-9.2 dB`；回放 ON → **`-3.7 dB`**（固定抬高 ~5.5dB） |

结果：AudioTrack 每 4s 才被灌入 2.2s 音频，underrun 持续累积（1→5），管道整体只有约 55% 实时速度。

---

## 二、修复前 / 修复后对比（同一设备、同一素材、MDX mode 5）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| `AudioTrack.getUnderrunCount()` | 1 → 5，**持续增长** | 恒定 **7**（60s soak 无增长） |
| 实际输出速率 | 断续：2.2s 音频 / 4.0s 墙钟 | **48,124 帧/秒**（60s 输出 2,887,467 帧 ≈ 48kHz 精确实时） |
| 每秒捕获帧数 | 9,600（丢 90%） | **96,000 / 100,800**（满速，无丢帧） |
| MDX 窗口间隔 | 7.8s / 4.0s（应为 2.211s） | **2.199s** = `STEP_SAMPLES / 44100`，正好实时 |
| 回放开关对捕获 RMS 的影响 | +5.5dB（反馈） | **无影响**（-14.0 / -13.6 / -10.9 / -13.6，差异来自节目内容） |
| AudioFlinger track 846 | — | `Active=yes`，`FrmRdy=21144`，持续消耗 |

---

## 三、根因细节

### 3.1 捕获线程阻塞（主因）

`captureLoop()`（捕获线程）里直接调用 `audioTrack.write(pcm16, …)`。
MDX 一个窗口产出 `STEP_SAMPLES=97536` 帧 @44.1k → 重采样后 **106161 帧 @48k = 2.21 秒音频**。

`AudioTrack` 缓冲区只有 `23064` 帧（0.48s）。`MODE_STREAM` 的 `write()` 是**阻塞**的，
要等到缓冲腾出空间才返回 —— 于是捕获线程被卡住约 2 秒，期间 `AudioRecord.read()` 无人服务，
环形缓冲溢出丢帧。丢帧又让 MDX 输入推进变慢，窗口间隔从 2.2s 拉长到 4~7.8s，
输出速率掉到 55% 实时，形成"越卡越饿"的正反馈。

### 3.2 MDX 初始化阻塞

`ensureMdxProcessor()` 在捕获线程内同步调用，QNN HTP 图固化耗时 **49~51 秒**
（实测 `MDX async init done in 51396 ms`）。这段时间内 `read()` 完全停摆。

### 3.3 自捕获反馈

`AudioPlaybackCaptureConfiguration` 按 `USAGE_MEDIA` 匹配**所有**应用，包括我们自己。
我们的 AudioTrack 也是 `USAGE_MEDIA`，于是被自己录了回去：输出 → 再捕获 → 再处理 → 再输出，
每轮叠加一份延迟副本。这也是交接文档里"gain=-inf"误判的来源。

> 附：交接文档第三节记录的 `gain=-inf` / `state=f` 是误读。
> `dumpsys media.audio_flinger` 的 **AT::add 历史行**里，新建 track 必然是
> `... 48000 3 1 2 -inf ... 23064 0 f ... new` —— `-inf` 是尚未 setVolume 的初始增益，
> `f` 是 flushed 初始状态，两者都是新建 track 的正常瞬时值，不代表 live 状态。
> 真正的 live 行在 "N Tracks of which N are active" 表里。

---

## 四、改动（`AudioCaptureService.kt`）

1. **独立回放线程 + 有界队列**
   - 新增 `playbackQueue` / `playbackThread`（`AudioPlaybackThread`，`THREAD_PRIORITY_URGENT_AUDIO`）。
   - 捕获线程只做 `enqueuePlayback()`（非阻塞），回放线程按 `PLAYBACK_SLICE_FRAMES = 480`（10ms）切片写出。
   - `MAX_QUEUE_FRAMES = 4s` 背压：消费者落后时丢**最旧**音频，保证端到端延迟有界而不是无限增长。
   - `writeToTrack()` 统一收口：记录 write 返回值、错误分类，并在 `playState != PLAYING` 时自动 `play()` 恢复。

2. **MDX 异步初始化 + 提前预热**
   - 新增 `requestMdxInitAsync()`，`mdxInitInFlight` 去重，跑在 `mdxScope` 上。
   - `startCapture()` 里复制完模型就立刻预热（6e 步骤），让 49s 与用户挑音源的时间重叠。
   - `captureLoop()` 的 mode 5 分支不再同步 `ensureMdxProcessor()`。
   - `mdxProcessor` 改 `@Volatile`；`initMdxOla()` 加 `synchronized(mdxOlaLock)`。

3. **消除自捕获**
   - `initAudioTrack()` 里 `AudioAttributes.Builder.setAllowedCapturePolicy(ALLOW_CAPTURE_BY_NONE)`
     （API 29+，带 `Build.VERSION` 判断）。自己的输出不再被 AudioPlaybackCapture 录回。

4. **配套**
   - `pausePlaybackForIdle()` 增加 `clearPlaybackQueue()`，避免源暂停后继续吐旧音频。
   - 基线/Spleeter 路径 `outputBuffer` 是复用数组，入队前 `copyOf(shortsRead)`。
   - `stopCapture()` 里 `stopPlaybackThread()`。
   - 新增 `AT-HEALTH` 周期日志（state / playState / session / head / underruns / 队列深度 / 丢弃计数）。

---

## 五、复现与验证命令

```bash
# 1. 起捕获（含 MediaProjection 授权，已脚本化）
python tools/grant_capture.py --serial 816d9a5 --app VLC

# 2. 起音源
adb -s 816d9a5 shell am start -a android.intent.action.VIEW \
    -d "file:///sdcard/Movies/yimeng_full.mp4" -t "video/*" \
    -n org.videolan.vlc/.StartActivity

# 3. MDX 模式 + 回放
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_DSP   --ez dsp_enabled true
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_GAIN  --ef gain 0.0
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_MODE  --ei mode 5
adb -s 816d9a5 shell am broadcast -a com.example.audiooverlay.SET_PLAYBACK --ez playback_enabled true

# 4. 看生命体征（重点：underruns 不增长、frames 增速 ≈ 48000/s、queued 稳定）
adb -s 816d9a5 logcat -s AudioRouteLab:D | grep AT-HEALTH

# 5. 静音原声
python tools/session_mute_helper.py --serial 816d9a5 --uid 10206 --interval 2

# 6. 确认 track 活跃
adb -s 816d9a5 shell dumpsys media.audio_flinger | grep -E "^\s+846\s"
```

---

## 六、遗留

- `errs=2`（26k 次写入中 2 次异常，发生在模式切换瞬间），`underruns` 未因此增长，暂不处理；
  已把 `writeToTrack` 的错误日志上限从 10 提到后续可调，便于下次抓到具体错误码。
- MDX 初始化 51s 仍然偏长。若需要可把 `htp_graph_finalization_optimization_mode`
  从 `"3"` 调到 `"1"`，代价是推理可能变慢 —— 待 P1 切 seg32 后重新评估。
