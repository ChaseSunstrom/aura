package ai.aura.app.automation.ui

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.automation.AutomationRuntime
import ai.aura.app.automation.audit.AuditEntry
import ai.aura.app.automation.audit.AuditLog
import ai.aura.app.automation.policy.Decision
import ai.aura.app.ui.ListActivityScaffold
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import ai.aura.app.ui.v2.ActionButton
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.Projection
import ai.aura.app.ui.v2.Screen
import ai.aura.app.ui.v2.ScreenState
import ai.aura.app.ui.v2.Tone
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import ai.aura.app.ui.v2.HoldButton

/**
 * The audit log, on the phone.
 *
 * Settings has promised for a long time that "the audit log records every action
 * this device actually executed, with its tier and how it was authorised", and
 * the button under that sentence toasted "not available in this build" because
 * this class did not exist — the activity was declared in the manifest and
 * launched by name from `ai.aura.app.ui.JarvisScreens`, and nothing was behind
 * the name. Every dispatch has been writing `filesDir/jarvis/audit.jsonl` all
 * along with no way to read it short of a cable.
 *
 * Two levels, following `ai.aura.app.ui.CrashLogActivity`: a list of lines,
 * and one full entry.
 *
 * Params are redacted by `ParamRedaction.redact` on the way to disk, before
 * anything is written. This screen shows what is in the file and makes no
 * attempt to recover the originals — there is nothing here to recover them
 * from, and that is the point.
 */
class AuditLogActivity : ListActivityScaffold() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private var audit: AuditLog? = null

    /** The entry whose detail is open, or null for the list. */
    private var showing: AuditEntry? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        audit = try {
            AutomationRuntime.ensure(applicationContext).audit
        } catch (t: Throwable) {
            // A log viewer that crashes on the way in is worse than an empty
            // one: this is the screen somebody opens when something has already
            // gone wrong.
            Log.w(TAG, "the automation runtime could not be built", t)
            null
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** See `CrashLogActivity.onBackPressed`: the manifest disables the new API. */
    @Suppress("DEPRECATION", "MissingSuperCall")
    override fun onBackPressed() {
        if (showing != null) {
            showing = null
            refresh()
            return
        }
        super.onBackPressed()
    }

    // --- rendering ----------------------------------------------------------

    private fun refresh() {
        val log = audit
        if (log == null) {
            show {
                Title()
                ScreenState(
                    Screen.ERROR,
                    "The automation runtime could not be started on this device, so there is " +
                        "no audit log to read.",
                )
            }
            return
        }
        val open = showing
        if (open != null) {
            show { Detail(open) }
            return
        }
        scope.launch {
            // Already newest-first: that is what a log screen wants.
            val entries = runCatching { log.read(limit = READ_LIMIT) }
                .onFailure { Log.w(TAG, "could not read the audit log", it) }
                .getOrDefault(emptyList())
            val total = runCatching { log.count() }.getOrDefault(entries.size)
            show { EntryList(entries, total) }
        }
    }

    @Composable
    private fun Title() {
        Headline("Audit log")
        Body("Every action the phone took for ${ActiveTheme.name}, and what allowed it.", dim = true)
    }

    @Composable
    private fun EntryList(entries: List<AuditEntry>, total: Int) {
        Title()
        Body(
            "Every action this device executed, with the tier enforced and how it was " +
                "authorised. Written to ${audit?.file()?.path}. App-private, excluded " +
                "from backups, and never sent anywhere. Parameters are redacted before " +
                "they are written.",
            dim = true,
        )
        if (entries.isEmpty()) {
            ScreenState(Screen.EMPTY, "Nothing has run yet. Each action the phone takes for ${ActiveTheme.name} lands here.")
            return
        }
        Label(
            if (total > entries.size) "${entries.size} of $total, newest first"
            else "$total recorded, newest first"
        )
        Rows(entries, { entry -> showing = entry; refresh() }) { entry ->
            Body("${entry.actionId}  ·  ${entry.status}", tone = toneFor(entry), dim = toneFor(entry) == null)
            Body(
                "${formatTime(entry.timestamp)}  ·  ${entry.tier.name}  ·  " +
                    "${entry.decision.name}  ·  ${entry.source}  ·  ${entry.durationMs}ms",
                dim = true,
            )
        }
        Buttons {
            ActionButton("Copy all", onClick = { copy(entries.joinToString("\n") { oneLine(it) }) })
            // Gone for good, so held rather than tapped twice.
            HoldButton("Clear", onConfirm = ::clearAll, tone = Tone.DANGER)
        }
    }

    /** Green for what ran, red for what was refused, plain for everything else. */
    private fun toneFor(entry: AuditEntry): Tone? = when {
        entry.decision == Decision.DENY || entry.status == "denied" -> Tone.DANGER
        entry.ok -> Tone.OK
        else -> null
    }

    @Composable
    private fun Detail(entry: AuditEntry) {
        Headline("Entry")
        Label(formatTime(entry.timestamp))
        Projection(Modifier.fillMaxWidth(), tone = toneFor(entry) ?: Tone.ACCENT) { Body(detailText(entry)) }
        Buttons {
            ActionButton("Copy", onClick = { copy(detailText(entry)) })
            ActionButton("Back to list", onClick = {
                showing = null
                refresh()
            })
        }
    }

    private fun detailText(entry: AuditEntry): String = buildString {
        append("action:     ${entry.actionId}\n")
        append("when:       ${formatTime(entry.timestamp)}\n")
        append("tier:       ${entry.tier.name}\n")
        append("decision:   ${entry.decision.name}\n")
        append("status:     ${entry.status}\n")
        append("ok:         ${entry.ok}\n")
        append("source:     ${entry.source}\n")
        append("duration:   ${entry.durationMs}ms\n")
        entry.commandId?.let { append("command id: $it\n") }
        entry.note?.let { append("note:       $it\n") }
        entry.error?.let { append("error:      $it\n") }
        append("\nparams (already redacted on write):\n")
        append(
            entry.params?.let { runCatching { it.toString(2) }.getOrElse { _ -> it.toString() } }
                ?: "(none)"
        )
    }

    private fun oneLine(entry: AuditEntry): String =
        "${formatTime(entry.timestamp)}  ${entry.actionId}  ${entry.tier.name}  " +
            "${entry.decision.name}  ${entry.status}  ${entry.durationMs}ms  ${entry.source}"

    // --- actions ------------------------------------------------------------

    private fun clearAll() {
        val log = audit ?: return
        scope.launch {
            runCatching { log.clear() }.onFailure { Log.w(TAG, "audit clear failed", it) }
            showing = null
            toast("Audit log cleared")
            refresh()
        }
    }

    private fun copy(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        if (cm == null) {
            toast("No clipboard on this device")
            return
        }
        try {
            val clip = ClipData.newPlainText("${ActiveTheme.name} audit log", text)
            // Android 13+ previews whatever is copied, and a system clipboard
            // entry is readable by the OS UI. These lines are already redacted
            // but they are still a record of what this phone did; flagging them
            // sensitive keeps them out of the preview.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                clip.description.extras = PersistableBundle().apply {
                    putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
                }
            }
            cm.setPrimaryClip(clip)
            toast("Copied ${text.length} characters")
        } catch (t: Throwable) {
            toast("Could not copy: ${t.javaClass.simpleName}")
        }
    }

    private companion object {
        private const val TAG = "JarvisAuditUi"

        /** How many entries to materialise. The file itself is capped too. */
        private const val READ_LIMIT = 200
    }
}
