package ai.jarvis.app.car.setup

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.car.link.BluetoothCarLink
import ai.jarvis.app.car.link.CarCompanion
import ai.jarvis.app.car.log.SessionLog
import ai.jarvis.app.car.service.CarLinkService
import ai.jarvis.app.ui.theme.AppTheme
import android.Manifest
import android.companion.AssociationInfo
import android.companion.CompanionDeviceManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.IntentSender
import android.content.pm.PackageManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import dev.headway.transport.LinkState
import kotlinx.coroutines.launch

/**
 * Setting up a car, once (ADR-0047). Replaces Headway's six-row checklist, its
 * separate "Pair the car's Wi-Fi" card and the static-IP recipe in its README with
 * one screen that runs the first connection itself and shows how far it gets.
 *
 * Does not replace Android's own prompts -- the Wi-Fi approval on the very first
 * join, the pairing chooser, the save-network sheet -- which no unprivileged app can
 * skip (docs/car/BLOCKERS.md B-005); it says what each one is for, one at a time.
 */
class CarSetupActivity : ComponentActivity() {

    private var ui by mutableStateOf(SetupUi(SetupStep.PERMISSIONS))

    private val needed = arrayOf(
        Manifest.permission.BLUETOOTH_CONNECT,
        Manifest.permission.BLUETOOTH_SCAN,
        Manifest.permission.NEARBY_WIFI_DEVICES,
        Manifest.permission.POST_NOTIFICATIONS,
    )

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { refresh() }

    private var pendingPairSsid: String? = null

    private val pairing = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
        val ssid = pendingPairSsid
        pendingPairSsid = null
        val info = result.data?.getParcelableExtra(CompanionDeviceManager.EXTRA_ASSOCIATION, AssociationInfo::class.java)
        if (result.resultCode != RESULT_OK || ssid == null || info == null) {
            say("Pairing cancelled")
        } else {
            val bssid = CarCompanion.of(this).remember(ssid, info)
            SessionLog.shared.info(TAG, "paired with the car's Wi-Fi $ssid at ${bssid ?: "no BSSID"}")
        }
        refresh()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AppTheme { CarSetupScreen(ui, actions) } }
        lifecycleScope.launch { CarGates.shared.state.collect { gates -> ui = ui.copy(gates = gates); refreshPairing() } }
        lifecycleScope.launch {
            CarLinkService.linkState.collect { state ->
                ui = ui.copy(running = state !is LinkState.Idle && state !is LinkState.GaveUp)
            }
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private val actions = SetupActions(
        grant = { quiet(); permissions.launch(needed) },
        openBluetooth = { quiet(); startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS)) },
        pick = { car -> ui = ui.copy(step = SetupStep.CONNECT, chosen = car, notice = null); refreshPairing() },
        connect = ::connect,
        pair = ::pair,
        remedy = ::remedy,
        copy = { value ->
            getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("car network", value))
            say("Copied $value", failed = false)
        },
        done = { finish() },
    )

    private fun granted(): Boolean = needed.all { ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED }

    @Suppress("MissingPermission")
    private fun refresh() {
        if (!granted()) {
            ui = ui.copy(step = SetupStep.PERMISSIONS)
            return
        }
        if (ui.step == SetupStep.PERMISSIONS) {
            val adapter = BluetoothCarLink.adapterOf(this)
            val cars = adapter?.let { a ->
                runCatching { BluetoothCarLink.bondedCars(a) }.getOrDefault(emptyList())
                    .map { CarChoice(runCatching { it.name }.getOrNull() ?: it.address, it.address) }
            }.orEmpty()
            ui = ui.copy(step = SetupStep.PICK_CAR, cars = cars)
        }
        refreshPairing()
    }

    private fun refreshPairing() {
        val car = ui.chosen ?: return
        val profile = CarProfiles.of(this, car.address)
        val ssid = profile?.ssid?.takeIf { it.isNotEmpty() }
        ui = ui.copy(
            canPair = ssid != null,
            paired = ssid != null && CarCompanion.of(this).associatedBssid(ssid) != null,
            staticIp = profile?.gateway?.let(::staticIpFor),
        )
    }

    private fun connect() {
        quiet()
        val car = ui.chosen ?: return
        CarGates.shared.reset()
        CarLinkService.start(this, carAddress = car.address)
    }

    private fun pair() {
        quiet()
        val car = ui.chosen ?: return
        val profile = CarProfiles.of(this, car.address) ?: return
        val manager = getSystemService(CompanionDeviceManager::class.java) ?: return say("Pairing is not available on this phone")
        pendingPairSsid = profile.ssid
        runCatching {
            // Filtered to the exact access point the car's handshake named, so the
            // chooser shows one row rather than a live scan of every network near.
            manager.associate(
                CarCompanion.of(this).requestFor(profile.ssid, profile.bssid),
                ContextCompat.getMainExecutor(this),
                object : CompanionDeviceManager.Callback() {
                    override fun onAssociationPending(intentSender: IntentSender) {
                        pairing.launch(IntentSenderRequest.Builder(intentSender).build())
                    }

                    override fun onFailure(error: CharSequence?) {
                        pendingPairSsid = null
                        say("The car's network was not found. Keep the car on and showing Android Auto, then try again.")
                    }
                },
            )
        }.onFailure { pendingPairSsid = null; say("Pairing is not available on this phone") }
    }

    private fun remedy(remedy: Remedy) {
        quiet()
        when (remedy) {
            Remedy.TURN_ON_CAR_WIFI, Remedy.RETRY_CERTIFICATE -> connect()
            Remedy.FORGET_OTHER_PHONE -> startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
            Remedy.FIX_MAC_RANDOMISATION -> startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
            Remedy.EXCLUDE_FROM_VPN -> startActivity(Intent(Settings.ACTION_VPN_SETTINGS))
            Remedy.SAVE_NETWORK_WITH_STATIC_IP -> saveCarNetwork()
        }
    }

    /**
     * Android's own "save this network" sheet, with the car's name and password filled
     * in from the last handshake. No unprivileged API sets a static IP on a requested
     * network, so the three values are on the setup screen beside it, one tap to copy.
     */
    private fun saveCarNetwork() {
        val known = CarLinkService.lastCarWifi(this) ?: return say("Connect once so ${ActiveTheme.name} learns the car's network")
        val suggestion = WifiNetworkSuggestion.Builder()
            .setSsid(known.ssid)
            .setWpa2Passphrase(known.passphrase)
            .setIsHiddenSsid(known.hiddenSsid)
            .build()
        runCatching {
            startActivity(
                Intent(Settings.ACTION_WIFI_ADD_NETWORKS)
                    .putParcelableArrayListExtra(Settings.EXTRA_WIFI_NETWORK_LIST, arrayListOf(suggestion)),
            )
        }.onFailure { startActivity(Intent(Settings.ACTION_WIFI_SETTINGS)) }
    }

    /** A word for the person, on the screen and announced (T-1324; a pop-up before, which left by itself and was cut on a small phone). */
    private fun say(text: String, failed: Boolean = true) {
        ui = ui.copy(notice = SetupNotice(text, failed))
    }

    /** The next action clears the last word, so a stale failure never sits beside a retry. */
    private fun quiet() {
        if (ui.notice != null) ui = ui.copy(notice = null)
    }

    private companion object {
        const val TAG = "CarSetup"
    }
}
