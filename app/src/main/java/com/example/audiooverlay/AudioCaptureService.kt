package com.example.audiooverlay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.audiofx.DynamicsProcessing
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.log10
import kotlin.math.sqrt

/**
 * Foreground Service that runs AudioPlaybackCapture + AudioRecord.
 *
 * Flow:
 *   MediaProjection (user授权)
 *     → AudioPlaybackCaptureConfiguration
 *     → AudioRecord
 *     → PCM data (read in dedicated thread)
 *     → RMS/Peak calculation
 *     → Native BGM DSP (optional)
 *     → [reserved] AudioTrack playback (disabled by default)
 */
class AudioCaptureService : Service() {

    companion object {
        private const val TAG = "AudioRouteLab"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "audio_capture_channel"

        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val ACTION_START = "com.example.audiooverlay.START_CAPTURE"
        const val ACTION_STOP = "com.example.audiooverlay.STOP_CAPTURE"

        // Debug broadcast actions (controlled via adb shell am broadcast)
        const val ACTION_SET_MODE = "com.example.audiooverlay.SET_MODE"
        const val ACTION_SET_GAIN = "com.example.audiooverlay.SET_GAIN"
        const val ACTION_SET_DSP = "com.example.audiooverlay.SET_DSP"
        const val ACTION_SET_PLAYBACK = "com.example.audiooverlay.SET_PLAYBACK"
        const val ACTION_MUTE_SESSION = "com.example.audiooverlay.MUTE_SESSION"
        const val ACTION_SET_TARGET_UID = "com.example.audiooverlay.SET_TARGET_UID"
        const val ACTION_SET_DUMP = "com.example.audiooverlay.SET_DUMP"
        const val EXTRA_MODE = "mode"
        const val EXTRA_GAIN = "gain"
        const val EXTRA_DSP_ENABLED = "dsp_enabled"
        const val EXTRA_PLAYBACK_ENABLED = "playback_enabled"
        const val EXTRA_SESSION_ID = "sid"
        const val EXTRA_MUTE_ENABLED = "mute"
        const val EXTRA_TARGET_UID = "uid"
        const val EXTRA_DUMP_ENABLED = "dump_enabled"

        // Target app UID for playback capture (static so it survives service restarts).
        // When >= 0, AudioRecord only captures this UID's audio — prevents self-capture
        // of our own AudioTrack output (which caused an infinite echo / noise loop).
        // Persisted to SharedPreferences so it survives process death too.
        private const val PREFS_NAME = "audio_overlay_prefs"
        private const val PREFS_KEY_TARGET_UID = "target_uid"
        @Volatile
        var targetUid = -1

        fun loadTargetUid(context: Context): Int {
            val uid = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getInt(PREFS_KEY_TARGET_UID, -1)
            if (uid >= 0) targetUid = uid
            return targetUid
        }

        fun saveTargetUid(context: Context, uid: Int) {
            targetUid = uid
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putInt(PREFS_KEY_TARGET_UID, uid).apply()
        }

        // Audio config
        private const val SAMPLE_RATE = 48000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_STEREO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        private const val CHANNEL_COUNT = 2
        private const val BUFFER_MULTIPLIER = 4

        // Spleeter config
        const val MODE_SPLEETER = 4  // Neural separation via Spleeter ONNX
        private const val SPLEETER_CHUNK_SECONDS = 2.0f  // Process 2s chunks (latency ignored)
        private const val SPLEETER_CHUNK_FRAMES = (SAMPLE_RATE * SPLEETER_CHUNK_SECONDS).toInt()

        // MDX-Net config (UVR-MDX-NET-Inst_HQ_3_b1)
        const val MODE_MDX = 5  // MDX-Net neural separation
        // 模型窗口 5.92s @44100 (261120) → @48000 = 284160 帧
        private const val MDX_CHUNK_FRAMES = 284160

        fun start(context: Context, resultCode: Int, resultData: Intent) {
            val intent = Intent(context, AudioCaptureService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_RESULT_CODE, resultCode)
                putExtra(EXTRA_RESULT_DATA, resultData)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            val intent = Intent(context, AudioCaptureService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }
    }

    private var mediaProjection: MediaProjection? = null
    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null
    private var isRunning = false
    private val nativeProcessor = NativeBgmProcessor()

    // Spleeter neural separator (lazy init when mode 4 selected)
    private var spleeterProcessor: SpleeterProcessor? = null
    private var spleeterBuffer: ShortArray? = null
    private var spleeterBufferPos = 0
    private val spleeterScope = CoroutineScope(Dispatchers.Default + Job())

    // MDX-Net neural separator (lazy init when mode 5 selected)
    // @Volatile: initialized off the capture thread (requestMdxInitAsync).
    @Volatile private var mdxProcessor: MDXProcessor? = null
    private var mdxBuffer: ShortArray? = null
    private var mdxBufferPos = 0
    private val mdxScope = CoroutineScope(Dispatchers.Default + Job())

    // === MDX sliding-window overlap-add (OLA) state — native 44100 Hz ===
    // Input buffer (interleaved stereo float @44100), append-only with front trimming
    private val mdxInput44 = mutableListOf<Float>()
    private var mdxInputOffset = 0  // absolute frame pos of mdxInput44[0]
    private var mdxInputFrames = 0  // total frames ever appended (absolute)
    private var mdxNextWindowStart = 0  // absolute start frame of next window to process
    private var mdxIsProcessing = false  // one-at-a-time inference guard
    private var mdxOlaInitialized = false
    // OLA 世代计数：setDumpEnabled/pausePlaybackForIdle 重置链路时自增，
    // 用于丢弃重置前已提交、重置后完成的在途窗口推理结果（防污染新链路）。
    private var mdxOlaEpoch = 0

    // OLA output buffers (interleaved stereo float @44100)
    private val mdxOlaSizeFrames = MDXProcessor.CHUNK_SAMPLES * 6  // ~36s buffer
    private lateinit var mdxOlaBuffer: FloatArray   // weighted sum of windowed outputs
    private lateinit var mdxOlaDivider: FloatArray  // sum of window weights (per sample)
    private var mdxOlaWriteFrame = 0  // buffer-relative frame for next window output
    private var mdxOlaPlayFrame = 0   // buffer-relative frame played up to
    private val mdxOlaLock = Any()

    // Symmetric Hanning window for OLA (length CHUNK_SAMPLES, matches np.hanning())
    private lateinit var mdxOlaWindow: FloatArray

    // AudioTrack for playback of processed audio
    private var audioTrack: AudioTrack? = null
    private var playbackEnabled = false  // Controlled via SET_PLAYBACK broadcast
    private var playbackPausedForIdle = false

    // === Playback subsystem (runs on its own thread) ===
    // Why: a single MDX window yields ~2.2s of audio (97536 frames @44.1k). Handing
    // that to AudioTrack.write() on the capture thread BLOCKS it for ~2s, during
    // which AudioRecord.read() is not serviced. Measured on canoe: captured frames
    // per second dropped from 96000 to 9600 (90% loss), which throttled the whole
    // pipeline to ~55% of real time and left AudioTrack permanently underrunning.
    // The capture thread now only *enqueues*; a dedicated thread drains the queue
    // in small slices so write() never blocks the capture loop.
    private val playbackQueue = ArrayDeque<ShortArray>()
    private var playbackQueueFrames = 0
    private var playbackHeadOffset = 0  // frames already consumed from playbackQueue.first()
    private val playbackLock = Object()
    private val MAX_QUEUE_FRAMES = SAMPLE_RATE * 4   // 4s back-pressure ceiling
    private val PLAYBACK_SLICE_FRAMES = 480          // 10ms per write
    @Volatile private var playbackThreadRunning = false
    private var playbackThread: Thread? = null
    private var playbackDroppedFrames = 0L

    // MDX init is expensive (QNN HTP graph finalization measured at ~49s on canoe).
    // It must never run on the capture thread, and is warmed up eagerly at capture
    // start so the 49s overlaps with the user picking a source app.
    @Volatile private var mdxInitInFlight = false

    // === Debug data dump (SET_DUMP broadcast) ===
    // Dumps captured input PCM16 @48000 and processed output PCM16 @48000 as WAV files
    // under <externalFilesDir>/dump/, for PC-side verification (same input, same MDX chain).
    @Volatile private var dumpEnabled = false
    private var dumpInFile: RandomAccessFile? = null
    private var dumpOutFile: RandomAccessFile? = null

    // Idle detection: source paused -> stop playing stale buffered audio.
    // read() returns <=0 once per ~50ms while the captured app is silent.
    private var idleReadCount = 0
    private val IDLE_STOP_THRESHOLD = 20  // ~1s of silence

    // Per-session mute effects (DynamicsProcessing -200dB) keyed by session id
    private val muteEffects = mutableMapOf<Int, DynamicsProcessing>()

    // === Session dynamic re-attachment (Phase D) ===
    // Polls `dumpsys media.audio_flinger` to detect target app session changes,
    // automatically remounts -200dB mute on the new session, releases old effect.
    @Volatile
    private var sessionPollingRunning = false
    private var sessionPollThread: Thread? = null
    @Volatile
    private var currentMutedSession = -1  // session id currently muted
    private val sessionPollIntervalMs = 3000L

    /**
     * Mount/unmount a -200dB DynamicsProcessing mute effect on a target audio session.
     * This is the core of "per-session output replacement": it silences the original
     * app's audio so only our processed AudioTrack playback is heard.
     */
    private fun muteSession(sid: Int, mute: Boolean) {
        if (sid <= 0) {
            Log.e(TAG, "muteSession: invalid sid=$sid")
            return
        }
        if (mute) {
            if (muteEffects.containsKey(sid)) {
                Log.d(TAG, "muteSession: already mounted on session $sid")
                return
            }
            try {
                val dp = DynamicsProcessing(Int.MAX_VALUE, sid, null)
                dp.setInputGainAllChannelsTo(-200f)  // -200dB -> inaudible
                dp.enabled = true
                muteEffects[sid] = dp
                Log.d(TAG, "muteSession: mounted DynamicsProcessing -200dB on session $sid OK")
            } catch (e: Exception) {
                Log.e(TAG, "muteSession: FAILED on session $sid -> ${e.javaClass.simpleName}: ${e.message}")
            }
        } else {
            muteEffects.remove(sid)?.let {
                try {
                    it.enabled = false
                    it.release()
                    Log.d(TAG, "muteSession: unmounted session $sid")
                } catch (e: Exception) {
                    Log.e(TAG, "muteSession: unmount error on session $sid: ${e.message}")
                }
            } ?: Log.d(TAG, "muteSession: no effect mounted on session $sid")
        }
    }

    // 跟踪上次设置到 native 层的 BGM 模式，避免每帧重复调用 JNI
    private var lastAppliedBgmMode: Int = -1

    // === Session dynamic re-attachment methods ===

    /**
     * Start the session polling thread. Detects target app session changes via
     * `dumpsys media.audio_flinger` and remounts -200dB mute automatically.
     */
    private fun startSessionPolling() {
        if (sessionPollingRunning) return
        sessionPollingRunning = true
        sessionPollThread = Thread({
            Log.d(TAG, "Session polling thread started (interval=${sessionPollIntervalMs}ms)")
            while (sessionPollingRunning) {
                try {
                    pollAndRemountSession()
                } catch (e: Exception) {
                    Log.e(TAG, "Session poll error: ${e.message}")
                }
                try {
                    Thread.sleep(sessionPollIntervalMs)
                } catch (_: InterruptedException) {
                    break
                }
            }
            Log.d(TAG, "Session polling thread exited")
        }, "SessionPollThread")
        sessionPollThread!!.start()
    }

    private fun stopSessionPolling() {
        sessionPollingRunning = false
        sessionPollThread?.interrupt()
        sessionPollThread?.join(1000)
        sessionPollThread = null
        currentMutedSession = -1
    }

    /**
     * One poll cycle: find target playback session via AudioManager API (no root),
     * fall back to dumpsys (root-only), remount mute if changed.
     */
    private fun pollAndRemountSession() {
        // Priority 1: AudioManager.getActivePlaybackConfigurations() + reflection (no root)
        var sessionId = findTargetSessionFromAudioManager()
        // Priority 2: dumpsys media.audio_flinger via su (root-only, precise UID match)
        if (sessionId <= 0 && targetUid >= 0) {
            sessionId = findTargetSessionFromDumpsys(targetUid)
        }
        if (sessionId <= 0) {
            Log.d(TAG, "Session poll: no active session found (targetUid=$targetUid)")
            return
        }

        if (sessionId != currentMutedSession) {
            Log.d(TAG, "Session poll: session changed $currentMutedSession -> $sessionId (targetUid=$targetUid), remounting mute")
            // Release old session mute
            if (currentMutedSession > 0) {
                muteSession(currentMutedSession, false)
            }
            // Mount mute on new session
            muteSession(sessionId, true)
            currentMutedSession = sessionId
        }
    }

    /**
     * Find an active playback session via AudioManager.getActivePlaybackConfigurations()
     * (API 26+, no root required). Uses reflection to read the hidden mSessionId field.
     * Excludes this app's own AudioTrack session and non-MEDIA/GAME/UNKNOWN usages.
     *
     * @return session id (>0) if found, -1 otherwise
     */
    private fun findTargetSessionFromAudioManager(): Int {
        return try {
            val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val configs = audioManager.activePlaybackConfigurations
            val ownSessionId = try { audioTrack?.audioSessionId ?: -1 } catch (_: Exception) { -1 }
            Log.d(TAG, "findTargetSession(AudioManager): configs=${configs?.size ?: "null"} ownSession=$ownSessionId")
            if (configs.isNullOrEmpty()) return -1
            var foundSession = -1

            for (config in configs) {
                val usage = config.audioAttributes?.usage ?: -1
                // Reflect hidden mSessionId field (AudioPlaybackConfiguration keeps it but no public getter)
                val sessionId = try {
                    val f = config::class.java.getDeclaredField("mSessionId")
                    f.isAccessible = true
                    f.getInt(config)
                } catch (e: Exception) {
                    Log.d(TAG, "findTargetSession: reflect mSessionId failed: ${e.javaClass.simpleName}")
                    -1
                }
                Log.d(TAG, "findTargetSession: config usage=$usage session=$sessionId (own=$ownSessionId)")
                if (usage != AudioAttributes.USAGE_MEDIA &&
                    usage != AudioAttributes.USAGE_GAME &&
                    usage != AudioAttributes.USAGE_UNKNOWN) continue
                if (sessionId > 0 && sessionId != ownSessionId) {
                    foundSession = sessionId
                }
            }

            if (foundSession > 0) {
                Log.d(TAG, "findTargetSession(AudioManager): found session=$foundSession")
            } else {
                Log.d(TAG, "findTargetSession(AudioManager): no non-self MEDIA session found")
            }
            foundSession
        } catch (e: Exception) {
            Log.d(TAG, "findTargetSession(AudioManager) failed: ${e.javaClass.simpleName}: ${e.message}")
            -1
        }
    }

    /**
     * Auto-detect the target app UID from MediaRouter2 (API 30+, no root).
     * MediaProjection single-app consent does not expose the selected app's UID,
     * so we infer it from the active media route's client package name.
     *
     * @return UID (>=0) if found, -1 otherwise
     */
    private fun findTargetUidFromMediaRouter(): Int {
        return try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return -1
            val mr = getSystemService(Context.MEDIA_ROUTER_SERVICE) as android.media.MediaRouter2
            val routes = mr.routes ?: return -1
            val ownPkg = packageName
            for (route in routes) {
                val pkg = route.clientPackageName ?: continue
                if (pkg == ownPkg) continue
                val uid = try {
                    packageManager.getApplicationInfo(pkg, 0).uid
                } catch (e: Exception) { continue }
                if (uid >= 0) {
                    Log.d(TAG, "findTargetUid(MediaRouter2): pkg=$pkg uid=$uid (routes=${routes.size})")
                    return uid
                }
            }
            -1
        } catch (e: Exception) {
            Log.d(TAG, "findTargetUid(MediaRouter2) failed: ${e.javaClass.simpleName}: ${e.message}")
            -1
        }
    }

