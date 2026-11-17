package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.EmergencyCountdown
import android.Manifest
import android.content.Context
import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/**
 * `tests/contracts/emergency.json`, as constants: the phone cannot read the file at run time,
 * so EmergencyAlertTest reads it and fails when any of these drifts.
 */
internal object EmergencyContract {
    const val CONTACTS_MAX = 5
    const val NAME_MAX = 40
    const val WHAT_MAX = 200
    const val COUNTDOWN_S = 10
    const val LATCH_S = 600
    const val MESSAGE =
        "Emergency alert from {owner}'s assistant: {what} Location: {where}. Sent {time}. " +
            "This is an automated message; please call them."
    const val WHERE_UNKNOWN = "unknown"
    const val MAP_LINK = "https://maps.google.com/?q={lat},{lon}"

    /** Never shorter than the contract's countdown: a model asking for 1 s must not take the owner's cancel away. */
    const val COUNTDOWN_MAX_S = 30

    /** A number shorter than this is a service short code (a premium-rate text), not a person. */
    const val MIN_DIGITS = 7
}

/** A last known position and how old it is. */
internal class Fix(val lat: Double, val lon: Double, val ageMs: Long)

/**
 * What the alert does to the world, so the rules above it are testable without a SmsManager:
 * the countdown the owner can cancel, one text, one call, the last known fix and the clocks.
 */
internal interface EmergencyIo {
    /** Shows the countdown with the words that will be sent; how it ended. */
    suspend fun countdown(ctx: Context, seconds: Int, summary: String, what: String): EmergencyCountdown.End

    suspend fun text(ctx: Context, number: String, body: String): ActionResult

    suspend fun call(ctx: Context, number: String): ActionResult

    /** The last known fix, or null. */
    suspend fun lastFix(ctx: Context): Fix?

    /** Wall clock, for the time in the text. */
    fun nowMs(): Long

    /** Monotonic, for the repeat latch: setting the clock back must not undo it. */
    fun elapsedMs(): Long
}

/**
 * The phone's own pieces: [SendSms] and [PlaceCall] (multipart, the carrier's alphabet, the
 * permission checks and every refusal they already have, including the emergency numbers),
 * [GetLocation] for the fix and [EmergencyCountdown] for the screen.
 */
internal object DeviceEmergencyIo : EmergencyIo {
    override suspend fun countdown(ctx: Context, seconds: Int, summary: String, what: String) =
        EmergencyCountdown.run(ctx, seconds, summary, what)

    override suspend fun text(ctx: Context, number: String, body: String) =
        SendSms.execute(ctx, json("number" to number, "body" to body))

    override suspend fun call(ctx: Context, number: String) =
        PlaceCall.execute(ctx, json("number" to number))

    override suspend fun lastFix(ctx: Context): Fix? {
        // "Last known": a day-old cached fix is a better answer than none, and a short wait for a
        // fresh one when there is no cache must not hold the alert for long. The age travels
        // with it: the text must not present a day-old place as where the owner is now.
        val fix = GetLocation.execute(ctx, json("max_age_ms" to 86_400_000L, "timeout_ms" to 3_000L))
        val data = fix.data?.takeIf { fix.ok } ?: return null
        if (!data.has("latitude") || !data.has("longitude")) return null
        return Fix(data.getDouble("latitude"), data.getDouble("longitude"), data.optLong("age_ms", 0L).coerceAtLeast(0L))
    }

    override fun nowMs() = System.currentTimeMillis()

    override fun elapsedMs() = SystemClock.elapsedRealtime()
}

/**
 * The owner's emergency alert (T-0947): a countdown the owner can cancel with one tap, then a text
 * to each contact the server sent (what, where, when) and a call to the first.
 *
 * Tier 1 (no consent prompt), and that is safe only because of what surrounds it. A text to
 * another person is [ActionTier.CONFIRM] everywhere else; here the countdown is the consent: the
 * screen names every person who will be texted and called and shows the words that will be sent,
 * it takes the whole phone for the duration, and one tap on CANCEL sends nothing. If the
 * countdown could not be shown at all (notification and screen both refused) nothing is sent. A per-text prompt would defeat the feature,
 * since the owner may not be able to tap through five of them. What keeps AUTO from being a hole:
 * the numbers come from the owner's saved list on the server and the command is refused whole if
 * any is an emergency-services number or not a number; the panic switch, the master switch and a
 * user policy of "never" still deny it before this runs; and a second alert within `latch_s` of one that went out is refused
 * here (the server latches too). The audit log keeps the count of contacts and nothing else about them.
 *
 * Does not cover: that a text reaches anyone (no signal, no SIM), that the first contact picks up,
 * or the emergency number, which this refuses to contact.
 */
