package ai.aura.app.assist

/**
 * The persona a wake word just named ("Hey Aura"), handed from the wake-word
 * service to the conversation it starts, once: the conversation's Hermes
 * session switches to it as soon as it attaches (`HermesConversation.onSession`),
 * so the answer is in that persona's voice and the reactor in its colour.
 *
 * A process-wide slot rather than a constructor argument because the service
 * starts the conversation through surfaces (the overlay, the locked-phone door)
 * that each build their own. Does not survive the process, and is cleared by
 * whoever takes it -- a later conversation must not inherit an old wake.
 */
object WakePersona {
    @Volatile
    var pending: String? = null

    /** The persona to switch to, or null; empty afterwards. */
    fun take(): String? = synchronized(this) { pending.also { pending = null } }

    /**
     * When (uptime ms) a head whose pack requires speaker verification fired ("Aura": her
     * name alone false-accepts far more than once a day, the AURA brief §4), or 0:
     * the turn it opens needs a recognised voice before anything is sent.
     */
    @Volatile
    var verifyAt: Long = 0L

    /**
     * Whether the turn starting [now] was opened by such a head: armed within
     * [VERIFY_WINDOW_MS], and cleared either way. The window is what keeps a wake whose turn
     * never began (a false accept into silence, a surface that bailed) from refusing a turn
     * the owner later starts by hand.
     */
    fun takeVerify(now: Long): Boolean = synchronized(this) {
        val at = verifyAt
        verifyAt = 0L
        at > 0L && now - at in 0..VERIFY_WINDOW_MS
    }

    /** When (uptime ms) a wake word, not a press, asked the car to listen, or 0: see [takeWake]. */
    @Volatile
    var wakeAt: Long = 0L

    /**
     * Whether the car listen starting [now] was a wake word's: armed within [VERIFY_WINDOW_MS],
     * cleared either way. A wake may be false, and a false one must not also open the car's
     * microphone to probe it (the owner, 2026-11-12: the car switched to voice with nobody asking).
     */
    fun takeWake(now: Long): Boolean = synchronized(this) {
        val at = wakeAt
        wakeAt = 0L
        at > 0L && now - at in 0..VERIFY_WINDOW_MS
    }

    /** Wake to the start of its turn: the conversation or the car's listening, seconds at most. */
    const val VERIFY_WINDOW_MS = 10_000L

    /**
     * Whether a transcription is refused as an unrecognised voice: the house's gate
     * dropped it, or words came in a turn a verified wake opened and earned no speaker
     * ticket. Silence is not refused here: it is "didn't catch that", as on any turn.
     * Does not cover a house with no voice enrolled: there no voice earns a ticket, so
     * "Aura" opens nothing but that refusal until one is.
     */
    fun refused(dropped: Boolean, ticket: String?, transcript: String, unverified: Boolean): Boolean =
        dropped || (unverified && ticket.isNullOrBlank() && transcript.isNotBlank())
}
