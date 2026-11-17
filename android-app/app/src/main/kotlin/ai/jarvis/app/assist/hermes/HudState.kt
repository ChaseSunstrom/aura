package ai.jarvis.app.assist.hermes

import ai.jarvis.app.assist.ActivityRows
import ai.jarvis.app.assist.ToolRun
import org.json.JSONObject
import java.util.EnumSet

/**
 * The HUD, as a reducer over the socket's events.
 *
 * One turn's streaming text, its reasoning, its tool calls in the phone's
 * existing [ToolRun] and [ActivityRows] vocabulary, the approvals and
 * clarifications waiting on the person, the goal loop's state and the model in
 * use — all from `apply(type, payload)` and nothing else. Pure, so a scripted
 * turn (tool → approval → goal continue → achieved) is a JVM test.
 *
 * Hermes's tool events carry a `tool_id` and no position in a round; the rows
 * are numbered in arrival order within the turn so [ToolRun]'s "n of N" reads
 * as the count so far. A `tool.complete` for an id never started still lands
 * (a resumed session replays history mid-turn), as [ToolRun.finished] allows.
 */
class HudState {

    /** Which parts of the HUD an event changed, so a surface redraws only those. */
    enum class Change { TEXT, REASONING, TOOLS, ACTIVITY, APPROVAL, CLARIFY, GOAL, MODEL, STATUS, COMPLETE, ERROR, OFFLINE, PANELS, HIGHLIGHT, PERSONA }

    /** A `hud.layout.highlight` frame: a 1.2 s rim pulse on one panel, not a scroll or a modal (§9.8). */
    data class Highlight(val panel: String, val surface: String, val durationMs: Int, val reason: String, val at: Long)

    data class Approval(
        val requestId: String,
        val tool: String,
        /** The command or the arguments, verbatim, as the server sent them. */
        val params: String,
        val reason: String,
        val choices: List<String>,
        /** `"phone"` when the adapter wants this ask signed on the phone (T-0945), else empty. */
        val confirm: String = "",
        /** What the signature must cover; empty unless [confirm] is `"phone"`. */
        val challenge: String = "",
    ) {
        /** An allow of this ask is valid only with a fingerprint-backed signature of [challenge]. */
        val needsPhone: Boolean get() = confirm == ai.jarvis.app.ui.ApprovalProof.CONFIRM_PHONE && challenge.isNotEmpty()
    }

    data class Clarify(val requestId: String, val question: String, val options: List<String>)

    /** The reply so far, appended from `message.delta`; replaced by `message.complete`. */
    var text: String = ""
        private set

    /** The model's thinking, from `reasoning.delta` / `reasoning.available`. */
    var reasoning: String = ""
        private set

    /** True between a submitted prompt and its `message.complete`. */
    var turnPending: Boolean = false
        private set

    /** True once any token of this turn has arrived. Drives the status poll rate. */
    var tokenSeen: Boolean = false
        private set

    var model: String = ""
        private set
    /**
     * Who is primary, lower-case, from `persona.switch` (`payload.active`); empty
     * until an event or the status says. The voice a reply is spoken in is this
     * name's (the persona and its voice share a name: `tests/contracts/persona.json`).
     * Settable from outside: the status poll writes what the gateway's file says
     * for a phone that connected after the switch.
     */
    var persona: String = ""
    var approvalMode: String = ""
        private set

    /** The last `status.update` that was not a goal, as a one-line status. */
    var status: String = ""
        private set

    /** The last error the server reported, or "" once a turn starts afresh. */
    var lastError: String = ""
        private set

    /** True after `adapter.error`: the adapter cannot reach Hermes. Cleared by any other event. */
    var offline: Boolean = false
        private set

    var goal: GoalStatus? = null
        private set

    /**
     * The panels JARVIS has pulled up, in arrival order; the phone raises a
     * notice from them ([freshNotices]) and draws none. Session state, like [goal]
     * and [approvals]: a new turn does not clear them, only `hud.panel.dismiss`
     * (or a local ttl) does.
     */
    var panels: List<Panel> = emptyList()
        private set

    /** The last highlight asked for; the renderer pulses the rim and lets it lapse. */
    var highlight: Highlight? = null
        private set

    val tools = ToolRun()
    val activity = ActivityRows()

    private val approvalsById = LinkedHashMap<String, Approval>()
    private val clarifiesById = LinkedHashMap<String, Clarify>()
    private val toolIndex = HashMap<String, Int>()
    private val toolNames = HashMap<String, String>()
    private var nextToolIndex = 0

