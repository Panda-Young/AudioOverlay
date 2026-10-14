package com.example.audiooverlay

import kotlin.math.floor

/**
 * Simple linear-interpolation resampler for mono float32 audio.
 * Used to convert between 48000 Hz (capture) and 44100 Hz (Spleeter).
 */
object Resampler {

    /**
     * Resample mono audio from sourceRate to targetRate.
     *
     * @param input     mono float32 samples
     * @param sourceRate input sample rate
     * @param targetRate output sample rate
     * @return resampled mono float32 samples
     */
    fun resampleMono(input: FloatArray, sourceRate: Int, targetRate: Int): FloatArray {
        if (sourceRate == targetRate) return input.copyOf()
        val ratio = sourceRate.toDouble() / targetRate.toDouble()
        val outLength = (input.size / ratio).toInt()
        val output = FloatArray(outLength)
        for (i in 0 until outLength) {
            val srcPos = i * ratio
            val idx0 = floor(srcPos).toInt()
            val idx1 = idx0 + 1
            val frac = (srcPos - idx0).toFloat()
            val v0 = if (idx0 < input.size) input[idx0] else 0f
            val v1 = if (idx1 < input.size) input[idx1] else 0f
            output[i] = v0 * (1f - frac) + v1 * frac
        }
        return output
    }

    /**
     * Convert interleaved stereo PCM16 to mono float32.
     */
    fun stereoPcm16ToMonoFloat(input: ShortArray): FloatArray {
        val numFrames = input.size / 2
        val output = FloatArray(numFrames)
        for (i in 0 until numFrames) {
            val l = input[i * 2].toFloat() / 32768f
            val r = input[i * 2 + 1].toFloat() / 32768f
            output[i] = (l + r) * 0.5f
        }
        return output
    }

    /**
     * Convert mono float32 to interleaved stereo PCM16 (duplicate to both channels).
     */
    fun monoFloatToStereoPcm16(input: FloatArray): ShortArray {
        val output = ShortArray(input.size * 2)
        for (i in input.indices) {
            val v = (input[i] * 32767f).coerceIn(-32768f, 32767f).toInt().toShort()
            output[i * 2] = v
            output[i * 2 + 1] = v
        }
        return output
    }
}