object EmergencyAlert : JarvisAction {
    override val id = "emergency_alert"
    override val tier = ActionTier.AUTO
    override val description = "Count down, then text the owner's emergency contacts and call the first."
    override val paramsSchema = mapOf(
        "contacts" to "array of {name, number}: at most ${EmergencyContract.CONTACTS_MAX}, never an emergency-services number",
        "what" to "string: what happened, one short sentence",
        "countdown_s" to "int: seconds the owner has to cancel (${EmergencyContract.COUNTDOWN_S}..${EmergencyContract.COUNTDOWN_MAX_S})"
    )
    override val capability = "sms"

    // Both asked for before the countdown starts, so the owner is not met by a system dialog at zero.
    // Location is not: a missing fix only turns "located" false, and a third dialog is a delay.
    override val requiredPermissions = listOf(Manifest.permission.SEND_SMS, Manifest.permission.CALL_PHONE)

    /** The countdown (at most 30 s) plus a text each for five people, the fix and the call. */
    override val timeoutMs = 120_000L

    /** The seam; only tests swap it (internal, like the other test seams: the phone always uses its own). */
    @Volatile
    internal var io: EmergencyIo = DeviceEmergencyIo

    private val busy = AtomicBoolean(false)

    /**
     * When the last alert that actually went out was sent, on the monotonic clock; null before one.
     * The phone's own floor under the server's latch: a model that loops, or a server that forgot,
     * must not text the owner's family every ten seconds. Held in memory: a killed process forgets it.
     */
    @Volatile
    private var lastSentAt: Long? = null

    /** Tests only: the latch is process-wide state. */
    internal fun forgetLastAlert() {
        lastSentAt = null
    }

