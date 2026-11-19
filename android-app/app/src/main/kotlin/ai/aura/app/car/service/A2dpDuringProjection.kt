package ai.aura.app.car.service

/**
 * The car's Bluetooth "Media audio" (A2DP) left connected while AURA projects, said once per
 * service start (T-1572). On the 2026-11-19 drive the sessions that started with a2dp connected
 * ended in the car's TCP reset 8 times in 18, after minutes of the car not acknowledging music;
 * with a2dp disconnected, 7 in 33. Google's Android Auto drops A2DP during projection, which needs
 * BLUETOOTH_PRIVILEGED; an app from the store can only ask the driver to turn it off for the car.
 *
 * Does not cover: proving A2DP causes the resets (the counts are one drive's), or the car's slow
 * Wi-Fi downlink, which is the head unit's.
 */
class A2dpDuringProjection {
    private var told = false

    /** The notice text the first time a session starts with [a2dp] "connected"; null otherwise. */
    @Synchronized
    fun check(a2dp: String): String? {
        if (a2dp != "connected" || told) return null
        told = true
        return REMEDY
    }

    companion object {
        const val TITLE = "Turn off the car's Media audio for this phone"

        const val REMEDY =
            "Bluetooth media audio is on while Android Auto runs over Wi-Fi; the car link resets " +
                "more often when the car streams both. In the car's Bluetooth settings " +
                "for this phone, turn Media audio off and keep Phone calls on. Music still plays " +
                "through Android Auto."

        const val LOG_LINE =
            "a2dp connected during projection: the car also streams Bluetooth media audio; sessions " +
                "like this reset more often on the 2026-11-19 drive (T-1572)"
    }
}
