package ai.aura.app.ui

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.crash.CrashRecord
import ai.aura.app.crash.JarvisCrashHandler
import ai.aura.app.ui.v2.ActionButton
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.HoldButton
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.Projection
import ai.aura.app.ui.v2.Screen
import ai.aura.app.ui.v2.ScreenState
import ai.aura.app.ui.v2.Tone
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.ui.Modifier

/**
 * The crash log, on the phone.
 *
 * The app this replaced crashed on GrapheneOS constantly and every diagnosis
 * needed a laptop, a cable, and a logcat buffer that had usually already rolled
 * over. [JarvisCrashHandler] writes each crash to app storage; this screen
 * turns that file into something a person can read, copy and clear without any
 * of that.
 *
 * Two levels only: a list of headlines, and one full report. There is no
 * upload button and no crash reporting service — the file is local, it is
 * yours, and copying it somewhere is a decision you make one crash at a time.
 */
class CrashLogActivity : ListActivityScaffold() {

    private var showing: CrashRecord? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        showList()
    }

    /**
     * Back steps out of a report before it leaves the screen.
     *
     * Still the classic callback rather than an `OnBackInvokedCallback`,
     * because the manifest sets `enableOnBackInvokedCallback="false"` for the
     * whole app — the consent screen depends on the old dispatch semantics.
     */
    @Suppress("DEPRECATION", "MissingSuperCall")
    override fun onBackPressed() {
        if (showing != null) {
            showing = null
            showList()
            return
        }
        super.onBackPressed()
    }

    // --- list ---------------------------------------------------------------

    private fun showList() {
        val records = JarvisCrashHandler.recent(this)
        val where = JarvisCrashHandler.file(this).path
        show {
            // The console's ScreenTitle, sentence case with its lede. The
            // settings screen's CRASH LOGS button keeps the caps label the
            // instrumented suite taps to get here.
            Headline("Crash logs")
            Body(
                "Every crash this phone recorded, newest first. Written to $where — app-private, " +
                    "excluded from backups, never sent anywhere.",
                dim = true,
            )
            if (records.isEmpty()) {
                // What would be here, and how it gets here.
                ScreenState(
                    Screen.EMPTY,
                    "No crashes recorded. When ${ActiveTheme.name} closes unexpectedly, the stack trace lands " +
                        "here — on this phone, and nowhere else.",
                )
            } else {
                Label("${records.size} recorded, newest first")
                Rows(records, ::showRecord) { record ->
                    // Danger as words: the text colour, not the mark's.
                    Body(record.headline(), tone = Tone.DANGER)
                    Body(
                        "${formatTime(record.timestamp)}  ·  ${record.thread}  ·  " +
                            "v${record.appVersion}  ·  Android ${record.androidVersion}",
                        dim = true,
                    )
                }
                Buttons {
                    ActionButton("Copy all", onClick = { copy(records.joinToString("\n\n---\n\n") { it.toText() }) })
                    // Gone for good, so held rather than tapped.
                    HoldButton("Clear", onConfirm = ::clearAll, tone = Tone.DANGER)
                }
            }
        }
    }

    // --- one report ---------------------------------------------------------

    private fun showRecord(record: CrashRecord) {
        showing = record
        show {
            Headline("Crash")
            Label(formatTime(record.timestamp))
            Projection(Modifier.fillMaxWidth(), tone = Tone.DANGER) { Body(record.toText()) }
            Buttons {
                ActionButton("Copy", onClick = { copy(record.toText()) })
                ActionButton("Back to list", onClick = {
                    showing = null
                    showList()
                })
            }
        }
    }

    // --- plumbing -----------------------------------------------------------

    private fun clearAll() {
        val ok = JarvisCrashHandler.clear(this)
        toast(if (ok) "Crash log cleared" else "Could not clear the crash log")
        showing = null
        showList()
    }

    private fun copy(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        if (cm == null) {
            toast("No clipboard on this device")
            return
        }
        try {
            val clip = ClipData.newPlainText("${ActiveTheme.name} crash", text)
            // Android 13+ shows a preview of whatever is copied, and a system
            // clipboard entry is readable by the OS UI. A stack trace is
            // already redacted (see JarvisCrashHandler.redact) but it is still
            // diagnostic detail about this device; flagging it sensitive keeps
            // it out of the preview toast.
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
}
