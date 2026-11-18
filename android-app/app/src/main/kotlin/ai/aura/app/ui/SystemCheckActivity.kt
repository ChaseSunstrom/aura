package ai.aura.app.ui

import ai.aura.app.assist.BundledVoice
import ai.aura.app.assist.OfflineVoice
import ai.aura.app.assist.hermes.HermesClient
import ai.aura.app.assist.hermes.HermesStatus
import ai.aura.app.compat.GrapheneCompat
import ai.aura.app.config.JarvisConfig
import ai.aura.app.config.ServerKind
import ai.aura.app.compat.RuntimePermissions
import ai.aura.app.crash.JarvisCrashHandler
import ai.aura.app.ui.theme.ActiveTheme
import android.content.Intent
import android.os.Bundle
import android.util.Log
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.ui.v2.ActionButton
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Hairline
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.Projection
import ai.aura.app.ui.v2.Stage
import ai.aura.app.ui.v2.Tone
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp

/**
 * One screen that says exactly what is missing.
 *
 * On GrapheneOS an app can be installed, launched, and completely inert because
 * a toggle the user has never heard of is off. The old Home-Assistant fork's
 * answer to that was a spinner that never stopped. This screen's answer is a
 * list: every permission and special access Jarvis can use, whether it has it,
 * what breaks without it, and a button that opens the exact settings page.
 *
 * The checklist itself is [GrapheneCompat.requirements]; this activity only
 * renders it. It re-probes in [onResume], because the user leaves to change a
 * setting and comes straight back — a stale checklist would be worse than none.
 */
class SystemCheckActivity : ComponentActivity() {

    private var requirements by mutableStateOf<List<GrapheneCompat.Requirement>>(emptyList())
    private var banner by mutableStateOf<String?>(null)
    private var crashes by mutableStateOf(false)
    /** The assistant behind the configured address: which server, which model, warming or ready. */
    private var assistant by mutableStateOf("")
    private var assistantWarn by mutableStateOf(false)
    /** Whether the offline voice is on this phone; without it a line offline is the system engine's. */
    private var offlineVoice by mutableStateOf("")
    private var offlineVoiceWarn by mutableStateOf(false)

