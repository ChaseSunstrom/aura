package ai.aura.app.messages

import ai.aura.app.automation.actions.ActionResult
import ai.aura.app.automation.actions.JarvisAction
import ai.aura.app.automation.actions.builtin.PhoneNumbers
import ai.aura.app.automation.actions.builtin.SendSms
import ai.aura.app.automation.actions.str
import ai.aura.app.automation.policy.ActionTier
import ai.aura.app.car.dash.tiles.CarMessage
import ai.aura.app.car.dash.tiles.HeadwayNotificationListener
import ai.aura.app.car.dash.tiles.send
import android.Manifest
import android.app.RemoteInput
import android.content.Context
import org.json.JSONObject

/**
 * Answers a mirrored conversation (T-0615): `sms:<number>` through SmsManager (by [SendSms], which
 * keeps its own checks: a plausible number, no emergency services, a length cap), `app:<package>:<sender>`
 * through the notification's own RemoteInput reply.
 *
 * Tier CONFIRM on the phone, so the owner sees the recipient and the exact words before anything
 * leaves, whatever the house's own level says. Does not cover: an app that withdrew its
 * notification (reported, not retried), or a short-code sender that cannot take a reply.
 */
object ReplyAction : JarvisAction {
    const val MAX_CHARS = 480

    override val id = "message_reply"
    override val tier = ActionTier.CONFIRM
    override val description = "Reply to a mirrored text or chat message, as the owner, from this phone."
    override val paramsSchema = mapOf(
        "conversation" to "string: a conversation id from the mirrored messages, sms:<number> or app:<package>:<sender>",
        "text" to "string: the reply, at most $MAX_CHARS characters",
    )
    override val capability = "sms"
    override val requiredPermissions = listOf(Manifest.permission.SEND_SMS)

    override fun permissionsFor(params: JSONObject): List<String> =
        if (params.str("conversation").orEmpty().startsWith("sms:")) requiredPermissions else emptyList()

    /**
     * The senders and the lookup. `DeviceChannelHost` fills them with the real ones below; unfilled
     * they send nothing, and tests replace them so nothing is ever sent.
     */
    @Volatile
    var smsSender: suspend (Context, String, String) -> ActionResult = { _, _, _ -> ActionResult.error("replies are not wired") }

    @Volatile
    var appFinder: (String, String) -> CarMessage? = { _, _ -> null }

    @Volatile
    var appSender: (Context, CarMessage, String) -> Boolean = { _, _, _ -> false }

    /** SmsManager through [SendSms], which keeps the SEND_SMS check, the number check and the length cap. */
    suspend fun sendSms(ctx: Context, number: String, body: String): ActionResult =
        SendSms.execute(ctx, JSONObject().put("number", number).put("body", body))

    fun findApp(pkg: String, sender: String): CarMessage? = HeadwayNotificationListener.latest.firstOrNull {
        it.packageName == pkg && it.sender.trim().lowercase() == sender
    }

    fun sendApp(ctx: Context, message: CarMessage, text: String): Boolean =
        message.reply?.send(ctx, message.packageName, text, RemoteInput.SOURCE_FREE_FORM_INPUT) ?: false

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val conversation = params.str("conversation") ?: return ActionResult.error("conversation is required")
        val text = params.str("text")?.takeIf { it.isNotBlank() } ?: return ActionResult.error("text is required")
        if (text.length > MAX_CHARS) return ActionResult.error("a reply is at most $MAX_CHARS characters")
        return when {
            conversation.startsWith("sms:") -> {
                val number = conversation.removePrefix("sms:")
                if (!PhoneNumbers.isPlausible(number)) {
                    ActionResult.error("that sender is a name or short code, not a number a text can go to")
                } else {
                    smsSender(ctx, number, text)
                }
            }
            conversation.startsWith("app:") -> {
                val rest = conversation.removePrefix("app:")
                val cut = rest.indexOf(':')
                if (cut <= 0) return ActionResult.error("not a conversation id")
                val message = appFinder(rest.substring(0, cut), rest.substring(cut + 1))
                    ?: return ActionResult.error("that conversation has no notification to reply to any more")
                if (message.reply == null) return ActionResult.unsupported("that app offers no reply from its notification")
                if (appSender(ctx, message, text)) ActionResult.ok("sent" to true)
                else ActionResult.error("the reply could not be sent: the app withdrew the notification")
            }
            else -> ActionResult.error("not a conversation id")
        }
    }
}
