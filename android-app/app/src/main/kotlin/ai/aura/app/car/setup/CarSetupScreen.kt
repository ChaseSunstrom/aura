package ai.aura.app.car.setup

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.ui.v2.ActionButton
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Hairline
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.Projection
import ai.aura.app.ui.v2.Stage
import ai.aura.app.ui.v2.Tone
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** Where the setup is (ADR-0047): each step is one screen with one primary action. */
enum class SetupStep { PERMISSIONS, PICK_CAR, CONNECT }

data class CarChoice(val name: String, val address: String)

/** Everything the setup screen draws, and nothing it computes -- so a golden can draw any moment. */
data class SetupUi(
    val step: SetupStep,
    val cars: List<CarChoice> = emptyList(),
    val chosen: CarChoice? = null,
    val gates: GateReport = GateReport.START,
    val running: Boolean = false,
    val canPair: Boolean = false,
    val paired: Boolean = false,
    val staticIp: StaticIp? = null,
    val notice: SetupNotice? = null,
)

/** A short word from the setup (T-1324; a Toast before): [failed] lights it as a warning. */
data class SetupNotice(val text: String, val failed: Boolean = false)

class SetupActions(
    val grant: () -> Unit = {},
    val openBluetooth: () -> Unit = {},
    val pick: (CarChoice) -> Unit = {},
    val connect: () -> Unit = {},
    val pair: () -> Unit = {},
    val remedy: (Remedy) -> Unit = {},
    val copy: (String) -> Unit = {},
    val done: () -> Unit = {},
)

/**
 * One guided flow per car, in place of Headway's checklist of six grants, a
 * separate Wi-Fi pairing screen and a static-IP recipe in the README. The five
 * gates are drawn live as the attempt reaches them, and a failed gate shows its
 * one remedy.
 *
 * Drawn from the v2 kit alone (`ui/v2`, T-0710): the stage, projections for the lists,
 * the kit's words and controls. Nothing here is held rather than tapped, because nothing
 * here destroys anything: every control asks, opens a system page, copies or retries.
 */
@Composable
fun CarSetupScreen(ui: SetupUi, actions: SetupActions) {
    Stage {
        Column(
            Modifier
                .fillMaxSize()
                // Android 15 lays the window out behind the bars; the screen pads for them.
                .safeDrawingPadding()
                .padding(JarvisTokens.Space.SCREEN.dp),
            verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
        ) {
            // The notice sits below the scroll, not in it: a "Copied" said while the person is
            // down at the static-IP rows must not land off the top of the screen. It is a polite
            // live region so TalkBack says it without taking focus.
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
            ) { Steps(ui, actions) }
            ui.notice?.let { notice ->
                val tone = if (notice.failed) Tone.WARN else Tone.ACCENT
                Projection(Modifier.fillMaxWidth(), tone = tone) {
                    Body(notice.text, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, tone = tone)
                }
            }
        }
    }
}

@Composable
private fun Steps(ui: SetupUi, actions: SetupActions) {
    Headline("Set up your car")
    when (ui.step) {
        SetupStep.PERMISSIONS -> {
            Para("${ActiveTheme.name} needs Bluetooth to find your car, nearby Wi-Fi to join the car's own network, and notifications to say how the connection is going. None of these is used for your location.")
            Primary("Allow", actions.grant)
        }
        SetupStep.PICK_CAR -> {
            if (ui.cars.isEmpty()) {
                Para("No paired device offers Android Auto. Pair your phone with the car in Bluetooth settings first, then come back.")
                Primary("Open Bluetooth settings", actions.openBluetooth)
            } else {
                Para("Which of these is your car?")
                Choices(ui.cars.map { car -> Triple(car.name, car.address) { actions.pick(car) } })
            }
        }
        SetupStep.CONNECT -> Connect(ui, actions)
    }
}

