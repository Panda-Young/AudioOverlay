package com.example.audiooverlay

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.OrtSession.SessionOptions
import android.util.Log
import java.io.File
import java.nio.FloatBuffer

/**
 * MDX-Net 音乐/人声分离处理器（UVR-MDX-Net-Inst_HQ_3_b1_seg32_static.onnx）。
 *
 * 处理链（参数与 audio-separator MDXSeparator 一致，seg=32 版本：PC 复刻 vocals corr=0.9948 vs seg256 基准）：
 *   stereo PCM16 @48000
 *     → stereo float @48000
 *     → 重采样 44100（Resampler 线性插值）
 *     → JNI FFTW STFT（n_fft=6144 / hop=1024 / hann periodic / center=True）→ [1,4,3072,32]
 *     → ONNX 推理（b1 固定 batch，输入输出 [1,4,3072,32]，4 通道=[L实,L虚,R实,R虚]）
 *     → 输出频谱 × compensate(1.022) → JNI FFTW ISTFT → accompaniment stereo @44100
 *     → vocals = mix − accompaniment；output = vocals + accompaniment × bgmGain
 *     → 重采样 48000 → stereo PCM16
 *
 * 输入 chunk 固定为 31744 采样（0.72s @44100），模型时间维固定 32 帧（低延迟版本，单窗推理比 seg128 快约 4 倍）。
 */
