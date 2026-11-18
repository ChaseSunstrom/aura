package ai.aura.app.assist.hermes

/**
 * Holds one "Ask why" line (T-1054, [AskWhy]) until the turn that the deny
 * lands in is over, because a `prompt.submit` sent into a running turn is the console's "left in
 * the composer": it either waits on the session lock or interrupts the model's own reply.
 *
 * The line is dropped, silently, when the link closes or errors ([drop]), when the session is
 * not the one it was asked on, or when [HOLD_MS] has passed. One line at a time: a newer ask
 * replaces an older one, so the memory it holds is bounded. Not thread-safe: the conversation
 * calls it on the main thread only.
 *
 * Does not cover: a turn that never completes. The line then waits for [drop] or the next ask,
 * and is never sent after [HOLD_MS].
 */
class AskWhyQueue(private val now: () -> Long = System::currentTimeMillis) {
    private class Held(val line: String, val session: String?, val until: Long)

    private var held: Held? = null

    /** Send [line] now when no turn runs, else keep it for [turnEnded]. [send] is called at most once per line. */
    fun submit(line: String, session: String?, turnRunning: Boolean, send: (String) -> Unit) {
        if (session == null) return
        if (!turnRunning) send(line) else held = Held(line, session, now() + HOLD_MS)
    }

    /** The turn on [session] completed: send what waited, if it is still the same session and fresh. */
    fun turnEnded(session: String?, send: (String) -> Unit) {
        val h = held ?: return
        held = null
        if (session == h.session && now() <= h.until) send(h.line)
    }

    /** The link closed or errored: nothing waits for a conversation that is gone. */
    fun drop() {
        held = null
    }

    companion object {
        /**
         * Longer than a turn the person is still reading and the line is stale: they asked "why"
         * about an ask that has moved on, and a late question would read as the model's own.
         */
        const val HOLD_MS = 60_000L
    }
}