    internal class Contact(val name: String, val number: String)
    private class Order(val contacts: List<Contact>, val what: String, val seconds: Int)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val order = try {
            parse(params)
        } catch (e: IllegalArgumentException) {
            return ActionResult.error(e.message ?: "bad emergency_alert command")
        }
        if (!ctx.granted(Manifest.permission.SEND_SMS)) {
            return ActionResult.missingPermission(Manifest.permission.SEND_SMS)
        }
        // One countdown at a time: a second command (a model in a loop) must not stack a second
        // screen or text the list twice. Released in the finally, whatever happens.
        if (!busy.compareAndSet(false, true)) return ActionResult.error("an emergency alert is already counting down")
        try {
            val last = lastSentAt
            if (last != null && io.elapsedMs() - last < EmergencyContract.LATCH_S * 1_000L) {
                return ActionResult.error("an emergency alert was already sent in the last ${EmergencyContract.LATCH_S / 60} minutes; nothing was sent")
            }
            return when (io.countdown(ctx, order.seconds, summary(order.contacts), order.what)) {
                EmergencyCountdown.End.CANCELLED -> ActionResult.ok("status" to "cancelled")
                // Nobody could have cancelled it, so nobody consented: send nothing.
                EmergencyCountdown.End.NOT_SHOWN -> ActionResult.error("the countdown could not be shown")
                EmergencyCountdown.End.ELAPSED -> {
                    // Kept for the phone's own fall and crash watch (T-1209), and only here: the
                    // countdown ran out uncancelled, so the owner let this list be texted. A
                    // cancelled, unshown, busy or latched command must not replace who a later
                    // fall reaches.
                    runCatching {
                        JarvisConfig(ctx).emergencyContacts = JSONArray().also { a ->
                            order.contacts.forEach { a.put(JSONObject().put("name", it.name).put("number", it.number)) }
                        }.toString()
                    }
                    send(ctx, order)
                }
            }
        } finally {
            busy.set(false)
        }
    }

    /** Everything is checked before anything is shown or sent: a bad command is refused whole. */
    private fun parse(params: JSONObject): Order {
        val array = params.optJSONArray("contacts")
        require(array != null && array.length() > 0) { "contacts is required: the people to reach" }
        val contacts = contactsFrom(array)
        val what = clean(params.str("what"))
        require(what != null && what.length <= EmergencyContract.WHAT_MAX) {
            "what (at most ${EmergencyContract.WHAT_MAX} characters) is required"
        }
        // What goes to the owner's family in the owner's name is words, not a link to click or a
        // number to dial. The server filters too; the phone does not rely on it.
        require(!LINK.containsMatchIn(what) && !DIGIT_RUN.containsMatchIn(what)) {
            "what must be plain words, with no link and no phone number; nothing was sent"
        }
        // The floor is the contract's countdown: the owner always has time to cancel.
        val seconds = params.intOr("countdown_s", EmergencyContract.COUNTDOWN_S)
            .coerceIn(EmergencyContract.COUNTDOWN_S, EmergencyContract.COUNTDOWN_MAX_S)
        return Order(contacts, what, seconds)
    }

    /**
     * The people, every one checked: shared with [EmergencyContactsSync] so the list the house
     * hands the phone to keep and the list an alert texts are held to one rule. Throws
     * [IllegalArgumentException] on the first bad entry; nothing is partial.
     */
    internal fun contactsFrom(array: JSONArray): List<Contact> {
        require(array.length() <= EmergencyContract.CONTACTS_MAX) {
            "at most ${EmergencyContract.CONTACTS_MAX} contacts"
        }
        return (0 until array.length()).map { i ->
            val c = array.optJSONObject(i)
            // The name goes on the owner's screen: a newline or a control character in it could
            // lay out a second, forged line there.
            val name = clean(c?.opt("name") as? String)
            // Text only: org.json would turn the number 15550100 into a string; the house refuses it.
            val number = c?.opt("number") as? String
            require(name != null && name.length <= EmergencyContract.NAME_MAX) { "each contact needs a name of at most ${EmergencyContract.NAME_MAX} characters" }
            require(number != null && PhoneNumbers.isPlausible(number) &&
                number.count { it.isDigit() } >= EmergencyContract.MIN_DIGITS) { "a contact's number is not a full phone number; nothing was sent" }
            // The one rule this action exists to keep: it is not a way to reach emergency services.
            require(!PhoneNumbers.isEmergency(number)) { "an emergency-services number is not an emergency contact; nothing was sent" }
            Contact(name, number)
        }
    }

    private const val STALE_FIX_MS = 10 * 60_000L

    // Control, format (bidi overrides, zero-width) and line/paragraph separators: none is a word.
    private val CONTROL = Regex("[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]")
    private val WHITESPACE = Regex("\\s+")

    /** Text that will be shown to the owner or sent: no control characters, one space between words. */
    private fun clean(text: String?): String? =
        text?.replace(CONTROL, " ")?.replace(WHITESPACE, " ")?.trim()?.takeIf { it.isNotEmpty() }

    /**
     * Anything a messaging app would turn into something to tap: a scheme (`tel:`, `https://`),
     * a "www." host, a mail address, a bare domain. The contract's `what_refused` are refused
     * here and by the plugin, its `what_allowed` by neither.
     */
    private val LINK = Regex(
        "(?i)(://|\\bwww\\.|@|\\b\\p{L}[\\p{L}\\p{N}+.-]{1,15}:\\S|\\b[\\p{L}\\p{N}_-]+\\.\\p{L}{2,}\\b)"
    )

    /**
     * Seven digits, of any script, with no letter between them: a phone number however it is
     * spaced or punctuated. A date and a time together trip it too; the phone adds the time itself.
     */
    private val DIGIT_RUN = Regex("\\p{Nd}(?:[^\\p{L}\\p{Nd}]*\\p{Nd}){${EmergencyContract.MIN_DIGITS - 1},}")

    /** "Texting Sam and Ada, then calling Sam": what the countdown screen and the notification say. */
    private fun summary(contacts: List<Contact>): String {
        val names = contacts.map { it.name }
        val list = if (names.size == 1) names[0] else names.dropLast(1).joinToString(", ") + " and " + names.last()
        return "Texting $list, then calling ${names.first()}"
    }

    private suspend fun send(ctx: Context, order: Order): ActionResult {
        val fix = attempt { io.lastFix(ctx) }
        val now = io.nowMs()
        val clock = SimpleDateFormat("HH:mm", Locale.US)
        val where = fix?.let {
            val link = EmergencyContract.MAP_LINK.replace("{lat}", "%.5f".format(Locale.US, it.lat))
                .replace("{lon}", "%.5f".format(Locale.US, it.lon))
            // An old fix is where the owner was, not where they are: say so.
            if (it.ageMs > STALE_FIX_MS) "$link (as of ${clock.format(Date(now - it.ageMs))})" else link
        } ?: EmergencyContract.WHERE_UNKNOWN
        val values = mapOf(
            "owner" to "the owner",
            "what" to order.what,
            "where" to where,
            "time" to clock.format(Date(now)),
        )
        // One pass over the template: a "{where}" the model put inside `what` stays text.
        val body = Regex("\\{(owner|what|where|time)}").replace(EmergencyContract.MESSAGE) { values.getValue(it.groupValues[1]) }

        var texted = 0
        var firstFailure: String? = null
        for (c in order.contacts) {
            val sent = attempt { io.text(ctx, c.number, body) } ?: ActionResult.error("the text could not be sent")
            if (sent.ok) texted++ else if (firstFailure == null) firstFailure = sent.error
        }
        // The call goes out even when no text did: it is the other way to reach someone.
        val first = order.contacts.first()
        val called = attempt { io.call(ctx, first.number) }?.takeIf { it.ok }?.let { first.name }

        // Something left the phone (a text or the call): that is an alert sent, and the latch starts.
        if (texted > 0 || called != null) lastSentAt = io.elapsedMs()
        if (texted == 0) {
            return ActionResult.error(
                "no text was sent (${firstFailure ?: "unknown reason"})" + (called?.let { "; $it was called" } ?: "")
            )
        }
        return ActionResult.ok(
            "status" to "ok",
            "texted" to texted,
            "called" to (called ?: JSONObject.NULL),
            "located" to (fix != null),
        )
    }

    /** One failed step must not stop the rest of an alert; cancellation still does. */
    private inline fun <T> attempt(block: () -> T): T? = try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        null
    }
}