class MDXProcessor(
    private val modelPath: String,
    private val numThreads: Int = 4,
    private val nativeLibDir: String? = null,
    private val qnnLibDir: String? = null
) : AutoCloseable {

    companion object {
        private const val TAG = "MDXProcessor"

        const val N_FFT = 6144
        const val HOP = 1024
        const val DIM_F = 3072
        const val MODEL_SR = 44100
        const val COMPENSATE = 1.022f
        // chunk_size = hop * (segment_size - 1) = 1024 * 31 = 31744（seg32 模型；低延迟版本）
        const val CHUNK_SAMPLES = 31744
        // 模型固定时间维（seg32 静态导出）
        const val MODEL_TIME = 32
        // trim = n_fft / 2 = 3072（STFT center=True 前后填充）
        const val TRIM = 3072
        // gen_size = chunk_size - 2 * trim = 28672
        const val GEN_SIZE = 28672
        // step = (1 - overlap) * chunk_size = 0.75 * 31744 = 25600
        const val STEP_SAMPLES = 25600

        init {
            System.loadLibrary("bgmprocessor")
        }
    }

    // JNI（mdstft.cpp, FFTW3 实现）
    private external fun mdxStft(stereo44100: FloatArray, nSamples: Int): FloatArray
    private external fun mdxIstft(spec4: FloatArray, nFrames: Int, outLen: Int): FloatArray
    private external fun nativeSetAdspLibraryPath(path: String)

    private var env: OrtEnvironment? = null
    private var session: OrtSession? = null
    private val inputName: String
    private val outputName: String

    // 复用 Resampler 的 mono 线性插值
    private val resampler = Resampler

    init {
        // 预加载 QNN 运行时（filesDir 自包含方案：assets/qnn → filesDir/qnn 解包后从 App 数据域加载，
        // 无需 root/permissive；null 时回退 canoe 调试用设备目录 /data/local/tmp/qnn_app）。
        // 目录组装：QNN SDK 2.48.40 的 libQnn* + libcdsprpc.so 依赖链
        // （libhidlbase/libutils/libcutils/libvndksupport 等非 public 库，保持原始库名/SONAME；
        //  libdl_android/libvndksupport 为自制 stub；vendor.qti.hardware.dsp@1.0.so 含 '@' 仅 assets 原样可用）。
        // 拓扑序：基础 C++/utils 链 → HIDL 链 → libcdsprpc → QNN 运行时。
        // 采用迭代式加载：每轮加载所有依赖已就绪的库，循环直至闭合（避免手工排错依赖顺序）。
        // 注意：libQnnHtpV81.so / libQnnHtpV81Skel.so / libQnnHtpV81CalculatorStub.so 不预加载——
        // 由 QNN 运行时经 ADSP_LIBRARY_PATH 从目录加载；预加载 Skel 到 AP 进程会干扰 CDSP 侧加载，
        // 曾导致 QNN partitioner 的 NHWC 节点未选中（graph invalid）而回退 CPU。
        val qnnDir = qnnLibDir ?: "/data/local/tmp/qnn_app"
        val qnnLibs = listOf(
            "libc++.so", "libbase.so", "libvmmem.so", "libdmabufheap.so",
            "libcutils.so", "libvndksupport.so", "libutils.so", "libdl_android.so",
            "libhidlbase.so", "libhardware.so", "android.hardware.common-V2-ndk.so",
            "vendor.qti.hardware.dsp@1.0.so", "vendor.qti.hardware.dsp-V1-ndk.so",
            "libcdsprpc.so", "libQnnSystem.so", "libQnnHtp.so", "libQnnHtpV81Stub.so",
        )
        try {
            val loaded = mutableSetOf<String>()
            var progressed = true
            var rounds = 0
            while (progressed && rounds < 30 && loaded.size < qnnLibs.size) {
                progressed = false
                rounds++
                for (l in qnnLibs) {
                    if (l in loaded) continue
                    try {
                        System.load("$qnnDir/$l")
                        loaded += l
                        progressed = true
                    } catch (e: Throwable) {
                        // 依赖未就绪，下一轮重试；首轮打印真实失败原因
                        if (rounds == 1) Log.w(TAG, "QNN lib load $l failed: ${e.message}")
                    }
                }
            }
            if (loaded.size < qnnLibs.size) {
                Log.w(TAG, "QNN libs preload partial ${loaded.size}/${qnnLibs.size}, missing=${qnnLibs - loaded} (fallback CPU)")
            }
        } catch (t: Throwable) {
            Log.w(TAG, "QNN libs preload failed (fallback CPU): ${t.message}")
        }

        try {
            env = OrtEnvironment.getEnvironment()
            var qnnAttempted = false
            var opts = SessionOptions()
            opts.setIntraOpNumThreads(numThreads)
            // QNN 尝试：ALL_OPT（实测 seg128 静态 ALL_OPT 与 NO_OPT 推理均 0.11-0.25s，ALL_OPT 更稳）
            opts.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)

            // QNN HTP 加速（dump 对比期间曾置 false 做 CPU EP 对照；对照已闭环 corr=1.0，恢复 HTP 主线）
            val enableQnn = true

            // QNN HTP (SM8850, Hexagon v81) — 配置与 unified_bench ONNX_QNN_HTP 一致
            // （canoe 实测 113.7ms/块, 48.7x; HTP O3 + burst + fp16 + VTCM 8MB）
            // soc_model/htp_arch 不显式指定：官方 qnn AAR 的 ORT 对 htp_arch 解析与
            // unified_bench 构建不同（"81" 报 Invalid HTP architecture），由 QNN 自动探测
            if (enableQnn) {
                try {
                    // ADSP_LIBRARY_PATH 指向 QNN 运行时所在目录（filesDir/qnn 或设备目录，
                    // 含 libQnnHtpV81Skel.so 等全套；App 数据域 own 目录无需 permissive）
                    nativeSetAdspLibraryPath(qnnDir)
                    opts.addQnn(
                        mapOf(
                            "backend_type" to "htp",
                            "soc_model" to "0",   // 0 = auto-probe（unified_bench 同）
                            "htp_arch" to "81",  // SM8850 Hexagon v81（1.26.0 官方源码 ParseHtpArchitecture 接受 bare "81"）
                            "htp_performance_mode" to "burst",
                            "htp_graph_finalization_optimization_mode" to "3",
                            "enable_htp_fp16_precision" to "0",
                            "vtcm_mb" to "8",
                            "rpc_control_latency" to "100",
                            "qnn_context_priority" to "HIGH"
                        )
                    )
                    // QNN 未激活时立即失败（宁可显式报错也不静默回退 CPU）
                    opts.addConfigEntry("session.disable_cpu_ep_fallback", "1")
                    qnnAttempted = true
                    Log.i(TAG, "QNN HTP EP enabled (burst/O3/fp32-vtcm8)")
                } catch (e: Exception) {
                    Log.w(TAG, "QNN EP options rejected, CPU only: ${e.message}")
                }
            }

            session = try {
                env?.createSession(modelPath, opts)
            } catch (e: Exception) {
                if (qnnAttempted) {
                    // QNN session 创建失败（库缺失/驱动不可用）→ 回退纯 CPU，保证链路可用
                    Log.w(TAG, "QNN session create failed: ${e.message} -> retry CPU-only")
                    opts = SessionOptions()
                    opts.setIntraOpNumThreads(numThreads)
                    opts.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                    env?.createSession(modelPath, opts)
                } else {
                    throw e
                }
            }
            inputName = session?.inputNames?.firstOrNull() ?: "x"
            outputName = session?.outputNames?.firstOrNull() ?: "y"
            Log.d(TAG, "MDXProcessor initialized: model=$modelPath in=$inputName out=$outputName")
        } catch (e: Exception) {
            Log.e(TAG, "MDXProcessor init failed", e)
            throw e
        }
    }

    /**
     * 处理一个 chunk（stereo PCM16 @48000），返回处理后的 stereo PCM16 @48000。
     * bgmGain: 0.0=只保留人声/对白，1.0=原声。
     */
    fun process(pcm16Stereo48000: ShortArray, bgmGain: Float): ShortArray {
        // 1. stereo PCM16 48000 → stereo float 48000
        val nFrames48 = pcm16Stereo48000.size / 2
        val left48 = FloatArray(nFrames48)
        val right48 = FloatArray(nFrames48)
        for (i in 0 until nFrames48) {
            left48[i] = pcm16Stereo48000[i * 2].toFloat() / 32768f
            right48[i] = pcm16Stereo48000[i * 2 + 1].toFloat() / 32768f
        }

        // 2. 重采样 48000 → 44100（每声道）
        val left44 = resampler.resampleMono(left48, 48000, MODEL_SR)
        val right44 = resampler.resampleMono(right48, 48000, MODEL_SR)
        val nFrames44 = left44.size
        // 与模型期望的 CHUNK_SAMPLES 对齐（截断/补零）
        val copyLen = minOf(nFrames44, CHUNK_SAMPLES)
        val mix44Interleaved = FloatArray(CHUNK_SAMPLES * 2)
        for (i in 0 until copyLen) {
            mix44Interleaved[i * 2] = left44[i]
            mix44Interleaved[i * 2 + 1] = right44[i]
        }

        // 3-7. 核心处理（STFT→推理→ISTFT→重混），复用 processStereoFloat44100
        val mixed44 = processStereoFloat44100(mix44Interleaved, bgmGain)

        // 8. 重采样 44100 → 48000（每声道）+ 转 PCM16
        val leftOut = FloatArray(CHUNK_SAMPLES)
        val rightOut = FloatArray(CHUNK_SAMPLES)
        for (i in 0 until CHUNK_SAMPLES) {
            leftOut[i] = mixed44[i * 2]
            rightOut[i] = mixed44[i * 2 + 1]
        }
        val left48o = resampler.resampleMono(leftOut, MODEL_SR, 48000)
        val right48o = resampler.resampleMono(rightOut, MODEL_SR, 48000)
        val outFrames = minOf(left48o.size, right48o.size)
        val result = ShortArray(outFrames * 2)
        for (i in 0 until outFrames) {
            val l = (left48o[i] * 32767f).coerceIn(-32768f, 32767f).toInt().toShort()
            val r = (right48o[i] * 32767f).coerceIn(-32768f, 32767f).toInt().toShort()
            result[i * 2] = l
            result[i * 2 + 1] = r
        }
        return result
    }

    /**
     * 原生 44100 float 处理入口（供滑动窗口 OLA 使用，不做重采样/PCM16 转换）。
     *
     * 输入：interleaved stereo float32 @44100，长度必须为 CHUNK_SAMPLES*2 = 63488
     * 输出：interleaved stereo float32 @44100，长度同为 CHUNK_SAMPLES*2
     *
     * 处理链：STFT → ONNX 推理 → compensate × 输出 → ISTFT → accompaniment
     *         → vocals = mix - accomp → output = vocals + accomp × bgmGain
     */
    fun processStereoFloat44100(stereoFloat44100: FloatArray, bgmGain: Float): FloatArray {
        val sess = session ?: throw IllegalStateException("MDX session not initialized")
        val ortEnv = env ?: throw IllegalStateException("ORT env not initialized")

        val nFrames = CHUNK_SAMPLES  // 31744

        // 1. 保存 mix（用于 vocals = mix - accomp）
        val mixL = FloatArray(nFrames)
        val mixR = FloatArray(nFrames)
        for (i in 0 until nFrames) {
            mixL[i] = stereoFloat44100[i * 2]
            mixR[i] = stereoFloat44100[i * 2 + 1]
        }

        // 2. STFT → [4, 3072, 32] 平铺
        val spec4 = mdxStft(stereoFloat44100, nFrames)
        val timeFrames = nFrames / HOP + 1  // = 32

        // 3. ONNX 推理
        val inputShape = longArrayOf(1, 4, DIM_F.toLong(), timeFrames.toLong())
        val out4 = try {
            OnnxTensor.createTensor(ortEnv, FloatBuffer.wrap(spec4), inputShape).use { tensor ->
                val result = sess.run(mapOf(inputName to tensor))
                try {
                    val outputTensor = result.get(outputName).orElseThrow {
                        RuntimeException("No output for $outputName")
                    } as OnnxTensor
                    outputTensor.floatBuffer.array()
                } finally {
                    result.close()
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "MDX inference failed", e)
            throw e
        }

        // 4. compensate × 输出频谱
        if (COMPENSATE != 1f) {
            for (i in out4.indices) out4[i] *= COMPENSATE
        }

        // 5. ISTFT → accompaniment stereo @44100
        val accomp44 = mdxIstft(out4, timeFrames, nFrames)

        // 6. vocals = mix - accomp；重混 output = vocals + accomp × bgmGain
        val output = FloatArray(nFrames * 2)
        for (i in 0 until nFrames) {
            val accompL = accomp44[i * 2]
            val accompR = accomp44[i * 2 + 1]
            val vocL = mixL[i] - accompL
            val vocR = mixR[i] - accompR
            output[i * 2]     = vocL + accompL * bgmGain
            output[i * 2 + 1] = vocR + accompR * bgmGain
        }
        return output
    }

    override fun close() {
        try {
            session?.close()
            session = null
        } catch (e: Exception) {
            Log.w(TAG, "close session: ${e.message}")
        }
    }
}
