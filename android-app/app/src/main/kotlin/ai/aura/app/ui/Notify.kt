package ai.aura.app.ui

import ai.aura.app.reactor.ReactorState
import ai.aura.app.ui.theme.JarvisTokens

/**
 * A notification echoes the reactor (`tests/contracts/notify.json`; GLASS_HUD
 * §10, §11 and the brief's "notification icons and colours match").
 *
 * Every notification this app posts wears the worn pack's small icon already
 * (`ActiveTheme.statusIcon`: JARVIS's `ic_jarvis_status`, drawn by
 * `scripts/gen-icons.py` from the reactor's geometry, or AURA's `ic_aura_status`
 * from her glyph, T-0908). What it did not wear was the reactor's colour: every builder that
 * set one set `JarvisUi.ACCENT`, and most set none, so an approval waiting on
 * the person and a plain "update available" were the same cyan in the shade
 * while the HUD showed one in amber and the other at rest.
 *
 * The contract has two kinds of row and this object has two entry points:
 *
 *  * a **level** — `info`, `ok`, `attention`, `danger` — names the reactor state
 *    whose colour the notification is drawn in and the word a surface with no
 *    glyph prints. `ok` is the one level with no reactor state (a reactor has no
 *    "succeeded") and wears `color.ok`. The `notify.*` templates the inventory
 *    names each carry a level; [Template] is that table.
 *  * a **state** — the wake-word service's standing notification is about the
 *    reactor itself, so it carries the state's own colour and word
 *    (`ReactorState.color`, `.word`): "Listening" in the listening light, the
 *    same word the tray and the console strip show (§10).
 *
 * The tables are mirrored, not read: `android-app/tools/notify_test.py` reads
 * the contract and this file and fails if a level's state, colour token or word
 * differs, or if a builder still sets a colour by hand. Pure Kotlin on purpose,
 * for the same reason `ReactorState` is: the JVM test and the Python mirror
 * both read it, and an `android.*` import would drag the framework into a table.
 */
object Notify {
    /** The contract's `levels`: the reactor state each carries, its colour, its word. */
    enum class Level(val key: String, val state: ReactorState, val color: Int, val word: String) {
        INFO("info", ReactorState.IDLE, JarvisTokens.Color.REACTOR_IDLE, ""),
        OK("ok", ReactorState.IDLE, JarvisTokens.Color.OK, "Done"),
        ATTENTION("attention", ReactorState.ATTENTION, JarvisTokens.Color.REACTOR_ATTENTION, "Needs you"),
        DANGER("danger", ReactorState.ALERT, JarvisTokens.Color.REACTOR_ALERT, "Alert"),
    }

    /** The contract's `templates`: the `notify.*` names the inventory uses, each at its level. */
    enum class Template(val key: String, val level: Level) {
        NOTICE("notice", Level.INFO),
        RECOVERED("recovered", Level.OK),
        ALERT("alert", Level.DANGER),
        APPROVAL("approval", Level.ATTENTION),
        REFUSED("refused", Level.DANGER),
        GOAL("goal", Level.INFO),
        LISTENING("listening", Level.INFO),
        TASK("task", Level.INFO),
        REMINDER("reminder", Level.ATTENTION),
        ASK("ask", Level.ATTENTION),
        SERVICE("service", Level.INFO),
        STOPPED("stopped", Level.ATTENTION),
    }

    /** The colour a notification of this template is drawn in (`Notification.Builder.setColor`). */
    fun color(template: Template): Int = template.level.color

    /** The colour of a notification that is about the reactor's own state. */
    fun color(state: ReactorState): Int = state.color

    /**
     * The contract's `aliases` folded in: a level as the panel protocol or the
     * toast bus spells it (`warn`, `warning`, `error`, `success`), or `INFO` for
     * anything else — the same fallback the web's `noticeLevel` makes.
     */
    fun level(key: String?): Level = when (key) {
        "warn", "warning" -> Level.ATTENTION
        "error" -> Level.DANGER
        "success" -> Level.OK
        else -> Level.entries.firstOrNull { it.key == key } ?: Level.INFO
    }
}
