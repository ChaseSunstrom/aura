package ai.jarvis.app.car.service

/**
 * The one log line written when a session ends on `Connection reset` (T-1217).
 *
 * A drive log showed the head unit resetting the link at strong signal with its keepalives
 * answered in milliseconds, and nothing in the log said what the phone had in flight. This
 * names the things the phone can compare against the next such reset: per audio sink the
 * buffers sent and not yet acknowledged, how long since the car last changed audio focus
 * (and to what), how long since the media queue last dropped audio, and the Bluetooth
 * headset/a2dp state with how long since it last changed.
 *
 * Pure, so the test needs no Android. It does not say why the car reset, and it changes no
 * behaviour: a reset that follows none of these is the answer too. Prints no SSID, address
 * or head unit name; sink names come from the car's own advertised audio types.
 */
object ResetReport {
    /** One open audio sink: [sent] is every buffer put on the wire, [unacked] those the car has not acknowledged. */
    data class Sink(val name: String, val sent: Long, val unacked: Int)

    /** The car's last audio focus word and when ([atMs], the [line] clock) it said it. */
    data class Focus(val state: String, val atMs: Long)

    /**
     * True when the supervisor's cause text ("the session ended: <cause>") names a reset. The
     * text, not the exception: the exception seen while the session unwinds is often the
     * scope's cancellation, and the reset only reaches the supervisor's description.
     */
    fun isReset(cause: String?): Boolean = cause?.contains("Connection reset", ignoreCase = true) == true

    /** The line, or null when [cause] is not a reset. Times are on one clock, [nowMs] its present. */
    fun line(
        cause: String?,
        sinks: List<Sink>,
        focus: Focus?,
        lastBacklogDropMs: Long?,
        bluetooth: String,
        nowMs: Long,
    ): String? {
        if (!isReset(cause)) return null
        val perSink = if (sinks.isEmpty()) "no audio sink open" else
            sinks.joinToString("; ") { "${it.name} ${it.sent} sent, ${it.unacked} unacknowledged" }
        val focusPart = focus?.let { "${nowMs - it.atMs} ms since the car's last audio focus change (${it.state})" }
            ?: "the car never changed audio focus"
        val dropPart = lastBacklogDropMs?.let { "${nowMs - it} ms since the media queue last dropped audio" }
            ?: "the media queue never dropped audio (never)"
        return "as it was reset: audio sinks: $perSink | $focusPart | $dropPart | $bluetooth"
    }

    /**
     * Headset and a2dp as last seen, and for how long. Fed by the system's connection
     * broadcasts ([changed]) and by one read when the service starts ([seed]), so a state
     * that never changed since the read says "unchanged for at least" rather than a time
     * it was never observed to change at.
     */
    class ProfileClock {
        private class Entry(val state: String, val atMs: Long, val observed: Boolean)

        private val entries = HashMap<String, Entry>()

        /** A read of the current state; never replaces one already held. */
        @Synchronized
        fun seed(profile: String, state: String, nowMs: Long) {
            entries.getOrPut(profile) { Entry(state, nowMs, observed = false) }
        }

        /** A broadcast: the clock restarts only when the state actually differs. */
        @Synchronized
        fun changed(profile: String, state: String, nowMs: Long) {
            if (entries[profile]?.state != state) entries[profile] = Entry(state, nowMs, observed = true)
        }

        @Synchronized
        fun describe(nowMs: Long): String = listOf("headset", "a2dp").joinToString(", ", "Bluetooth: ") {
            val e = entries[it] ?: return@joinToString "$it unknown"
            val age = nowMs - e.atMs
            "$it ${e.state} " + if (e.observed) "($age ms since it changed)" else "(unchanged for at least $age ms)"
        }
    }
}