    /**
     * Run `dumpsys media.audio_flinger` (via su on rooted devices) and parse
     * the Global session refs to find an active playback session for the target UID.
     *
     * @return session id (>0) if found, -1 otherwise
     */
    private fun findTargetSessionFromDumpsys(targetUid: Int): Int {
        return try {
            // Try dumpsys without su first (userdebug builds may allow app to run dumpsys directly).
            // Fall back to su -c if direct execution fails (rooted devices with Magisk).
            val process = try {
                Runtime.getRuntime().exec(arrayOf("dumpsys", "media.audio_flinger"))
            } catch (e: Exception) {
                Log.d(TAG, "findTargetSession: direct dumpsys failed (${e.javaClass.simpleName}), trying su")
                Runtime.getRuntime().exec(arrayOf("su", "-c", "dumpsys media.audio_flinger"))
            }
            val output = process.inputStream.bufferedReader().use { it.readText() }
            process.waitFor()

            if (output.isBlank()) {
                Log.d(TAG, "findTargetSession: dumpsys output empty (possibly no permission)")
                return -1
            }
            Log.d(TAG, "findTargetSession: dumpsys output ${output.length} chars, first200=[${output.take(200)}]")

            // Parse: look for session entries associated with target UID.
            // Typical formats in media.audio_flinger dump:
            //   "session  1234: pid 5678 uid 12345"
            //   "Normal playback ... session 1234 ... uid 12345"
            // We scan line by line for lines containing both a session id and the target uid.
            val sessionRegex = Regex("""session\s+(\d+)""", RegexOption.IGNORE_CASE)
            val uidRegex = Regex("""uid\s+(\d+)""", RegexOption.IGNORE_CASE)
            var foundSession = -1

            for (line in output.lines()) {
                val uidMatch = uidRegex.find(line)
                if (uidMatch != null && uidMatch.groupValues[1].toIntOrNull() == targetUid) {
                    val sessMatch = sessionRegex.find(line)
                    if (sessMatch != null) {
                        val sid = sessMatch.groupValues[1].toIntOrNull()
                        if (sid != null && sid > 0) {
                            foundSession = sid
                            // Keep scanning — the last matching session is typically the active one
                        }
                    }
                }
            }

            if (foundSession > 0) {
                Log.d(TAG, "findTargetSession: found session=$foundSession for uid=$targetUid")
            }
            foundSession
        } catch (e: Exception) {
            Log.d(TAG, "findTargetSession failed: ${e.javaClass.simpleName}: ${e.message}")
            -1
        }
    }

