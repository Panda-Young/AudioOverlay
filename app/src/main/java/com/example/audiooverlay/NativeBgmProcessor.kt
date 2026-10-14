package com.example.audiooverlay

/**
 * JNI bridge to native BgmProcessor (C++).
 *
 * The native library is loaded statically. All methods map 1:1 to
 * JNI functions in native-lib.cpp.
 */
class NativeBgmProcessor {

    companion object {
        init {
            System.loadLibrary("bgmprocessor")
        }
    }

    /**
     * Process one frame of interleaved PCM16 data.
     *
     * @param input      input PCM samples (interleaved stereo)
     * @param output     output PCM samples (interleaved stereo), must be same size as input
     * @param numFrames  number of frames (each frame = channels samples)
     * @param sampleRate sample rate in Hz
     * @param channels   channel count
     * @param bgmGain    BGM gain 0.0~1.0 (0=eliminate, 1=original)
     * @return 0 on success, non-zero error code
     */
    external fun processFrame(
        input: ShortArray,
        output: ShortArray,
        numFrames: Int,
        sampleRate: Int,
        channels: Int,
        bgmGain: Float
    ): Int

    external fun setBgmGain(gain: Float)

    external fun getBgmGain(): Float

    /**
     * 设置 BGM 处理模式。
     * 0 = GLOBAL_GAIN（全局增益 baseline）
     * 1 = CENTER_CHANNEL（中置声道消除）
     * 2 = ADAPTIVE_SPECTRAL（自适应谱滤波）
     * 3 = NEURAL_SEPARATION（神经分离，当前不可用，降级为全局增益）
     */
    external fun setMode(mode: Int)

    /** 获取当前 BGM 处理模式 */
    external fun getMode(): Int

    external fun release()
}
