package ai.jarvis.app.ui

import ai.jarvis.app.JarvisApp
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withTimeoutOrNull

/**
 * The emergency alert's countdown (T-0947): raises [EmergencyCountdownActivity] over the lock
 * screen, ticks once a second and answers whether the owner cancelled. The action
 * (`EmergencyAlert`) owns what happens at zero; this owns only the wait.
 *
 * What makes the person notice: the notification goes out on the approval channel (high
 * importance, vibration, the default sound, through Do Not Disturb) with a full-screen intent,
 * the same route a consent prompt takes, and the activity is started directly as well. Nothing is
 * spoken: the phone may be in a pocket in a meeting, and the sound, buzz and screen are the
 * countdown. If the platform refuses both the screen and the notification, nobody could have
 * cancelled: the run answers [End.NOT_SHOWN] at once and the action sends nothing. The countdown is
 * the owner's whole consent for a text to other people; "it ran unseen" is not consent.
 *
 * Does not cover a countdown the OS killed with the process (that sends nothing), nor a screen
 * start the platform drops without an error while the notification is also off: the only signal
 * for "shown" is that neither the post nor the start was refused.
 */
object EmergencyCountdown {
    private const val TAG = "EmergencyCountdown"
    private const val NOTIFICATION_ID = 0x1015
    private const val TICK_MS = 1_000L

    /** How a countdown ended: the owner's tap, the count reaching zero, or nobody being able to see it. */
    enum class End { CANCELLED, ELAPSED, NOT_SHOWN }

    class State(val secondsLeft: Int, val summary: String, val what: String)

    /** The screen's one input: null when no countdown is running. */
    val state = MutableStateFlow<State?>(null)

    @Volatile
    private var gate: CompletableDeferred<Unit>? = null

    /** The CANCEL control. Safe to call with nothing counting down. */
    fun cancel() {
        gate?.complete(Unit)
    }

    /**
     * [launch] starts the screen; only a test passes another, to prove a refused start.
     * [what] is the model-written text that will be sent: shown beside the names, so the owner
     * cancels on what will actually go out.
     */
    suspend fun run(
        ctx: Context,
        seconds: Int,
        summary: String,
        what: String,
        launch: (Context, Intent) -> Unit = { c, i -> c.startActivity(i) },
    ): End {
        val app = ctx.applicationContext
        val cancelled = CompletableDeferred<Unit>()
        gate = cancelled
        state.value = State(seconds, summary, what)
        try {
            if (!raise(app, seconds, summary, what, launch)) return End.NOT_SHOWN
            for (left in seconds downTo 1) {
                state.value = State(left, summary, what)
                if (withTimeoutOrNull(TICK_MS) { cancelled.await() } != null) return End.CANCELLED
            }
            return if (cancelled.isCompleted) End.CANCELLED else End.ELAPSED
        } finally {
            gate = null
            state.value = null
            runCatching { app.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID) }
        }
    }

    /** True when the owner can have seen it: the notification was posted or the screen was started. */
    private fun raise(app: Context, seconds: Int, summary: String, what: String, launch: (Context, Intent) -> Unit): Boolean {
        var shown = false
        val intent = Intent(app, EmergencyCountdownActivity::class.java)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS or
                    Intent.FLAG_ACTIVITY_NO_USER_ACTION
            )
        // Notification first, as the consent prompt does: a background activity start the
        // platform refuses does not throw, and the notification is then the only route.
        try {
            val nm = app.getSystemService(NotificationManager::class.java)
            // notify() does not throw for a switched-off app or a silenced channel: ask first.
            val blocked = nm == null || !nm.areNotificationsEnabled() ||
                nm.getNotificationChannel(JarvisApp.CHANNEL_APPROVAL)?.importance == NotificationManager.IMPORTANCE_NONE
            val pi = PendingIntent.getActivity(app, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val text = "$summary: \"$what\". Tap to cancel."
            val note = Notification.Builder(app, JarvisApp.CHANNEL_APPROVAL)
                .setSmallIcon(ai.jarvis.app.ui.theme.ActiveTheme.statusIcon)
                .setColor(Notify.color(Notify.Template.ALERT))
                .setContentTitle("Emergency alert in $seconds seconds")
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setCategory(Notification.CATEGORY_ALARM)
                .setContentIntent(pi)
                .setFullScreenIntent(pi, true)
                .setOngoing(true)
                .setAutoCancel(false)
                .setTimeoutAfter((seconds + 5) * TICK_MS)
                .build()
            if (!blocked) {
                nm?.notify(NOTIFICATION_ID, note)
                shown = true
            } else {
                Log.w(TAG, "notifications are off for the countdown")
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not post the countdown notification", t)
        }
        try {
            launch(app, intent)
            shown = true
        } catch (t: Throwable) {
            Log.w(TAG, "direct start of the countdown screen refused", t)
        }
        return shown
    }
}