    val approvals: List<Approval> get() = approvalsById.values.toList()
    val clarifies: List<Clarify> get() = clarifiesById.values.toList()

    /** A prompt was submitted: the reply, the thinking and the tool rows start over. */
    fun turnStarted() {
        text = ""
        reasoning = ""
        lastError = ""
        turnPending = true
        tokenSeen = false
        tools.clear()
        toolIndex.clear()
        toolNames.clear()
        nextToolIndex = 0
    }

    /** The turn was interrupted from this end. */
    fun turnInterrupted() {
        turnPending = false
    }

    /**
     * A token arrived with no turn pending: the goal loop started a turn of
     * its own (`↻ Continuing toward goal` is followed by a fresh reply with
     * no `prompt.submit` from this end). Without this the new reply would be
     * appended to the last one, and the tool rows of two turns would share a
     * list. The goal, the model and the approvals are session state and stay.
     */
    private fun serverStartedTurn() {
        if (!turnPending) turnStarted()
    }

    /** The person answered; the request leaves the list. */
    fun approvalAnswered(requestId: String): Boolean = approvalsById.remove(requestId) != null
    fun clarifyAnswered(requestId: String): Boolean = clarifiesById.remove(requestId) != null

    /**
     * Seed the surface from a `hud @<sid> list` reply, issued once after a
     * session is attached so a late joiner sees the panels already up. Returns
     * true when the list changed anything, so the caller can redraw. Folded
     * through [HudPanels.applyShow], so an event that beat the list back is not
     * clobbered by a staler row.
     */
    fun seedPanels(list: org.json.JSONArray?, now: Long = System.currentTimeMillis()): Boolean {
        val next = HudPanels.seedFromList(panels, list, now)
        if (next === panels) return false
        panels = next
        return true
    }

    /**
     * Take a panel down here and now, without waiting for the server's dismiss:
     * the optimistic removal a phone-side dismiss shows, reconciled by the
     * `hud.panel.dismiss` the plugin then emits, and the local ttl fallback.
     * Returns true when something left the list.
     */
    fun dismissPanelLocally(id: String): Boolean {
        val next = HudPanels.applyDismiss(panels, id)
        if (next === panels) return false
        panels = next
        return true
    }

    /** Drop any panel whose local ttl has passed — the fallback when no dismiss arrived. */
    fun pruneExpiredPanels(now: Long = System.currentTimeMillis()): Boolean {
        val next = HudPanels.pruneExpired(panels, now)
        if (next === panels) return false
        panels = next
        return true
    }

