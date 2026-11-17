package ai.jarvis.app.car.voice

import android.content.Context

/**
 * Whether a car session is a new drive or the same drive reconnecting.
 *
 * The owner's GM head unit drops and re-forms the Android Auto link several times a drive
 * (2026-10-07: five sessions between 10:44 and 11:00), and each session told Jarvis "drive
 * started" -- five full model turns with tools, for one drive. A session that begins within
 * [GAP_MS] of the last one seen is the same drive. Does not know about a second car: two
 * cars a minute apart read as one drive.
 */
object DriveStart {
    /** A car gone this long, then back, is a new drive. */
    const val GAP_MS = 15 * 60_000L

    private const val PREFS = "jarvis_drive"
    private const val KEY_SEEN = "last_car_seen_ms"

    /** Pure: [lastSeenMs] is when a car session was last up (0 for never). A clock gone backwards is a new drive. */
    fun isNewDrive(lastSeenMs: Long, nowMs: Long): Boolean =
        lastSeenMs <= 0L || nowMs < lastSeenMs || nowMs - lastSeenMs > GAP_MS

    fun lastSeen(context: Context): Long =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_SEEN, 0L)

    /** Marked when a session starts and when it ends, so a crash mid-drive still leaves a time. */
    fun markSeen(context: Context, nowMs: Long = System.currentTimeMillis()) {
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(KEY_SEEN, nowMs).apply()
    }
}