    // 每帧处理延迟跟踪（纳秒）
    private var totalProcessNanos = 0L
    private var processFrameCount = 0L

    // Debug broadcast receiver for adb-controlled mode/gain/dsp changes
    private val debugReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                ACTION_SET_MODE -> {
                    val mode = intent.getIntExtra(EXTRA_MODE, -1)
                    if (mode in 0..MODE_MDX) {
                        AudioCaptureState.setBgmMode(mode)
                        Log.d(TAG, "DebugReceiver: BGM mode set to $mode")
                    } else {
                        Log.w(TAG, "DebugReceiver: invalid mode $mode")
                    }
                }
                ACTION_SET_GAIN -> {
                    val gain = intent.getFloatExtra(EXTRA_GAIN, -1f)
                    if (gain in 0f..1f) {
                        AudioCaptureState.setBgmGain(gain)
                        Log.d(TAG, "DebugReceiver: BGM gain set to $gain")
                    } else {
                        Log.w(TAG, "DebugReceiver: invalid gain $gain")
                    }
                }
                ACTION_SET_DSP -> {
                    val enabled = intent.getBooleanExtra(EXTRA_DSP_ENABLED, false)
                    AudioCaptureState.setDspEnabled(enabled)
                    Log.d(TAG, "DebugReceiver: DSP enabled=$enabled")
                }
                ACTION_SET_PLAYBACK -> {
                    val enabled = intent.getBooleanExtra(EXTRA_PLAYBACK_ENABLED, false)
                    playbackEnabled = enabled
                    logAudioTrackHealth()
                    if (enabled) {
                        // 恢复 AudioTrack 播放（可能被 idle 检测暂停）
                        try {
                            if (audioTrack?.state == AudioTrack.STATE_INITIALIZED) {
                                audioTrack?.play()
                                playbackPausedForIdle = false
                                Log.d(TAG, "DebugReceiver: AudioTrack play() called (playback enabled)")
                            } else {
                                Log.w(TAG, "DebugReceiver: AudioTrack not initialized, cannot play()")
                            }
                        } catch (e: Exception) {
                            Log.e(TAG, "DebugReceiver: AudioTrack play() failed", e)
                        }
                    } else {
                        try {
                            if (audioTrack?.state == AudioTrack.STATE_INITIALIZED) {
                                audioTrack?.pause()
                                audioTrack?.flush()
                                Log.d(TAG, "DebugReceiver: AudioTrack pause() called (playback disabled)")
                            }
                        } catch (e: Exception) {
                            Log.e(TAG, "DebugReceiver: AudioTrack pause() failed", e)
                        }
                    }
                    Log.d(TAG, "DebugReceiver: AudioTrack playback enabled=$enabled")
                }
                ACTION_MUTE_SESSION -> {
                    val sid = intent.getIntExtra(EXTRA_SESSION_ID, -1)
                    val mute = intent.getBooleanExtra(EXTRA_MUTE_ENABLED, false)
                    muteSession(sid, mute)
                }
                ACTION_SET_TARGET_UID -> {
                    val uid = intent.getIntExtra(EXTRA_TARGET_UID, -1)
                    if (uid >= 0) saveTargetUid(this@AudioCaptureService, uid)
                    Log.d(TAG, "DebugReceiver: target UID set to $uid (applies on next capture start)")
                }
                ACTION_SET_DUMP -> {
                    val enabled = intent.getBooleanExtra(EXTRA_DUMP_ENABLED, false)
                    setDumpEnabled(enabled)
                }
            }
        }
    }

    // Accumulators for per-second RMS/Peak
    private var sumSquares = 0.0
    private var sampleCount = 0L
    private var peakAbs = 0
    private var lastReportTime = 0L

    /**
     * Enable/disable debug data dump. WAV files (PCM16 @48000 stereo) are created under
     * <externalFilesDir>/dump/ with a timestamp; disabled → headers finalized.
     */
    private fun setDumpEnabled(enabled: Boolean) {
        if (enabled == dumpEnabled) {
            Log.d(TAG, "DUMP already ${if (enabled) "enabled" else "disabled"}")
            return
        }
        dumpEnabled = enabled
        if (enabled) {
            try {
                // dump 目录用 internal filesDir：run-as 可读（external filesDir 在 Android 11+ 对 shell 派生进程不可访问，无 root 设备拉取 dump 需要 internal）
                val dir = File(getFilesDir(), "dump").apply { mkdirs() }
                val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
                dumpInFile = RandomAccessFile(File(dir, "input_$stamp.wav"), "rw")
                dumpOutFile = RandomAccessFile(File(dir, "output_$stamp.wav"), "rw")
                writeWavHeader(dumpInFile!!)
                writeWavHeader(dumpOutFile!!)
                // Reset MDX OLA chain so dump input start == processing start.
                // Without this, dump input (captured from enable-time) and mdxInput44
                // (accumulated from init-time) cover different audio spans → PC A/B corr ≈ 0.
                synchronized(mdxOlaLock) {
                    mdxOlaEpoch++  // 丢弃所有在途窗口推理结果
                    mdxOlaInitialized = false
                    mdxInput44.clear()
                    mdxInputOffset = 0
                    mdxInputFrames = 0
                    mdxNextWindowStart = 0
                    mdxIsProcessing = false
                    if (::mdxOlaBuffer.isInitialized) {
                        mdxOlaBuffer.fill(0f)
                        mdxOlaDivider.fill(0f)
                    }
                    mdxOlaWriteFrame = 0
                    mdxOlaPlayFrame = 0
                    // 立即重新初始化（重新前置 TRIM silence），保证 dump 起点 == 处理起点，
                    // 且链路不中断（否则 mdxOlaInitialized=false 会停摆到下一次 resume 才恢复）。
                    initMdxOla()
                }
                Log.d(TAG, "DUMP started: ${dir.absolutePath}/input_$stamp.wav + output_$stamp.wav (MDX OLA chain reset for aligned capture)")
            } catch (e: Exception) {
                Log.e(TAG, "DUMP start failed", e)
                dumpEnabled = false
            }
        } else {
            try {
                finalizeWavHeader(dumpInFile)
                finalizeWavHeader(dumpOutFile)
                dumpInFile?.close()
                dumpOutFile?.close()
                dumpInFile = null
                dumpOutFile = null
                Log.d(TAG, "DUMP stopped & headers finalized")
            } catch (e: Exception) {
                Log.e(TAG, "DUMP stop failed", e)
            }
        }
    }

    /** Write a 44-byte RIFF/WAVE header (PCM16 stereo @ SAMPLE_RATE), then seek past it. */
    private fun writeWavHeader(f: RandomAccessFile) {
        val bb = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        bb.put("RIFF".toByteArray(Charsets.US_ASCII))
        bb.putInt(0) // RIFF size placeholder
        bb.put("WAVEfmt ".toByteArray(Charsets.US_ASCII))
        bb.putInt(16)          // fmt chunk size
        bb.putShort(1)         // PCM
        bb.putShort(2)         // channels
        bb.putInt(SAMPLE_RATE)
        bb.putInt(SAMPLE_RATE * 4) // byte rate
        bb.putShort(4)         // block align
        bb.putShort(16)        // bits per sample
        bb.put("data".toByteArray(Charsets.US_ASCII))
        bb.putInt(0) // data size placeholder
        f.seek(0)
        f.write(bb.array())
        f.seek(44)
    }

    /** Patch RIFF/data sizes into an already-written WAV header. */
    private fun finalizeWavHeader(f: RandomAccessFile?) {
        if (f == null) return
        try {
            val dataSize = (f.length() - 44).toInt()
            val bb = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN)
            bb.putInt(36 + dataSize)
            f.seek(4)
            f.write(bb.array())
            bb.clear()
            bb.putInt(dataSize)
            f.seek(40)
            f.write(bb.array())
        } catch (e: Exception) {
            Log.e(TAG, "DUMP header finalize failed", e)
        }
    }

    /** Convert shorts (PCM16) to little-endian bytes. */
    private fun shortsToLeBytes(s: ShortArray, count: Int): ByteArray {
        val bb = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN)
        for (i in 0 until count) bb.putShort(s[i])
        return bb.array()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val filter = IntentFilter().apply {
            addAction(ACTION_SET_MODE)
            addAction(ACTION_SET_GAIN)
            addAction(ACTION_SET_DSP)
            addAction(ACTION_SET_PLAYBACK)
            addAction(ACTION_MUTE_SESSION)
            addAction(ACTION_SET_TARGET_UID)
            addAction(ACTION_SET_DUMP)
        }
        registerReceiver(debugReceiver, filter, Context.RECEIVER_EXPORTED)
        loadTargetUid(this)  // restore persisted target UID after process death
        Log.d(TAG, "Debug broadcast receiver registered (targetUid=$targetUid)")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
                val resultData = intent.getParcelableExtra<Intent>(EXTRA_RESULT_DATA)
                if (resultData != null) {
                    startForegroundWithNotification()
                    startCapture(resultCode, resultData)
                } else {
                    Log.e(TAG, "START_CAPTURE: resultData is null")
                    AudioCaptureState.setErrorMessage("授权数据为空")
                    stopSelf()
                }
            }
            ACTION_STOP -> {
                stopCapture()
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun startForegroundWithNotification() {
        createNotificationChannel()
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AudioRouteLab")
            .setContentText("正在捕获系统音频...")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        Log.d(TAG, "Foreground service started (mediaProjection type)")
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Audio Capture",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "AudioRouteLab 音频捕获前台服务通知"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    @android.annotation.SuppressLint("NewApi")
    private fun startCapture(resultCode: Int, resultData: Intent) {
        if (isRunning) {
            Log.w(TAG, "Capture already running")
            return
        }

        try {
            // Version check: AudioPlaybackCapture requires API 29+
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                AudioCaptureState.setErrorMessage("AudioPlaybackCapture 需要 Android 10 (API 29+)")
                Log.e(TAG, "AudioPlaybackCapture requires API 29+, current=${Build.VERSION.SDK_INT}")
                stopSelf()
                return
            }

            // 1. Create MediaProjection
            val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            mediaProjection = mpm.getMediaProjection(resultCode, resultData)
            Log.d(TAG, "MediaProjection created")

            // 1b. Auto-detect target UID if not set.
            // MediaProjection single-app consent does NOT expose the selected app's UID;
            // without targetUid, per-session mute (DynamicsProcessing) never activates →
            // original app audio + processed AudioTrack = double sound.
            // Infer from MediaRouter2 active route client package name (API 30+, no root).
            if (targetUid < 0) {
                val detectedUid = findTargetUidFromMediaRouter()
                if (detectedUid >= 0) {
                    saveTargetUid(this, detectedUid)
                    Log.d(TAG, "Auto-detected targetUid=$detectedUid from MediaRouter2 (will limit capture + enable per-session mute)")
                } else {
                    Log.w(TAG, "Could not auto-detect targetUid (MediaRouter2 empty or unavailable); capture=USAGE match, per-session mute will target first non-self active MEDIA playback")
                }
            }

            // 2. Create AudioPlaybackCaptureConfiguration
            // 始终按 usage 匹配所有允许捕获的应用（不按 UID 过滤）。
            // 原因：部分播放器（如 VLC）即使在前台也可能因 ALLOW_CAPTURE_BY_NONE 或
            // MediaRouter2 检测偏差导致 addMatchingUid 后捕获不到（RMS 恒 -96dB）。
            // targetUid 仍保留，仅用于 per-session DynamicsProcessing 静音原声。
            val builder = AudioPlaybackCaptureConfiguration.Builder(mediaProjection!!)
            builder.addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            val config = builder.build()
            Log.d(TAG, "AudioPlaybackCaptureConfiguration created (USAGE_MEDIA/GAME/UNKNOWN, targetUid=$targetUid for mute only)")

            // 3. Calculate buffer size
            val minBufferSize = AudioRecord.getMinBufferSize(
                SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT
            )
            val bufferSize = minBufferSize * BUFFER_MULTIPLIER
            Log.d(TAG, "minBufferSize=$minBufferSize, actual bufferSize=$bufferSize")

            // 4. Create AudioRecord
            val audioFormat = AudioFormat.Builder()
                .setSampleRate(SAMPLE_RATE)
                .setChannelMask(CHANNEL_CONFIG)
                .setEncoding(AUDIO_FORMAT)
                .build()

            audioRecord = AudioRecord.Builder()
                .setAudioPlaybackCaptureConfig(config)
                .setAudioFormat(audioFormat)
                .setBufferSizeInBytes(bufferSize)
                .build()

            Log.d(TAG, "AudioRecord created: sampleRate=${audioRecord!!.sampleRate}, " +
                    "channels=${audioRecord!!.channelCount}, " +
                    "format=${audioRecord!!.audioFormat}, " +
                    "bufferSize=${audioRecord!!.bufferSizeInFrames} frames")

            // 5. Update state with actual config
            AudioCaptureState.setSampleRate(audioRecord!!.sampleRate)
            AudioCaptureState.setChannels(audioRecord!!.channelCount)
            AudioCaptureState.setPcmFormat("PCM_16BIT")
            AudioCaptureState.setBufferSize(bufferSize)

            // 6. Start recording
            audioRecord!!.startRecording()
            isRunning = true
            AudioCaptureState.setCapturing(true)
            AudioCaptureState.setErrorMessage("")
            Log.d(TAG, "AudioRecord.startRecording() — capture started")

            // 6b. Initialize AudioTrack for playback of processed audio
            initAudioTrack()
            startPlaybackThread()

            // 6c. Copy Spleeter ONNX models from assets to internal storage
            copySpleeterModelsIfNeeded()

            // 6d. Copy MDX-Net ONNX model from assets to internal storage
            copyMdxModelsIfNeeded()

            // 6e. Warm up the MDX model in the background. QNN HTP graph
            // finalization takes ~49s on canoe; doing it now (off the capture
            // thread) means mode 5 is ready by the time the source app plays.
            requestMdxInitAsync()

            // 7. Start capture thread
            lastReportTime = System.currentTimeMillis()
            sumSquares = 0.0
            sampleCount = 0
            peakAbs = 0

            captureThread = Thread({ captureLoop() }, "AudioCaptureThread")
            captureThread!!.start()
            Log.d(TAG, "Capture thread started")

            // 7b. Start session polling for dynamic re-attachment (Phase D)
            startSessionPolling()

        } catch (e: Exception) {
            Log.e(TAG, "startCapture failed", e)
            AudioCaptureState.setErrorMessage("启动捕获失败: ${e.message}")
            stopSelf()
        }
    }

    private fun initAudioTrack() {
        try {
            val minBufferSize = AudioTrack.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_OUT_STEREO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            val bufferSize = minBufferSize * 4
            val attrsBuilder = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                // Critical: without this, AudioPlaybackCapture (which matches
                // USAGE_MEDIA across all apps) records OUR OWN output and feeds it
                // back into the capture → the processed audio re-enters the pipeline,
                // gets processed again and echoes / builds up on every round trip.
                attrsBuilder.setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE)
            }
            audioTrack = AudioTrack.Builder()
                .setAudioAttributes(attrsBuilder.build())
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build()
                )
                .setBufferSizeInBytes(bufferSize)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            val at = audioTrack!!
            Log.d(TAG, "AudioTrack built: bufferSize=$bufferSize, minBuffer=$minBufferSize, " +
                    "state=${at.state}, playState=${at.playState}, sessionId=${at.audioSessionId}, " +
                    "bufferFrames=${at.bufferSizeInFrames}, sampleRate=${at.sampleRate}, " +
                    "channelCount=${at.channelCount}, format=${at.audioFormat}")
            if (at.state != AudioTrack.STATE_INITIALIZED) {
                Log.e(TAG, "AudioTrack NOT initialized (state=${at.state}) — playback will be silent")
            }
            at.play()
            totalWrittenFrames = 0
            writeCallCount = 0
            writeErrorCount = 0
            Log.d(TAG, "AudioTrack play() done: playState=${at.playState} (expect ${AudioTrack.PLAYSTATE_PLAYING})")
        } catch (e: Exception) {
            Log.e(TAG, "initAudioTrack failed", e)
        }
    }

    // === AudioTrack diagnostics (P0 debug) ===
    private var totalWrittenFrames = 0L
    private var writeCallCount = 0L
    private var writeErrorCount = 0L

    /**
     * Write PCM to AudioTrack and log the result. Returns frames actually written.
     * Centralised so every playback path gets the same diagnostics.
     */
    private fun writeToTrack(pcm: ShortArray, frames: Int): Int {
        val at = audioTrack
        if (at == null) {
            if (writeErrorCount++ < 5) Log.e(TAG, "writeToTrack: audioTrack == null, dropping $frames frames")
            return 0
        }
        if (at.state != AudioTrack.STATE_INITIALIZED) {
            if (writeErrorCount++ < 5) Log.e(TAG, "writeToTrack: state=${at.state} != STATE_INITIALIZED, dropping $frames frames")
            return 0
        }
        if (at.playState != AudioTrack.PLAYSTATE_PLAYING) {
            // Auto-recover: an underrun or idle-pause left the track stopped.
            Log.w(TAG, "writeToTrack: playState=${at.playState} != PLAYING — calling play() to recover")
            try {
                at.play()
            } catch (e: Exception) {
                Log.e(TAG, "writeToTrack: play() recovery failed", e)
            }
        }
        val shortCount = frames * 2
        val written = at.write(pcm, 0, shortCount)
        when {
            written < 0 -> {
                if (writeErrorCount++ < 10) {
                    Log.e(TAG, "writeToTrack: write() error=$written (${writeErrName(written)}), requested=$shortCount shorts")
                }
            }
            written != shortCount -> {
                if (writeErrorCount++ < 10) {
                    Log.w(TAG, "writeToTrack: partial write $written/$shortCount shorts")
                }
                totalWrittenFrames += written / 2
            }
            else -> {
                totalWrittenFrames += frames
                writeCallCount++
            }
        }
        return if (written > 0) written / 2 else 0
    }

    private fun writeErrName(code: Int): String = when (code) {
        AudioTrack.ERROR_INVALID_OPERATION -> "ERROR_INVALID_OPERATION"
        AudioTrack.ERROR_BAD_VALUE -> "ERROR_BAD_VALUE"
        AudioTrack.ERROR_DEAD_OBJECT -> "ERROR_DEAD_OBJECT"
        else -> "UNKNOWN($code)"
    }

    /** Periodic AudioTrack health dump — the primary P0 diagnostic. */
    private fun logAudioTrackHealth() {
        val at = audioTrack
        if (at == null) {
            Log.w(TAG, "AT-HEALTH: audioTrack == null")
            return
        }
        val underruns = try { at.underrunCount } catch (e: Exception) { -1 }
        val queued = synchronized(playbackLock) { playbackQueueFrames }
        Log.d(TAG, "AT-HEALTH: state=${at.state} playState=${at.playState} session=${at.audioSessionId} " +
                "head=${at.playbackHeadPosition} underruns=$underruns " +
                "enabled=$playbackEnabled idlePaused=$playbackPausedForIdle " +
                "writes=$writeCallCount frames=$totalWrittenFrames errs=$writeErrorCount " +
                "queued=$queued dropped=$playbackDroppedFrames")
    }

    // --- Playback thread: drains the queue in small slices ---

    private fun startPlaybackThread() {
        if (playbackThreadRunning) return
        playbackThreadRunning = true
        playbackThread = Thread({ playbackLoop() }, "AudioPlaybackThread").also { it.start() }
        Log.d(TAG, "Playback thread started (slice=${PLAYBACK_SLICE_FRAMES} frames)")
    }

    private fun stopPlaybackThread() {
        playbackThreadRunning = false
        synchronized(playbackLock) { playbackLock.notifyAll() }
        playbackThread?.join(500)
        playbackThread = null
        clearPlaybackQueue()
    }

    private fun clearPlaybackQueue() {
        synchronized(playbackLock) {
            playbackQueue.clear()
            playbackQueueFrames = 0
            playbackHeadOffset = 0
        }
    }

    /** Hand processed PCM to the playback thread. Never blocks on AudioTrack. */
    private fun enqueuePlayback(pcm: ShortArray) {
        if (!playbackEnabled) return
        val frames = pcm.size / 2
        if (frames <= 0) return
        synchronized(playbackLock) {
            // Back-pressure: if the consumer falls behind (e.g. inference hiccup),
            // drop the OLDEST audio so end-to-end latency stays bounded instead of
            // growing without limit.
            while (playbackQueueFrames + frames > MAX_QUEUE_FRAMES && playbackQueue.isNotEmpty()) {
                val old = playbackQueue.removeFirst()
                val oldFrames = old.size / 2 - playbackHeadOffset
                playbackQueueFrames -= oldFrames
                playbackDroppedFrames += oldFrames
                playbackHeadOffset = 0
            }
            playbackQueue.addLast(pcm)
            playbackQueueFrames += frames
            playbackLock.notifyAll()
        }
    }

    /** Pull up to PLAYBACK_SLICE_FRAMES out of the queue. Returns frames filled. */
    private fun takePlaybackSlice(dst: ShortArray): Int {
        synchronized(playbackLock) {
            val want = dst.size / 2
            var filled = 0
            while (filled < want && playbackQueue.isNotEmpty()) {
                val head = playbackQueue.first()
                val headFrames = head.size / 2
                val take = minOf(headFrames - playbackHeadOffset, want - filled)
                System.arraycopy(head, playbackHeadOffset * 2, dst, filled * 2, take * 2)
                filled += take
                playbackHeadOffset += take
                playbackQueueFrames -= take
                if (playbackHeadOffset >= headFrames) {
                    playbackQueue.removeFirst()
                    playbackHeadOffset = 0
                }
            }
            return filled
        }
    }

    private fun playbackLoop() {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
        val slice = ShortArray(PLAYBACK_SLICE_FRAMES * 2)
        while (playbackThreadRunning) {
            val frames = takePlaybackSlice(slice)
            if (frames > 0) {
                writeToTrack(slice, frames)
            } else {
                // Queue empty (source silent, or MDX still warming up): wait briefly.
                synchronized(playbackLock) {
                    if (playbackQueueFrames <= 0 && playbackThreadRunning) {
                        try {
                            playbackLock.wait(20)
                        } catch (_: InterruptedException) {
                            return
                        }
                    }
                }
            }
        }
        Log.d(TAG, "Playback thread exited")
    }

    /**
     * Kick off MDX model init off the capture thread. Safe to call repeatedly.
     */
    private fun requestMdxInitAsync() {
        if (mdxProcessor != null || mdxInitInFlight) return
        mdxInitInFlight = true
        mdxScope.launch {
            try {
                val t0 = System.currentTimeMillis()
                val p = ensureMdxProcessor()
                Log.i(TAG, "MDX async init done in ${System.currentTimeMillis() - t0} ms (ok=${p != null})")
            } catch (e: Exception) {
                Log.e(TAG, "MDX async init failed", e)
            } finally {
                mdxInitInFlight = false
            }
        }
    }

    /**
     * Source app paused/stopped: pause AudioTrack and flush OLA/input state so no
     * stale buffered audio keeps playing (the "paused but still making sound" bug).
     */
    private fun pausePlaybackForIdle() {
        try {
            if (audioTrack?.state == AudioTrack.STATE_INITIALIZED) {
                audioTrack?.pause()
                audioTrack?.flush()
            }
            playbackPausedForIdle = true
            // Drop queued-but-unplayed audio so nothing stale keeps coming out after
            // the source app pauses.
            clearPlaybackQueue()
            // Drop all pending MDX input/OLA state; will be re-initialized on resume
            synchronized(mdxOlaLock) {
                mdxOlaEpoch++  // 丢弃在途窗口推理结果
                mdxOlaInitialized = false
                if (::mdxOlaBuffer.isInitialized) mdxOlaBuffer.fill(0f)
                if (::mdxOlaDivider.isInitialized) mdxOlaDivider.fill(0f)
                mdxOlaWriteFrame = 0
                mdxOlaPlayFrame = 0
                mdxInput44.clear()
                mdxInputFrames = 0
                mdxNextWindowStart = 0
                mdxInputOffset = 0
                mdxIsProcessing = false
            }
            Log.d(TAG, "playback paused + state flushed (idle source)")
        } catch (e: Exception) {
            Log.e(TAG, "pausePlaybackForIdle error", e)
        }
    }

    /**
     * Source resumed: restart AudioTrack and re-initialize OLA chain.
     */
    private fun resumePlaybackAfterIdle() {
        try {
            if (audioTrack?.state == AudioTrack.STATE_INITIALIZED) {
                audioTrack?.play()
            }
            playbackPausedForIdle = false
            if (mdxProcessor != null) {
                initMdxOla()
            }
            Log.d(TAG, "playback resumed (source active)")
        } catch (e: Exception) {
            Log.e(TAG, "resumePlaybackAfterIdle error", e)
        }
    }

    /**
     * Copy MDX-Net ONNX model from assets to internal storage.
     */
    private fun copyMdxModelsIfNeeded() {
        val modelDir = File(filesDir, "mdx_models")
        if (!modelDir.exists()) modelDir.mkdirs()

        val modelName = "UVR-MDX-NET-Inst_HQ_3_b1_seg32_static.onnx"
        val outFile = File(modelDir, modelName)
        // 长度校验：assets 与已解包文件长度一致才跳过（seg128/seg32 模型长度不同，保证升级后重新解包）
        // 注意：APK asset 默认压缩，openFd 会抛异常——改用流式统计长度；读不到长度时保守重拷。
        val assetLen = try {
            assets.open("mdx/$modelName").use { input ->
                var n = 0L
                val buf = ByteArray(1 shl 14)
                while (true) {
                    val r = input.read(buf)
                    if (r < 0) break
                    n += r
                }
                n
            }
        } catch (e: Exception) { -1L }
        if (outFile.exists() && outFile.length() > 0 && assetLen > 0 && outFile.length() == assetLen) {
            Log.d(TAG, "MDX model already exists: $modelName (${outFile.length()} bytes)")
            return
        }
        try {
            assets.open("mdx/$modelName").use { input ->
                outFile.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            Log.d(TAG, "Copied MDX model: $modelName (${outFile.length()} bytes)")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to copy MDX model: $modelName", e)
        }
    }

    /**
     * Copy QNN runtime libs from assets to internal storage (self-contained, no root/permissive).
     * assets 下 qnn 目录的全部 .so → filesDir 下 qnn 目录，逐个校验存在+长度一致则跳过；
     * vendor.qti.hardware.dsp@1.0.so 在 assets 中保持原名（不经 installd，无 '@' 剥版本问题），解包到数据域后 dlopen 正常。
     */
    private fun copyQnnLibsIfNeeded() {
        val qnnDir = File(filesDir, "qnn")
        if (!qnnDir.exists()) qnnDir.mkdirs()
        val names = try {
            assets.list("qnn")?.filter { it.endsWith(".so") } ?: emptyList()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to list assets/qnn", e)
            emptyList()
        }
        if (names.isEmpty()) {
            Log.w(TAG, "assets/qnn empty — QNN libs not packaged")
            return
        }
        var copied = 0
        var skipped = 0
        for (name in names) {
            val outFile = File(qnnDir, name)
            val assetLen = try { assets.openFd("qnn/$name").length } catch (e: Exception) { -1L }
            if (outFile.exists() && (assetLen < 0 || outFile.length() == assetLen)) {
                skipped++
                continue
            }
            try {
                assets.open("qnn/$name").use { input ->
                    outFile.outputStream().use { output -> input.copyTo(output) }
                }
                copied++
            } catch (e: Exception) {
                Log.e(TAG, "Failed to copy QNN lib: $name", e)
            }
        }
        Log.d(TAG, "QNN libs ready: dir=$qnnDir total=${names.size} copied=$copied skipped=$skipped")
    }

    /**
     * Lazily initialize MDXProcessor when mode 5 is first selected.
     */
    private fun ensureMdxProcessor(): MDXProcessor? {
        if (mdxProcessor != null) return mdxProcessor
        try {
            copyQnnLibsIfNeeded()
            val modelDir = File(filesDir, "mdx_models")
            val modelPath = File(modelDir, "UVR-MDX-NET-Inst_HQ_3_b1_seg32_static.onnx").absolutePath
            if (!File(modelPath).exists()) {
                Log.e(TAG, "MDX model file not found: $modelPath")
                return null
            }
            val qnnDir = File(filesDir, "qnn")
            mdxProcessor = MDXProcessor(
                modelPath, numThreads = 4,
                nativeLibDir = applicationInfo.nativeLibraryDir,
                qnnLibDir = if (qnnDir.exists() && qnnDir.list()?.isNotEmpty() == true) qnnDir.absolutePath else null
            )
            mdxBuffer = ShortArray(MDX_CHUNK_FRAMES * CHANNEL_COUNT)
            mdxBufferPos = 0
            initMdxOla()
            Log.d(TAG, "MDXProcessor initialized successfully")
            return mdxProcessor
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize MDXProcessor", e)
            return null
        }
    }

    /**
     * Initialize MDX OLA buffers, Hanning window, and prepend trim silence.
     * Must be called once before processing.
     */
    private fun initMdxOla() {
        // May now be called from the async MDX-init coroutine while the capture
        // thread reads mdxOlaInitialized — guard with the same lock.
        synchronized(mdxOlaLock) {
        if (mdxOlaInitialized) return
        // Symmetric Hanning window (matches np.hanning(N): 0.5*(1-cos(2πn/(N-1))))
        mdxOlaWindow = FloatArray(MDXProcessor.CHUNK_SAMPLES) { i ->
            (0.5 * (1.0 - Math.cos(2.0 * Math.PI * i / (MDXProcessor.CHUNK_SAMPLES - 1)))).toFloat()
        }
        mdxOlaBuffer = FloatArray(mdxOlaSizeFrames * 2)
        mdxOlaDivider = FloatArray(mdxOlaSizeFrames * 2)
        mdxOlaWriteFrame = 0
        mdxOlaPlayFrame = MDXProcessor.TRIM  // skip leading trim padding region
        // Prepend TRIM silence to input (matches PC script: mixture = [zeros(trim), mix, ...])
        mdxInput44.clear()
        for (i in 0 until MDXProcessor.TRIM * 2) {
            mdxInput44.add(0f)
        }
        mdxInputOffset = 0
        mdxInputFrames = MDXProcessor.TRIM
        mdxNextWindowStart = 0
        mdxIsProcessing = false
        mdxOlaInitialized = true
        Log.d(TAG, "MDX OLA initialized: chunk=${MDXProcessor.CHUNK_SAMPLES} step=${MDXProcessor.STEP_SAMPLES} trim=${MDXProcessor.TRIM} olaSize=$mdxOlaSizeFrames")
        }
    }

    /**
     * Add a processed window's output to the OLA buffer with Hanning weighting.
     * Caller must hold mdxOlaLock.
     *
     * @param processed interleaved stereo float @44100, length CHUNK_SAMPLES*2
     * @param writeFrame buffer-relative start frame for this window's output
     */
    private fun addToMdxOlaLocked(processed: FloatArray, writeFrame: Int) {
        val baseIdx = writeFrame * 2
        val chunk = MDXProcessor.CHUNK_SAMPLES
        for (i in 0 until chunk) {
            val w = mdxOlaWindow[i]
            val idx = baseIdx + i * 2
            mdxOlaBuffer[idx]     += processed[i * 2]     * w
            mdxOlaBuffer[idx + 1] += processed[i * 2 + 1] * w
            mdxOlaDivider[idx]     += w
            mdxOlaDivider[idx + 1] += w
        }
        // 内容真实写入后再推进 writeFrame：playable=writeFrame-TRIM 只在数据就绪后增长，
        // playAvailableMdxOutput 不会消费空段（修复 dump 输出断续/丢真实内容）
        mdxOlaWriteFrame += MDXProcessor.STEP_SAMPLES
    }

    /**
     * Compact OLA buffer: move unplayed data to front, reset positions.
     * Caller must hold mdxOlaLock.
     */
    private fun compactMdxOlaLocked() {
        val playIdx = mdxOlaPlayFrame * 2
        val writeIdx = mdxOlaWriteFrame * 2
        val keepCount = writeIdx - playIdx
        if (keepCount > 0) {
            System.arraycopy(mdxOlaBuffer, playIdx, mdxOlaBuffer, 0, keepCount)
            System.arraycopy(mdxOlaDivider, playIdx, mdxOlaDivider, 0, keepCount)
        }
        // Clear remainder
        for (i in keepCount until mdxOlaSizeFrames * 2) {
            mdxOlaBuffer[i] = 0f
            mdxOlaDivider[i] = 0f
        }
        val shift = mdxOlaPlayFrame
        mdxOlaWriteFrame -= shift
        mdxOlaPlayFrame = 0
        Log.d(TAG, "MDX OLA compacted: shift=$shift write=${mdxOlaWriteFrame} play=${mdxOlaPlayFrame}")
    }

    /**
     * Extract newly-available OLA output, normalize, resample to 48000, play via AudioTrack.
     * Called from captureLoop (main capture thread).
     */
    private fun playAvailableMdxOutput() {
        val output44: FloatArray
        synchronized(mdxOlaLock) {
            // Playable up to (writeFrame - TRIM): conservative, ensures window edges excluded
            val playable = mdxOlaWriteFrame - MDXProcessor.TRIM
            if (playable <= mdxOlaPlayFrame) return
            val playCount = playable - mdxOlaPlayFrame
            val offset = mdxOlaPlayFrame * 2
            output44 = FloatArray(playCount * 2)
            for (i in 0 until playCount * 2) {
                val div = mdxOlaDivider[offset + i]
                output44[i] = if (div > 1e-8f) mdxOlaBuffer[offset + i] / div else 0f
                mdxOlaBuffer[offset + i] = 0f
                mdxOlaDivider[offset + i] = 0f
            }
            mdxOlaPlayFrame = playable
            // Compact if write pointer is getting close to end
            if (mdxOlaWriteFrame + MDXProcessor.CHUNK_SAMPLES > mdxOlaSizeFrames) {
                compactMdxOlaLocked()
            }
            var oacc = 0.0; var cnt3 = 0
            for (i in output44.indices step 211) { oacc += kotlin.math.abs(output44[i].toDouble()); cnt3++ }
            Log.d(TAG, "MDX OLA out44: rms~${kotlin.math.sqrt(oacc / cnt3)} n=${output44.size} first=${output44[0]},${output44[1]},${output44[2]},${output44[3]}")
        }

        // Resample 44100 → 48000 (per channel) and play
        val n44 = output44.size / 2
        val left44 = FloatArray(n44)
        val right44 = FloatArray(n44)
        for (i in 0 until n44) {
            left44[i] = output44[i * 2]
            right44[i] = output44[i * 2 + 1]
        }
        val left48 = Resampler.resampleMono(left44, MDXProcessor.MODEL_SR, SAMPLE_RATE)
        val right48 = Resampler.resampleMono(right44, MDXProcessor.MODEL_SR, SAMPLE_RATE)
        val n48 = minOf(left48.size, right48.size)
        val pcm16 = ShortArray(n48 * 2)
        for (i in 0 until n48) {
            val l = (left48[i] * 32767f).coerceIn(-32768f, 32767f).toInt().toShort()
            val r = (right48[i] * 32767f).coerceIn(-32768f, 32767f).toInt().toShort()
            pcm16[i * 2] = l
            pcm16[i * 2 + 1] = r
        }
        // Debug dump: processed output PCM16 @48000 (played audio)
        if (dumpEnabled) {
            try {
                dumpOutFile?.write(shortsToLeBytes(pcm16, pcm16.size))
            } catch (e: Exception) {
                Log.e(TAG, "DUMP out write failed", e)
            }
        }
        if (playbackEnabled) {
            enqueuePlayback(pcm16)
        }
        Log.d(TAG, "MDX OLA played: frames=$n48 (44.1k→48k), olaPlay=$mdxOlaPlayFrame olaWrite=$mdxOlaWriteFrame")
    }

    /**
     * Process accumulated audio with MDX-Net and play back via AudioTrack.
     */
    private fun processMdxChunk(chunk: ShortArray, bgmGain: Float) {
        mdxScope.launch {
            val processor = ensureMdxProcessor() ?: return@launch
            try {
                val processed = withContext(Dispatchers.Default) {
                    processor.process(chunk, bgmGain)
                }
                if (playbackEnabled) {
                    enqueuePlayback(processed)
                }
                Log.d(TAG, "MDX chunk processed: in=${chunk.size} out=${processed.size}")
            } catch (e: Exception) {
                Log.e(TAG, "MDX chunk processing failed", e)
            }
        }
    }

    /**
     * Copy Spleeter ONNX models from assets to internal storage.
     * ONNX Runtime requires a file path, not an asset file descriptor.
     */
    private fun copySpleeterModelsIfNeeded() {
        val modelDir = File(filesDir, "spleeter_models")
        if (!modelDir.exists()) modelDir.mkdirs()

        val modelFiles = listOf("vocals.fp16.onnx", "accompaniment.fp16.onnx")
        for (name in modelFiles) {
            val outFile = File(modelDir, name)
            if (outFile.exists() && outFile.length() > 0) {
                Log.d(TAG, "Spleeter model already exists: $name (${outFile.length()} bytes)")
                continue
            }
            try {
                assets.open("spleeter/$name").use { input ->
                    outFile.outputStream().use { output ->
                        input.copyTo(output)
                    }
                }
                Log.d(TAG, "Copied Spleeter model: $name (${outFile.length()} bytes)")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to copy Spleeter model: $name", e)
            }
        }
    }

    /**
     * Lazily initialize SpleeterProcessor when mode 4 is first selected.
     */
    private fun ensureSpleeterProcessor(): SpleeterProcessor? {
        if (spleeterProcessor != null) return spleeterProcessor
        try {
            val modelDir = File(filesDir, "spleeter_models")
            val vocalsPath = File(modelDir, "vocals.fp16.onnx").absolutePath
            val accompPath = File(modelDir, "accompaniment.fp16.onnx").absolutePath

            if (!File(vocalsPath).exists() || !File(accompPath).exists()) {
                Log.e(TAG, "Spleeter model files not found. Vocals: ${File(vocalsPath).exists()}, Accompaniment: ${File(accompPath).exists()}")
                return null
            }

            spleeterProcessor = SpleeterProcessor(vocalsPath, accompPath, numThreads = 2)
            spleeterBuffer = ShortArray(SPLEETER_CHUNK_FRAMES * CHANNEL_COUNT)
            spleeterBufferPos = 0
            Log.d(TAG, "SpleeterProcessor initialized successfully")
            return spleeterProcessor
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize SpleeterProcessor", e)
            return null
        }
    }

    /**
     * Process accumulated audio with Spleeter and play back via AudioTrack.
     * Runs on a background coroutine to avoid blocking the capture thread.
     */
    private fun processSpleeterChunk(chunk: ShortArray, bgmGain: Float) {
        spleeterScope.launch {
            val processor = ensureSpleeterProcessor() ?: return@launch
            try {
                val processed = withContext(Dispatchers.Default) {
                    processor.process(chunk, bgmGain)
                }
                // Play back processed audio via AudioTrack
                if (playbackEnabled) {
                    enqueuePlayback(processed)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Spleeter chunk processing failed", e)
            }
        }
    }

    private fun captureLoop() {
        val record = audioRecord ?: return
        val sr = record.sampleRate
        val ch = record.channelCount
        val frameSize = ch * 2 // PCM16 = 2 bytes per sample
        // Read ~50ms of audio per iteration
        val framesPerRead = sr / 20
        val buffer = ShortArray(framesPerRead * ch)
        val outputBuffer = ShortArray(framesPerRead * ch)

        Log.d(TAG, "captureLoop: framesPerRead=$framesPerRead, bufferSize=${buffer.size}")

        while (isRunning) {
            val shortsRead = record.read(buffer, 0, buffer.size)
            var framesRead = 0
            var sourceSilent = false
            if (shortsRead <= 0) {
                if (shortsRead == AudioRecord.ERROR_INVALID_OPERATION) {
                    Log.e(TAG, "AudioRecord.read: ERROR_INVALID_OPERATION")
                } else if (shortsRead == AudioRecord.ERROR_BAD_VALUE) {
                    Log.e(TAG, "AudioRecord.read: ERROR_BAD_VALUE")
                }
                sourceSilent = true
            } else {
                framesRead = shortsRead / ch
                // RMS-based idle detection: read() keeps returning full zero buffers
                // while the captured app is paused/stopped, so read<=0 alone is not enough.
                var sum = 0L
                val n = framesRead * ch
                for (i in 0 until n) {
                    val s = buffer[i].toInt()
                    sum += s * s.toLong()
                }
                val rmsDb = if (framesRead > 0) {
                    20.0 * kotlin.math.log10(kotlin.math.sqrt(sum.toDouble() / n) / 32767.0)
                } else -96.0
                sourceSilent = rmsDb < -80.0
            }

            if (sourceSilent && !dumpEnabled) {
                // Idle detection: source app paused/stopped -> stop playing stale audio
                idleReadCount++
                if (idleReadCount == IDLE_STOP_THRESHOLD) {
                    Log.d(TAG, "captureLoop: source idle (${idleReadCount} reads, RMS<80dB), pausing playback")
                    pausePlaybackForIdle()
                }
                continue
            }
            if (sourceSilent) {
                // dump 模式：静音不触发 idle 暂停/重置（否则 OLA 时间线回绕产生重复窗口，
                // dump input/output 与 PC 全长对比错乱）；静音帧照常走下方 append（零数据），
                // 保持时间线连续。
                idleReadCount = 0
            } else {
                if (idleReadCount >= IDLE_STOP_THRESHOLD) {
                    Log.d(TAG, "captureLoop: source resumed, restarting playback")
                    resumePlaybackAfterIdle()
                }
                idleReadCount = 0
            }


            // --- RMS / Peak accumulation ---
            for (i in 0 until shortsRead) {
                val sample = buffer[i].toInt()
                val sq = sample * sample
                sumSquares += sq
                val abs = if (sample < 0) -sample else sample
                if (abs > peakAbs) peakAbs = abs
            }
            sampleCount += shortsRead

            // --- DSP processing (optional) ---
            if (AudioCaptureState.dspEnabled.value) {
                val currentMode = AudioCaptureState.bgmMode.value

                if (currentMode == MODE_SPLEETER) {
                    // --- Spleeter neural separation (mode 4) ---
                    // Accumulate audio into 2-second chunk, then process with ONNX
                    val buf = spleeterBuffer
                    if (buf != null) {
                        val copyLen = minOf(shortsRead, buf.size - spleeterBufferPos)
                        System.arraycopy(buffer, 0, buf, spleeterBufferPos, copyLen)
                        spleeterBufferPos += copyLen
                        if (spleeterBufferPos >= buf.size) {
                            // Chunk full — process with Spleeter
                            val chunk = buf.copyOf()
                            val gain = AudioCaptureState.bgmGain.value
                            processSpleeterChunk(chunk, gain)
                            spleeterBufferPos = 0
                        }
                    } else {
                        // Buffer not initialized yet — trigger lazy init
                        ensureSpleeterProcessor()
                    }
                } else if (currentMode == MODE_MDX) {
                    // --- MDX-Net neural separation (mode 5) — sliding window OLA @44100 ---
                    // Auto-enable AudioTrack playback for "processed audio only" output
                    if (!playbackEnabled) {
                        playbackEnabled = true
                        Log.d(TAG, "MDX mode: auto-enabled AudioTrack playback")
                    }

                    // Ensure processor + OLA initialized.
                    // Must NOT be called inline: QNN graph finalization blocks for
                    // ~49s, which would stall AudioRecord.read() and drop ~49s of audio.
                    if (mdxProcessor == null) {
                        requestMdxInitAsync()
                    }

                    if (mdxOlaInitialized) {
                        // 0. Debug dump: captured input PCM16 @48000 (for PC-side A/B verification)
                        if (dumpEnabled) {
                            try {
                                dumpInFile?.write(shortsToLeBytes(buffer, shortsRead))
                            } catch (e: Exception) {
                                Log.e(TAG, "DUMP in write failed", e)
                            }
                        }
                        // 1. Convert captured PCM16 @48000 → float, resample L/R → 44100, append
                        val framesRead = shortsRead / ch
                        val left48 = FloatArray(framesRead)
                        val right48 = FloatArray(framesRead)
                        for (i in 0 until framesRead) {
                            left48[i] = buffer[i * 2].toFloat() / 32768f
                            right48[i] = buffer[i * 2 + 1].toFloat() / 32768f
                        }
                        val left44 = Resampler.resampleMono(left48, SAMPLE_RATE, MDXProcessor.MODEL_SR)
                        val right44 = Resampler.resampleMono(right48, SAMPLE_RATE, MDXProcessor.MODEL_SR)
                        val n44 = minOf(left44.size, right44.size)

                        var windowToProcess: FloatArray? = null
                        var windowWriteFrame = 0
                        var windowEpoch = 0
                        synchronized(mdxOlaLock) {
                            // Append resampled audio to input buffer
                            for (i in 0 until n44) {
                                mdxInput44.add(left44[i])
                                mdxInput44.add(right44[i])
                            }
                            mdxInputFrames += n44

                            // 2. Check if we can process a new window (one at a time)
                            if (!mdxIsProcessing &&
                                mdxInputFrames - mdxNextWindowStart >= MDXProcessor.CHUNK_SAMPLES
                            ) {
                                val absStart = mdxNextWindowStart
                                mdxNextWindowStart += MDXProcessor.STEP_SAMPLES
                                mdxIsProcessing = true

                                // Extract window from input buffer
                                val bufOffset = absStart - mdxInputOffset
                                windowToProcess = FloatArray(MDXProcessor.CHUNK_SAMPLES * 2)
                                val w = windowToProcess!!
                                val base = bufOffset * 2
                                for (i in 0 until MDXProcessor.CHUNK_SAMPLES * 2) {
                                    w[i] = mdxInput44[base + i]
                                }
                                // OLA write position (buffer-relative)
                                // 注意：mdxOlaWriteFrame 不在提交时推进（推理完成 addToMdxOlaLocked 内推进），
                                // 否则 playAvailableMdxOutput 会在推理完成前消费 playable 段（写入全 0 并推进
                                // playFrame），导致每窗真实输出被跳过、dump output 出现段-静音-段结构。
                                windowWriteFrame = mdxOlaWriteFrame
                                windowEpoch = mdxOlaEpoch  // 记录当前 OLA 世代（锁内读取保证可见性）

                                // Trim input: discard samples before next window start
                                val discardFrames = mdxNextWindowStart - mdxInputOffset
                                if (discardFrames > 0) {
                                    mdxInput44.subList(0, discardFrames * 2).clear()
                                    mdxInputOffset = mdxNextWindowStart
                                }
                                Log.d(TAG, "MDX window start: abs=$absStart writeFrame=$windowWriteFrame inputFrames=$mdxInputFrames bufSize=${mdxInput44.size / 2}")
                            }
                        }

                        // 3. Process window asynchronously (ONNX inference is CPU-heavy)
                        val win = windowToProcess
                        if (win != null) {
                            val wFrame = windowWriteFrame
                            val epoch = windowEpoch
                            val gain = AudioCaptureState.bgmGain.value
                            mdxScope.launch {
                                try {
                                    val processor = mdxProcessor ?: return@launch
                                    val processed = withContext(Dispatchers.Default) {
                                        processor.processStereoFloat44100(win, gain)
                                    }
                                    synchronized(mdxOlaLock) {
                                        if (epoch == mdxOlaEpoch) {
                                            addToMdxOlaLocked(processed, wFrame)
                                            Log.d(TAG, "MDX window processed: writeFrame=$wFrame len=${processed.size / 2}")
                                        } else {
                                            // 链路已被重置（SET_DUMP / idle pause）——丢弃过期窗口结果
                                            Log.d(TAG, "MDX stale window dropped (epoch $epoch != $mdxOlaEpoch)")
                                        }
                                    }
                                } catch (e: Exception) {
                                    Log.e(TAG, "MDX window processing failed", e)
                                } finally {
                                    mdxIsProcessing = false
                                }
                            }
                        }

                        // 4. Play any newly-available OLA output
                        playAvailableMdxOutput()
                    }
                } else {
                    // --- Native DSP modes (0-3) ---
                    // 仅在模式变化时调用 JNI setMode，避免每帧开销
                    if (currentMode != lastAppliedBgmMode) {
                        nativeProcessor.setMode(currentMode)
                        lastAppliedBgmMode = currentMode
                        Log.d(TAG, "captureLoop: BGM mode changed to $currentMode")
                    }
                    val gain = AudioCaptureState.bgmGain.value
                    val t0 = System.nanoTime()
                    val ret = nativeProcessor.processFrame(
                        buffer, outputBuffer, framesRead, sr, ch, gain
                    )
                    val t1 = System.nanoTime()
                    totalProcessNanos += (t1 - t0)
                    processFrameCount++
                    if (ret != 0) {
                        Log.e(TAG, "native processFrame returned error: $ret")
                    }
                    // AudioTrack playback for native modes (if enabled)
                    if (playbackEnabled) {
                        // outputBuffer is reused every iteration — must copy before
                        // handing it to the playback thread.
                        enqueuePlayback(outputBuffer.copyOf(shortsRead))
                    }
                }
            }

            // --- Report once per second ---
            val now = System.currentTimeMillis()
            if (now - lastReportTime >= 1000) {
                reportLevels()
                lastReportTime = now
                sumSquares = 0.0
                sampleCount = 0
                peakAbs = 0
                // Report average DSP processing latency
                if (processFrameCount > 0) {
                    val avgLatencyUs = totalProcessNanos / processFrameCount / 1000
                    val mode = AudioCaptureState.bgmMode.value
                    Log.d(TAG, "DSP latency: mode=$mode avg=${avgLatencyUs}us frames=$processFrameCount")
                    totalProcessNanos = 0
                    processFrameCount = 0
                }
            }
        }

        Log.d(TAG, "captureLoop exited")
    }

    private fun reportLevels() {
        if (sampleCount <= 0) {
            AudioCaptureState.setRmsDb(-96.0)
            AudioCaptureState.setPeakDb(-96.0)
            Log.d(TAG, "Level report: no samples (silence)")
            return
        }

        // RMS: 20 * log10(sqrt(mean(samples^2)) / 32768)
        val rms = sqrt(sumSquares / sampleCount)
        val rmsDb = if (rms > 0) 20.0 * log10(rms / 32768.0) else -96.0
        // Peak: 20 * log10(max(abs(samples)) / 32768)
        val peakDb = if (peakAbs > 0) 20.0 * log10(peakAbs.toDouble() / 32768.0) else -96.0

        val rmsClamped = if (rmsDb < -96.0) -96.0 else rmsDb
        val peakClamped = if (peakDb < -96.0) -96.0 else peakDb

        AudioCaptureState.setRmsDb(rmsClamped)
        AudioCaptureState.setPeakDb(peakClamped)

        Log.d(TAG, "Level report: RMS=%.1f dB, Peak=%.1f dB, samples=%d".format(rmsClamped, peakClamped, sampleCount))
        logAudioTrackHealth()
    }

    private fun stopCapture() {
        isRunning = false
        captureThread?.join(2000)
        captureThread = null
        stopPlaybackThread()

        try {
            audioRecord?.stop()
        } catch (e: Exception) {
            Log.e(TAG, "AudioRecord.stop() exception", e)
        }
        try {
            audioRecord?.release()
        } catch (e: Exception) {
            Log.e(TAG, "AudioRecord.release() exception", e)
        }
        audioRecord = null

        try {
            mediaProjection?.stop()
        } catch (e: Exception) {
            Log.e(TAG, "MediaProjection.stop() exception", e)
        }
        mediaProjection = null

        nativeProcessor.release()
        lastAppliedBgmMode = -1

        // Release AudioTrack
        try {
            audioTrack?.stop()
            audioTrack?.release()
        } catch (e: Exception) {
            Log.e(TAG, "AudioTrack release exception", e)
        }
        audioTrack = null

        // Release SpleeterProcessor
        try {
            spleeterProcessor?.release()
        } catch (e: Exception) {
            Log.e(TAG, "SpleeterProcessor release exception", e)
        }
        spleeterProcessor = null
        spleeterBuffer = null
        spleeterBufferPos = 0

        // Release MDXProcessor + OLA state
        try {
            mdxProcessor?.close()
        } catch (e: Exception) {
            Log.e(TAG, "MDXProcessor release exception", e)
        }
        mdxProcessor = null
        mdxBuffer = null
        mdxBufferPos = 0
        mdxInput44.clear()
        mdxInputOffset = 0
        mdxInputFrames = 0
        mdxNextWindowStart = 0
        mdxIsProcessing = false
        mdxOlaInitialized = false
        mdxOlaWriteFrame = 0
        mdxOlaPlayFrame = 0

        // Release all mute effects
        for ((sid, dp) in muteEffects) {
            try {
                dp.enabled = false
                dp.release()
            } catch (e: Exception) {
                Log.e(TAG, "release mute effect sid=$sid: ${e.message}")
            }
        }
        muteEffects.clear()

        // Stop session polling
        stopSessionPolling()

        AudioCaptureState.setCapturing(false)
        AudioCaptureState.reset()
        Log.d(TAG, "Capture stopped and resources released")
    }

    override fun onDestroy() {
        try { unregisterReceiver(debugReceiver) } catch (e: Exception) { Log.w(TAG, "unregisterReceiver: ${e.message}") }
        stopCapture()
        super.onDestroy()
    }
}
