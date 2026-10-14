package com.example.audiooverlay

import kotlin.math.sqrt

/**
 * Short-Time Fourier Transform (STFT) and inverse STFT.
 *
 * Spleeter 2-stem parameters:
 *   N_FFT = 1024, hop_length = 256, window = Hann, sample_rate = 44100
 *   freq_bins = N_FFT/2 + 1 = 513
 *
 * Uses 75% overlap (hop = N_FFT/4) for perfect reconstruction with
 * constant-overlap-add (COLA) Hann window.
 */
class STFT(
    val nFft: Int = 1024,
    val hopLength: Int = 256
) {
    private val fft = FFT(nFft)
    private val window = FFT.hannWindow(nFft)
    private val freqBins = nFft / 2 + 1

    // Reusable buffers to avoid GC pressure
    private val realBuf = FloatArray(nFft)
    private val imagBuf = FloatArray(nFft)
    private val ifftReal = FloatArray(nFft)
    private val ifftImag = FloatArray(nFft)

    /**
     * Compute STFT of a mono signal.
     *
     * @param signal mono float32 audio samples (range -1..1)
     * @return Pair of (magnitude, phase) each of shape [numFrames][freqBins]
     */
    fun stft(signal: FloatArray): Pair<Array<FloatArray>, Array<FloatArray>> {
        val numFrames = (signal.size - nFft) / hopLength + 1
        val magnitude = Array(numFrames) { FloatArray(freqBins) }
        val phase = Array(numFrames) { FloatArray(freqBins) }

        for (f in 0 until numFrames) {
            val start = f * hopLength
            // Windowed frame
            for (i in 0 until nFft) {
                val idx = start + i
                realBuf[i] = if (idx < signal.size) signal[idx] * window[i] else 0f
                imagBuf[i] = 0f
            }
            fft.fft(realBuf, imagBuf)
            // Extract magnitude and phase for positive frequencies
            for (k in 0 until freqBins) {
                val re = realBuf[k]
                val im = imagBuf[k]
                magnitude[f][k] = sqrt(re * re + im * im)
                phase[f][k] = kotlin.math.atan2(im, re)
            }
        }
        return Pair(magnitude, phase)
    }

    /**
     * Compute inverse STFT from magnitude and phase.
     *
     * @param magnitude [numFrames][freqBins]
     * @param phase     [numFrames][freqBins]
     * @param outLength expected output length (signal.size from original stft)
     * @return reconstructed mono float32 audio
     */
    fun istft(
        magnitude: Array<FloatArray>,
        phase: Array<FloatArray>,
        outLength: Int
    ): FloatArray {
        val output = FloatArray(outLength)
        val windowSum = FloatArray(outLength)
        val numFrames = magnitude.size

        for (f in 0 until numFrames) {
            // Reconstruct complex spectrum
            for (k in 0 until freqBins) {
                val mag = magnitude[f][k]
                val ph = phase[f][k]
                ifftReal[k] = mag * kotlin.math.cos(ph)
                ifftImag[k] = mag * kotlin.math.sin(ph)
            }
            // Hermitian symmetry for negative frequencies
            for (k in freqBins until nFft) {
                val mirror = nFft - k
                ifftReal[k] = ifftReal[mirror]
                ifftImag[k] = -ifftImag[mirror]
            }
            // Inverse FFT
            fft.ifft(ifftReal, ifftImag)
            // Overlap-add with window
            val start = f * hopLength
            for (i in 0 until nFft) {
                val idx = start + i
                if (idx < outLength) {
                    output[idx] += ifftReal[i] * window[i]
                    windowSum[idx] += window[i] * window[i]
                }
            }
        }
        // Normalize by window sum (avoid division by zero)
        for (i in 0 until outLength) {
            if (windowSum[i] > 1e-8f) {
                output[i] /= windowSum[i]
            }
        }
        return output
    }

    /**
     * Apply a soft mask to a complex spectrogram (magnitude + phase).
     * The mask is applied to magnitude only; phase is preserved.
     *
     * @param magnitude input magnitude [numFrames][freqBins]
     * @param phase     input phase [numFrames][freqBins]
     * @param mask      soft mask [numFrames][freqBins], values 0..1
     * @return new magnitude array with mask applied
     */
    fun applyMask(
        magnitude: Array<FloatArray>,
        mask: Array<FloatArray>
    ): Array<FloatArray> {
        val numFrames = magnitude.size
        val result = Array(numFrames) { FloatArray(freqBins) }
        for (f in 0 until numFrames) {
            for (k in 0 until freqBins) {
                result[f][k] = magnitude[f][k] * mask[f][k]
            }
        }
        return result
    }
}
