package ai.aura.app.assist.hermes

import org.json.JSONObject

/**
 * What a `command.dispatch` result means, and which sentences are goals.
 *
 * The result's `type` decides the client's next move (`docs/hermes-hud-protocol.md`
 * § command.dispatch result types): `send` shows a notice and then MUST
 * `prompt.submit` the message (that is how `/goal <text>` starts its first
 * turn); `exec` shows output; `prefill` fills the composer and sends nothing;
 * anything else is an error to show. Pure, so the table is a JVM test.
 */
object HermesDispatch {

    sealed interface Outcome {
        /** Show [notice], then submit [message] as a prompt. */
        data class Send(val notice: String, val message: String) : Outcome

        /** Show [output]. Nothing else to do. A plugin command's reply (`type: "plugin"`) is this too. */
        data class Exec(val output: String) : Outcome

        /** Put [message] in the composer. Do not send. */
        data class Prefill(val message: String) : Outcome

        /** Show [message]. */
        data class Error(val message: String) : Outcome
    }

    fun interpret(result: JSONObject): Outcome = when (result.optString("type")) {
        "send" -> Outcome.Send(result.optString("notice"), result.optString("message"))
        "exec", "plugin" -> Outcome.Exec(result.optString("output"))
        "prefill" -> Outcome.Prefill(result.optString("message"))
        "error" -> Outcome.Error(
            result.optString("message").ifEmpty { result.optString("error").ifEmpty { "command failed" } }
        )
        else -> Outcome.Error("unexpected dispatch result: ${result.optString("type").ifEmpty { "(no type)" }}")
    }

    /**
     * A JSON-RPC error for a dispatch, as a sentence.
     *
     * 4018 is the TUI gateway saying the command is not routed here — which
     * for `jgoal` means the aura-goals plugin is not installed on the
     * backend (verified on v0.21.0: an unregistered command returns 4018).
     */
    fun interpretError(code: Int, message: String, name: String): Outcome.Error = when (code) {
        PLUGIN_MISSING -> Outcome.Error(
            "The backend has no `$name` command (error $PLUGIN_MISSING): the aura-goals plugin " +
                "is not installed on Hermes. Plain `goal` still works."
        )
        else -> Outcome.Error("$name failed (error $code): ${message.ifEmpty { "no message" }}")
    }

    const val PLUGIN_MISSING = 4018

    /**
     * The commands the aura-goals plugin registers. A plugin command handler
     * does not receive the session, so the app prefixes its arg with
     * `@<session_id> ` (`docs/hermes-hud-protocol.md` § Goal commands); the
     * built-in `goal` needs no prefix. `/subgoal` is a reserved built-in name
     * Hermes will not let a plugin register, so subgoals live under `jgoal`
     * (`@<sid> subgoal <text>`).
     */
    val PLUGIN_COMMANDS: Set<String> = setOf("jgoal")

    /** The arg as the wire wants it: session-prefixed for a plugin command, untouched otherwise. */
    fun argFor(name: String, arg: String, sessionId: String?): String {
        if (name !in PLUGIN_COMMANDS) return arg
        val sid = sessionId.orEmpty()
        if (sid.isEmpty()) return arg
        val prefix = "@$sid "
        return if (arg.startsWith(prefix)) arg else prefix + arg
    }

    /**
     * "keep working on this until …" is a goal, not a prompt: the app
     * dispatches `goal` with the sentence instead of `prompt.submit` when the
     * transcript starts with one of these (`docs/hermes-hud-protocol.md` § Voice 5).
     */
    val GOAL_TRIGGERS: List<String> = listOf(
        "keep working on",
        "keep going until",
        "don't stop until",
        "get this done",
        "set a goal",
    )

    /** Whether [text] is a goal, by its opening words. Case and curly apostrophes do not matter. */
    fun isGoal(text: String): Boolean {
        val plain = text.trim().lowercase().replace('’', '\'')
        return GOAL_TRIGGERS.any { plain.startsWith(it) }
    }
}
