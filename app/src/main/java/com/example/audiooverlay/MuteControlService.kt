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
import android.media.audiofx.DynamicsProcessing
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Lightweight foreground service that keeps the process alive. Purpose: even when
 * the app UI is in background, the process stays foreground so MIUI's "Greezer"
 * does not block our broadcasts and the capture service keeps running.
 *
 * MUTE_SESSION handling is intentionally centralized in AudioCaptureService
 * (single receiver = single DynamicsProcessing per session, no duplicate mounts).
 */
class MuteControlService : Service() {

    companion object {
        private const val TAG = "AudioRouteLab"
        private const val NOTIFICATION_ID = 1002
        private const val CHANNEL_ID = "mute_control_channel"

        fun start(context: Context) {
            val intent = Intent(context, MuteControlService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, MuteControlService::class.java))
        }
    }

    override fun onCreate() {
        super.onCreate()
        startForegroundWithNotification()
        Log.d(TAG, "MuteControlService: foreground keep-alive started")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.d(TAG, "MuteControlService destroyed")
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startForegroundWithNotification() {
        val channel = NotificationChannel(
            CHANNEL_ID, "Mute Control", NotificationManager.IMPORTANCE_LOW
        ).apply { setShowBadge(false) }
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .createNotificationChannel(channel)

        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AudioOverlay")
            .setContentText("Per-session 静音控制运行中")
            .setSmallIcon(android.R.drawable.ic_lock_silent_mode)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }
}
