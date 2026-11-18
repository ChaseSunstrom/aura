package ai.aura.app.assist.hermes

import android.os.Handler
import android.os.Looper

/**
 * "Run this later, on the thread the UI lives on" — as a seam.
 *
 * [HermesClient] delivers every callback on the main thread and schedules its
 * reconnects with a delay, which on a phone is a [Handler] on the main
 * [Looper]. A JVM unit test has neither, and the stubbed `android.jar` makes
 * `Handler.post` silently return false — so a client that took a Handler
 * would be a client whose reconnect logic could only be tested on a device.
 * This is the one method each of those needs.
 */
interface Clockwork {
    fun post(task: Runnable)
    fun postDelayed(task: Runnable, delayMs: Long)
    fun remove(task: Runnable)
}

/** The real one: the main looper. */
class MainClockwork : Clockwork {
    private val handler = Handler(Looper.getMainLooper())
    override fun post(task: Runnable) {
        handler.post(task)
    }

    override fun postDelayed(task: Runnable, delayMs: Long) {
        handler.postDelayed(task, delayMs)
    }

    override fun remove(task: Runnable) = handler.removeCallbacks(task)
}
