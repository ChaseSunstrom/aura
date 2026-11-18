package ai.aura.app.car.voice

import ai.aura.app.JarvisApp
import ai.aura.app.config.JarvisConfig
import ai.aura.app.ui.ApprovalBridge
import ai.aura.app.ui.Notify
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope

/**
 * The drive's end, told to Jarvis once ([CarTurn.DRIVE_END]).
 *
 * The head unit drops the link several times a drive (see [DriveStart]), so a session ending
 * is not the drive ending: the service waits [DriveStart.GAP_MS] and tells Jarvis only if no
 * session came back, and only if the session lasted [MIN_DRIVE_MS] (a car started and
 * stopped is not a drive to debrief). Does not survive the service being killed during the
 * wait: that drive is simply not debriefed.
 */
object DriveEnd {
    const val MIN_DRIVE_MS = 5 * 60_000L

    private const val NOTE_ID = 47_299

    fun isRealDrive(startMs: Long, endMs: Long): Boolean = endMs - startMs >= MIN_DRIVE_MS

    /** [lastSeenMs] is [DriveStart.lastSeen] after the wait: unchanged means no session returned. */
    fun stillOver(endedAtMs: Long, lastSeenMs: Long): Boolean = lastSeenMs == endedAtMs

    /**
     * Asks on the phone's own link (the car's is closed) and leaves the reply, if there is
     * one, as a notification: the phone is the only output, and (quiet) says nothing.
     */
    suspend fun tell(context: Context, scope: CoroutineScope) {
        val link = HermesCarJarvis(context, JarvisConfig(context), scope)
        try {
            val answer = runCatching {
                link.ask(CarTurn.DRIVE_END, null) { approval ->
                    ApprovalBridge.requestOutcome(
                        context, approval.tool, approval.params, approval.reason,
                        description = "Asked for by the end of the drive",
                    ).approved
                }
            }.getOrNull()?.trim()
            if (answer.isNullOrEmpty() || answer == CarTurn.QUIET) return
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            val note = NotificationCompat.Builder(context, JarvisApp.CHANNEL_ALERTS)
                .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
                .setColor(Notify.color(Notify.Template.TASK))
                .setContentTitle("The drive")
                .setContentText(answer)
                .setStyle(NotificationCompat.BigTextStyle().bigText(answer))
                .setAutoCancel(true)
                .build()
            runCatching { nm.notify(NOTE_ID, note) }
        } finally {
            link.close()
        }
    }
}
