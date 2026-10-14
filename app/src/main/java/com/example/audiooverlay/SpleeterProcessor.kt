package com.example.audiooverlay

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.util.Log
import java.nio.FloatBuffer
import kotlin.math.cos
import kotlin.math.sin

/**
 * Spleeter-based BGM/vocal separator (sherpa-onnx fp16 model format).
 *
 * Model format (verified by ONNX inspection):
 *   Input:  x [2, num_splits, 512, 1024] float32
 *           dim0 = real/imag (complex spectrogram)
 *           dim1 = num_splits (chunks of 1024 STFT frames)
 *           dim2 = 512 frequency bins (N_FFT/2, excluding Nyquist)
 *           dim3 = 1024 time frames per split
 *   Output: y [2, num_splits, 512, 1024] float32 (masked complex spectrogram)
 *
 * Architecture: U-Net + Sigmoid mask + Mul (output = input * mask)
 *
 * Pipeline:
 *   1. stereo PCM16 -> mono float32
 *   2. resample 48000 -> 44100
 *   3. STFT (N_FFT=1024, hop=256, Hann) -> complex spec [512, n_frames]
 *   4. reshape -> [2, num_splits, 512, 1024]
 *   5. ONNX inference (vocals + accompaniment models)
 *   6. reshape back -> [512, total_frames]
 *   7. iSTFT (Nyquist bin = 0) -> vocals/accompaniment waveforms
 *   8. mix: output = vocals * 1.0 + accompaniment * bgmGain
 *   9. resample 44100 -> 48000
 *  10. mono float32 -> stereo PCM16
 *
 * NOTE: Latency intentionally ignored per user request (functional verification).
 */