/**
 * The house hands the phone the owner's emergency contacts (T-1262), sent when they are saved and
 * when a phone connects, so the fall and crash watch (T-1209) has them from day one and after a
 * reinstall instead of only after a first alert. This STORES the list and does nothing else: it
 * places no call, sends no text and shows nothing.
 *
 * Tier 1 because there is nothing to consent to: the list is only kept, and the watch still counts
 * down before it reaches anyone. What keeps that safe is that the house alone sends it (the model
 * is refused it on every general route) and that the list is held to the alert's own rule: a list
 * the phone cannot take whole (too many, a bad name or number, an emergency-services number) is
 * refused and the stored one is left alone. An empty list clears, which is how a person the owner
 * removed is forgotten here. The audit log keeps only the count.
 *
 * Does not cover who is allowed to speak for the owner: the device channel is the house's, a
 * paired and authenticated link, and the phone trusts what it carries as it does every command.
 */
object EmergencyContactsSync : JarvisAction {
    override val id = "emergency_contacts_sync"
    override val tier = ActionTier.AUTO
    override val description = "Keep the owner's emergency contacts for the fall and crash watch; calls and texts no one."
    override val paramsSchema = mapOf(
        "contacts" to "array of {name, number}: at most ${EmergencyContract.CONTACTS_MAX}; [] clears the stored list"
    )
    override val capability = "device"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        // A missing key is not an empty list: only an explicit [] clears what the owner saved.
        val array = params.optJSONArray("contacts")
            ?: return ActionResult.error("contacts is required: an array, [] to clear")
        val contacts = try {
            EmergencyAlert.contactsFrom(array)
        } catch (e: IllegalArgumentException) {
            return ActionResult.error(e.message ?: "bad contacts list")
        }
        // Stored as the alert stores it, so the watch reads one shape.
        val kept = JSONArray().also { a ->
            contacts.forEach { a.put(JSONObject().put("name", it.name).put("number", it.number)) }
        }
        JarvisConfig(ctx).emergencyContacts = if (contacts.isEmpty()) "" else kept.toString()
        return ActionResult.ok("status" to "ok", "stored" to contacts.size)
    }
}