    /** Apply one event. Returns what changed; empty for an event the HUD does not draw. */
    fun apply(type: String, payload: JSONObject, now: Long = System.currentTimeMillis()): Set<Change> {
        val changed = EnumSet.noneOf(Change::class.java)
        if (offline && type != ADAPTER_ERROR) {
            offline = false
            // The adapter's "backend unavailable" goes with the outage it reported: kept, it
            // read as an error event once the backend answered, and the reactor sat at alert
            // until the next turn (T-1483). No Change.ERROR: that is how an error is announced,
            // and nothing failed here.
            lastError = ""
            changed.add(Change.OFFLINE)
        }
        when (type) {
            "persona.switch" -> {
                val active = payload.optString("active").lowercase()
                if (active.isNotEmpty() && active != persona) {
                    persona = active
                    changed.add(Change.PERSONA)
                }
            }
            "session.info" -> {
                model = payload.optString("model", model)
                approvalMode = payload.optString("approval_mode", approvalMode)
                changed.add(Change.MODEL)
            }
            "message.delta" -> {
                val delta = payload.optString("text")
                if (delta.isNotEmpty()) {
                    serverStartedTurn()
                    text += delta
                    tokenSeen = true
                    changed.add(Change.TEXT)
                }
            }
            "message.interim" -> {
                // A mid-turn note the model wants shown. Already streamed
                // text is not repeated.
                if (!payload.optBoolean("already_streamed", false)) {
                    val note = payload.optString("text")
                    if (note.isNotEmpty()) {
                        text = if (text.isEmpty()) note else "$text\n$note"
                        tokenSeen = true
                        changed.add(Change.TEXT)
                    }
                }
            }
            "message.complete" -> {
                val whole = payload.optString("text")
                if (whole.isNotEmpty()) text = whole
                tokenSeen = tokenSeen || whole.isNotEmpty()
                turnPending = false
                changed.add(Change.TEXT)
                changed.add(Change.COMPLETE)
            }
            "reasoning.delta" -> {
                val delta = payload.optString("text")
                if (delta.isNotEmpty()) {
                    serverStartedTurn()
                    reasoning += delta
                    tokenSeen = true
                    changed.add(Change.REASONING)
                }
            }
            "reasoning.available" -> {
                val whole = payload.optString("text")
                if (whole.isNotEmpty()) {
                    reasoning = whole
                    changed.add(Change.REASONING)
                }
            }
            "tool.start" -> {
                serverStartedTurn()
                val id = payload.optString("tool_id").ifEmpty { "tool-$nextToolIndex" }
                val name = payload.optString("name").ifEmpty { "tool" }
                val index = toolIndex.getOrPut(id) { nextToolIndex++ }
                toolNames[id] = name
                val args = flatten(payload.optJSONObject("args"))
                tokenSeen = true
                tools.started(name, ROUND, index, nextToolIndex, ToolRun.summarise(args))
                activity.apply(
                    "jarvis_tool_started",
                    JSONObject()
                        .put("name", name)
                        .put("round", ROUND)
                        .put("index", index)
                        .put("arguments", payload.optJSONObject("args") ?: JSONObject()),
                    now,
                )
                changed.add(Change.TOOLS)
                changed.add(Change.ACTIVITY)
            }
            "tool.progress" -> {
                val id = payload.optString("tool_id")
                val index = toolIndex[id]
                val name = toolNames[id]
                if (index != null && name != null) {
                    val line = payload.optString("text").ifEmpty { payload.optString("message") }
                    // A progress line replaces the row's summary while it runs.
                    tools.started(name, ROUND, index, nextToolIndex, line.ifEmpty { tools.rows.firstOrNull { it.index == index }?.summary.orEmpty() })
                    changed.add(Change.TOOLS)
                }
            }
            "tool.complete" -> {
                val id = payload.optString("tool_id").ifEmpty { "tool-$nextToolIndex" }
                val name = payload.optString("name").ifEmpty { toolNames[id] ?: "tool" }
                val index = toolIndex.getOrPut(id) { nextToolIndex++ }
                val error = payload.optString("error").takeIf { it.isNotEmpty() && it != "null" }
                val ok = error == null
                val durationMs = durationMs(payload.opt("duration"))
                tools.finished(name, ROUND, index, nextToolIndex, ok, error, durationMs)
                activity.apply(
                    "jarvis_tool_finished",
                    JSONObject()
                        .put("name", name)
                        .put("round", ROUND)
                        .put("index", index)
                        .put("ok", ok)
                        .put("error", error ?: "")
                        .put("duration_ms", durationMs),
                    now,
                )
                changed.add(Change.TOOLS)
                changed.add(Change.ACTIVITY)
            }
            "approval.request" -> {
                val id = payload.optString("request_id")
                if (id.isNotEmpty()) {
                    val params = payload.optString("command").ifEmpty {
                        payload.optJSONObject("args")?.toString() ?: payload.optString("args")
                    }
                    val confirm = payload.optString(ai.jarvis.app.ui.ApprovalProof.CONFIRM_FIELD)
                    val challenge = payload.optString(ai.jarvis.app.ui.ApprovalProof.CHALLENGE_FIELD)
                    val phone = confirm == ai.jarvis.app.ui.ApprovalProof.CONFIRM_PHONE && challenge.isNotEmpty()
                    approvalsById[id] = Approval(
                        requestId = id,
                        tool = payload.optString("tool"),
                        params = params,
                        reason = payload.optString("reason"),
                        // Only once and deny: the adapter forwards no wider allow for a signed ask.
                        choices = if (phone) ai.jarvis.app.ui.ApprovalProof.CHOICES else choicesOf(payload),
                        confirm = if (phone) confirm else "",
                        challenge = if (phone) challenge else "",
                    )
                    activity.apply(
                        "jarvis_approval_required",
                        JSONObject().put("id", id).put("tool", payload.optString("tool")),
                        now,
                    )
                    changed.add(Change.APPROVAL)
                    changed.add(Change.ACTIVITY)
                }
            }
            "approval.resolved", "approval.response" -> {
                val id = payload.optString("request_id")
                if (approvalsById.remove(id) != null) {
                    activity.apply(
                        "jarvis_approval_resolved",
                        JSONObject().put("id", id).put("tool", payload.optString("tool")).put("decision", payload.optString("choice")),
                        now,
                    )
                    changed.add(Change.APPROVAL)
                    changed.add(Change.ACTIVITY)
                }
            }
            "clarify.request" -> {
                val id = payload.optString("request_id")
                if (id.isNotEmpty()) {
                    clarifiesById[id] = Clarify(id, payload.optString("question"), strings(payload.optJSONArray("options")))
                    changed.add(Change.CLARIFY)
                }
            }
            "hud.panel.show" -> {
                val panel = HudPanels.panelFromShow(payload, now)
                if (panel != null) {
                    val next = HudPanels.applyShow(panels, panel)
                    if (next !== panels) {
                        panels = next
                        changed.add(Change.PANELS)
                    }
                }
            }
            "hud.panel.update" -> {
                val next = HudPanels.applyUpdate(panels, payload, now)
                if (next !== panels) {
                    panels = next
                    changed.add(Change.PANELS)
                }
            }
            "hud.panel.dismiss" -> {
                val id = payload.optString("id")
                if (id.isNotEmpty()) {
                    val next = HudPanels.applyDismiss(panels, id)
                    if (next !== panels) {
                        panels = next
                        changed.add(Change.PANELS)
                    }
                }
            }
            "hud.layout.highlight" -> {
                val panel = payload.optString("panel")
                if (panel.isNotEmpty()) {
                    highlight = Highlight(
                        panel = panel,
                        surface = payload.optString("surface"),
                        durationMs = payload.optInt("duration_ms", 0),
                        reason = payload.optString("reason"),
                        at = now,
                    )
                    changed.add(Change.HIGHLIGHT)
                }
            }
            "status.update" -> {
                val text = payload.optString("text")
                if (payload.optString("kind") == "goal") {
                    goal = GoalStatus.parse(text)
                    changed.add(Change.GOAL)
                } else {
                    status = text
                    changed.add(Change.STATUS)
                }
            }
            "error" -> {
                lastError = payload.optString("message").ifEmpty { payload.optString("error").ifEmpty { "error" } }
                turnPending = false
                changed.add(Change.ERROR)
            }
            ADAPTER_ERROR -> {
                lastError = payload.optString("error").ifEmpty { "backend unavailable" }
                offline = true
                turnPending = false
                changed.add(Change.OFFLINE)
                changed.add(Change.ERROR)
            }
        }
        return changed
    }

