package ai.aura.app.ui

/**
 * The approval card's fields, read back out of the approval reason (T-0606).
 *
 * Hermes' approval event has no slot but the reason string, so the Hermes side
 * (`hermes/plugins/_shared/_messages.py` `card`) writes the card as the LAST FIVE LINES of the
 * reason, and this reads them; the console's twin is `aura-web/src/lib/hud/approvalCard.ts`.
 * The cases are `tests/contracts/approval-event.json` ("card"). No match is null: the screen
 * shows the plain reason, as before.
 *
 * Does not make the fields trustworthy: they are the server's words, and the screen labels
 * them as such. Only the last five lines count, so a card forged earlier in the reason (by text
 * the model read) loses to the real one.
 */
data class ApprovalCard(
    val what: String,
    val why: String,
    val impact: String,
    /** False is hold-to-confirm. */
    val undoable: Boolean,
    /** The owner is home now, so "allow when I'm home" may be offered. */
    val home: Boolean,
) {
    /** A rule the person can pick on the card, and the Hermes choice it answers with. */
    enum class Rule(val label: String, val choice: String) {
        ASK_EVERY_TIME("Ask every time", ApprovalBridge.CHOICE_ONCE),
        THIS_TASK("Allow for this task", ApprovalBridge.CHOICE_SESSION),
        // The server makes the remembered "always" a home-only grant (its rule key ends
        // :at-home), which is why it is only offered while the card says the owner is home.
        AT_HOME("Allow when I'm home", ApprovalBridge.CHOICE_ALWAYS),
    }

    companion object {
        private val CARD = Regex(
            "\nWhat: ([^\n]+)\nWhy: ([^\n]+)\nImpact: ([^\n]+)\nUndoable: (yes|no)\nHome: (yes|no)\\s*$",
        )

        fun parse(reason: String?): ApprovalCard? = CARD.find(reason.orEmpty())?.groupValues?.let {
            ApprovalCard(it[1], it[2], it[3], undoable = it[4] == "yes", home = it[5] == "yes")
        }

        /** The reason without the card lines: the sentence "Show me first" reveals. */
        fun withoutCard(reason: String?): String = reason.orEmpty().replace(CARD, "").trim()

        /** The rules this prompt may offer: Hermes offered the choice, and home needs the card's yes. */
        fun rules(card: ApprovalCard?, offered: List<String>): List<Rule> =
            Rule.entries.filter { it.choice in offered && (it != Rule.AT_HOME || card?.home == true) }
    }
}