@Composable
private fun Connect(ui: SetupUi, actions: SetupActions) {
    Para("${ui.chosen?.name ?: "Your car"}: keep the car on and showing Android Auto while ${ActiveTheme.name} connects.")
    Gates(ui.gates)
    ui.gates.remedy?.let { remedy ->
        Para(remedy.label)
        if (remedy == Remedy.SAVE_NETWORK_WITH_STATIC_IP && ui.staticIp != null) StaticIpBox(ui.staticIp, actions.copy)
        Primary(if (remedy == Remedy.RETRY_CERTIFICATE) "Try again" else "Fix this", { actions.remedy(remedy) })
    }
    if (ui.canPair && !ui.paired) {
        Para("Pair the car's Wi-Fi once and Android stops asking you to approve the network on every drive, so a drive can start with the phone in your pocket.")
        Primary("Pair the car's Wi-Fi", actions.pair)
    }
    when {
        ui.gates.reached == Gate.SESSION && ui.gates.passed -> {
            Para("${ActiveTheme.name} is on the car screen. From now on it connects when the car appears.")
            Primary("Done", actions.done)
        }
        // One filled control per screen: with a remedy showing, that is the remedy.
        !ui.running -> if (ui.gates.remedy != null) ActionButton("Connect again", actions.connect) else Primary("Connect", actions.connect)
    }
}

@Composable
private fun Gates(report: GateReport) {
    // A failed gate lights the whole projection, so the list reads as stopped from across the room.
    val failed = report.verdict != null || report.remedy != null
    Projection(Modifier.fillMaxWidth(), tone = if (failed) Tone.DANGER else Tone.ACCENT) {
        Gate.entries.forEachIndexed { index, gate ->
            val state = when {
                gate.ordinal < report.reached.ordinal || (gate == report.reached && report.passed) -> "done"
                gate == report.reached && report.verdict == null && report.remedy == null -> "now"
                gate == report.reached -> "failed"
                else -> "waiting"
            }
            // The state is said in its word; the tone only repeats it (the kit has no dot).
            val tone = when (state) {
                "done" -> Tone.OK
                "now" -> Tone.ACCENT
                "failed" -> Tone.DANGER
                else -> null
            }
            if (index > 0) Hairline()
            Row(
                Modifier.fillMaxWidth().semantics { contentDescription = "${gate.label}: $state" },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Body(gate.label, Modifier.weight(1f), dim = tone == null)
                Label(state, tone = tone)
            }
        }
    }
}

@Composable
private fun StaticIpBox(ip: StaticIp, copy: (String) -> Unit) {
    Choices(
        listOf("IP address" to ip.address, "Gateway" to ip.gateway, "Network prefix length" to ip.prefix.toString())
            .map { (label, value) -> Triple(label, value) { copy(value) } },
        // Inside the projection, on its backing: on the bare stage, at font scale 1.3, a star sat
        // under this paragraph's corner and the dim words read 4.19:1 in the accessibility gate.
        intro = "Android opens the car's network with its name and password filled in. Choose Advanced, then IP settings: Static, and enter:",
    )
}

/** Explanation is dim, as every other screen's is; the bright text is for what a person picks. */
@Composable
private fun Para(text: String) = Body(text, dim = true)

/** The screen's one filled control; a label wide, like every button on the other phone screens. */
@Composable
private fun Primary(label: String, onClick: () -> Unit) = ActionButton(label, onClick, filled = true)

/** Rows to tap, in one projection: what it is, bright, and its detail beside it, dim. */
@Composable
private fun Choices(rows: List<Triple<String, String, () -> Unit>>, intro: String? = null) {
    Projection(Modifier.fillMaxWidth()) {
        intro?.let { Para(it) }
        rows.forEachIndexed { index, (title, detail, onClick) ->
            if (index > 0 || intro != null) Hairline()
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = JarvisTokens.Size.TOUCH.dp)
                    .clickable(role = Role.Button, onClick = onClick),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
            ) {
                Body(title, Modifier.weight(1f))
                Body(detail, dim = true)
            }
        }
    }
}