class SpleeterProcessor(
    private val vocalsModelPath: String,
    private val accompanimentModelPath: String,
    private val numThreads: Int = 2
) {
    companion object {
        private const val TAG = "SpleeterProcessor"
        const val SPLEETER_SAMPLE_RATE = 44100
        const val N_FFT = 1024
        const val HOP_LENGTH = 256
        const val FREQ_BINS = 512  // N_FFT/2, excluding Nyquist
        const val FRAMES_PER_SPLIT = 1024
    }

    private val ortEnv: OrtEnvironment = OrtEnvironment.getEnvironment()
    private val vocalsSession: OrtSession
    private val accompanimentSession: OrtSession
    private val fft = FFT(N_FFT)
    private val hannWindow = FloatArray(N_FFT)

    // Reusable buffers
    private val fftReal = FloatArray(N_FFT)
    private val fftImag = FloatArray(N_FFT)
    private val ifftReal = FloatArray(N_FFT)
    private val ifftImag = FloatArray(N_FFT)

    init {
        // Hann window
        for (i in 0 until N_FFT) {
            hannWindow[i] = (0.5 * (1.0 - cos(2.0 * Math.PI * i / (N_FFT - 1)))).toFloat()
        }

        val opts = OrtSession.SessionOptions()
        opts.setIntraOpNumThreads(numThreads)
        opts.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)

        vocalsSession = ortEnv.createSession(vocalsModelPath, opts)
        accompanimentSession = ortEnv.createSession(accompanimentModelPath, opts)

        val vIn = vocalsSession.inputNames.first()
        val vOut = vocalsSession.outputNames.first()
        Log.i(TAG, "Vocals model: input=$vIn output=$vOut")
        Log.i(TAG, "Vocals input shape: ${vocalsSession.inputInfo[vIn]?.info}")
        Log.i(TAG, "Accompaniment model: input=${accompanimentSession.inputNames.first()} output=${accompanimentSession.outputNames.first()}")
    }

    /**
     * Process a chunk of interleaved stereo PCM16 audio at 48000 Hz.
     *
     * @param inputPcm16 interleaved stereo PCM16 (48000 Hz)
     * @param bgmGain   BGM (accompaniment) gain 0.0~1.0 (0=eliminate BGM, 1=original)
     * @return processed interleaved stereo PCM16 (48000 Hz), same length as input
     */
    fun process(inputPcm16: ShortArray, bgmGain: Float): ShortArray {
        val startTime = System.currentTimeMillis()

        // Step 1: stereo PCM16 -> mono float32
        val monoFloat = Resampler.stereoPcm16ToMonoFloat(inputPcm16)

        // Step 2: resample 48000 -> 44100
        val mono44100 = Resampler.resampleMono(monoFloat, 48000, SPLEETER_SAMPLE_RATE)

        // Step 3: STFT -> complex spectrogram [512, n_frames]
        val (specReal, specImag, nFrames) = stft(mono44100)

        // Step 4: reshape -> [2, num_splits, 512, 1024] and run inference
        val vocalsReal = runModel(vocalsSession, specReal, specImag, nFrames)
        val accompReal = runModel(accompanimentSession, specReal, specImag, nFrames)

        // Step 5: iSTFT -> vocals and accompaniment waveforms
        val vocalsWave = istft(vocalsReal.first, vocalsReal.second, nFrames, mono44100.size)
        val accompWave = istft(accompReal.first, accompReal.second, nFrames, mono44100.size)

        // Step 6: mix: output = vocals * 1.0 + accompaniment * bgmGain
        val mixed = FloatArray(mono44100.size)
        for (i in mixed.indices) {
            mixed[i] = vocalsWave[i] + accompWave[i] * bgmGain
        }

        // Step 7: resample 44100 -> 48000
        val mixed48000 = Resampler.resampleMono(mixed, SPLEETER_SAMPLE_RATE, 48000)

        // Step 8: mono float32 -> stereo PCM16
        val outputPcm16 = Resampler.monoFloatToStereoPcm16(mixed48000)

        val elapsed = System.currentTimeMillis() - startTime
        val durationSec = inputPcm16.size / 2 / 48000.0
        val rtf = elapsed / 1000.0 / durationSec
        Log.i(TAG, "Processed %.2fs audio in %dms, RTF=%.2f, bgmGain=%.2f, nFrames=%d".format(
            durationSec, elapsed, rtf, bgmGain, nFrames))

        return outputPcm16
    }

    /**
     * STFT: mono float32 -> complex spectrogram [512, n_frames]
     * Returns Pair(real[512][n_frames], imag[512][n_frames])
     */
    private fun stft(signal: FloatArray): Triple<Array<FloatArray>, Array<FloatArray>, Int> {
        val nFrames = (signal.size - N_FFT) / HOP_LENGTH + 1
        val real = Array(FREQ_BINS) { FloatArray(nFrames) }
        val imag = Array(FREQ_BINS) { FloatArray(nFrames) }

        for (f in 0 until nFrames) {
            val start = f * HOP_LENGTH
            // Windowed frame
            for (i in 0 until N_FFT) {
                val idx = start + i
                fftReal[i] = if (idx < signal.size) signal[idx] * hannWindow[i] else 0f
                fftImag[i] = 0f
            }
            fft.fft(fftReal, fftImag)
            // Take first 512 freq bins (exclude Nyquist bin at index 512)
            for (k in 0 until FREQ_BINS) {
                real[k][f] = fftReal[k]
                imag[k][f] = fftImag[k]
            }
        }
        return Triple(real, imag, nFrames)
    }

    /**
     * iSTFT: complex spectrogram [512, n_frames] -> mono float32
     * Nyquist bin (512) is set to 0.
     */
    private fun istft(
        real: Array<FloatArray>,
        imag: Array<FloatArray>,
        nFrames: Int,
        outLength: Int
    ): FloatArray {
        val output = FloatArray(outLength)
        val windowSum = FloatArray(outLength)

        for (f in 0 until nFrames) {
            // Reconstruct full N_FFT spectrum
            for (k in 0 until FREQ_BINS) {
                ifftReal[k] = real[k][f]
                ifftImag[k] = imag[k][f]
            }
            // Nyquist bin = 0
            ifftReal[FREQ_BINS] = 0f
            ifftImag[FREQ_BINS] = 0f
            // Hermitian symmetry for negative frequencies (bins 513..1023)
            for (k in FREQ_BINS + 1 until N_FFT) {
                val mirror = N_FFT - k
                ifftReal[k] = ifftReal[mirror]
                ifftImag[k] = -ifftImag[mirror]
            }
            // Inverse FFT
            fft.ifft(ifftReal, ifftImag)
            // Overlap-add with window
            val start = f * HOP_LENGTH
            for (i in 0 until N_FFT) {
                val idx = start + i
                if (idx < outLength) {
                    output[idx] += ifftReal[i] * hannWindow[i]
                    windowSum[idx] += hannWindow[i] * hannWindow[i]
                }
            }
        }
        // Normalize by window sum
        for (i in 0 until outLength) {
            if (windowSum[i] > 1e-8f) {
                output[i] /= windowSum[i]
            }
        }
        return output
    }

    /**
     * Run ONNX model on complex spectrogram.
     * Input: real[512][nFrames], imag[512][nFrames]
     * Output: masked real[512][nFrames], imag[512][nFrames]
     */
    private fun runModel(
        session: OrtSession,
        specReal: Array<FloatArray>,
        specImag: Array<FloatArray>,
        nFrames: Int
    ): Pair<Array<FloatArray>, Array<FloatArray>> {
        val inputName = session.inputNames.first()
        val outputName = session.outputNames.first()

        // Calculate number of splits (pad to multiple of FRAMES_PER_SPLIT)
        val numSplits = (nFrames + FRAMES_PER_SPLIT - 1) / FRAMES_PER_SPLIT
        val totalFrames = numSplits * FRAMES_PER_SPLIT

        // Build input tensor [2, numSplits, 512, 1024]
        // Layout: [real/imag][split][freq][time]
        val inputData = FloatArray(2 * numSplits * FREQ_BINS * FRAMES_PER_SPLIT)
        for (s in 0 until numSplits) {
            for (f in 0 until FRAMES_PER_SPLIT) {
                val frameIdx = s * FRAMES_PER_SPLIT + f
                val valid = frameIdx < nFrames
                for (k in 0 until FREQ_BINS) {
                    val idxReal = (0 * numSplits + s) * FREQ_BINS * FRAMES_PER_SPLIT + k * FRAMES_PER_SPLIT + f
                    val idxImag = (1 * numSplits + s) * FREQ_BINS * FRAMES_PER_SPLIT + k * FRAMES_PER_SPLIT + f
                    inputData[idxReal] = if (valid) specReal[k][frameIdx] else 0f
                    inputData[idxImag] = if (valid) specImag[k][frameIdx] else 0f
                }
            }
        }

        val shape = longArrayOf(2, numSplits.toLong(), FREQ_BINS.toLong(), FRAMES_PER_SPLIT.toLong())
        val tensor = OnnxTensor.createTensor(ortEnv, FloatBuffer.wrap(inputData), shape)
        val result = session.run(mapOf(inputName to tensor))
        // ONNX Runtime 1.17: result.get() returns Optional<OnnxTensor>
        val outputTensor = result.get(outputName).orElseThrow { RuntimeException("No output for $outputName") } as OnnxTensor
        val outputData = outputTensor.floatBuffer.array()

        // Extract output [512][nFrames]
        val outReal = Array(FREQ_BINS) { FloatArray(nFrames) }
        val outImag = Array(FREQ_BINS) { FloatArray(nFrames) }
        for (s in 0 until numSplits) {
            for (f in 0 until FRAMES_PER_SPLIT) {
                val frameIdx = s * FRAMES_PER_SPLIT + f
                if (frameIdx >= nFrames) break
                for (k in 0 until FREQ_BINS) {
                    val idxReal = (0 * numSplits + s) * FREQ_BINS * FRAMES_PER_SPLIT + k * FRAMES_PER_SPLIT + f
                    val idxImag = (1 * numSplits + s) * FREQ_BINS * FRAMES_PER_SPLIT + k * FRAMES_PER_SPLIT + f
                    outReal[k][frameIdx] = outputData[idxReal]
                    outImag[k][frameIdx] = outputData[idxImag]
                }
            }
        }

        result.close()
        tensor.close()
        return Pair(outReal, outImag)
    }

    fun release() {
        vocalsSession.close()
        accompanimentSession.close()
        ortEnv.close()
    }
}
