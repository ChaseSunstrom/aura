package ai.aura.app.messages

import ai.aura.app.car.dash.tiles.CarMessage
import ai.aura.app.car.voice.CarMessageAnnouncer
import ai.aura.app.ui.ApprovalBridge
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import android.provider.Telephony
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The owner's texts and chat notifications, mirrored to their desktops through the house (T-0615).
 *
 * A new SMS, or a new MessagingStyle notification, becomes one `message_mirror` device event. The
 * shape, the conversation ids and the emergency rule are `tests/contracts/message-mirror.json`,
 * which `MessageMirrorTest` and `android-app/tools/message_mirror_test.py` both read.
 *
 * What it does not cover: the sender is whatever the other app or contact card says, so every frame
 * is sent untrusted. Nothing here logs a message body, a sender or a number: the text is the
 * owner's and other people's, and logcat is readable by anyone with adb. The frame leaves only over
 * a live socket (the [sink] is null otherwise), after the owner said yes once.
 */
object MessageMirror {
    const val EVENT = "message_mirror"
    const val TEXT_MAX = 2000
    const val FROM_MAX = 64
    const val CONVERSATION_MAX = 160

    /** With a starred sender: the words that make a text an emergency. */
    val EMERGENCY_WORDS = listOf("emergency", "urgent", "911", "hospital", "accident", "call me now")

    private const val PREFS = "message_mirror"
    private const val KEY_CONSENT = "mirror"
    private const val SEEN_MAX = 200

    /** Set by `DeviceChannelHost` to send the event only over a live socket; null: nothing leaves. */
    @Volatile
    var sink: ((JSONObject) -> Unit)? = null

    /** How the owner is asked once; `DeviceChannelHost` fills it with [askOwner]. Unfilled: no mirror. */
    @Volatile
    var ask: suspend (Context, JSONObject) -> ApprovalBridge.Outcome = { _, _ -> ApprovalBridge.Outcome.UNDELIVERABLE }

    suspend fun askOwner(ctx: Context, params: JSONObject): ApprovalBridge.Outcome =
        ApprovalBridge.requestOutcome(
            ctx,
            actionId = "messages.mirror",
            params = params.toString(),
            reason = "Show your texts and message notifications on your computers?",
            description = "Texts on your desktops",
        )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val asking = AtomicBoolean(false)
    private val seen = LinkedHashSet<String>()
    private var primed = false

    // --- pure rules (the contract's) ----------------------------------------------------

    /** `sms:<digits and plus>`, `sms:<lower-cased name>` for a short code, or `app:<package>:<sender>`. */
    fun conversationId(source: String, address: String?, pkg: String?, from: String?): String {
        val id = if (source == "app") {
            "app:${pkg.orEmpty().trim().lowercase()}:${from.orEmpty().trim().lowercase()}"
        } else {
            val a = address.orEmpty().trim()
            val numberish = a.isNotEmpty() && a.all { it.isDigit() || it in "+ ()-." }
            "sms:" + if (numberish) a.filter { it.isDigit() || it == '+' } else a.lowercase()
        }
        return id.take(CONVERSATION_MAX)
    }

    /** An emergency needs a starred sender AND a word: a stranger writing URGENT is not one. */
    fun isEmergency(urgent: Boolean, text: String): Boolean {
        if (!urgent) return false
        val t = text.lowercase()
        return EMERGENCY_WORDS.any { t.contains(it) }
    }

    /** The event, or null for an empty text or a missing id or conversation. */
    fun event(id: String, source: String, conversation: String, from: String, text: String, at: Long, urgent: Boolean): JSONObject? {
        if (id.isBlank() || conversation.isBlank() || text.isBlank()) return null
        val body = text.take(TEXT_MAX)
        return JSONObject().put("id", id).put("conversation", conversation).put("source", source)
            .put("from", CarMessageAnnouncer.inert(from).take(FROM_MAX)).put("text", body).put("at", at)
            .put("urgent", urgent).put("emergency", isEmergency(urgent, body))
    }

