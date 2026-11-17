package ai.jarvis.app.assist.hermes

/**
 * The goal panel's state, read off a `status.update` with `kind == "goal"`.
 *
 * Hermes announces the goal loop as one line of text whose leading glyph is
 * the state (`docs/hermes-hud-protocol.md` § events): `⊙` set, `↻` continuing
 * (`(n/N)` is turn/budget, the text after `: ` the judge's reason), `⏳`
 * parked, `⏸` paused, `✓` achieved, and the bare `Goal cleared`. The reason
 * is always shown verbatim — it is the judge's own sentence, and the whole
 * point of the panel is to read it.
 *
 * Pure; mirrored by `android-app/tools/hermes_goal_glyph_test.py`.
 */
data class GoalStatus(
    val state: State,
    /** The turn the loop is on, when the text said. */
    val turn: Int?,
    /** The turn budget, when the text said. */
    val budget: Int?,
    /** The judge's reason, or the goal text for SET — verbatim, never empty for a known state. */
    val reason: String,
    /** The whole line, as received. */
    val text: String,
) {
    enum class State(val glyph: String, val label: String) {
        SET("⊙", "Goal set"),
        CONTINUING("↻", "Continuing"),
        PARKED("⏳", "Parked"),
        PAUSED("⏸", "Paused"),
        ACHIEVED("✓", "Achieved"),
        CLEARED("", "No goal"),
        UNKNOWN("", "Goal"),
    }

    /** "n/N" when both are known, "n" or "" otherwise. */
    val progress: String
        get() = when {
            turn != null && budget != null -> "$turn/$budget"
            turn != null -> "$turn"
            budget != null -> "0/$budget"
            else -> ""
        }

    /** True while the loop is still running toward the goal. */
    val active: Boolean get() = state == State.SET || state == State.CONTINUING

    /** True when the panel has something to show at all. */
    val present: Boolean get() = state != State.CLEARED

    companion object {
        /** Glyph → state, in the order they are tried. The doc's table. */
        val GLYPHS: Map<String, State> = linkedMapOf(
            "⊙" to State.SET,
            "↻" to State.CONTINUING,
            "⏳" to State.PARKED,
            "⏸" to State.PAUSED,
            "✓" to State.ACHIEVED,
        )

        const val CLEARED_TEXT = "Goal cleared"

        private val TURN_OF_BUDGET = Regex("""\((\d+)/(\d+)\)""")
        private val TURNS_USED = Regex("""(\d+)/(\d+)\s+turns?\s+used""")
        private val BUDGET = Regex("""\((\d+)-turn budget\)""")

        /** Null for a blank line; UNKNOWN (reason = the whole line) for one with no glyph. */
        fun parse(raw: String?): GoalStatus? {
            val text = raw?.trim().orEmpty()
            if (text.isEmpty()) return null
            if (text.equals(CLEARED_TEXT, ignoreCase = true) || text.startsWith("$CLEARED_TEXT.", ignoreCase = true)) {
                return GoalStatus(State.CLEARED, null, null, "", text)
            }
            val state = GLYPHS.entries.firstOrNull { text.startsWith(it.key) }?.value
                ?: return GoalStatus(State.UNKNOWN, null, null, text, text)
            val body = text.removePrefix(state.glyph).trim()

            val (turn, budget) = TURN_OF_BUDGET.find(body)?.let { it.groupValues[1].toIntOrNull() to it.groupValues[2].toIntOrNull() }
                ?: TURNS_USED.find(body)?.let { it.groupValues[1].toIntOrNull() to it.groupValues[2].toIntOrNull() }
                ?: (null to BUDGET.find(body)?.groupValues?.get(1)?.toIntOrNull())

            // The reason is what follows the first ": " — the judge's sentence
            // for CONTINUING and ACHIEVED, the goal itself for SET. Lines with
            // no colon (parked, paused) are shown whole after the glyph.
            val colon = body.indexOf(": ")
            val reason = if (colon >= 0) body.substring(colon + 2).trim() else body
            return GoalStatus(state, turn, budget, reason.ifEmpty { body }, text)
        }
    }
}
