package com.example.audiooverlay

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Singleton state holder for audio capture.
 * Updated by AudioCaptureService, observed by ViewModel / UI.
 */
object AudioCaptureState {
    private val _isCapturing = MutableStateFlow(false)
    val isCapturing: StateFlow<Boolean> = _isCapturing.asStateFlow()

    private val _sampleRate = MutableStateFlow(0)
    val sampleRate: StateFlow<Int> = _sampleRate.asStateFlow()

    private val _channels = MutableStateFlow(0)
    val channels: StateFlow<Int> = _channels.asStateFlow()

    private val _pcmFormat = MutableStateFlow("—")
    val pcmFormat: StateFlow<String> = _pcmFormat.asStateFlow()

    private val _bufferSize = MutableStateFlow(0)
    val bufferSize: StateFlow<Int> = _bufferSize.asStateFlow()

    private val _rmsDb = MutableStateFlow(-96.0)
    val rmsDb: StateFlow<Double> = _rmsDb.asStateFlow()

    private val _peakDb = MutableStateFlow(-96.0)
    val peakDb: StateFlow<Double> = _peakDb.asStateFlow()

    private val _dspEnabled = MutableStateFlow(false)
    val dspEnabled: StateFlow<Boolean> = _dspEnabled.asStateFlow()

    private val _bgmGain = MutableStateFlow(1.0f)
    val bgmGain: StateFlow<Float> = _bgmGain.asStateFlow()

    // BGM 处理模式：0=全局增益, 1=中置声道消除, 2=自适应谱滤波, 3=神经分离
    private val _bgmMode = MutableStateFlow(0)
    val bgmMode: StateFlow<Int> = _bgmMode.asStateFlow()

    private val _targetApp = MutableStateFlow("未指定")
    val targetApp: StateFlow<String> = _targetApp.asStateFlow()

    private val _errorMessage = MutableStateFlow("")
    val errorMessage: StateFlow<String> = _errorMessage.asStateFlow()

    // --- Setters (called by Service / UI) ---
    fun setCapturing(value: Boolean) { _isCapturing.value = value }
    fun setSampleRate(value: Int) { _sampleRate.value = value }
    fun setChannels(value: Int) { _channels.value = value }
    fun setPcmFormat(value: String) { _pcmFormat.value = value }
    fun setBufferSize(value: Int) { _bufferSize.value = value }
    fun setRmsDb(value: Double) { _rmsDb.value = value }
    fun setPeakDb(value: Double) { _peakDb.value = value }
    fun setDspEnabled(value: Boolean) { _dspEnabled.value = value }
    fun setBgmGain(value: Float) { _bgmGain.value = value }
    fun setBgmMode(value: Int) { _bgmMode.value = value }
    fun setTargetApp(value: String) { _targetApp.value = value }
    fun setErrorMessage(value: String) { _errorMessage.value = value }

    fun reset() {
        _isCapturing.value = false
        _sampleRate.value = 0
        _channels.value = 0
        _pcmFormat.value = "—"
        _bufferSize.value = 0
        _rmsDb.value = -96.0
        _peakDb.value = -96.0
        _errorMessage.value = ""
    }
}
