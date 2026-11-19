package ai.aura.app.assist

import org.json.JSONObject
import java.util.Locale

/**
 * The wake word's second stage on the phone (T-1573): the house's speaker gate hears the
 * [WINDOW_MS] the head fired on before any turn opens, through the adapter's
 * `/api/audio/wake-check`. On the 2026-11-19 drive the head woke AURA on voices that were not
 * the owner's, and a turn's own speaker check came only after the popup or the car mic was open.
 *
 * [decide] drops a wake only on the route's `drop`, which it answers only for a voice the gate
 * itself would drop; no answer in [TIMEOUT_MS], an old adapter, or any other body opens it, so
 * a house that cannot say never costs the owner a wake. [line] is the one decision line per
 * wake, for logcat and the car's session log. Rows: `tests/contracts/wake_check.json`, mirrored
 * in `android-app/tools/wake_check_test.py`.
 *
 * Does not cover: the head's own threshold (DECISIONS 103), a gate in observe mode (it lets
 * every voice through, by its setting), or the owner on a microphone the gate has not learnt,
 * which it refuses like any voice until a claim on the console teaches it.
 */
object WakeCheck {
    const val TIMEOUT_MS: Long = 1_500L
    const val WINDOW_MS: Int = 2_000

    data class Verdict(val open: Boolean, val verdict: String, val similarity: Double?)

    /** [status] null: nothing came back within [TIMEOUT_MS]. */
    fun decide(status: Int?, body: String?): Verdict {
        if (status == null) return Verdict(true, "no answer", null)
        if (status != 200) return Verdict(true, "house answered $status", null)
        val o = runCatching { JSONObject(body.orEmpty()) }.getOrNull() ?: return Verdict(true, "no verdict", null)
        val wake = o.optString("wake")
        if (wake != "open" && wake != "drop") return Verdict(true, "no verdict", null)
        val similarity = if (!o.has("similarity") || o.isNull("similarity")) null else o.optDouble("similarity").takeIf { !it.isNaN() }
        return Verdict(wake == "open", o.optString("reason").ifEmpty { "no verdict" }, similarity)
    }

    fun line(head: String, score: Float, bar: Float, music: Boolean, v: Verdict): String {
        val sim = v.similarity?.let { " (similarity ${"%.2f".format(Locale.ROOT, it)})" }.orEmpty()
        return "wake $head: score ${"%.2f".format(Locale.ROOT, score)} over ${"%.2f".format(Locale.ROOT, bar)}, " +
            "car music ${if (music) "on" else "off"}, voice ${v.verdict}$sim: ${if (v.open) "opened" else "dropped"}"
    }
}

/**
 * The last [WakeCheck.WINDOW_MS] of 16 kHz mono PCM the head scored, for the check. Pushed from
 * the capture thread every 80 ms; [snapshot] copies it oldest first. Not a recording: it is
 * overwritten as it goes and leaves the phone only when the head fires.
 */
class WakeWindow(sampleRate: Int = 16_000) {
    private val ring = ByteArray(sampleRate * 2 * WakeCheck.WINDOW_MS / 1000)
    private var end = 0
    private var filled = 0

    @Synchronized
    fun push(buffer: ByteArray, length: Int) {
        val n = length.coerceIn(0, buffer.size)
        val from = (n - ring.size).coerceAtLeast(0)
        for (i in from until n) {
            ring[end] = buffer[i]
            end = (end + 1) % ring.size
        }
        filled = (filled + n - from).coerceAtMost(ring.size)
    }

    @Synchronized
    fun snapshot(): ByteArray {
        val out = ByteArray(filled)
        val start = (end - filled + ring.size) % ring.size
        for (i in 0 until filled) out[i] = ring[(start + i) % ring.size]
        return out
    }

    @Synchronized
    fun clear() {
        end = 0
        filled = 0
    }
}
