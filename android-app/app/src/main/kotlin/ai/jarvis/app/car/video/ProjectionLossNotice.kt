package ai.jarvis.app.car.video

/**
 * The platform ends the screen-sharing grant whenever the phone locks (Android 15 and
 * later, and no app can opt out), and music and app panes go with it. Until this the car
 * said nothing, so the owner heard the music stop and could not tell why.
 *
 * Says [TEXT] once per grant lost, and only when a grant was held and the car is
 * connected: a grant never given has nothing to lose, and a car that went first has no
 * one to tell. The service reports one loss from three places (the service's own
 * callback, the app pane's, and a stale token found at a foreground claim); the first
 * says it and the rest find the grant already gone.
 *
 * Does not cover: Android delivering `onStop` on a particular phone's lock (needs a
 * phone), nor re-asking for the grant, which the system dialog cannot do from the car.
 * Not thread-safe: every caller is on the service's main looper.
 */
class ProjectionLossNotice(private val say: (String) -> Unit) {
    var held = false
        private set
    private var connected = false

    /** A grant was adopted. */
    fun granted() {
        held = true
    }

    /** The car's session came up or went. */
    fun carConnected(on: Boolean) {
        connected = on
    }

    /** The grant stopped, from whichever path found out. */
    fun stopped() {
        if (!held) return
        held = false
        if (!connected) return
        // A voice that fails costs the line, not the loss: [held] is already clear, so a
        // second report does not try again.
        runCatching { say(TEXT) }
    }

    companion object {
        /**
         * Spoken: no command in it, a driver cannot type one. The one-time adb line that
         * keeps the grant through a lock is in the notification and in adb-setup.txt.
         */
        const val TEXT = "Screen sharing stopped when the phone locked. " +
            "Unlock the phone and tap the notification to share again."
    }
}
