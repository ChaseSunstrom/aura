package ai.jarvis.app.calls

import ai.jarvis.app.car.phone.LiveCall
import android.content.Context
import org.json.JSONObject

/**
 * The owner's phone calls, told to the house so their other screens can show them (T-0616).
 *
 * A ringing call, its answer and its end become `call_event` device events. The shape, the
 * relationships, the reply lines and the screening table are `tests/contracts/call-event.json`,
 * which `CallEventsTest`, `CallActionsTest` and `android-app/tools/call_event_test.py` all read.
 *
 * What it does not cover: the caller is the label the dialer put in its notification, which is a
 * stranger's text, so the frame is sent untrusted and nothing here logs it. The number never
 * leaves the phone: a call back is `call_back {call_id}`, resolved here. Call audio is never
 * recorded: the platform gives a non-default dialer none. The source is the dialer's notification
 * ([ai.jarvis.app.car.phone.CarPhone]), so a dialer that posts none is a call this never sees.
 */
object CallEvents {
    const val EVENT = "call_event"
    const val FROM_MAX = 64
    const val ID_MAX = 64
    const val UNKNOWN = "Unknown caller"

    /** The device commands the house sends (the contract's `actions`). */
    const val ACTION_ANSWER = "call_answer"
    const val ACTION_DECLINE = "call_decline"
    const val ACTION_BACK = "call_back"
    const val ACTION_POLICY = "call_policy"

    val STATES = listOf("ringing", "active", "ended", "missed")
    val RELATIONSHIPS = listOf("starred", "contact", "unknown")
    val SCREEN_RULES = listOf("ring", "silence", "reject")

    /** The only texts a call can be answered with: fixed lines, never the model's words. */
    val REPLIES = mapOf(
        "driving" to "I'm driving. I'll call you back soon.",
        "busy" to "I can't talk right now. I'll call you back.",
    )

    // --- pure rules (the contract's) ----------------------------------------------------

    fun relationshipOf(contact: Boolean, starred: Boolean): String =
        if (starred) "starred" else if (contact) "contact" else "unknown"

    /** `allow`, `silence` or `reject`. Only a stranger is ever screened, and a rule not in the list allows. */
    fun screenResponse(rule: String, relationship: String): String =
        if (relationship != "unknown") "allow" else when (rule) {
            "silence" -> "silence"
            "reject" -> "reject"
            else -> "allow"
        }

    /** The frame's `from`: one line, bounded, never empty. */
    fun label(who: String?): String =
        who.orEmpty().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(FROM_MAX)
            .ifEmpty { UNKNOWN }

    /**
     * A saved person's name is what the dialer shows; an unsaved caller shows as digits or as no
     * name at all. This is the whole "is this a stranger" test the notification allows.
     */
    fun looksUnsaved(label: String): Boolean =
        label == UNKNOWN || label.isEmpty() || label.all { it.isDigit() || it in "+ ()-.#*" }

    // --- the live call ------------------------------------------------------------------

    private class Seen(val id: String, val from: String, val relationship: String, var answered: Boolean)

    @Volatile
    private var seen: Seen? = null
    private var counter = 0L

    /** Set by `DeviceChannelHost` to send the event only over a live socket; null: nothing leaves. */
    @Volatile
    var sink: ((JSONObject) -> Unit)? = null

    /** Whether the name is a saved contact, and a starred one. Filled by the host; unfilled, everyone is a stranger. */
    @Volatile
    var lookup: (Context, String) -> Pair<Boolean, Boolean> = { _, _ -> false to false }

    /** What the last frame said, for the car to read without a socket. */
    @Volatile
    private var last: JSONObject? = null

    fun snapshot(): JSONObject? = last?.let { JSONObject(it.toString()) }

    @Synchronized
    fun reset() {
        seen = null
        last = null
        labels.clear()
    }

    /** The dialer's call changed (null: no call). Turns the change into at most one event. */
    @Synchronized
    fun onCall(ctx: Context, call: LiveCall?, now: Long = System.currentTimeMillis()) {
        val prev = seen
        if (call == null) {
            if (prev != null) emit(prev, if (prev.answered) "ended" else "missed", now)
            seen = null
            return
        }
        if (prev == null) {
            val from = label(call.who)
            val (contact, starred) = if (looksUnsaved(from)) false to false else lookup(ctx, from)
            val next = Seen("call-${++counter}", from, relationshipOf(contact, starred), !call.ringing)
            seen = next
            emit(next, if (call.ringing) "ringing" else "active", now)
        } else if (!call.ringing && !prev.answered) {
            prev.answered = true
            emit(prev, "active", now)
        }
    }

    private fun emit(c: Seen, state: String, now: Long) {
        val data = JSONObject()
            .put("id", c.id.take(ID_MAX)).put("state", state).put("from", c.from)
            .put("relationship", c.relationship).put("urgent", c.relationship == "starred").put("at", now)
        last = data
        labels[c.id] = c.from
        sink?.invoke(data)
    }

    /** The last few calls' labels by id, so a call back works after the call ended. Memory only. */
    private val labels = object : LinkedHashMap<String, String>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>?) = size > 10
    }

    /** The label of a call this phone saw, if it is one of the last few. */
    @Synchronized
    fun fromOf(callId: String): String? = labels[callId]
}
