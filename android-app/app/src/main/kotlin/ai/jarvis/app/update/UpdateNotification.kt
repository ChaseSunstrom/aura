package ai.jarvis.app.update

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.JarvisApp
import ai.jarvis.app.MainActivity
import ai.jarvis.app.ui.Notify
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.util.Log

/**
 * The "a new build is available" notification, for when the automatic check
 * found one while the app was in the background.
 *
 * The in-app notice on the home screen ([MainActivity]) is enough when the app
 * is open; this exists for the case it was written for — the check runs on a
 * resume, the user leaves, and a backgrounded app with no notification has told
 * nobody anything. Tapping it opens the home screen, where the same offer is
 * waiting as a notice (see [AutoUpdate.pendingOffer]).
 *
 * It reuses [JarvisApp.CHANNEL_ALERTS] — "anything Jarvis posts on the user's
 * behalf" — rather than inventing a channel: an available update is exactly
 * that, and a fourth near-duplicate switch in system settings is a cost with no
 * benefit. It deliberately does NOT reach for the approval channel, which
 * bypasses Do Not Disturb; a new build is not an interruption worth waking
 * someone for.
 *
 * **It never installs.** Tapping leads to the INSTALL button, which leads to the
 * system installer prompt. Nothing here shortcuts that chain.
 */
object UpdateNotification {

    /**
     * Post (or refresh) the notice for [update]. Safe on any thread; a failure
     * to post — no NotificationManager, or POST_NOTIFICATIONS not granted — is
     * logged and swallowed, because a missed notification must never take the
     * resume path down with it.
     */
    fun post(context: Context, update: ReleaseFeed.Update) {
        val app = context.applicationContext
        val nm = app.getSystemService(NotificationManager::class.java) ?: return

        // CLEAR_TOP so tapping lands on the existing home screen rather than
        // stacking a second one behind it; its onResume re-shows the notice.
        val intent = Intent(app, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(
            app,
            REQUEST_CODE,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val title = "${ActiveTheme.name} update available"
        val text = "Version ${update.versionName} is ready to install."
        val builder = Notification.Builder(app, JarvisApp.CHANNEL_ALERTS)
            .setSmallIcon(ai.jarvis.app.ui.theme.ActiveTheme.statusIcon)
            .setColor(Notify.color(Notify.Template.NOTICE))
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setCategory(Notification.CATEGORY_RECOMMENDATION)
            .setContentIntent(pending)
            .setAutoCancel(true)

        runCatching { nm.notify(NOTIFICATION_ID, builder.build()) }
            .onFailure { Log.w(TAG, "could not post the update notification", it) }
    }

    /**
     * The download's progress bar, in the shade: every path shows it -- the INSTALL
     * button, Settings, and the unattended install nobody is watching. Quiet and
     * ongoing on the service channel; [downloaded] takes it down.
     */
    fun downloading(context: Context, versionName: String, done: Long, total: Long) {
        // One post per PROGRESS_PACE_MS: the caller reports every 1%, ~14 a second on the LAN, and
        // the system sheds a package's updates past five a second (the owner's log, 2026-11-10).
        val now = SystemClock.elapsedRealtime()
        if (now - postedAt < PROGRESS_PACE_MS) return
        postedAt = now
        val app = context.applicationContext
        val nm = app.getSystemService(NotificationManager::class.java) ?: return
        val percent = if (total > 0) (done * 100 / total).toInt() else 0
        val text = if (total > 0) "$percent% · ${done shr 20} of ${total shr 20} MB" else "${done shr 20} MB"
        val builder = Notification.Builder(app, JarvisApp.CHANNEL_SERVICE)
            .setSmallIcon(ai.jarvis.app.ui.theme.ActiveTheme.statusIcon)
            .setColor(Notify.color(Notify.Template.NOTICE))
            .setContentTitle("Downloading ${ActiveTheme.name} $versionName")
            .setContentText(text)
            .setProgress(100, percent, total <= 0)
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_PROGRESS)
        runCatching { nm.notify(PROGRESS_ID, builder.build()) }
            .onFailure { Log.w(TAG, "could not post the download progress", it) }
    }

    /** The download ended, either way: the bar comes down (the installer's prompt or a failure says the rest). */
    fun downloaded(context: Context) {
        postedAt = -PROGRESS_PACE_MS
        runCatching {
            context.applicationContext.getSystemService(NotificationManager::class.java)?.cancel(PROGRESS_ID)
        }
    }

    /** Take the notice down — after INSTALL, or when the user taps LATER. */
    fun cancel(context: Context) {
        runCatching {
            context.applicationContext
                .getSystemService(NotificationManager::class.java)
                ?.cancel(NOTIFICATION_ID)
        }
    }

    private const val TAG = "JarvisUpdate"

    // 6301/6302 belong to InstallResultReceiver; this is the next free one.
    private const val NOTIFICATION_ID = 6303
    private const val REQUEST_CODE = 6304
    private const val PROGRESS_ID = 6305
    private const val PROGRESS_PACE_MS = 250L

    @Volatile private var postedAt = -PROGRESS_PACE_MS
}
