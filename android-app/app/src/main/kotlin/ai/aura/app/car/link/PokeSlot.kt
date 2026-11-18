package ai.aura.app.car.link

import java.io.Closeable

/**
 * Sockets the car opened to the phone, waiting for the next session (ADR-0047).
 *
 * A head unit set to connect-device-first opens RFCOMM to the phone at ignition
 * and waits for Android Auto to answer; Headway never listened, so that attempt
 * died unanswered and the driver had to press Connect. Jarvis listens, and a
 * socket the car opens is parked here, keyed by the car's address, until
 * CarLinkService's next session takes it instead of dialling.
 *
 * Does not decide whether the address is a car: the listener only parks sockets
 * from a device the setup screen profiled.
 */
class PokeSlot<S : Closeable> {
    private val waiting = mutableMapOf<String, S>()

    @Synchronized
    fun offer(address: String, socket: S) {
        waiting.put(address, socket)?.let { runCatching { it.close() } }
    }

    @Synchronized
    fun take(address: String): S? = waiting.remove(address)

    @Synchronized
    fun clear() {
        waiting.values.forEach { runCatching { it.close() } }
        waiting.clear()
    }
}
