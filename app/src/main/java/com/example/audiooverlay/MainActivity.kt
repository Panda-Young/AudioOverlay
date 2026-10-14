package com.example.audiooverlay

import android.Manifest
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.audiofx.DynamicsProcessing
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.audiooverlay.ui.theme.AudioOverlayTheme

class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "AudioRouteLab"
    }

    private lateinit var mediaProjectionManager: MediaProjectionManager

    // Debug receiver for SET_TARGET_UID (UI sync only; MUTE_SESSION handled by service)
    private val muteReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == AudioCaptureService.ACTION_SET_TARGET_UID) {
                val uid = intent.getIntExtra(AudioCaptureService.EXTRA_TARGET_UID, -1)
                AudioCaptureService.targetUid = uid
                Log.d(TAG, "MainActivity: target UID set to $uid (uid-only capture)")
            }
        }
    }

    // Activity result launcher for MediaProjection permission
    private val mediaProjectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            Log.d(TAG, "MediaProjection permission granted, resultCode=${result.resultCode}")
            AudioCaptureService.start(this, result.resultCode, result.data!!)
        } else {
            Log.w(TAG, "MediaProjection permission denied, resultCode=${result.resultCode}")
            AudioCaptureState.setErrorMessage("用户拒绝了 MediaProjection 授权")
        }
    }

    // Permission launcher for POST_NOTIFICATIONS (Android 13+)
    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        Log.d(TAG, "POST_NOTIFICATIONS granted=$granted")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        mediaProjectionManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        setContent {
            AudioOverlayTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    AudioRouteLabScreen(
                        onStartCapture = { startCapture() },
                        onStopCapture = { stopCapture() }
                    )
                }
            }
        }

        registerReceiver(
            muteReceiver,
            IntentFilter().apply {
                addAction(AudioCaptureService.ACTION_SET_TARGET_UID)
            },
            Context.RECEIVER_EXPORTED
        )
        Log.d(TAG, "muteReceiver registered in MainActivity")

        // Start foreground service to keep process alive so broadcasts
        // still reach us when app UI is in background.
        MuteControlService.start(this)
    }

    override fun onDestroy() {
        super.onDestroy()
        try { unregisterReceiver(muteReceiver) } catch (_: Exception) {}
    }

    private fun startCapture() {
        Log.d(TAG, "startCapture: launching MediaProjection consent intent")
        val intent = mediaProjectionManager.createScreenCaptureIntent()
        mediaProjectionLauncher.launch(intent)
    }

    private fun stopCapture() {
        Log.d(TAG, "stopCapture: stopping AudioCaptureService")
        AudioCaptureService.stop(this)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AudioRouteLabScreen(
    onStartCapture: () -> Unit,
    onStopCapture: () -> Unit,
    viewModel: AudioCaptureViewModel = viewModel()
) {
    val isCapturing by viewModel.isCapturing.collectAsState()
    val sampleRate by viewModel.sampleRate.collectAsState()
    val channels by viewModel.channels.collectAsState()
    val pcmFormat by viewModel.pcmFormat.collectAsState()
    val bufferSize by viewModel.bufferSize.collectAsState()
    val rmsDb by viewModel.rmsDb.collectAsState()
    val peakDb by viewModel.peakDb.collectAsState()
    val dspEnabled by viewModel.dspEnabled.collectAsState()
    val bgmGain by viewModel.bgmGain.collectAsState()
    val bgmMode by viewModel.bgmMode.collectAsState()
    val targetApp by viewModel.targetApp.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // Title
        Text(
            text = "AudioRouteLab",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold
        )

        HorizontalDivider()

        // Capture control buttons
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Button(
                onClick = onStartCapture,
                enabled = !isCapturing,
                modifier = Modifier.weight(1f)
            ) {
                Text("Start Capture")
            }
            Button(
                onClick = onStopCapture,
                enabled = isCapturing,
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error
                )
            ) {
                Text("Stop Capture")
            }
        }

        // Target app
        InfoRow(label = "Target App", value = targetApp)

        HorizontalDivider()

        // Capture status
        Text(
            text = "Capture Status",
            fontWeight = FontWeight.Bold,
            fontSize = 16.sp
        )
        InfoRow(label = "Capture", value = if (isCapturing) "YES" else "NO")
        InfoRow(label = "Sample Rate", value = if (sampleRate > 0) "$sampleRate Hz" else "—")
        InfoRow(label = "Channels", value = if (channels > 0) "$channels" else "—")
        InfoRow(label = "PCM Format", value = pcmFormat)
        InfoRow(label = "Buffer Size", value = if (bufferSize > 0) "$bufferSize bytes" else "—")

        HorizontalDivider()

        // Audio levels
        Text(
            text = "Audio Levels",
            fontWeight = FontWeight.Bold,
            fontSize = 16.sp
        )
        InfoRow(label = "RMS", value = "%.1f dB".format(rmsDb))
        InfoRow(label = "Peak", value = "%.1f dB".format(peakDb))

        // Level bar visualization
        LevelBar(valueDb = rmsDb, label = "RMS")
        LevelBar(valueDb = peakDb, label = "Peak")

        HorizontalDivider()

        // DSP controls
        Text(
            text = "DSP Control",
            fontWeight = FontWeight.Bold,
            fontSize = 16.sp
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text("DSP: ${if (dspEnabled) "ON" else "OFF"}")
            Switch(
                checked = dspEnabled,
                onCheckedChange = { viewModel.setDspEnabled(it) }
            )
        }

        // BGM 处理模式选择器
        val modeOptions = listOf(
            0 to "全局增益 (Baseline)",
            1 to "中置声道消除",
            2 to "自适应谱滤波",
            3 to "神经分离 (降级)",
            4 to "Spleeter 神经分离 (ONNX)",
            5 to "MDX-Net 神经分离 (ONNX+FFTW)"
        )
        var modeExpanded by remember { mutableStateOf(false) }
        ExposedDropdownMenuBox(
            expanded = modeExpanded,
            onExpandedChange = { modeExpanded = it },
            modifier = Modifier.fillMaxWidth()
        ) {
            OutlinedTextField(
                value = modeOptions.firstOrNull { it.first == bgmMode }?.second ?: "未知",
                onValueChange = {},
                readOnly = true,
                label = { Text("BGM 处理模式") },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = modeExpanded) },
                modifier = Modifier
                    .fillMaxWidth()
                    .menuAnchor(),
                enabled = dspEnabled
            )
            ExposedDropdownMenu(
                expanded = modeExpanded,
                onDismissRequest = { modeExpanded = false }
            ) {
                modeOptions.forEach { (mode, label) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        onClick = {
                            viewModel.setBgmMode(mode)
                            modeExpanded = false
                        }
                    )
                }
            }
        }

        // BGM Gain slider
        Text("BGM Gain: ${(bgmGain * 100).toInt()}%")
        Slider(
            value = bgmGain,
            onValueChange = { viewModel.setBgmGain(it) },
            valueRange = 0f..1f,
            enabled = dspEnabled
        )

        // AudioTrack playback toggle
        var playbackEnabled by remember { mutableStateOf(false) }
        val context = androidx.compose.ui.platform.LocalContext.current
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text("回放处理后音频 (AudioTrack)")
            Switch(
                checked = playbackEnabled,
                onCheckedChange = {
                    playbackEnabled = it
                    // Send broadcast to service to enable/disable playback
                    val intent = android.content.Intent("com.example.audiooverlay.SET_PLAYBACK")
                    intent.putExtra("playback_enabled", it)
                    context.sendBroadcast(intent)
                }
            )
        }

        HorizontalDivider()

        // Error message
        if (errorMessage.isNotEmpty()) {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer
                )
            ) {
                Text(
                    text = "Error: $errorMessage",
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    modifier = Modifier.padding(12.dp)
                )
            }
        }

        Spacer(modifier = Modifier.height(16.dp))
        Text(
            text = "AudioRouteLab v1.1 — AudioPlaybackCapture + Spleeter ONNX + AudioTrack",
            fontSize = 11.sp,
            color = Color.Gray
        )
    }
}

@Composable
fun InfoRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(text = label, color = Color.Gray)
        Text(text = value, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun LevelBar(valueDb: Double, label: String) {
    // Map -96..0 dB to 0..1 fraction
    val fraction = ((valueDb + 96.0) / 96.0).coerceIn(0.0, 1.0)
    Column(modifier = Modifier.fillMaxWidth()) {
        LinearProgressIndicator(
            progress = { fraction.toFloat() },
            modifier = Modifier
                .fillMaxWidth()
                .height(8.dp),
            color = if (valueDb > -6.0) Color.Red else MaterialTheme.colorScheme.primary,
            trackColor = MaterialTheme.colorScheme.surfaceVariant
        )
    }
}
