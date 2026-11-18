package ai.aura.app.assist.hermes

/**
 * PURE LOGIC: "Ask why" on a Hermes approval (T-1054). It is a no first, and then a line the
 * model reads before it asks again. Hermes's `approval.respond` carries no text, so the line is
 * the next `prompt.submit`, exactly as the console sends it. The sentence, the template and the
 * cap on the headline are `tests/contracts/approval-event.json` `ask_why`, which `ApprovalAskWhyTest`
 * reads; the console's `approval.ts` builds the same line from the same table.
 *
 * Does not cover: that the model answers. Nothing here reads a reply, and the line is the
 * person's words to the model, never an instruction the phone obeys.
 */
object AskWhy {
    const val SENTENCE = "Before asking again, say why you need this."

    /** A card's headline is the server's text, so the line the model reads is bounded. */
    const val WHAT_CAP = 200

    /** What the model is told: the ask it was refused, in the card's own words, and the sentence. */
    fun line(what: String): String =
        "You asked: “${what.trim().take(WHAT_CAP)}”. I said no for now. $SENTENCE"
}
