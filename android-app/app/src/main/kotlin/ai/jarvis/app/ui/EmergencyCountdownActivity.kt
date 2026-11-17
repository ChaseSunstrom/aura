package ai.jarvis.app.ui

import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.ui.v2.ActionButton
import ai.jarvis.app.ui.v2.Body
import ai.jarvis.app.ui.v2.Figure
import ai.jarvis.app.ui.v2.IslandCard
import ai.jarvis.app.ui.v2.Label
import ai.jarvis.app.ui.v2.Stage
import ai.jarvis.app.ui.v2.Tone
import android.os.Bundle
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/**
 * The emergency countdown screen: the seconds left, who will be texted and called, the words that
 * will be sent, and one CANCEL control. It is the owner's whole consent for the alert (see `EmergencyAlert`), so it
 * does what a consent screen does and a little more:
 *
 *  * It shows over the lock screen and keeps the display on ([DoorActivity]), and refuses a touch
 *    that passes through another window, so an overlay cannot cancel it.
 *  * CANCEL is the only way out. Back is ignored on purpose: a stray Back (a pocket, a gesture)
 *    must not cancel a real emergency. Cancelling is a tap on a labelled control.
 *  * It is a view of [EmergencyCountdown.state] and nothing else; it finishes when the countdown
 *    ends, however it ended, and at once when opened with no countdown running (a stale
 *    notification).
 *
 * Does not cover a touch an accessibility service injects.
 */
class EmergencyCountdownActivity : DoorActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val view = ComposeView(this).apply {
            filterTouchesWhenObscured = true
            setContent { Stage { Screen() } }
        }
        setContentView(view)
        JarvisUi.fitSystemBars(view)
    }

    /** Only CANCEL cancels. */
    override fun onBack() = Unit

    /** There is no gate to arm: cancelling is safe from anywhere, locked or not. */
    override fun refreshGate() = Unit

    @Composable
    private fun Screen() {
        val now by EmergencyCountdown.state.collectAsState()
        val current = now
        // Null is the end of the countdown (or no countdown at all): this screen has nothing to show.
        LaunchedEffect(current == null) { if (current == null) finish() }
        if (current == null) return
        Box(Modifier.fillMaxWidth().padding(JarvisTokens.Space.SECTION.dp), contentAlignment = Alignment.Center) {
            IslandCard {
                Label("Emergency alert", tone = Tone.DANGER)
                Figure(
                    current.secondsLeft.toString(),
                    "s",
                    Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                    Tone.DANGER,
                )
                Body(current.summary)
                // The model wrote this; the owner reads exactly what will be sent, in quotes.
                Body("\"${current.what}\"")
                Body("Nothing is sent until the count reaches zero.", dim = true)
                ActionButton(
                    "Cancel",
                    onClick = { EmergencyCountdown.cancel() },
                    Modifier.fillMaxWidth(),
                    Tone.DANGER,
                    filled = true,
                )
            }
        }
    }
}
