package ai.aura.app.car.setup

import ai.aura.app.ui.theme.ActiveTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The five gates a connection passes in series (Headway README, "Why the same car
 * behaves differently"), and the session after them. A failure at gate n hides every
 * gate after it, which is why a setting that fixes one gate can look like it fixed
 * nothing: the setup screen shows how far the attempt got instead of one timeout.
 */
enum class Gate(val label: String) {
    BLUETOOTH("Bluetooth"),
    ACCESS_POINT("The car's Wi-Fi"),
    ASSOCIATION("Joined"),
    ADDRESS("Address"),
    TLS("Secure link"),
    SESSION("On the car screen"),
}

/** What the driver can do about a failed gate, one button each (CarSetupActivity). */
enum class Remedy(val label: String) {
    TURN_ON_CAR_WIFI("Turn on Wi-Fi and Android Auto in the car's own settings"),
    FORGET_OTHER_PHONE("Another phone is connected to the car. Disconnect it and try again"),
    SAVE_NETWORK_WITH_STATIC_IP("This car hands out no addresses. Save its network with a static IP"),
    FIX_MAC_RANDOMISATION("The car gave no address. Use the device MAC for its network"),
    EXCLUDE_FROM_VPN("A VPN is keeping ${ActiveTheme.name} off the car's network. Exclude ${ActiveTheme.name} from it"),
    RETRY_CERTIFICATE("The car refused the certificate. ${ActiveTheme.name} tries the next one"),
}

data class GateReport(val reached: Gate, val passed: Boolean, val verdict: String?, val remedy: Remedy?) {
    companion object {
        val START = GateReport(Gate.BLUETOOTH, passed = false, verdict = null, remedy = null)
    }
}

/**
 * The remedy for a failure at [gate]. [verdict] is the platform's own word for a
 * failed join (`NOT_FOUND`, `NO_RESPONSE`, `AUTHENTICATION`, `ASSOCIATION`,
 * `IP_PROVISIONING`); [accessPointStatic] is what the car announced over
 * Bluetooth, or null before it said.
 *
 * Does not cover a failure whose cause is not visible from the phone -- a head unit
 * that simply stops answering after TLS -- which gets no button, only the retry.
 */
fun remedyFor(gate: Gate, verdict: String?, accessPointStatic: Boolean?, vpnActive: Boolean): Remedy? = when {
    verdict == "NOT_FOUND" || verdict == "NO_RESPONSE" -> Remedy.TURN_ON_CAR_WIFI
    // The 2021 Malibu announces STATIC and assigns nothing; that is the one this
    // project has seen, so an unannounced car gets the same answer first.
    verdict == "IP_PROVISIONING" -> if (accessPointStatic == false) Remedy.FIX_MAC_RANDOMISATION else Remedy.SAVE_NETWORK_WITH_STATIC_IP
    (gate == Gate.TLS || gate == Gate.SESSION) && vpnActive -> Remedy.EXCLUDE_FROM_VPN
    gate == Gate.TLS -> Remedy.RETRY_CERTIFICATE
    gate == Gate.BLUETOOTH -> Remedy.FORGET_OTHER_PHONE
    else -> null
}

/** Where the current attempt has got to, for the setup screen (one per process, as the link is). */
class CarGates {
    private val mutable = MutableStateFlow(GateReport.START)
    val state: StateFlow<GateReport> = mutable.asStateFlow()

    fun reached(gate: Gate) {
        mutable.value = GateReport(gate, passed = false, verdict = null, remedy = null)
    }

    fun passed(gate: Gate) {
        mutable.value = GateReport(gate, passed = true, verdict = null, remedy = null)
    }

    fun failed(gate: Gate, verdict: String?, accessPointStatic: Boolean?, vpnActive: Boolean) {
        mutable.value = GateReport(gate, passed = false, verdict = verdict, remedy = remedyFor(gate, verdict, accessPointStatic, vpnActive))
    }

    fun reset() {
        mutable.value = GateReport.START
    }

    companion object {
        /** The process's one link, the process's one report. */
        val shared: CarGates = CarGates()
    }
}
