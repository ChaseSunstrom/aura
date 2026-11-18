package ai.aura.app.assist.hermes

import org.json.JSONObject

/**
 * `GET /api/status`, read: the model JARVIS runs on and whether it is still
 * loading (`docs/hermes-hud-protocol.md` § Cold start).
 *
 * `model.state` is llama-swap's state for the configured model — `ready`,
 * `starting` (render "warming up model"; the first token can take minutes),
 * `stopping`, `unloaded`, `other-model`, `unreachable`. The poll rate depends
 * on whether a turn is waiting on a token: every 5 s then, every 30 s
 * otherwise, so a cold start is noticed while somebody is watching and costs
 * nothing while nobody is.
 */
data class HermesStatus(
    val ok: Boolean,
    val model: String,
    val state: String,
    val warmingUp: Boolean,
    val backendOk: Boolean,
    val apiOk: Boolean,
    /**
     * Who is primary (`tests/contracts/persona.json`): the adapter reads the
     * gateway's `state/persona.json` into `persona.active`, so a phone that
     * connects after a switch speaks in the right voice from its first reply.
     * Lower-case; empty when the status does not say, which means JARVIS.
     */
    val persona: String = "",
    /**
     * The adapter's one word and why (`status: {word, reason}`, T-0583): decided there, printed
     * here, the same on the console and the desktop (`tests/contracts/status-words.json`). Both
     * empty from an adapter older than the field, and the wording below is then what it was.
     */
    val word: String = "",
    val reason: String = "",
) {
    /** `word: reason`, the word alone with no reason; empty when the adapter sent no word. */
    val label: String get() = if (word.isNotEmpty() && reason.isNotEmpty()) "$word: $reason" else word

    /** The adapter sent a word and it is not `ready`; false for an older adapter, which sends none. */
    val notReady: Boolean get() = word.isNotEmpty() && word != "ready"

    /** The orb's caption while the model loads: the adapter's label, or this phone's own words for an older adapter. */
    val caption: String
        get() = label.ifEmpty { if (warmingUp) WARMING_LABEL else state.lowercase().replaceFirstChar { it.uppercase() } }

    /** One line for a settings readout: `qwen3.8-27b · READY`. */
    val readout: String
        get() = listOf(model.ifEmpty { "no model" }, caption).joinToString(" · ")

    companion object {
        const val WARMING_LABEL = "Warming up model"

        /** Shown when the adapter does not answer at all: the table's `no_answer`, the one label made here. */
        const val NO_ANSWER_LABEL = "down: no answer"

        const val POLL_PENDING_MS = 5_000L
        const val POLL_IDLE_MS = 30_000L

        fun parse(body: String?): HermesStatus? {
            if (body.isNullOrBlank()) return null
            val json = try {
                JSONObject(body)
            } catch (e: Exception) {
                return null
            }
            val model = json.optJSONObject("model") ?: return null
            val hermes = json.optJSONObject("hermes")
            return HermesStatus(
                ok = json.optBoolean("ok", false),
                model = model.optString("model"),
                state = model.optString("state", "unknown"),
                warmingUp = model.optBoolean("warming_up", false),
                backendOk = hermes?.optJSONObject("backend")?.optBoolean("ok", false) ?: false,
                apiOk = hermes?.optJSONObject("api_server")?.optBoolean("ok", false) ?: false,
                persona = json.optJSONObject("persona")?.optString("active").orEmpty().lowercase(),
                word = json.optJSONObject("status")?.optString("word").orEmpty(),
                reason = json.optJSONObject("status")?.optString("reason").orEmpty(),
            )
        }

        /**
         * The caption for a turn waiting on its first token: the no-answer label when the status
         * poll got nothing back, the warming caption while the model loads, null once it is ready
         * (the turn's own caption then stands).
         */
        fun waitingCaption(status: HermesStatus?): String? = when {
            status == null -> NO_ANSWER_LABEL
            status.warmingUp -> status.caption
            else -> null
        }

        /** How long until the next poll. */
        fun pollIntervalMs(turnPending: Boolean, tokenSeen: Boolean): Long =
            if (turnPending && !tokenSeen) POLL_PENDING_MS else POLL_IDLE_MS
    }
}
