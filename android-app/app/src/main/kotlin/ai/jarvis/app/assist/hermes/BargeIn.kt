package ai.jarvis.app.assist.hermes

/**
 * Whether a sound heard while Jarvis speaks is the person cutting in (the owner,
 * 2026-09-25: "it says something and then starts listening again, because it
 * hears itself ... so it cancels mid speech").
 *
 * The phone's microphone hears the phone's speaker, and one buffer over a fixed
 * edge was enough to stop the reply. So the reply's first [LEARN_MS] teach what
 * Jarvis sounds like in the microphone (nothing interrupts then), and after that
 * only a level [OVER_ECHO] times that echo's peak, held for [HOLD_MS], is a
 * person. Jarvis's own louder syllables raise the echo as they come, so the
 * reply's peaks are not mistaken for a voice either.
 *
 * Does not cover a person who talks no louder than the phone's speaker: they are
 * heard as echo, and they wait for the reply to end or tap the orb. Pure, so the
 * rule has a unit test without a microphone.
 */
class BargeIn {
    private var since = 0L
    private var echo = 0f
    private var aboveSince = 0L

    fun reset(nowMs: Long) {
        since = nowMs
        echo = 0f
        aboveSince = 0L
    }

    /** One smoothed microphone level during the reply; true once it is the person. */
    fun hear(nowMs: Long, level: Float, floor: Float): Boolean {
        if (nowMs - since < LEARN_MS) {
            if (level > echo) echo = level
            return false
        }
        val edge = maxOf(MIN_LEVEL, floor * FLOOR_RATIO, echo * OVER_ECHO)
        if (level <= edge) {
            aboveSince = 0L
            // Jarvis's own louder moments: the echo follows them up, and eases down.
            echo = maxOf(echo * ECHO_DECAY, minOf(level, edge / OVER_ECHO))
            return false
        }
        if (aboveSince == 0L) aboveSince = nowMs
        return nowMs - aboveSince >= HOLD_MS
    }

    companion object {
        const val LEARN_MS = 800L
        const val HOLD_MS = 400L
        const val OVER_ECHO = 2.2f
        const val MIN_LEVEL = 0.02f
        const val FLOOR_RATIO = 8f
        const val ECHO_DECAY = 0.995f
    }
}
