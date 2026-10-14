package com.example.audiooverlay

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.StateFlow

/**
 * ViewModel that exposes AudioCaptureState to the Compose UI.
 * All state is backed by the singleton AudioCaptureState.
 */
class AudioCaptureViewModel : ViewModel() {

    val isCapturing: StateFlow<Boolean> = AudioCaptureState.isCapturing
    val sampleRate: StateFlow<Int> = AudioCaptureState.sampleRate
    val channels: StateFlow<Int> = AudioCaptureState.channels
    val pcmFormat: StateFlow<String> = AudioCaptureState.pcmFormat
    val bufferSize: StateFlow<Int> = AudioCaptureState.bufferSize
    val rmsDb: StateFlow<Double> = AudioCaptureState.rmsDb
    val peakDb: StateFlow<Double> = AudioCaptureState.peakDb
    val dspEnabled: StateFlow<Boolean> = AudioCaptureState.dspEnabled
    val bgmGain: StateFlow<Float> = AudioCaptureState.bgmGain
    val bgmMode: StateFlow<Int> = AudioCaptureState.bgmMode
    val targetApp: StateFlow<String> = AudioCaptureState.targetApp
    val errorMessage: StateFlow<String> = AudioCaptureState.errorMessage

    fun setDspEnabled(enabled: Boolean) {
        AudioCaptureState.setDspEnabled(enabled)
    }

    fun setBgmGain(gain: Float) {
        AudioCaptureState.setBgmGain(gain)
    }

    fun setBgmMode(mode: Int) {
        AudioCaptureState.setBgmMode(mode)
    }

    fun setTargetApp(packageName: String) {
        AudioCaptureState.setTargetApp(packageName)
    }
}
