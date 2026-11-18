package ai.aura.app.calls

import ai.aura.app.automation.actions.ActionResult
import ai.aura.app.automation.actions.JarvisAction
import ai.aura.app.automation.actions.str
import ai.aura.app.automation.policy.ActionTier
import ai.aura.app.car.phone.CarPhone
import android.content.Context
import android.net.Uri
import android.provider.ContactsContract
import org.json.JSONObject

/**
 * The house's call commands (T-0616): answer, decline, call back, and the screening policy.
 *
 * The names and parameters are `tests/contracts/call-event.json`'s `actions`. Each is the owner's
 * own words relayed by the house (the Hermes tool asks first), so none of them is CONFIRM here.
 * The seams are empty until `DeviceChannelHost` fills them, and tests replace them, so nothing in
 * a test touches the telecom stack or dials.
 *
 * Does not cover: a decline's `reply` (a fixed line, `driving` or `busy`) is texted only when the
 * number is known from the contact book or the call log, and never to a stranger; the notification
 * carries no number. Answering a call on a dialer that publishes no action needs `ANSWER_PHONE_CALLS`.
 */
private fun callParam(params: JSONObject) = params.str("call_id")

object CallAnswer : JarvisAction {
    override val id = CallEvents.ACTION_ANSWER
    override val tier = ActionTier.NOTIFY
    override val description = "Answer the ringing phone call."
    override val paramsSchema = emptyMap<String, String>()

    @Volatile
    var answerer: (Context) -> Boolean = { false }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        if (answerer(ctx)) ActionResult.ok("answered" to true)
        else ActionResult.error("the call could not be answered: no ringing call, or the answer permission is missing")
}

object CallDecline : JarvisAction {
    override val id = CallEvents.ACTION_DECLINE
    override val tier = ActionTier.NOTIFY
    override val description = "Decline the ringing phone call, optionally with one of two fixed text replies."
    override val paramsSchema = mapOf("reply" to "string, optional: driving or busy")

    @Volatile
    var hanger: (Context) -> Boolean = { false }

    /** Texts a fixed line to a saved number; null number: nothing is sent. Filled by the host. */
    @Volatile
    var texter: suspend (Context, String, String) -> Boolean = { _, _, _ -> false }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val reply = params.str("reply")
        if (reply != null && reply !in CallEvents.REPLIES) return ActionResult.error("reply is driving or busy")
        val from = CallEvents.snapshot()?.optString("from").orEmpty()
        val number = reply?.let { CallActions.numberFor(ctx, from) }
        if (!hanger(ctx)) return ActionResult.error("the call could not be declined: no ringing call")
        val texted = reply != null && number != null && texter(ctx, number, CallEvents.REPLIES.getValue(reply))
        return ActionResult.ok("declined" to true, "replied" to texted)
    }
}

object CallBack : JarvisAction {
    override val id = CallEvents.ACTION_BACK
    override val tier = ActionTier.NOTIFY
    override val description = "Call back the person from an earlier call."
    override val paramsSchema = mapOf("call_id" to "string: the id from the call event")

    @Volatile
    var dialer: (Context, String) -> Boolean = { _, _ -> false }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val callId = callParam(params) ?: return ActionResult.error("call_id is required")
        val from = CallEvents.fromOf(callId) ?: return ActionResult.error("this phone has no such call any more")
        val number = CallActions.numberFor(ctx, from)
            ?: return ActionResult.error("the call has no number this phone can dial: the dialer showed only a name")
        return if (dialer(ctx, number)) ActionResult.ok("calling" to true) else ActionResult.error("the call could not be placed")
    }
}

object CallPolicy : JarvisAction {
    override val id = CallEvents.ACTION_POLICY
    override val tier = ActionTier.AUTO
    override val description = "Set what the phone does with a call from a number that is not a contact."
    override val paramsSchema = mapOf("rule" to "string: ring, silence or reject")

    @Volatile
    var rule: String = "ring"

    @Volatile
    var store: (Context, String) -> Unit = { _, _ -> }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val next = params.str("rule") ?: return ActionResult.error("rule is required")
        if (next !in CallEvents.SCREEN_RULES) return ActionResult.error("rule is ring, silence or reject")
        rule = next
        store(ctx, next)
        return ActionResult.ok("rule" to next)
    }
}

object CallActions {
    val all: List<JarvisAction> = listOf(CallAnswer, CallDecline, CallBack, CallPolicy)

    private const val PREFS = "jarvis_calls"

    /** The rule survives a restart: the screening service can run before the app does anything else. */
    fun savedRule(ctx: Context): String =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("rule", "ring")
            ?.takeIf { it in CallEvents.SCREEN_RULES } ?: "ring"

    fun saveRule(ctx: Context, rule: String) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("rule", rule).apply()
    }

    /** A name a number can be found for: a plain number is itself, a saved name comes from the book. */
    @Volatile
    var numberFor: (Context, String) -> String? = { _, from -> from.takeIf { it.isNotEmpty() && it.all { c -> c.isDigit() || c in "+ ()-." } } }

    /** The real lookups, filled by the host: the contact book first, then the call log. */
    fun realNumberFor(ctx: Context, from: String): String? {
        if (from.isEmpty() || from == CallEvents.UNKNOWN) return null
        if (from.all { it.isDigit() || it in "+ ()-." }) return from
        return CarPhone.recentCalls(ctx).firstOrNull { it.name == from }?.number
    }

    /** Whether a name is a saved contact, and a starred one. No permission: everyone is a stranger. */
    fun realLookup(ctx: Context, name: String): Pair<Boolean, Boolean> = try {
        val uri = Uri.withAppendedPath(ContactsContract.CommonDataKinds.Phone.CONTENT_FILTER_URI, Uri.encode(name))
        val cols = arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.STARRED)
        var saved = false
        var starred = false
        ctx.contentResolver.query(uri, cols, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                if (c.getString(0).orEmpty().equals(name, ignoreCase = true)) {
                    saved = true
                    if (c.getInt(1) == 1) starred = true
                }
            }
        }
        saved to starred
    } catch (_: Exception) {
        false to false
    }
}
