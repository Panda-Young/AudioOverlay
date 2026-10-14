package com.example.audiooverlay

import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Pure-Kotlin Complex FFT (Cooley-Tukey radix-2).
 * Used for Spleeter STFT/iSTFT. No external dependencies.
 */
class FFT(private val size: Int) {
    private val cosTable = FloatArray(size / 2)
    private val sinTable = FloatArray(size / 2)

    init {
        require(size and (size - 1) == 0) { "FFT size must be power of 2, got $size" }
        for (i in 0 until size / 2) {
            val angle = -2.0 * Math.PI * i / size
            cosTable[i] = cos(angle).toFloat()
            sinTable[i] = sin(angle).toFloat()
        }
    }

    /**
     * In-place FFT. real and imag arrays must both have length == size.
     */
    fun fft(real: FloatArray, imag: FloatArray) {
        require(real.size == size && imag.size == size) { "Array size must be $size" }
        // Bit-reversal permutation
        var j = 0
        for (i in 1 until size) {
            var bit = size shr 1
            while (j and bit != 0) {
                j = j xor bit
                bit = bit shr 1
            }
            j = j xor bit
            if (i < j) {
                val tmpR = real[i]; real[i] = real[j]; real[j] = tmpR
                val tmpI = imag[i]; imag[i] = imag[j]; imag[j] = tmpI
            }
        }
        // Cooley-Tukey
        var len = 2
        while (len <= size) {
            val halfLen = len shr 1
            val step = size / len
            var i = 0
            while (i < size) {
                var k = 0
                for (l in i until i + halfLen) {
                    val c = cosTable[k]
                    val s = sinTable[k]
                    val r1 = real[l + halfLen]
                    val i1 = imag[l + halfLen]
                    val tr = r1 * c - i1 * s
                    val ti = r1 * s + i1 * c
                    real[l + halfLen] = real[l] - tr
                    imag[l + halfLen] = imag[l] - ti
                    real[l] += tr
                    imag[l] += ti
                    k += step
                }
                i += len
            }
            len = len shl 1
        }
    }

    /**
     * In-place inverse FFT.
     */
    fun ifft(real: FloatArray, imag: FloatArray) {
        // Conjugate
        for (i in 0 until size) imag[i] = -imag[i]
        fft(real, imag)
        // Conjugate and scale
        val scale = 1.0f / size
        for (i in 0 until size) {
            real[i] *= scale
            imag[i] = -imag[i] * scale
        }
    }

    companion object {
        /** Hann window of given size. */
        fun hannWindow(size: Int): FloatArray {
            val w = FloatArray(size)
            for (i in 0 until size) {
                w[i] = (0.5 * (1.0 - cos(2.0 * Math.PI * i / (size - 1)))).toFloat()
            }
            return w
        }
    }
}