    /** What [askInPlace] last asked for, so the result can be judged. */
    private var pendingGroup: List<String> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { Screen() }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Screen() {
        Stage {
            Column(
                Modifier
                    .fillMaxSize()
                    .safeDrawingPadding()
                    .verticalScroll(rememberScrollState())
                    .padding(JarvisTokens.Space.SCREEN.dp),
                verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
            ) {
                // The console's ScreenTitle: sentence case, a lede. The instrumented
                // suite finds this screen by "SYSTEM CHECK" ignoring case, which a
                // sentence-case title still satisfies; the button on the settings
                // screen keeps the caps label the suite taps.
                Headline("System check")
                Body(
                    "Every permission and special access ${ActiveTheme.name} can use: whether it has it, what breaks " +
                        "without it, and where to grant it.",
                    dim = true,
                )
                val missing = requirements.count { it.essential && !it.satisfied }
                val optional = requirements.count { !it.essential && !it.satisfied }
                Body(
                    when {
                        missing > 0 && optional > 0 -> "$missing required item(s) missing, $optional optional item(s) off."
                        missing > 0 -> "$missing required item(s) missing."
                        optional > 0 -> "Everything required is granted. $optional optional item(s) are off."
                        else -> "Everything is granted."
                    },
                    tone = if (missing > 0) Tone.WARN else null,
                )
                banner?.let { text ->
                    Projection(Modifier.fillMaxWidth(), tone = Tone.WARN, pinned = true) {
                        Body(text)
                        ActionButton("Open permissions", onClick = ::openAppDetails, tone = Tone.WARN)
                    }
                }

                Label("Requirements")
                // One projection, a hairline between rows — not a box per requirement.
                Projection(Modifier.fillMaxWidth()) {
                    requirements.forEachIndexed { index, req ->
                        if (index > 0) Hairline()
                        Check(req)
                    }
                }
                if (crashes) Body("There are recorded crashes on this device — see Diagnostics.", tone = Tone.WARN)

                Label("Assistant")
                Body(
                    "Which server the address in Settings turned out to be. Against the Hermes " +
                        "adapter this also says which model is loaded, and whether it is still warming up.",
                    dim = true,
                )
                Body(assistant, tone = if (assistantWarn) Tone.WARN else null)

                Label("Offline voice")
                Body(
                    "What ${ActiveTheme.name} speaks with when your server is out: a voice kept on this phone, " +
                        "fetched once from your server (Settings, Download models, or the first drive).",
                    dim = true,
                )
                Body(offlineVoice, tone = if (offlineVoiceWarn) Tone.WARN else null)

                Label("Diagnostics")
                Body(
                    "If ${ActiveTheme.name} has closed unexpectedly, the stack trace is on this device — " +
                        "no cable, no laptop, no logcat needed.",
                    dim = true,
                )
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                ) {
                    ActionButton("Crash logs", onClick = {
                        startActivity(Intent(this@SystemCheckActivity, CrashLogActivity::class.java))
                    })
                    ActionButton("App info", onClick = ::openAppDetails)
                }
            }
        }
    }

    /**
     * One requirement: its name and its state in words, then what breaks without it. A missing
     * essential is the one row that is not quiet (amber, the needs-you colour); tapping a row
     * asks for it, or opens the page that grants it.
     */
    @Composable
    private fun Check(req: GrapheneCompat.Requirement) {
        val tone = when {
            req.satisfied -> Tone.OK
            req.essential -> Tone.WARN
            else -> null
        }
        val state = when {
            req.satisfied -> "Granted"
            req.essential -> "Missing"
            else -> "Off"
        }
        Column(
            Modifier
                .fillMaxWidth()
                .heightIn(min = JarvisTokens.Size.TOUCH.dp)
                .clickable(enabled = !req.satisfied, role = Role.Button, onClickLabel = "Grant") { grant(req) },
            verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.TIGHT.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Body(req.label, Modifier.weight(1f), dim = tone == null)
                Label(state, tone = tone)
            }
            Body(req.why, dim = true)
        }
    }

    private fun grant(req: GrapheneCompat.Requirement) {
        // Sending the user off to change this toggle makes
        // everything observed on the wire so far stale; keeping
        // it would leave a fixed problem still on screen.
        if (req.id == GrapheneCompat.ID_NETWORK) {
            GrapheneCompat.resetNetworkObservations()
        }
        // A runtime permission is one tap here. Only rows with
        // nothing askable left fall through to Settings — this
        // screen exists because "go and find it in Settings"
        // is how the grants got missed in the first place.
        if (!askInPlace(req)) GrapheneCompat.openSettingsFor(this, req)
    }

    private fun openAppDetails() = GrapheneCompat.openAppDetails(this)

    private fun refresh() {
        refreshAssistant()
        requirements = GrapheneCompat.requirements(this)
        banner = GrapheneCompat.networkBanner(this)
        crashes = JarvisCrashHandler.hasRecords(this)
    }

    private fun refreshAssistant() {
        // A clips-only pack (AURA) never falls back to another voice: hers is Kokoro on the phone (T-0872).
        val clipsOnly = ActiveTheme.theme.offlineFallback == OfflineVoice.CLIPS_ONLY
        val model = if (clipsOnly) BundledVoice.Model.KOKORO else BundledVoice.Model.PIPER
        val voiceReady = if (clipsOnly) BundledVoice.realtime(this, model) else BundledVoice.ready(this, model)
        offlineVoice = when {
            voiceReady -> "Ready"
            clipsOnly && BundledVoice.ready(this, model) -> "Too slow on this phone: offline, ${ActiveTheme.name} says only the lines kept on it"
            clipsOnly -> "Not downloaded: offline, ${ActiveTheme.name} says only the lines kept on the phone"
            else -> "Not downloaded: offline, ${ActiveTheme.name} falls back to the phone's own voice"
        }
        offlineVoiceWarn = !voiceReady
        val config = JarvisConfig(this)
        val url = config.serverUrl
        val token = config.token
        if (!config.isConfigured) {
            assistant = "No server configured"
            assistantWarn = true
            return
        }
        val kind = config.serverKind
        assistant = when (kind) {
            ServerKind.CORE -> "jarvis-core"
            ServerKind.RELAY -> "Console relay → jarvis-core"
            ServerKind.HERMES -> "Hermes adapter, checking…"
            null -> "Not detected yet — save settings or start a conversation"
        }
        assistantWarn = kind == null
        if (kind != ServerKind.HERMES) return
        Thread {
            val status = HermesClient(url, token, NO_LISTENER).status()
            runOnUiThread {
                if (isFinishing) return@runOnUiThread
                if (status == null) {
                    assistant = "Hermes adapter: ${HermesStatus.NO_ANSWER_LABEL}"
                    assistantWarn = true
                } else {
                    // The adapter's own word already says whether the backend answers (T-0583);
                    // only an older adapter needs this phone to add it.
                    val backend = if (status.backendOk) "Backend OK" else "Backend down"
                    assistant = "Hermes adapter: ${status.readout}" + if (status.word.isEmpty()) ", $backend" else ""
                    assistantWarn = status.warmingUp || !status.ok || status.notReady
                }
            }
        }.start()
    }

    /**
     * Ask for the runtime permissions behind [req], here, rather than opening
     * a Settings page and hoping.
     *
     * Returns false when there is nothing this screen can ask for — a special
     * access, a grant that is already held, or a group with no runtime
     * permissions at all — and the caller then falls back to Settings.
     *
     * This is the other half of the fix for permissions that were declared and
     * never requested. The dispatcher asks at the moment an action needs one;
     * this is for the person who came here after reading `permission … not
     * granted` in a transcript and wants to fix it before trying again.
     */
    private fun askInPlace(req: GrapheneCompat.Requirement): Boolean {
        val wanted = RuntimePermissions.missing(this, RuntimePermissions.inGroup(req.id))
        if (wanted.isEmpty()) return false
        pendingGroup = wanted
        return try {
            requestPermissions(wanted.toTypedArray(), REQ_GROUP)
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not request ${req.id} in place", t)
            pendingGroup = emptyList()
            false
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_GROUP) return
        val outstanding = RuntimePermissions.missing(this, pendingGroup)
        pendingGroup = emptyList()
        // "Don't ask again" means the dialog will never appear from here again,
        // and a row that does nothing when tapped is worse than one that sends
        // you to Settings. So when the platform is done asking, we take over.
        //
        // AN EMPTY `grantResults` IS NOT AN ANSWER. It means the request was
        // cancelled — the user pressed Back on the dialog, or something else
        // took the foreground — and nothing was decided. The platform reports
        // "no rationale needed" for a permission that has never been answered
        // just as it does for one refused with don't-ask-again, so without this
        // the two are indistinguishable and the FIRST Back on the first
        // Calendar tap threw the user out to the App info screen.
        //
        // `PermissionRequestActivity` guards exactly this, in the same words,
        // for the same reason. This one did not.
        val decided = grantResults.isNotEmpty()
        val exhausted = decided && outstanding.isNotEmpty() && outstanding.none {
            runCatching { shouldShowRequestPermissionRationale(it) }.getOrDefault(false)
        }
        if (exhausted) GrapheneCompat.openAppDetails(this)
        refresh()
    }

    private companion object {
        const val TAG = "JarvisSystemCheck"
        const val REQ_GROUP = 5401

        val NO_LISTENER = object : HermesClient.Listener {
            override fun onSession(sessionId: String, resumed: Boolean) = Unit
            override fun onEvent(type: String, sessionId: String, payload: org.json.JSONObject) = Unit
            override fun onDisconnected(reason: String, willRetry: Boolean) = Unit
            override fun onNotice(text: String) = Unit
            override fun onPrefill(text: String) = Unit
            override fun onError(message: String) = Unit
        }
    }
}
