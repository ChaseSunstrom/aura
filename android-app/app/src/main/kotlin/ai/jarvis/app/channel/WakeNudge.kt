package ai.jarvis.app.channel

import android.content.Intent
import android.os.PowerManager

/**
 * Which system broadcasts mean "the phone is awake again, check the house link now".
 *
 * Half of the house's proactive pushes from 2026-10-03 to 10-08 found no reachable phone
 * and waited in the companion queue, which drains only when the phone registers again --
 * hours later, because Doze defers the reconnect backoff and the heartbeat alike. Picking
 * the phone up is exactly when a waiting brief matters, so those moments nudge the channel.
 * Leaving Doze counts too; entering it does not.
 *
 * Does not cover a phone that stays asleep: nothing here wakes it.
 */
object WakeNudge {
    fun wanted(action: String?, deviceIdle: Boolean): Boolean = when (action) {
        Intent.ACTION_USER_PRESENT, Intent.ACTION_SCREEN_ON -> true
        PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED -> !deviceIdle
        else -> false
    }

    val actions = listOf(Intent.ACTION_USER_PRESENT, Intent.ACTION_SCREEN_ON, PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED)
}
