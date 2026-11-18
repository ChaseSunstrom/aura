package ai.aura.app.car.service

/**
 * Whether the wait for Android to join the car's saved Wi-Fi can be skipped (T-1100).
 *
 * The wait polls for a network the link does not hold yet. On a reconnect inside a drive the
 * link still holds the car's network, so the poll can never answer and the whole wait (25 s)
 * passed before `join` said "already joined": on 2026-11-11 a head unit's reset at speed took
 * 27 s to come back, 25 of them this.
 *
 * Does not cover a held network that has died without Android saying so: the AAP connect then
 * fails and the next attempt asks for the network again, as before.
 */
internal object SavedNetworkWait {
    /** [holdsNetwork]: the link object has a network; [joinedSsid]: the one it joined. */
    fun skip(holdsNetwork: Boolean, joinedSsid: String?, wanted: String): Boolean =
        holdsNetwork && joinedSsid == wanted
}
