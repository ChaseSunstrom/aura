package ai.aura.app

import ai.aura.app.assist.BundledVoice
import ai.aura.app.assist.OnPhoneSpeech
import ai.aura.app.assist.ModelStore
import ai.aura.app.assist.OfflineVoice
import ai.aura.app.assist.OnDeviceWakeWord
import ai.aura.app.assist.WakeSensitivity
import ai.aura.app.assist.WakeWordService
import ai.aura.app.automation.JarvisAutomationService
import ai.aura.app.automation.actions.ActionEnv
import ai.aura.app.automation.policy.PolicyStore
import ai.aura.app.channel.ChannelConfig
import ai.aura.app.channel.DeviceChannelHost
import ai.aura.app.compat.GrapheneCompat
import ai.aura.app.car.input.HeadwayAccessibilityService
import ai.aura.app.config.JarvisConfig
import ai.aura.app.assist.hermes.HermesClient
import ai.aura.app.assist.hermes.HermesRpc
import ai.aura.app.assist.hermes.HermesStatus
import ai.aura.app.config.HermesPairing
import ai.aura.app.config.HermesSettings
import ai.aura.app.config.PairingClaim
import ai.aura.app.config.PairingPayload
import ai.aura.app.config.ServerDiscovery
import ai.aura.app.config.ServerKind
import ai.aura.app.config.ServerUrl
import ai.aura.app.update.UpdateChecker
import ai.aura.app.ui.JarvisScreens
import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisThemes
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.ui.v2.ActionButton
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Field
import ai.aura.app.ui.v2.Fold
import ai.aura.app.ui.v2.Hairline
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.Meter
import ai.aura.app.ui.v2.RuleChip
import ai.aura.app.ui.v2.ScreenState
import ai.aura.app.ui.v2.Stage
import ai.aura.app.ui.v2.Toggle
import ai.aura.app.ui.v2.Tone
import ai.aura.app.ui.v2.Screen as StateKind
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** A typed field's state, under the names the logic below used on its EditText. */
private class Typed(initial: String) {
    var text by mutableStateOf(initial)
    fun setText(value: CharSequence) {
        text = value.toString()
    }
}

/** A switch's state, under the name the logic below used on its Switch. */
private class Checked(initial: Boolean) {
    var isChecked by mutableStateOf(initial)
}

/** A status line: what is ACTUALLY happening, and whether that needs the person. */
private class Line(initial: String = "") {
    var text by mutableStateOf(initial)
    var warn by mutableStateOf(false)
}

/** The update download's progress, shown only while an APK is coming down. */
private class Progress {
    var shown by mutableStateOf(false)
    var isIndeterminate by mutableStateOf(false)
    var progress by mutableStateOf(0)
}

/**
 * Connection and behaviour settings, on the v2 kit (T-0639): one short screen of folds, each
 * one line saying what it is set to now, opened for its controls (v2 §1: progressive
 * disclosure; the owner, 2026-10-08: "the settings are unorganized and unintuitive").
 *
 * The logic is the Views screen's, unchanged: the controls are state holders under the names
 * it already read ([Typed], [Checked], [Line]), so save, the dirty check, pairing and updates
 * did not move. The screen is deliberately plain-spoken about what it is asking for — the
 * token it stores is the key to the whole house.
 */
class SettingsActivity : ComponentActivity() {

    private lateinit var config: JarvisConfig

    private lateinit var urlField: Typed
    private lateinit var tokenField: Typed
    private lateinit var pipelineField: Typed
    private lateinit var deviceNameField: Typed

    private val overlayStatus = Line()
    private val listenStatus = Line()
    private val modelStatus = Line()
    private lateinit var wakeOnDevice: Checked
    private lateinit var wakeServerFallback: Checked
    private lateinit var sttOnDevice: Checked
    private val sttStatus = Line()
    private val updateStatus = Line()
    /** The download's bar under [updateStatus]; shown only while an APK is coming down. */
    private val updateProgress = Progress()
    private lateinit var prereleaseUpdates: Checked
    private lateinit var autoUpdate: Checked
    private lateinit var autoInstall: Checked
    private lateinit var wakeEnabled: Checked
    private lateinit var wakeInCar: Checked
    /** New messages read aloud in the car, with a reply by voice (CarMessageAnnouncer). */
    private lateinit var carReadMessages: Checked
    private lateinit var carMap3d: Checked
    /** The fall and crash watch (T-1209): off by default, untuned. */
    private lateinit var impactWatch: Checked
    /** The automation master switch (PolicyStore): Jarvis may act on this phone -- text, call, alarms. */
    private lateinit var phoneActions: Checked
    private lateinit var textingDelegated: Checked
    /** The car's reactor moves with the music (CarMusicLevel). */
    private lateinit var carMusicPulse: Checked
    private lateinit var wakeAtHome: Checked
    private lateinit var headsetMode: Checked
    private lateinit var headsetWarmLink: Checked
    private lateinit var personaWake: Checked
    private val tailnetStatus = Line()
    private lateinit var headsetButton: Checked
    /** The island resting on the camera, and the phone's status in it (T-0890). */
    private lateinit var islandRest: Checked
    private lateinit var islandStatus: Checked
    /** The conversation's island at the bottom in every state, not on the camera (T-0968). */
    private lateinit var islandBottom: Checked
    private lateinit var islandCover: Checked
    /** How many required grants are missing, refreshed on resume. */
    private val permissionStatus = Line()

    /**
     * Waking hours, as hours rather than text.
     *
     * They used to be two `EditText`s that accepted "25", "nine" and the empty
     * string for a value with twenty-four possibilities, each needing its own
     * parse, fallback and explanatory hint. Start is 0..23; end is 1..24, where
     * 24 means midnight — see `WakeWordGate`, which treats the window as
     * half-open, so the end steps past 23 to 24 and never to 0.
     */
    private var wakingStart by mutableStateOf(0)
    private var wakingEnd by mutableStateOf(24)

    /** The wake sensitivity the chooser shows; stored on save like the rest. */
    private var wakeSensitivity by mutableStateOf(WakeSensitivity.NORMAL)

    /** Which server the address turned out to be, and for the adapter, its model. */
    private val serverKindStatus = Line()

    /**
     * A kind learned from a scanned adapter QR, applied on save only when the
     * saved URL is the one that was scanned — a hand-edited address after the
     * scan is a different server until it says otherwise.
     */
    private var scannedKind: Pair<String, ServerKind>? = null

    /** The adapter a claim at [first] handed over, saved with that address. */
    private var claimedAdapter: Pair<String, HermesPairing>? = null

    /** The folds that are open. All start folded: the screen opens as its one-line summaries. */
    private val opened = mutableStateListOf<String>()

    /** The Jarvis fold's not-ready face; null once its settings are loaded. */
    private var hermesState by mutableStateOf<NotReady?>(null)
    private var persona by mutableStateOf("")
    private val hermesValues = mutableStateMapOf<String, Any?>()
    private val hermesNotes = mutableStateMapOf<String, String>()
    private var hermes: HermesClient? = null

    /** Bumped by each save, so the save bar recomposes against what is now stored. */
    private var savedCount by mutableStateOf(0)
    private var hermesSpecs: List<HermesSettings.Spec> = emptyList()

