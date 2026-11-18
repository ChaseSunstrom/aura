package ai.aura.app.car.service
import ai.aura.app.ui.theme.ActiveTheme


/**
 * The one line the car says a few seconds after it links, naming what will not work
 * this drive, so the driver hears it up front instead of finding out by failure (no
 * music because the screen-share grant is missing, a blank-looking car because the
 * console did not load, no answers because home is unreachable).
 *
 * The rule and its cases are `tests/contracts/car_connect_check.json`, run by
 * CarConnectCheckTest and `android-app/tools/car_connect_check_test.py`.
 *
 * Does not re-check later in the drive: a grant shared or a tailnet that comes up
 * after the line has been said is not announced, and a fault that starts mid-drive
 * is not either.
 */
object CarConnectCheck {
    const val HOME = "I can't reach home, so I can't answer and the car screen is on its own."
    const val CONSOLE = "The console isn't loading, so the car shows its own screen."
    const val MUSIC = "Music needs the screen-share tap today."

    /**
     * @param home the adapter answered `/api/status`; null when the phone has no Hermes link.
     * @param console the car page loaded; null when the phone has no console to show.
     * @param grantMissing music goes over the car's media channel and no screen-share grant is held.
     * @return the line to say, or null when nothing is wrong.
     */
    fun line(home: Boolean?, console: Boolean?, grantMissing: Boolean): String? {
        val parts = mutableListOf<String>()
        // The console is served from home: naming both would say one fault twice.
        if (home == false) parts += HOME else if (console == false) parts += CONSOLE
        if (grantMissing) parts += MUSIC
        return parts.joinToString(" ").ifEmpty { null }
    }

    /** Everything [line] can say, so each is kept as a clip in her voice (JarvisVoiceLines.FIXED). */
    val LINES: List<String> = listOf(HOME, CONSOLE, MUSIC, "$HOME $MUSIC", "$CONSOLE $MUSIC")

    /**
     * [ask] again, [gapMs] apart, until it says yes or [tries] are spent: the car page was
     * still loading eight seconds after the link and was called down (2026-11-12).
     *
     * @return true as soon as one answer is; null at once when there is nothing to ask about.
     */
    suspend fun settled(tries: Int, gapMs: Long, ask: suspend () -> Boolean?): Boolean? {
        repeat(tries) { n ->
            if (n > 0) kotlinx.coroutines.delay(gapMs)
            when (ask()) {
                true -> return true
                null -> return null
                else -> Unit
            }
        }
        return false
    }

    // {name} is the worn pack's; {app} is the app's fixed label, as a VPN app lists it.
    private const val TITLE = "{name} can't reach home from the car"
    private const val TEXT = "In the car Android keeps an app inside a VPN off the car's Wi-Fi, and an app left outside it has no way home. Give {name} its own tunnel: {name} > Settings > WireGuard > Scan WireGuard QR, with a peer of its own (one peer can't be up twice). Then exclude {app} in {vpn}."
    val REMEDY_TITLE: String get() = TITLE.replace("{name}", ActiveTheme.name)
    val REMEDY_TEXT: String get() = TEXT.replace("{name}", ActiveTheme.name).replace("{app}", ActiveTheme.appLabel)
    const val VPN_FALLBACK = "your VPN app"

    /**
     * What to do about home being out of reach in the car (T-0713): a VPN app's tunnel and the
     * car's Wi-Fi cannot both carry Jarvis, so it needs a tunnel of its own. The owner's phone
     * showed WireGuard connected and the car linked while Jarvis, excluded from the VPN, had
     * no way home; the car log said so and could not upload.
     *
     * @param vpnApps the VPN apps on the phone, by label (`CarWifiNetwork.vpnApps`).
     * @return the remedy, or null when home answers, there is no Hermes link, or Jarvis
     *   already runs its own tunnel (then the fault is elsewhere and this would mislead).
     */
    fun remedy(home: Boolean?, ownTunnel: Boolean, vpnApps: List<String>): String? {
        if (home != false || ownTunnel) return null
        return REMEDY_TEXT.replace("{vpn}", vpnApps.joinToString(" or ").ifEmpty { VPN_FALLBACK })
    }
}
