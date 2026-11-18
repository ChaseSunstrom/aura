package ai.aura.app.car.service

/**
 * How long the AAP dial keeps trying before the session is torn down (T-0997).
 *
 * The owner's Chevrolet binds its AAP port when it sends WIFI_START_REQUEST on Bluetooth, and
 * in the field that came 25 s after RFCOMM was up (log 820537cef65f). Giving up at 20 s closes
 * RFCOMM under it and the handshake starts again: six times in a row in log 87a1ac793e46. So a
 * refusal from a head unit that has not yet named its endpoint is "not ready", and is waited on.
 *
 * Does not cover a head unit that sends the request only after a fresh RFCOMM connect: that one
 * now costs [PATIENT_MS] per attempt instead of the caller's total.
 */
internal object DialPatience {
    const val PATIENT_MS = 60_000L

    /**
     * Has the head unit named its endpoint: in the handshake ([inHandshake], the credentials'
     * endpoint) or after it ([later], its re-announce on the service link)? Either is its start
     * request; a port it named and then refuses is wrong, not late.
     */
    fun named(inHandshake: Any?, later: Any?): Boolean = inHandshake != null || later != null

    /** [refused]: the last failure was a refused connection. [named]: the start request has come. */
    fun millis(refused: Boolean, named: Boolean, totalMillis: Long): Long =
        if (refused && !named) maxOf(totalMillis, PATIENT_MS) else totalMillis
}