    /** What to do with an event given the owner's one-time answer: send, drop, or ask first. */
    enum class Route { SEND, DROP, ASK }

    fun route(consent: String?): Route = when (consent) {
        "yes" -> Route.SEND
        "no" -> Route.DROP
        else -> Route.ASK
    }

    /**
     * The notification messages not offered before. The first call only learns what is already
     * there (a backlog is not news). A message the SMS inbox path mirrors is left out here:
     * [defaultSms] is the default messaging app, whose notifications are those same texts.
     */
    @Synchronized
    fun fresh(messages: List<CarMessage>, defaultSms: String?): List<CarMessage> {
        val out = ArrayList<CarMessage>()
        for (m in messages) {
            if (!seen.add("${m.key}|${m.postedAtMillis}")) continue
            if (primed && m.packageName != defaultSms && m.text.isNotBlank()) out.add(m)
        }
        primed = true
        while (seen.size > SEEN_MAX) seen.remove(seen.first())
        return out
    }

    /** A fresh channel learns the feed again as its baseline: what arrived while it was down is not replayed. */
    @Synchronized
    fun reset() {
        seen.clear()
        primed = false
        sink = null
    }

    // --- the two sources ----------------------------------------------------------------

    /** Wired by `DeviceChannelHost` for the length of a channel; the listener replays its feed on observe. */
    fun onMessages(ctx: Context, messages: List<CarMessage>) {
        if (sink == null) return
        val defaultSms = runCatching { Telephony.Sms.getDefaultSmsPackage(ctx) }.getOrNull()
        for (m in fresh(messages, defaultSms)) {
            val conversation = conversationId("app", null, m.packageName, m.sender)
            emit(ctx, event("n-${m.key}-${m.postedAtMillis}", "app", conversation, m.sender, m.text, m.postedAtMillis, false))
        }
    }

    /** A new row in the SMS inbox (`RoutineWatcher.onSms`). */
    fun onSms(ctx: Context, rowId: Long, address: String, body: String, at: Long) {
        if (sink == null) return
        val name = contactName(ctx, address)
        val starred = isStarred(ctx, address)
        emit(ctx, event("sms-$rowId", "sms", conversationId("sms", address, null, null), name ?: address, body, at, starred))
    }

    private fun emit(ctx: Context, frame: JSONObject?) {
        val out = sink ?: return
        frame ?: return
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        when (route(prefs.getString(KEY_CONSENT, null))) {
            Route.SEND -> runCatching { out(frame) }
            Route.DROP -> Unit
            // The first message ever: ask once. One that arrives while the question is on screen is
            // dropped, because holding it would keep the text at rest; a lapse asks again next time.
            Route.ASK -> if (asking.compareAndSet(false, true)) scope.launch {
                try {
                    val outcome = ask(ctx, JSONObject().put("from", frame.optString("from")))
                    when (outcome) {
                        ApprovalBridge.Outcome.APPROVED -> prefs.edit().putString(KEY_CONSENT, "yes").apply()
                        ApprovalBridge.Outcome.DENIED -> prefs.edit().putString(KEY_CONSENT, "no").apply()
                        else -> Unit
                    }
                } finally {
                    asking.set(false)
                }
            }
        }
    }

    private fun contactName(ctx: Context, phone: String): String? = lookup(ctx, phone, ContactsContract.PhoneLookup.DISPLAY_NAME)

    private fun isStarred(ctx: Context, phone: String): Boolean = lookup(ctx, phone, "starred") == "1"

    private fun lookup(ctx: Context, phone: String, column: String): String? {
        if (phone.isBlank()) return null
        if (ctx.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return null
        val uri = android.net.Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, android.net.Uri.encode(phone))
        return runCatching {
            ctx.contentResolver.query(uri, arrayOf(column), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) else null
            }
        }.getOrNull()
    }
}