    override fun onDestroy() {
        hermes?.close()
        super.onDestroy()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Back is the other way off this screen, and it discarded edits exactly as silently
        // as the tab strip did. A dispatcher callback, not an onBackPressed override: lint
        // fails an override that does not call super (MissingSuperCall), and the super call
        // would leave before the person answers the dialog.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = leaveIfSaved { finish() }
        })
        config = JarvisConfig(this)
        val policy = PolicyStore(applicationContext)
        urlField = Typed(config.serverUrl)
        tokenField = Typed(config.token)
        pipelineField = Typed(config.pipeline)
        deviceNameField = Typed(config.deviceName)
        wakeEnabled = Checked(config.wakeWordEnabled)
        wakeOnDevice = Checked(config.wakeWordOnDevice)
        // T-0566: until the phone's own model is here, the house can listen instead -- which
        // means everything the mic hears goes to it. The owner may refuse that.
        wakeServerFallback = Checked(config.wakeServerFallback)
        personaWake = Checked(config.personaWakeWords)
        sttOnDevice = Checked(config.sttOnDevice)
        wakeInCar = Checked(config.wakeInCar)
        wakeAtHome = Checked(config.wakeAtHome)
        headsetMode = Checked(config.headsetMode)
        headsetButton = Checked(config.headsetButton)
        headsetWarmLink = Checked(config.warmLink)
        phoneActions = Checked(policy.automationEnabled)
        textingDelegated = Checked(policy.textingDelegated)
        carReadMessages = Checked(config.carReadMessages)
        carMap3d = Checked(config.carMap3d)
        impactWatch = Checked(config.impactWatch)
        carMusicPulse = Checked(config.carMusicPulse)
        islandRest = Checked(config.islandRest)
        islandStatus = Checked(config.islandStatus)
        islandBottom = Checked(config.islandBottom)
        islandCover = Checked(config.islandCover)
        autoUpdate = Checked(config.autoUpdateEnabled)
        autoInstall = Checked(config.autoInstallUpdates)
        prereleaseUpdates = Checked(config.allowPrereleaseUpdates)
        wakingStart = config.wakingHourStart.coerceIn(0, 23)
        wakingEnd = config.wakingHourEnd.coerceIn(1, 24)
        wakeSensitivity = config.wakeSensitivity
        updateStatus.text = "Version ${appVersionName()} (build ${appVersionCode()})"
        // With no server yet the address is the one thing this screen is for.
        if (!config.isConfigured) opened += "Server"
        refreshTailnetStatus()
        hermesSpecs = runCatching {
            HermesSettings.contract(assets.open(CONTRACT).bufferedReader().use { it.readText() }).second
        }.getOrDefault(emptyList()).filter { it.key in SHORT_LIST }
        setContent { Screen() }
        connectHermes()
    }

    // --- the screen -------------------------------------------------------------

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Screen() {
        Stage {
            Column(Modifier.fillMaxSize().safeDrawingPadding()) {
                Column(
                    Modifier
                        .weight(1f)
                        .verticalScroll(rememberScrollState())
                        .padding(JarvisTokens.Space.SCREEN.dp),
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
                ) {
                    // The console's ScreenTitle: what this screen is, in a sentence.
                    Headline("Phone")
                    Body(getString(R.string.settings_lede), dim = true)
                    Buttons {
                        // One control for every paragraph below. See [explanationsShown].
                        ActionButton(explanationsLabel(), onClick = ::toggleExplanations)
                    }
                    Connection()
                    // The worn pack's own settings (persona and a short list), which were a
                    // second screen; the long list is the console's, one tap below.
                    Hermes()
                    Voice()
                    WhenToListen()
                    Headset()
                    Island()
                    Actions()
                    Car()
                    Permissions()
                    More()
                    Updates()
                }
                // SAVE stays on screen rather than at the foot of a long scroll: the instrumented
                // suite once lost it below the fold, and a person editing the top had to scroll
                // the whole screen to keep the edit.
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = JarvisTokens.Space.SCREEN.dp, vertical = JarvisTokens.Space.STEP.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
                ) {
                    Label(if (isDirty()) "Unsaved changes" else "Saved", Modifier.weight(1f), tone = if (isDirty()) Tone.WARN else null)
                    ActionButton("Save", onClick = { save() }, filled = true)
                }
            }
        }
    }

    @Composable
    private fun Part(title: String, summary: String, tone: Tone = Tone.ACCENT, content: @Composable ColumnScope.() -> Unit) {
        val open = title in opened
        Fold(title, summary, open, onToggle = { if (open) opened.remove(title) else opened.add(title) }, tone = tone, content = content)
    }

    /**
     * The fold's own line is what is actually at that address: jarvis-core, the console's relay,
     * or the Hermes adapter, and for the adapter its model and whether it is still loading.
     */
    @Composable
    private fun Connection() = Part("Server", serverKindStatus.text.ifEmpty { NO_SERVER }, if (serverKindStatus.warn) Tone.WARN else Tone.ACCENT) {
        Field("Server URL", urlField.text, { urlField.text = it }, placeholder = "http://192.168.2.10:8123")
        Explain(R.string.settings_server_url_explain)
        Field("Access token", tokenField.text, { tokenField.text = it }, placeholder = "long-lived access token", secret = true)
        Buttons {
            ActionButton("Paste", onClick = ::pasteToken)
            ActionButton("Scan QR", onClick = ::scanToken)
        }
        Explain(R.string.settings_token_explain)
        Field("Pipeline name", pipelineField.text, { pipelineField.text = it }, placeholder = JarvisConfig.DEFAULT_PIPELINE)
        Field("Device name", deviceNameField.text, { deviceNameField.text = it }, placeholder = "This phone")
        Explain(R.string.settings_device_name_explain, config.deviceId)
        // The car link needs the car's Wi-Fi and Jarvis needs home, and a VPN app will not
        // let one app have both: Jarvis runs its own WireGuard tunnel (2026-10-29).
        Label("WireGuard")
        Status(tailnetStatus)
        // Any scan or paste of a wg-quick config lands in [takeWireGuard].
        Buttons {
            ActionButton("Scan WireGuard QR", onClick = ::scanToken)
            ActionButton("Forget WireGuard", onClick = ::forgetWireGuard)
        }
        Explain(TAILNET_EXPLAIN)
    }

    @Composable
    private fun Voice() = Part(
        "Voice",
        if (wakeEnabled.isChecked) "Listening for “${ActiveTheme.wakePhrase}”" else "Not listening for its name",
        if (wakeEnabled.isChecked && listenStatus.warn) Tone.WARN else Tone.ACCENT,
    ) {
        Toggle("Listen for \"${ActiveTheme.wakePhrase}\"", wakeEnabled.isChecked, { wakeEnabled.isChecked = it })
        Explain(R.string.settings_wake_word_explain)
        // Whether listening can come back on its own after a restart, which is a different
        // question from whether it is switched on. Android will not let a microphone service
        // start from the background, so without one of these two grants the switch above is
        // only true until the next reboot — and the failure is silent.
        Status(listenStatus)
        Buttons {
            ActionButton("Allow background", onClick = ::requestBackgroundStart)
            ActionButton("Display over apps", onClick = ::openOverlaySetting)
        }
        Status(overlayStatus)

        Label("On this phone")
        Explain(R.string.settings_on_device_explain)
        // Every one of these switches has a line of status under it that says what is ACTUALLY
        // happening, refreshed as it flips, not only on save.
        Toggle("Detect “${ActiveTheme.wakePhrase}” on this phone", wakeOnDevice.isChecked, { wakeOnDevice.isChecked = it; refreshModelStatus() })
        Toggle("Let the house listen until this phone’s model is ready", wakeServerFallback.isChecked, { wakeServerFallback.isChecked = it })
        // "Hey Aura" wakes the assistant as AURA whichever pack is worn, on this phone, once
        // its model is downloaded below.
        val personaPhrase = "Hey " + JarvisThemes.AURA.spokenName
        Toggle("Wake by persona name (“$personaPhrase”)", personaWake.isChecked, { personaWake.isChecked = it })
        Label(getString(R.string.settings_wake_sensitivity))
        Buttons {
            for (level in WakeSensitivity.entries) {
                RuleChip(sensitivityLabel(level), selected = wakeSensitivity == level, onClick = { wakeSensitivity = level }, kind = "Wake")
            }
        }
        Explain(R.string.settings_wake_sensitivity_explain)
        // T-0714: the owner's own ways of saying it, rather than a lower bar for every voice.
        Buttons {
            ActionButton("Teach it the name", onClick = { startActivity(Intent(this@SettingsActivity, WakeEnrolActivity::class.java)) })
        }
        Status(modelStatus)
        Buttons {
            ActionButton("Download models", onClick = ::downloadModels)
            ActionButton("Delete models", onClick = ::deleteModels)
        }

        // Named for the thing it is, because the two are constantly confused: the models above
        // are the WAKE WORD's, and they have nothing to do with transcription.
        Label("Speech to text")
        Explain(R.string.settings_stt_explain)
        Toggle("Transcribe on this phone", sttOnDevice.isChecked, { sttOnDevice.isChecked = it; refreshSttStatus() })
        Status(sttStatus)

        Label("Whose voice")
        Explain(R.string.settings_voice_identity_explain)
        Buttons {
            ActionButton("Teach ${ActiveTheme.name} my voice", onClick = { startActivity(Intent(this@SettingsActivity, VoiceIdentityActivity::class.java)) })
        }
        Explain(R.string.settings_voice_identity_conflict_explain)
    }

    /**
     * When the phone listens. `WakeListenWatch` gathers the signals and consults `WakeWordGate`
     * before every microphone open. "Am I at home" is usually unknowable on a phone, so it is
     * modelled as unknown rather than false; the explanation says what happens then.
     */
    @Composable
    private fun WhenToListen() = Part("When to listen", listenSummary()) {
        Toggle("While car Bluetooth is connected", wakeInCar.isChecked, { wakeInCar.isChecked = it })
        Toggle("While at home, during waking hours", wakeAtHome.isChecked, { wakeAtHome.isChecked = it })
        HourStep("From", wakingStart, { wakingStart = (wakingStart + 23) % 24 }, { wakingStart = (wakingStart + 1) % 24 })
        HourStep("Until", wakingEnd, { wakingEnd = if (wakingEnd <= 1) 24 else wakingEnd - 1 }, { wakingEnd = if (wakingEnd >= 24) 1 else wakingEnd + 1 })
        Explain(R.string.settings_when_to_listen_explain)
    }

    private fun listenSummary(): String {
        val hours = "%02d:00–%02d:00".format(wakingStart, wakingEnd % 24)
        return listOfNotNull(
            "In the car".takeIf { wakeInCar.isChecked },
            "At home, $hours".takeIf { wakeAtHome.isChecked },
        ).joinToString(" · ").ifEmpty { "Whenever it is switched on" }
    }

    /**
     * Off by default, deliberately: plugging in a headset must never silently move the
     * microphone off the phone. These had getters, defaults and docs/how-to/earpiece.md and no
     * way to turn any of them on, until this screen gave them one.
     */
    @Composable
    private fun Headset() = Part("Headset", if (headsetMode.isChecked) "Captures through a connected headset" else "Off") {
        Toggle("Capture through a connected headset", headsetMode.isChecked, { headsetMode.isChecked = it })
        Toggle("Let its button summon ${ActiveTheme.name}", headsetButton.isChecked, { headsetButton.isChecked = it }, enabled = headsetMode.isChecked)
        Toggle("Keep listening after a reply", headsetWarmLink.isChecked, { headsetWarmLink.isChecked = it }, enabled = headsetMode.isChecked)
        Explain(R.string.settings_headset_explain)
    }

    /**
     * The island at the camera (T-0890; the owner, 2026-11-09: "this should be able to be turned
     * off"). The explanation says why the system's own status icons stay beside it.
     */
    @Composable
    private fun Island() = Part("Island", if (islandRest.isChecked) "Rests on the camera while ${ActiveTheme.name} is on" else "Off") {
        Toggle("Keep the island at the camera", islandRest.isChecked, { islandRest.isChecked = it })
        Toggle("Show the phone's status in the island", islandStatus.isChecked, { islandStatus.isChecked = it }, enabled = islandRest.isChecked)
        // The cover is drawn by the car's touch service: switched on without it, its page opens.
        Toggle("Cover the phone's status icons", islandCover.isChecked, {
            islandCover.isChecked = it
            if (it && !HeadwayAccessibilityService.isEnabled(this@SettingsActivity)) startActivity(HeadwayAccessibilityService.settingsIntent(this@SettingsActivity))
        }, enabled = islandRest.isChecked && islandStatus.isChecked)
        Toggle("Keep conversations at the bottom of the screen", islandBottom.isChecked, { islandBottom.isChecked = it })
        Explain(R.string.settings_island_explain)
    }

    /**
     * What Jarvis may do on this phone. These sat under Listen with the wake-word switches (the
     * owner, 2026-10-08): a texting consent between two listening rules read as one more rule.
     */
    @Composable
    private fun Actions() = Part("Actions", if (phoneActions.isChecked) "${ActiveTheme.name} can act on this phone" else "${ActiveTheme.name} only answers") {
        // The phone's own consent for Jarvis acting on it (M22: off until turned on). It lived
        // only on the Phone tasks screen, so "text Mom" was refused with "automation is switched
        // off" and no switch anyone could find (the owner, 2026-09-25).
        Toggle("${ActiveTheme.name} can text, call and set alarms on this phone", phoneActions.isChecked, { phoneActions.isChecked = it })
        // The owner's standing yes for texts (2026-10-07). Only here, on the phone: no server
        // message can turn it on. Replies go unasked only to a contact who texted this week,
        // 20 a day (TextingDelegation).
        Toggle("Read my texts, and reply to contacts, without asking", textingDelegated.isChecked, { textingDelegated.isChecked = it })
        // Beside what the phone may do for the owner in an emergency: the alert's countdown is still the owner's cancel.
        Toggle("Start an emergency alert after a hard fall or crash (untuned, off by default)", impactWatch.isChecked, { impactWatch.isChecked = it })
        Explain("Watches this phone's motion for a hard impact followed by stillness, then counts down so you can cancel before anyone is texted or called. The thresholds are a first guess and have not been tried on a real fall, so it may miss one or start an alert you did not need. It reaches the people the house last sent this phone; until then it has no one to alert.")
    }

    /**
     * The car. Reported as "why is Jarvis not showing up in Android Auto": the build is
     * sideloaded, and Android Auto's unknown-sources option does not cover Car App Library apps.
     * Nothing here can change that, so the setup says so instead of letting it look broken.
     */
    @Composable
    private fun Car() = Part("Car", if (carReadMessages.isChecked) "Reads new messages aloud" else "Keeps messages quiet") {
        Explain(R.string.settings_car_setup_explain)
        Buttons {
            ActionButton("Set up a car", onClick = {
                startActivity(Intent(this@SettingsActivity, ai.aura.app.car.setup.CarSetupActivity::class.java))
            })
            ActionButton("Car self-test", onClick = ::shareCarSelfTest)
            ActionButton("Share drive log", onClick = ::shareDriveLog)
        }
        Explain(R.string.settings_car_explain)
        Toggle("Read new messages aloud in the car", carReadMessages.isChecked, { carReadMessages.isChecked = it })
        Toggle("Reactor moves with the music in the car", carMusicPulse.isChecked, { carMusicPulse.isChecked = it })
        Toggle("3D map in the car (paused: the flat map is the default)", carMap3d.isChecked, { carMap3d.isChecked = it })
    }

    /**
     * One way to every grant, not a grid of raw Settings shortcuts with no state:
     * SystemCheckActivity lists each one WITH its state, whether it is required, and what
     * breaks without it.
     */
    @Composable
    private fun Permissions() = Part("Permissions", permissionStatus.text, if (permissionStatus.warn) Tone.WARN else Tone.ACCENT) {
        Buttons {
            ActionButton("System check", onClick = {
                startActivity(Intent(this@SettingsActivity, ai.aura.app.ui.SystemCheckActivity::class.java))
            })
            ActionButton("App info", onClick = ::openAppInfo)
        }
        Explain(R.string.settings_permissions_explain)
    }

    @Composable
    private fun More() = Part("More", "Phone tasks, the audit log, crash logs, approvals") {
        Buttons {
            // NOT "automations": the console has a page by that name and it is the HOUSE's;
            // this one lists the tasks this phone runs by itself.
            ActionButton("Phone tasks", onClick = { JarvisScreens.open(this@SettingsActivity, JarvisScreens.AUTOMATIONS, "Phone tasks") })
            ActionButton("Audit log", onClick = { JarvisScreens.open(this@SettingsActivity, JarvisScreens.AUDIT_LOG, "The audit log") })
        }
        Explain(R.string.settings_more_explain)
        Buttons {
            ActionButton("Crash logs", onClick = {
                startActivity(Intent(this@SettingsActivity, ai.aura.app.ui.CrashLogActivity::class.java))
            })
            ActionButton("Approvals", onClick = { JarvisScreens.open(this@SettingsActivity, JarvisScreens.ACTION_POLICY, "Action approvals") })
        }
        Explain(R.string.settings_logs_explain)
    }

    @Composable
    private fun Updates() = Part("Updates", updateStatus.text) {
        Status(updateStatus)
        if (updateProgress.shown) Meter(if (updateProgress.isIndeterminate) null else updateProgress.progress / 100f)
        Toggle("Check for updates automatically", autoUpdate.isChecked, { autoUpdate.isChecked = it })
        Toggle("Install updates automatically", autoInstall.isChecked, { autoInstall.isChecked = it })
        Toggle("Include test builds", prereleaseUpdates.isChecked, { prereleaseUpdates.isChecked = it })
        Buttons {
            ActionButton("Check for updates", onClick = ::checkForUpdates)
            ActionButton("Releases", onClick = ::openReleasesPage)
            // The grant without which none of the above can finish: a per-app decision since
            // Android 8, so another one that looked handled and was not.
            ActionButton("Install permission", onClick = ::openInstallPermission)
        }
    }

    /** "From  EARLIER 08:00 LATER": words, not glyphs, so TalkBack says what each does. */
    @Composable
    private fun HourStep(name: String, hour: Int, earlier: () -> Unit, later: () -> Unit) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
        ) {
            Label(name, Modifier.weight(1f))
            ActionButton("Earlier", onClick = earlier)
            Body("%02d:00".format(hour % 24))
            ActionButton("Later", onClick = later)
        }
    }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Buttons(content: @Composable () -> Unit) {
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
            verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
        ) { content() }
    }

    @Composable
    private fun Status(line: Line) {
        if (line.text.isNotEmpty()) Body(line.text, dim = !line.warn, tone = if (line.warn) Tone.WARN else null)
    }

    /** A paragraph worth reading once: folded away until [explanationsShown]. */
    @Composable
    private fun Explain(@StringRes id: Int, vararg args: Any) = Explain(getString(id, *args))

    @Composable
    private fun Explain(text: String) {
        if (explanationsShown) Body(text, dim = true)
    }

    private fun sensitivityLabel(level: WakeSensitivity): String = getString(
        when (level) {
            WakeSensitivity.STRICT -> R.string.settings_wake_sensitivity_strict
            WakeSensitivity.NORMAL -> R.string.settings_wake_sensitivity_normal
            WakeSensitivity.EAGER -> R.string.settings_wake_sensitivity_eager
        }
    )

    // --- leaving with unsaved edits -----------------------------------------

    /**
     * Every field's saved value, as one comparable string.
     *
     * Compared against the same function run over the *controls* — see
     * [isDirty]. A snapshot rather than a per-control dirty flag because there
     * are eleven of them across four types (EditText, Switch, two chooser
     * indices) and a flag on each is eleven places to forget one; this is one
     * place, and adding a control that is not in it is a visible omission rather
     * than a silent one.
     */
    private fun savedSnapshot(): String = listOf(
        ServerUrl.normalize(config.serverUrl),
        config.token,
        config.pipeline,
        config.deviceName,
        config.allowPrereleaseUpdates,
        config.autoUpdateEnabled,
        config.autoInstallUpdates,
        config.wakeWordEnabled,
        config.wakeWordOnDevice,
        config.wakeServerFallback,
        config.sttOnDevice,
        config.wakeInCar,
        config.wakeAtHome,
        config.wakeSensitivity,
        config.wakingHourStart,
        config.wakingHourEnd,
        config.headsetMode,
        config.headsetButton,
        config.warmLink,
        config.personaWakeWords,
        config.carReadMessages,
        config.carMap3d,
        config.impactWatch,
        config.carMusicPulse,
        config.islandRest,
        config.islandStatus,
        config.islandBottom,
        config.islandCover,
        ai.aura.app.automation.policy.PolicyStore(applicationContext).automationEnabled,
        ai.aura.app.automation.policy.PolicyStore(applicationContext).textingDelegated,
    ).joinToString(" ")

    private fun editedSnapshot(): String = listOf(
        ServerUrl.normalize(urlField.text.toString()),
        tokenField.text.toString().trim(),
        // Through the same normalisation `save` applies, or an empty pipeline
        // box would read as an edit against the default it is about to become.
        pipelineField.text.toString().trim().ifEmpty { JarvisConfig.DEFAULT_PIPELINE },
        deviceNameField.text.toString().trim().ifEmpty { config.deviceName },
        prereleaseUpdates.isChecked,
        autoUpdate.isChecked,
        autoInstall.isChecked,
        wakeEnabled.isChecked,
        wakeOnDevice.isChecked,
        wakeServerFallback.isChecked,
        sttOnDevice.isChecked,
        wakeInCar.isChecked,
        wakeAtHome.isChecked,
        wakeSensitivity,
        wakingStart,
        wakingEnd,
        headsetMode.isChecked,
        // The two that read `headsetMode` in their own getters, mirrored here so
        // the comparison sees what `save` will actually store rather than what
        // the switch shows.
        headsetMode.isChecked && headsetButton.isChecked,
        headsetMode.isChecked && headsetWarmLink.isChecked,
        personaWake.isChecked,
        carReadMessages.isChecked,
        carMap3d.isChecked,
        impactWatch.isChecked,
        carMusicPulse.isChecked,
        islandRest.isChecked,
        islandStatus.isChecked,
        islandBottom.isChecked,
        islandCover.isChecked,
        phoneActions.isChecked,
        textingDelegated.isChecked,
    ).joinToString(" ")

    /**
     * True when something on screen differs from what is stored.
     *
     * Reported as settings silently vanishing: the console tab strip at the top
     * of this screen went straight to `startActivity`, and Back went straight
     * out, while `save()` only ever ran from the SAVE pill at the bottom of a
     * long ScrollView. Editing the server URL and then tapping any tab — the
     * most natural thing to do on a screen with a nav bar across the top —
     * discarded the edit with no warning and no way to get it back.
     */
    private fun isDirty(): Boolean = try {
        // Read for its side: the stored settings are not Compose state, so without this the
        // bar would keep saying "Unsaved changes" after a save that stays on the screen.
        savedCount
        savedSnapshot() != editedSnapshot()
    } catch (t: Throwable) {
        // A control that has not been built yet cannot be dirty, and a
        // half-constructed screen must not trap the user on it.
        false
    }

    /**
     * Run [go], having given the user a chance to keep their edits.
     *
     * Three answers, and the third is the one that matters: SAVE runs the same
     * validation the pill does, so leaving cannot store an invalid URL by the
     * side door — if it refuses, the user stays here with the error, which is
     * the correct outcome and not a dropped navigation.
     */
    private fun leaveIfSaved(go: () -> Unit) {
        if (!isDirty()) {
            go()
            return
        }
        android.app.AlertDialog.Builder(this)
            .setTitle("Unsaved changes")
            .setMessage(
                "You have changed something on this screen. Leaving now discards it."
            )
            .setPositiveButton("Save") { _, _ ->
                // `save()` stays on the screen, so the destination is started from here.
                if (save()) go()
            }
            .setNegativeButton("Discard") { _, _ -> go() }
            .setNeutralButton("Keep editing", null)
            .show()
    }

    // --- persistence --------------------------------------------------------

    /**
     * @return true when everything was stored. False means a validation refused
     *   it and the screen has said why — which [leaveIfSaved] needs, because
     *   "SAVE and then leave" must not leave when the save did not happen.
     */
    private fun save(): Boolean {
        val check = ServerUrl.check(urlField.text.toString())
        if (!check.isValid) {
            note("Not saved: " + (check.error ?: "invalid server URL"))
            return false
        }
        val token = tokenField.text.toString().trim()
        if (token.isEmpty()) {
            note("Not saved: an access token is required")
            return false
        }
        val hermesBefore = config.hermesUrl

        config.serverUrl = check.normalized
        config.token = token
        config.pipeline = pipelineField.text.toString()
        rememberServerKind(check.normalized, token)
        config.allowPrereleaseUpdates = prereleaseUpdates.isChecked
        config.autoUpdateEnabled = autoUpdate.isChecked
        config.autoInstallUpdates = autoInstall.isChecked
        config.deviceName = deviceNameField.text.toString()

        config.wakeWordEnabled = wakeEnabled.isChecked
        config.wakeWordOnDevice = wakeOnDevice.isChecked
        config.wakeServerFallback = wakeServerFallback.isChecked
        config.personaWakeWords = personaWake.isChecked
        config.carReadMessages = carReadMessages.isChecked
        config.carMap3d = carMap3d.isChecked
        config.impactWatch = impactWatch.isChecked
        ai.aura.app.automation.policy.PolicyStore(applicationContext).let { store ->
            if (store.automationEnabled != phoneActions.isChecked) {
                store.automationEnabled = phoneActions.isChecked
                runCatching { ai.aura.app.automation.JarvisAutomationService.ensureRunning(this, "settings") }
            }
            if (store.textingDelegated != textingDelegated.isChecked) store.textingDelegated = textingDelegated.isChecked
        }
        config.carMusicPulse = carMusicPulse.isChecked
        config.islandRest = islandRest.isChecked
        config.islandStatus = islandStatus.isChecked
        config.islandBottom = islandBottom.isChecked
        config.islandCover = islandCover.isChecked
        config.sttOnDevice = sttOnDevice.isChecked
        config.wakeInCar = wakeInCar.isChecked
        config.wakeAtHome = wakeAtHome.isChecked
        config.wakeSensitivity = wakeSensitivity
        config.wakingHourStart = wakingStart
        config.wakingHourEnd = wakingEnd

        // One note, not three: a note replaces the one before it, so a separate cleartext
        // warning or listening caveat would be gone before anyone read it.
        val said = mutableListOf("Saved")
        check.warning?.let { said += it }

        // Order matters by one line: `warmLink` and `headsetButton` read
        // `headsetMode` in their own getters, so writing the mode first is what
        // makes the two below mean what the screen showed.
        config.headsetMode = headsetMode.isChecked
        config.headsetButton = headsetButton.isChecked
        config.warmLink = headsetWarmLink.isChecked
        Thread {
            ai.aura.app.tailnet.TailnetLink.apply(applicationContext, config)
            runOnUiThread { refreshTailnetStatus() }
        }.start()

        // Both of these run once at startup and then never again, so without
        // this the app keeps the values it read before the user typed anything:
        // ActionEnv would hold the OLD jarvis-core host (the one exemption
        // `http_request` has from its SSRF guard) and the command channel would
        // sit out its backoff before noticing the new URL.
        runCatching { ActionEnv.refreshFromConfig(applicationContext) }
        runCatching { DeviceChannelHost.configChanged() }
        // A phone that has just been given a server for the first time should
        // not have to wait for a reboot to connect to it.
        runCatching { JarvisAutomationService.ensureRunning(this, "settings-saved") }
        // Saving is where wakeWordEnabled changes, so it is also where the
        // listener has to start or stop. ensureRunning checks the setting
        // itself; the stop is explicit because nothing else would issue it.
        runCatching {
            if (config.wakeWordEnabled) {
                WakeWordService.ensureRunning(this, fromForeground = true)
                // Said at the moment it becomes relevant, rather than left on a
                // status line further up the screen the user has already
                // scrolled past. Without one of these two, listening works
                // until the next reboot and then quietly does not.
                if (!isExemptFromBatteryOptimisation() && !Settings.canDrawOverlays(this)) {
                    said += "Listening will stop at the next restart: see Permissions below"
                }
            } else {
                WakeWordService.cancelHeartbeat(this)
                WakeWordService.clearAttention(this)
                stopService(Intent(this, WakeWordService::class.java))
            }
        }

        savedCount++
        note(said.joinToString(". "))
        // A new adapter pairing is a different Hermes: read its settings, not the old one's.
        if (config.hermesUrl != hermesBefore) connectHermes()
        return true
    }

    /**
     * The kind belongs to the address, and is not a control on this screen:
     * a scanned adapter QR said what it is; anything else is asked over HTTP
     * so the first conversation does not have to guess. Kept out of [save]
     * because the dirty check compares what save() stores, and this is
     * derived from the address rather than edited.
     */
    private fun rememberServerKind(url: String, token: String) {
        // The adapter belongs to the address it was claimed with; the adapter's
        // own QR makes the main link Hermes, so a separate one is dropped.
        val adapter = claimedAdapter
        if (adapter != null && adapter.first == url) {
            config.adapterUrl = adapter.second.url
            config.adapterKey = adapter.second.key
        } else if (scannedKind?.let { it.first == url && it.second == ServerKind.HERMES } == true) {
            config.adapterUrl = ""
            config.adapterKey = ""
        }
        val scanned = scannedKind
        if (scanned != null && scanned.first == url) {
            config.serverKind = scanned.second
            return
        }
        if (config.serverKind != null) return
        Thread {
            val kind = ServerDiscovery.detect(url, token)
            if (kind != null && config.serverUrl == url) {
                config.serverKind = kind
                // save() compared the Hermes address before this answer existed, so a freshly
                // typed Hermes address left the fold saying "not paired" until the screen was
                // reopened. Hermes settings live on this screen now: connect when the kind lands.
                runOnUiThread { if (!isFinishing && !isDestroyed) connectHermes() }
            }
        }.start()
    }

    // --- token entry --------------------------------------------------------

    private fun pasteToken() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val text = clipboard?.primaryClip
            ?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)
            ?.coerceToText(this)
            ?.toString()
            ?.trim()
        if (text.isNullOrEmpty()) {
            note("Clipboard is empty")
            return
        }
        if (ai.aura.app.tailnet.TailnetLink.isWireGuardConfig(text)) {
            takeWireGuard(text)
            return
        }
        tokenField.setText(text)
        note("Pasted ${text.length} characters")
    }

    /**
     * Hands off to whatever barcode scanner the user installed. Jarvis bundles
     * no scanner: every offline decoder worth using is a large dependency, and
     * the Google ones are exactly what a degoogled phone is avoiding. Binary Eye
     * and QR Scanner (both F-Droid) answer this intent.
     */
    private fun scanToken() {
        val intent = Intent(ZXING_SCAN)
            .putExtra("SCAN_MODE", "QR_CODE_MODE")
            .putExtra("SAVE_HISTORY", false)
        try {
            startActivityForResult(intent, REQ_SCAN)
        } catch (e: ActivityNotFoundException) {
            note("No QR scanner installed. Install Binary Eye from F-Droid, or use PASTE.")
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_SCAN || resultCode != RESULT_OK) return
        val scanned = data?.getStringExtra("SCAN_RESULT")?.trim()
        if (scanned.isNullOrEmpty()) {
            note("Nothing scanned")
            return
        }
        // A WireGuard peer's QR holds its private key: it is never a token.
        if (ai.aura.app.tailnet.TailnetLink.isWireGuardConfig(scanned)) {
            takeWireGuard(scanned)
            return
        }

        // Three outcomes, and the middle one is the reason this is not just
        // `tokenField.setText(scanned)`.
        when (val parsed = PairingPayload.parse(scanned)) {
            is PairingPayload.Result.Ok -> pair(parsed.payload)
            // Recognisably one of ours and not acceptable — a stale version, an
            // address this app may not dial, a malformed code. This must never
            // fall through to the bare-token path: doing so would put a refused
            // payload's text in the token field and call it success.
            is PairingPayload.Result.Refused -> note(parsed.message)
            // Not addressed to us at all, so it is somebody's hand-made QR with
            // a token in it — the way this worked before pairing existed, and
            // still the fallback for a server too old to pair.
            is PairingPayload.Result.NotAPayload -> when (val adapter = HermesPairing.parse(scanned)) {
                // The adapter's QR: `{url, key, kind: "hermes-adapter"}`. The
                // key IS the token — there is no claim step — and the kind
                // travels with it so the first conversation dials Hermes.
                is HermesPairing.Result.Ok -> {
                    urlField.setText(adapter.pairing.url)
                    tokenField.setText(adapter.pairing.key)
                    scannedKind = adapter.pairing.url to ServerKind.HERMES
                    val model = adapter.pairing.model.ifEmpty { "Hermes" }
                    note("Paired with the Hermes adapter ($model). Tap SAVE to finish.")
                }
                is HermesPairing.Result.Refused -> note(adapter.message)
                is HermesPairing.Result.NotAPayload -> {
                    tokenField.setText(scanned)
                    note("Scanned ${scanned.length} characters")
                }
            }
        }
    }

    /**
     * Say which server the saved address is, and for the adapter, what model
     * it is running and whether that model is up yet. Off the main thread;
     * the answer lands only if the screen is still there to show it.
     */
    private fun refreshServerKindStatus() {
        val url = config.serverUrl
        val token = config.token
        if (url.isEmpty() || token.isEmpty()) {
            serverKindStatus.text = NO_SERVER
            serverKindStatus.warn = false
            return
        }
        val known = config.serverKind
        serverKindStatus.text = when (known) {
            ServerKind.CORE -> "jarvis-core"
            ServerKind.RELAY -> "Console relay → jarvis-core"
            ServerKind.HERMES -> "Hermes adapter, checking…"
            null -> "Server kind not detected yet"
        }
        serverKindStatus.warn = false
        if (known != ServerKind.HERMES) return
        Thread {
            val status = HermesClient(url, token, NO_LISTENER).status()
            runOnUiThread {
                if (isFinishing || config.serverUrl != url) return@runOnUiThread
                if (status == null) {
                    serverKindStatus.text = "Hermes adapter: ${HermesStatus.NO_ANSWER_LABEL}"
                    serverKindStatus.warn = true
                } else {
                    serverKindStatus.text = "Hermes adapter: ${status.readout}"
                    serverKindStatus.warn = status.warmingUp || !status.ok || status.notReady
                }
            }
        }.start()
    }

    /**
     * Exchange a scanned pairing code for a token, off the main thread.
     *
     * Fills BOTH fields on success: the address travelled in the QR alongside
     * the code, which is the point — the whole reason typing a token is the
     * worst moment of setup is that it comes with an address to type as well.
     */
    private fun pair(payload: PairingPayload) {
        note("Pairing…")
        Thread {
            val result = PairingClaim.claim(
                payload,
                deviceName = deviceNameField.text.toString().trim()
                    .ifEmpty { config.deviceName },
                // Read through ChannelConfig, which is the one place that
                // knows where the user's own cleartext acknowledgements live.
                // The QR must not be able to add to that list — only a person
                // typing on this device can.
                acknowledgedCleartextHosts =
                    ChannelConfig.from(this, appVersionName()).acknowledgedCleartextHosts,
            )
            runOnUiThread {
                when (result) {
                    is PairingClaim.Result.Ok -> {
                        urlField.setText(result.url)
                        tokenField.setText(result.token)
                        claimedAdapter = result.adapter?.let { result.url to it }
                        note(
                            "Paired as ${result.name}" +
                                (if (result.adapter != null) ", with Hermes" else "") +
                                ". Tap SAVE to finish."
                        )
                    }
                    is PairingClaim.Result.Failed -> note(result.message)
                }
            }
        }.start()
    }

    /**
     * Re-read on resume: granting the permission happens in another app, so the
     * only moment this can be correct is when we come back from it.
     */
    override fun onResume() {
        super.onResume()
        refreshServerKindStatus()
        refreshOverlayStatus()
        refreshPermissionStatus()
        refreshListenStatus()
        refreshModelStatus()
        refreshSttStatus()
    }

    /**
     * One line for the whole permission set, so the section is worth tapping.
     *
     * The grid this replaces gave no indication of what was already granted, so
     * the only way to find out was to open each of the eight screens in turn.
     */
    private fun refreshPermissionStatus() {
        val requirements = GrapheneCompat.requirements(this)
        val missing = requirements.count { it.essential && !it.satisfied }
        val optional = requirements.count { !it.essential && !it.satisfied }
        permissionStatus.text = when {
            missing > 0 -> getString(R.string.status_permissions_missing, missing, optional)
            optional > 0 -> getString(R.string.status_permissions_optional, optional)
            else -> getString(R.string.status_permissions_ok)
        }
        permissionStatus.warn = missing > 0
    }

    private fun refreshOverlayStatus() {
        val overlay = Settings.canDrawOverlays(this)
        val notify = GrapheneCompat.canPostNotifications(this)
        val fullScreen = GrapheneCompat.canUseFullScreenIntent(this)
        // Three separate grants decide whether a wake word puts anything on
        // screen, and until now the screen only mentioned one of them. Reported
        // together because the useful question is not "which of these do I
        // have" but "will saying my name do anything".
        overlayStatus.text = getString(
            when {
                !notify -> R.string.status_overlay_no_notifications
                overlay -> R.string.status_overlay_granted
                fullScreen -> R.string.status_overlay_full_screen
                else -> R.string.status_overlay_none
            }
        )
        overlayStatus.warn = !(notify && (overlay || fullScreen))
    }


    /**
     * Whether always-on listening will survive a restart.
     *
     * Android refuses to start a foreground service typed `microphone` while
     * the app is in the background, and `BOOT_COMPLETED` is not an exemption
     * for that class of service. Two grants are: exemption from battery
     * optimisation, and "display over other apps". Either one is enough, so
     * this reports the pair as a single yes/no rather than making the user
     * reason about which.
     */
    private fun refreshListenStatus() {
        val exempt = isExemptFromBatteryOptimisation()
        val overlay = Settings.canDrawOverlays(this)
        val ok = exempt || overlay
        listenStatus.text = getString(
            when {
                ok && exempt && overlay -> R.string.status_listen_both
                ok && exempt -> R.string.status_listen_battery
                ok -> R.string.status_listen_overlay
                else -> R.string.status_listen_no
            }
        )
        listenStatus.warn = !(ok)
    }

    /**
     * Whether the phone can do its own listening, and what it costs if it cannot.
     *
     * Two independent facts — the switch and the weights — and the interesting
     * state is the one where they disagree: the switch on with nothing
     * downloaded silently falls back to the server, which is exactly the shape
     * of bug where a privacy feature looks enabled and is not.
     */
    private fun refreshModelStatus() {
        val have = ModelStore.isDownloaded(this, OnDeviceWakeWord.REQUIRED_MODELS)
        val wanted = config.wakeWordOnDevice || wakeOnDevice.isChecked
        val megabytes = ModelStore.bytesOnDisk(this) / 1024.0 / 1024.0
        modelStatus.text = when {
            have && wanted -> getString(R.string.status_models_ready, megabytes)
            have -> getString(R.string.status_models_unused, megabytes)
            wanted -> getString(R.string.status_models_wanted)
            else -> getString(R.string.status_models_absent)
        }
        modelStatus.warn = !(have && wanted)
    }

    /**
     * Whether what the user asked for is what is happening.
     *
     * The setting is a preference; the speech model on the phone is a fact.
     * When they disagree the server is still transcribing, and saying so is
     * the entire point — a privacy switch that looks on while the thing it
     * turns off is still running is worse than no switch.
     */
    private fun refreshSttStatus() {
        // The console home asks the phone's bridge (ConsolePage.hears), which reads the
        // same two facts. The recording goes with the words whatever this phone believes
        // of the speaker gate, so the line never claims it stays here.
        val possible = ModelStore.isDownloaded(this, OnPhoneSpeech.MODELS)
        val wanted = sttOnDevice.isChecked
        sttStatus.text = getString(
            when {
                wanted && possible -> R.string.status_stt_checked
                wanted -> R.string.status_stt_fetching
                else -> R.string.status_stt_streamed
            }
        )
        sttStatus.warn = wanted && !possible
    }

    private fun downloadModels() {
        val check = ServerUrl.check(urlField.text.toString())
        val token = tokenField.text.toString().trim()
        if (!check.isValid || token.isEmpty()) {
            note("Set the server URL and token first, and SAVE.")
            return
        }
        note("Downloading from your server…")
        Thread {
            val problem = ModelStore.download(
                this,
                check.normalized,
                token,
                OnDeviceWakeWord.REQUIRED_MODELS,
            )
            // The personas' names, each on its own: a house without one yet is
            // not a failed download, only a name this phone cannot hear.
            if (problem == null) {
                for (file in OnDeviceWakeWord.PERSONA_MODELS.values) {
                    ModelStore.download(this, check.normalized, token, listOf(file))
                }
            }
            // The speech model too, with its switch on: otherwise it comes on first use.
            val speech = if (problem == null && sttOnDevice.isChecked) {
                ModelStore.download(this, check.normalized, token, OnPhoneSpeech.MODELS)
            } else {
                null
            }
            // The offline voice (63 MB, once): what the phone and the car speak with when the
            // server is out. Not tied to a switch; System check says whether it is here.
            val voice = if (problem == null) {
                BundledVoice.fetch(this, check.normalized, token)
                    // AURA's own (132 MB, T-0872), for a pack that lets no other voice speak.
                    ?: BundledVoice.Model.KOKORO.takeIf { ActiveTheme.theme.offlineFallback == OfflineVoice.CLIPS_ONLY }
                        ?.let { BundledVoice.fetch(this, check.normalized, token, it) }
            } else {
                null
            }
            runOnUiThread {
                note(problem ?: speech ?: voice ?: "On-device models ready.")
                refreshModelStatus()
                refreshSttStatus()
                // Restart the listener so it picks the local path up now rather
                // than at the next reconnect.
                if (problem == null && config.wakeWordEnabled) {
                    runCatching { WakeWordService.ensureRunning(this, fromForeground = true) }
                }
            }
        }.start()
    }

    private fun deleteModels() {
        ModelStore.deleteAll(this)
        // Not just the files: leaving the switch on with nothing behind it is
        // the state this screen exists to make impossible.
        config.wakeWordOnDevice = false
        wakeOnDevice.isChecked = false
        refreshModelStatus()
        note("Deleted. Detection is back on your server.")
    }

    private fun isExemptFromBatteryOptimisation(): Boolean = runCatching {
        getSystemService(PowerManager::class.java)
            ?.isIgnoringBatteryOptimizations(packageName) == true
    }.getOrDefault(false)

    /**
     * Ask to be left running in the background.
     *
     * The direct request dialog is the one that can be answered in place; some
     * ROMs remove it, so a failure falls back to the settings list where the
     * user can find Jarvis themselves.
     */
    // BatteryLife: asking for this outright is a Play-policy concern, and this
    // app is not on Play. An always-on microphone that Android may kill and
    // then refuse to restart is precisely what the exemption is for.
    @SuppressLint("BatteryLife")
    private fun requestBackgroundStart() {
        if (isExemptFromBatteryOptimisation()) {
            openSetting(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            return
        }
        try {
            startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.fromParts("package", packageName, null))
            )
        } catch (e: ActivityNotFoundException) {
            openSetting(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
        }
    }

    // --- updates -----------------------------------------------------------

    private fun appVersionName(): String =
        runCatching { packageManager.getPackageInfo(packageName, 0).versionName }
            .getOrNull() ?: "?"

    /**
     * The installed build number, which is the only thing Android compares.
     *
     * `longVersionCode` since 28; minSdk is 29, so there is no legacy branch
     * to keep and no deprecation to suppress.
     */
    private fun appVersionCode(): Long =
        runCatching { packageManager.getPackageInfo(packageName, 0).longVersionCode }
            .getOrDefault(0L)

    /**
     * Ask GitHub for a newer build, and install it if the user agrees.
     *
     * On a plain thread rather than a coroutine: this Activity has no other
     * async machinery and one thread for one blocking check is less to explain
     * than a scope, a dispatcher and a lifecycle to cancel it against. The
     * result is posted back with `runOnUiThread`, and every path ends by
     * writing a sentence into [updateStatus] — a check that says nothing is
     * indistinguishable from a button that does nothing.
     */
    private fun checkForUpdates() {
        updateStatus.text = "Checking GitHub…"
        val allowPrerelease = prereleaseUpdates.isChecked
        val installed = appVersionCode()
        Thread {
            val checker = UpdateChecker(applicationContext)
            val found = checker.check(installed, allowPrerelease)
            if (found !is UpdateChecker.Result.Offered) {
                runOnUiThread { updateStatus.text = describe(found) }
                return@Thread
            }
            runOnUiThread {
                updateStatus.text = "Downloading ${found.update.versionName}…"
            }
            val installedResult = checker.install(found.update) { done, total ->
                runOnUiThread {
                    updateProgress.shown = true
                    updateProgress.isIndeterminate = total <= 0
                    if (total > 0) updateProgress.progress = (done * 100 / total).toInt()
                    updateStatus.text = "Downloading ${found.update.versionName}… " +
                        if (total > 0) "${done * 100 / total}% of ${total shr 20} MB" else "${done shr 20} MB"
                }
            }
            runOnUiThread {
                updateProgress.shown = false
                updateStatus.text = describe(installedResult)
            }
        }.start()
    }

    private fun describe(result: UpdateChecker.Result): String = when (result) {
        is UpdateChecker.Result.UpToDate ->
            "Up to date — version ${appVersionName()} (build ${appVersionCode()})"
        is UpdateChecker.Result.Offered ->
            "${result.update.versionName} is available."
        // "Confirm the system prompt" was printed for the whole life of the app
        // by a build in which no prompt could appear — nothing received the
        // installer's STATUS_PENDING_USER_ACTION, so the activity that carries
        // was never started. InstallResultReceiver starts it now, and falls
        // back to a notification when Android refuses the background start.
        is UpdateChecker.Result.Handed ->
            "Downloaded ${result.update.versionName}. Confirm the install prompt — " +
                "if it does not appear, look in your notifications."
        is UpdateChecker.Result.Failed -> result.message
    }

    /**
     * "Install unknown apps" for this app.
     *
     * `ACTION_MANAGE_UNKNOWN_APP_SOURCES` wants a `package:` URI to land on
     * Jarvis's own switch; without it some ROMs open the full list and some
     * open nothing at all, so the plain action is the fallback rather than the
     * first try.
     */
    private fun openInstallPermission() {
        val direct = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:$packageName")
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(direct)
        } catch (e: ActivityNotFoundException) {
            openSetting(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
        }
    }

    private fun openReleasesPage() {
        try {
            startActivity(
                Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse("https://github.com/${UpdateChecker.DEFAULT_REPO}/releases")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            note("No browser to open the releases page")
        }
    }

    // --- system settings deep links ----------------------------------------

    private fun openSetting(action: String) {
        try {
            startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            note("This device has no screen for that setting")
        }
    }

    private fun openOverlaySetting() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            note("This device has no screen for that setting")
        }
    }

    private fun openAppInfo() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            note("This device has no screen for that setting")
        }
    }

    // --- small builders -----------------------------------------------------

    // --- explanations, folded away by default -------------------------------

    /**
     * Every explanatory paragraph on this screen, so one control can hide them.
     *
     * Reported as *"clean up the settings in the android app, there's a lot of
     * text, buttons are too close together, it's hard to understand/navigate"*.
     * There are fourteen sections here and fourteen paragraphs explaining them —
     * around 570 words — and every one of them is worth reading ONCE. Read
     * every time, they are what makes the screen hard to navigate: the labels
     * and the controls, which are the things somebody came here to find, are
     * separated by prose they have already read.
     *
     * So nothing is deleted and nothing is shortened. It starts folded, and one
     * control at the top unfolds all of it. Deliberately not persisted: the
     * default is the state that makes the screen usable, and a person who wants
     * the prose wants it for the visit they are on.
     */
    private var explanationsShown by mutableStateOf(false)

    /**
     * Saves a scanned or pasted WireGuard config and brings its tunnel up at
     * once: it is a link, not a field to review, so it does not wait for SAVE.
     */
    private fun takeWireGuard(conf: String) {
        tailnetStatus.text = "Starting WireGuard…"
        Thread {
            // Saved only once it starts: a refused config would fail every start, and the
            // tunnel already up (or the tailnet) stays as it was.
            val failed = ai.aura.app.tailnet.TailnetLink.startWireGuard(conf, ai.aura.app.tailnet.TailnetLink.stateDir(applicationContext))
            if (failed == null) config.wireGuardConf = conf
            runOnUiThread {
                if (failed != null) {
                    note("WireGuard config refused: $failed")
                } else {
                    note("WireGuard saved. ${ActiveTheme.name} reaches home through it.")
                }
                refreshTailnetStatus()
            }
        }.start()
    }

    private fun forgetWireGuard() {
        if (config.wireGuardConf.isEmpty()) {
            note("No WireGuard tunnel saved")
            return
        }
        config.wireGuardConf = ""
        Thread {
            ai.aura.app.tailnet.TailnetLink.apply(applicationContext, config)
            runOnUiThread { note("WireGuard forgotten"); refreshTailnetStatus() }
        }.start()
    }

    /** The embedded link's state, in words, off the main thread. */
    private fun refreshTailnetStatus() {
        val uri = runCatching { java.net.URI(config.hermesUrl) }.getOrNull()
        val host = uri?.host.orEmpty()
        val port = uri?.port?.takeIf { it > 0 } ?: if (uri?.scheme == "http") 80 else 443
        // The check can take 8 s; a later refresh (Forget, a new scan) must not be overwritten by it.
        val gen = ++tailnetRefreshes
        Thread {
            val state = ai.aura.app.tailnet.TailnetLink.state()
            val words = when {
                ai.aura.app.tailnet.TailnetLink.wireGuard -> when {
                    state != "Running" && state != "Starting" -> state
                    host.isEmpty() -> "WireGuard is on. Set ${ActiveTheme.name}'s address to check it reaches home through the tunnel."
                    // Each step in turn (T-0473), so a tunnel that won't connect says which step broke.
                    else -> ai.aura.app.tailnet.TailnetLink.run { wireGuardWords(diagnose(host, port), host) }
                }
                // Saved but down (T-0913): the start is tried again until it works; say why it has not.
                config.wireGuardConf.isNotBlank() ->
                    "WireGuard is saved but not up${ai.aura.app.tailnet.TailnetLink.lastError?.let { ": $it" } ?: ""}. ${ActiveTheme.name} keeps trying."
                else -> "Off. ${ActiveTheme.name} uses the phone's own network or VPN."
            }
            runOnUiThread { if (gen == tailnetRefreshes) tailnetStatus.text = words }
        }.start()
    }
    private var tailnetRefreshes = 0

    private fun toggleExplanations() {
        explanationsShown = !explanationsShown
    }

    private fun explanationsLabel(): String =
        if (explanationsShown) "Hide explanations" else "What does all this do?"

    /**
     * The car's self-test (Headway's, ADR-0047): binds every installed car app, lists
     * the displays and the grants, and names any package holding the car-app
     * permission -- the answers that do not need the car. Shared as text.
     */
    private fun shareCarSelfTest() {
        note("Running the car self-test…")
        Thread({
            val report = runCatching { ai.aura.app.car.diag.SelfTest.run(applicationContext) }
                .getOrElse { "The self-test failed: ${it.message}" }
            runOnUiThread {
                if (isFinishing) return@runOnUiThread
                startActivity(
                    Intent.createChooser(
                        Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, report),
                        "Share the car self-test",
                    ),
                )
            }
        }, "car-self-test").start()
    }

    /** The drive log as a file: a drive's worth is too long to go as text. */
    private fun shareDriveLog() {
        val file = runCatching { ai.aura.app.car.log.SessionLog.shared.export(this) }.getOrElse {
            note("Could not write the drive log: ${it.message}")
            return
        }
        val uri = androidx.core.content.FileProvider.getUriForFile(this, "$packageName.carlogs", file)
        startActivity(
            Intent.createChooser(
                Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_STREAM, uri)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                "Share the drive log",
            ),
        )
    }

    /** What just happened, as the screen's one note (never a system toast); callable from any thread. */
    private fun note(message: String) = runOnUiThread { ai.aura.app.ui.ScreenNote.show(this, message) }

    // --- Jarvis's own settings, over the Hermes link -------------------------------

    /** The Jarvis fold's not-ready face; null once the settings are loaded. */
    private class NotReady(
        val kind: StateKind,
        val line: String,
        val action: String? = null,
        val onAction: (() -> Unit)? = null,
    )

    /**
     * Opens the Hermes link for the Jarvis fold, once: the fold's rows only ever read what Hermes
     * says, so nothing is shown that Hermes did not return. Unpaired, it says where to pair.
     */
    private fun connectHermes() {
        if (config.hermesUrl.isEmpty()) {
            hermes?.close()
            hermes = null
            hermesState = NotReady(
                StateKind.EMPTY,
                "${ActiveTheme.name} isn't paired to Hermes yet. Open Server above, then Scan QR with the code from the console's Pairing panel.",
            )
            return
        }
        hermesState = NotReady(StateKind.LOADING, "Reading ${ActiveTheme.name}'s settings.")
        hermes?.close()
        hermes = HermesClient(config.hermesUrl, config.hermesKey, object : HermesClient.Listener {
            override fun onSession(sessionId: String, resumed: Boolean) = reloadHermes()
            override fun onEvent(type: String, sessionId: String, payload: org.json.JSONObject) {
                if (type == "persona.switch") persona = payload.optString("active")
            }
            override fun onDisconnected(reason: String, willRetry: Boolean) {
                if (!willRetry) hermesState = NotReady(StateKind.OFFLINE, reason, "Reconnect", ::connectHermes)
            }
            override fun onNotice(text: String) = Unit
            override fun onPrefill(text: String) = Unit
            override fun onError(message: String) {
                hermesState = NotReady(StateKind.ERROR, message, "Retry", ::connectHermes)
            }
        }).also { it.connect() }
        Thread { hermes?.status()?.persona?.let { runOnUiThread { persona = it } } }.start()
    }

    private fun hermesCommand(arg: String, onOutput: (String?) -> Unit) {
        val c = hermes ?: return
        val sid = c.sessionId ?: return
        c.request(
            "command.dispatch",
            org.json.JSONObject().put("session_id", sid).put("name", SETTINGS_COMMAND).put("arg", "@$sid $arg"),
        ) { reply ->
            when (reply) {
                is HermesRpc.Reply.Ok -> onOutput(reply.result.optString("output"))
                is HermesRpc.Reply.Error -> hermesState = NotReady(StateKind.ERROR, reply.message, "Retry", ::reloadHermes)
            }
        }
    }

    private fun reloadHermes() = hermesCommand("list") { output ->
        hermesValues.clear()
        hermesValues.putAll(HermesSettings.values(output))
        hermesState = null
    }

    private fun writeHermes(spec: HermesSettings.Spec, input: String) {
        val json = HermesSettings.encode(spec, input)
        if (json == null) {
            hermesNotes[spec.key] = if (spec.min != null || spec.max != null) {
                "Between ${spec.min?.toLong() ?: ""} and ${spec.max?.toLong() ?: ""}."
            } else {
                "That value doesn't fit this setting."
            }
            return
        }
        hermesNotes[spec.key] = "Saving…"
        hermesCommand("set ${spec.key} $json") { output ->
            when (val written = HermesSettings.written(output)) {
                HermesSettings.Written.Done -> savedHermes(spec)
                // The tap that made the change is the person's yes, so a proposal is applied at once.
                is HermesSettings.Written.Proposed -> hermesCommand("apply ${written.id}") { applied ->
                    when (val result = HermesSettings.written(applied)) {
                        is HermesSettings.Written.Refused -> hermesNotes[spec.key] = result.message
                        else -> savedHermes(spec)
                    }
                }
                is HermesSettings.Written.Refused -> hermesNotes[spec.key] = written.message
            }
        }
    }

    private fun savedHermes(spec: HermesSettings.Spec) {
        hermesNotes[spec.key] = "Saved."
        reloadHermes()
    }

    private fun switchPersona(name: String) {
        hermes?.dispatchCommand(PERSONA_COMMAND, name) { persona = name.lowercase() }
    }

    /** A console page in the phone's home, after the same question Back asks. */
    private fun openConsole(path: String) {
        leaveIfSaved {
            startActivity(
                Intent(this, MainActivity::class.java)
                    .putExtra(MainActivity.EXTRA_CONSOLE_PATH, path)
                    .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            )
            finish()
        }
    }

    /**
     * The worn pack's name over the persona, a short list of the settings people change, and the
     * console for the rest. The long list stays in the console's own settings (`/settings`), which
     * writes through the same plugin and approval matrix.
     */
    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Hermes() = Part(
        ActiveTheme.name,
        hermesState?.line ?: persona.ifEmpty { "Persona and a few settings" },
        if (hermesState?.kind == StateKind.ERROR || hermesState?.kind == StateKind.OFFLINE) Tone.WARN else Tone.ACCENT,
    ) {
        val shown = hermesState
        if (shown != null) {
            ScreenState(shown.kind, shown.line, action = shown.action, onAction = shown.onAction)
        } else {
            Label("Persona")
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
            ) {
                for (name in JarvisThemes.ALL.map { it.name }) {
                    RuleChip(name, selected = persona.equals(name, ignoreCase = true), onClick = { switchPersona(name) }, kind = "Persona")
                }
            }
            for (key in SHORT_LIST) {
                val spec = hermesSpecs.firstOrNull { it.key == key } ?: continue
                Hairline()
                HermesSetting(spec)
            }
        }
        Hairline()
        Buttons {
            ActionButton("Console settings", onClick = { openConsole("/settings") })
            ActionButton("Devices", onClick = { openConsole("/devices") })
        }
    }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun HermesSetting(spec: HermesSettings.Spec) {
        val current = hermesValues[spec.key]
        Column(verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.TIGHT.dp)) {
            when (spec.widget) {
                "toggle" -> Toggle(labelOf(spec.key), current == true, { on -> writeHermes(spec, if (on) "true" else "false") })
                else -> Body(labelOf(spec.key))
            }
            Body(spec.description, dim = true)
            when (spec.widget) {
                "toggle" -> Unit
                "select" -> FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                ) {
                    for (choice in spec.choices) {
                        RuleChip(choice, selected = current?.toString() == choice, onClick = { writeHermes(spec, choice) }, kind = "Set")
                    }
                }
                "managed" -> Body(HermesSettings.shown(current))
                else -> {
                    var draft by remember(current) { mutableStateOf(HermesSettings.shown(current)) }
                    Field(labelOf(spec.key), draft, { draft = it })
                    ActionButton("Save", onClick = { writeHermes(spec, draft) })
                }
            }
            hermesNotes[spec.key]?.let { Body(it, dim = true) }
        }
    }

    companion object {
        /** Copied from `tests/contracts/` by the build (`copySettingsContract`). */
        const val CONTRACT = "hermes-settings.json"
        private const val SETTINGS_COMMAND = "jsettings"
        private const val PERSONA_COMMAND = "persona"

        /** The settings a phone's owner changes; the rest are the console's (`/settings`). */
        private val SHORT_LIST = listOf("approvals.mode")

        /** `goals.max_turns` → "Max turns": the key's last part, in words. */
        fun labelOf(key: String): String =
            key.substringAfterLast('.').replace('_', ' ').replaceFirstChar { it.uppercase() }

        private val TAILNET_EXPLAIN: String get() =
            "For the car. A VPN app keeps any app inside it from using the car's Wi-Fi, so ${ActiveTheme.name} can " +
                "reach home by itself. WireGuard: scan the QR your WireGuard server makes for a new peer " +
                "(give ${ActiveTheme.name} a peer of its own: one key on two devices fights), then exclude ${ActiveTheme.appLabel} in " +
                "the WireGuard app."
        private const val REQ_SCAN = 5001

        /** The readout when no server is set yet. */
        private const val NO_SERVER = "No server"

        /** A status probe needs a client and the client needs a listener; nothing here listens. */
        private val NO_LISTENER = object : HermesClient.Listener {
            override fun onSession(sessionId: String, resumed: Boolean) = Unit
            override fun onEvent(type: String, sessionId: String, payload: org.json.JSONObject) = Unit
            override fun onDisconnected(reason: String, willRetry: Boolean) = Unit
            override fun onNotice(text: String) = Unit
            override fun onPrefill(text: String) = Unit
            override fun onError(message: String) = Unit
        }

        /** The de-facto standard scan intent; F-Droid scanners answer it. */
        private const val ZXING_SCAN = "com.google.zxing.client.android.SCAN"
    }
}