    companion object {
        const val ADAPTER_ERROR = "adapter.error"

        /** Hermes has no rounds; every call of a turn is round one. */
        const val ROUND = 1

        /** The set the protocol allows, in the order the buttons are offered. */
        val ALL_CHOICES: List<String> = listOf("once", "session", "always", "deny")

        /**
         * The choices a request offers, in [ALL_CHOICES] order, from `choices`
         * or the older `options`. A request that names none gets the full set
         * minus nothing — the server decides what it will accept, and `deny`
         * is always offered because refusing is always safe.
         */
        fun choicesOf(payload: JSONObject): List<String> {
            val named = strings(payload.optJSONArray("choices")).ifEmpty { strings(payload.optJSONArray("options")) }
                .map { it.lowercase() }
            val offered = ALL_CHOICES.filter { it in named }
            val base = offered.ifEmpty { ALL_CHOICES }
            return if ("deny" in base) base else base + "deny"
        }

        private fun strings(arr: org.json.JSONArray?): List<String> {
            if (arr == null) return emptyList()
            val out = ArrayList<String>(arr.length())
            for (i in 0 until arr.length()) {
                val v = arr.opt(i)
                if (v != null && v != JSONObject.NULL) out.add(v.toString())
            }
            return out
        }

        private fun flatten(args: JSONObject?): List<Pair<String, String>> {
            if (args == null) return emptyList()
            val out = ArrayList<Pair<String, String>>(args.length())
            val keys = args.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                val value = args.opt(key)
                if (value == null || value === JSONObject.NULL) continue
                out.add(key to value.toString())
            }
            return out
        }

        /** `duration` arrives in seconds (a double); the rows want milliseconds. */
        fun durationMs(value: Any?): Int = when (value) {
            is Number -> (value.toDouble() * 1000).toInt().coerceAtLeast(0)
            is String -> value.toDoubleOrNull()?.let { (it * 1000).toInt() } ?: 0
            else -> 0
        }
    }
}
