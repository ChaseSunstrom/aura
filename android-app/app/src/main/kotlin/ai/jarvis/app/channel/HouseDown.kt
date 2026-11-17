package ai.jarvis.app.channel

/**
 * PURE LOGIC: no Android, no clock. Decides when the phone tells the owner the house has been
 * unreachable for 15 minutes while the phone is at home (T-0578).
 *
 * "Home" is the Wi-Fi connection on which the house last answered: [net] is that connection's
 * `Network` handle, null for mobile data or none. The handle needs no SSID and no location
 * permission; it lasts one connection. Away from home with no tunnel the house is unreachable as a
 * matter of course, so failures on any other network, on mobile data, or before the house has
 * answered on this connection are ignored and never accumulate.
 *
 * Times are monotonic milliseconds. The outage clock starts at the first failure after the last
 * answer and one notice is due at the first failure (or [tick]) at or after [QUIET_MS]; the next
 * one only after the house has answered again.
 *
 * Not covered: state is in memory, so a killed app notices nothing; a phone that joins home Wi-Fi
 * while the house is already down gets no notice, since the house never answered on that
 * connection.
 */
class HouseDown {
    private var home: Long? = null
    private var since: Long? = null
    private var lastFail = 0L
    private var notified = false

    /** The house answered on [net]. True when the owner was told it was quiet: say it is back. */
    @Synchronized
    fun answered(at: Long, net: Long?): Boolean {
        home = net
        since = null
        val back = notified
        notified = false
        return back
    }

    /** An attempt to reach the house failed on [net]. The time it went quiet, once, or null. */
    @Synchronized
    fun failed(at: Long, net: Long?): Long? {
        networkChanged(net)
        if (home == null) return null
        if (since == null) since = at
        lastFail = at
        return due(at)
    }

    /**
     * A minute's tick, so the notice lands at 15:00 whatever the retry backoff is. It only
     * completes an outage that failures were seen to start: a phone that slept through it has
     * seen no failures, and the next real one decides.
     */
    @Synchronized
    fun tick(at: Long, net: Long?): Long? {
        networkChanged(net)
        if (home == null || since == null || at - lastFail > TICK_GRACE_MS) return null
        return due(at)
    }

    /** The default network is now [net]. Leaving the home connection clears the outage clock. */
    @Synchronized
    fun networkChanged(net: Long?) {
        if (net != home || net == null) {
            home = null
            since = null
        }
    }

    private fun due(at: Long): Long? {
        val s = since ?: return null
        if (notified || at - s < QUIET_MS) return null
        notified = true
        return s
    }

    companion object {
        const val QUIET_MS = 15 * 60_000L

        /** The link's longest wait between tries (Backoff.DEFAULT_MAX_MS) plus its jitter. */
        const val TICK_GRACE_MS = 6 * 60_000L
    }
}
