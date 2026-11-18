package ai.aura.app.car.holo

import ai.aura.app.ui.theme.JarvisThemes
import java.util.Locale

/**
 * What the driver says to the drive itself (T-0613): the shown card's action, by its label
 * or the words people use for it, and "reroute" / "next turn". The whole utterance has to
 * be the phrase (a wake word in front and a "please" behind aside): "answer and tell them
 * I'll be late" is a sentence for Jarvis, not a button, and so is "reroute via the bridge".
 *
 * Not covered: English only; a phrase the recogniser mishears is Jarvis's to answer.
 */
object DriveWords {
    enum class Command { REROUTE, NEXT_TURN }

    /** The action on [actions] that [text] says, or `null`. */
    fun action(text: String, actions: List<CarAction>): CarAction? {
        val said = normal(text)
        if (said.isEmpty()) return null
        return actions.firstOrNull { said == normal(it.label) || said in (SAID[it.id] ?: emptySet()) }
    }

    fun command(text: String): Command? {
        val said = normal(text)
        return COMMANDS.entries.firstOrNull { said in it.value }?.key
    }

    private val SAID = mapOf(
        "answer" to setOf("answer", "answer it", "answer the call", "pick up", "pick it up", "accept", "take it", "take the call"),
        "decline" to setOf("decline", "decline it", "reject", "reject it", "hang up", "ignore", "ignore it", "ignore the call", "not now"),
        "driving" to setOf("im driving", "tell them im driving", "say im driving"),
        "allow" to setOf("allow", "allow it", "allow once", "yes", "approve", "go ahead"),
        "deny" to setOf("deny", "deny it", "no", "dont"),
    )

    private val COMMANDS = mapOf(
        Command.REROUTE to setOf("reroute", "re route", "recalculate", "find another route", "find a new route", "new route"),
        Command.NEXT_TURN to setOf(
            "next turn", "whats the next turn", "what is the next turn", "where do i turn", "wheres my next turn", "when do i turn",
        ),
    )

    // Every pack's name leads, not the worn one's: the recogniser may hear either, and a word
    // typed here would stay the old name after a theme switch.
    private val LEAD = JarvisThemes.ALL.flatMap { listOf("hey ${it.spokenName.lowercase()}", it.spokenName.lowercase()) } +
        listOf("okay", "ok", "please")
    private val TAIL = listOf("please", "thank you", "thanks")

    /** Lower case, no apostrophes, words split on anything else, wake words and courtesy off. */
    private fun normal(text: String): String {
        var s = text.lowercase(Locale.ROOT).replace("'", "").replace("’", "")
            .replace(NON_WORD, " ").trim().replace(SPACES, " ")
        var changed = true
        while (changed) {
            changed = false
            for (w in LEAD) if (s == w || s.startsWith("$w ")) { s = s.removePrefix(w).trim(); changed = true }
            for (w in TAIL) if (s == w || s.endsWith(" $w")) { s = s.removeSuffix(w).trim(); changed = true }
        }
        return s
    }

    private val NON_WORD = Regex("[^a-z0-9]+")
    private val SPACES = Regex(" +")
}
