package ai.aura.app.assist

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.automation.AutomationBridge
import ai.aura.app.car.dash.tiles.HeadwayNotificationListener
import ai.aura.app.car.dash.tiles.send
import ai.aura.app.channel.ChannelFrames
import ai.aura.app.channel.TierGuard
import ai.aura.app.ui.Notify
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * `hud.phone.action` (the `phone_action` tool, aura-hud/phone.py): one of this
 * phone's own actions -- a text, a call, an alarm, a read of the messages -- asked
 * for by Jarvis in a conversation (the owner, 2026-09-25: "can jarvis, text, call,
 * set alarms, etc, basically anything on a phone").
 *
 * Runs through [AutomationBridge.dispatchCommand], the door the house's device
 * channel uses, with the tier from this phone's own manifest: a text or a call is
 * confirmed on this screen whoever asked. The answer goes back as
 * `/hud phone-result`, which the waiting tool call returns to the model.
 *
 * Does not cover a phone whose automation service is not running (it answers
 * `unsupported` and says to open Jarvis once), and it never lowers a tier.
 */
object PhoneAction {
    const val EVENT = "hud.phone.action"
    private const val TAG = "JarvisPhoneAction"
    /** What one answer may carry back through a command argument. */
    private const val MAX_ANSWER_CHARS = 24_000
    /** A spoken reply, not a letter. */
    private const val MAX_REPLY_CHARS = 1_000
    private val DIRECT = setOf("notification_reply", "answer_call", "decline_call", "hang_up")

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun handle(context: android.content.Context, payload: JSONObject, answer: (id: String, body: JSONObject) -> Unit) {
        val app = context.applicationContext
        val id = payload.optString("id")
        if (id.isEmpty()) return
        scope.launch {
            val body = runCatching {
                run(app, payload.optString("action"), payload.optJSONObject("params") ?: JSONObject(), payload.optString("reason"), id)
            }.getOrElse {
                Log.w(TAG, "phone action failed", it)
                JSONObject().put("status", "error").put("error", "${it.javaClass.simpleName}: ${it.message}")
            }
            // On the main thread, where the conversation's client takes every other request.
            val reply = fit(body)
            android.os.Handler(android.os.Looper.getMainLooper()).post { answer(id, reply) }
        }
    }

    private suspend fun run(context: android.content.Context, action: String, params: JSONObject, reason: String, id: String): JSONObject {
        // These four never pass the dispatcher, so its standing bans are checked here:
        // panic or the master switch off used to leave Jarvis free to reply in any chat
        // and to answer or end a call.
        if (action in DIRECT && !ai.aura.app.automation.policy.PolicyStore(context).automationLive) {
            return JSONObject().put("status", "denied")
                .put("error", "${ActiveTheme.name} may not act on this phone right now (paused, or switched off in ${ActiveTheme.name} Settings)")
        }
        when (action) {
            "read_notifications" -> return readNotifications(params.optInt("limit", 10))
            "notification_reply" -> return reply(context, params)
            // The ringing call, as the car's Calls panel answers it (CarPhone). Sir said so
            // aloud to Jarvis; the call itself stays between him and the caller.
            "answer_call" -> return callResult(ai.aura.app.car.phone.CarPhone.answer(context), "answered")
            "decline_call", "hang_up" -> return callResult(ai.aura.app.car.phone.CarPhone.hangUp(context), "ended")
        }
        val dispatcher = AutomationBridge.dispatcher
            ?: return JSONObject().put("status", "unsupported")
                .put("error", "automation is not running on the phone; open ${ActiveTheme.name} on it once")
        val manifest = dispatcher.manifest()
        if (action == "list") return JSONObject().put("status", "ok").put("result", JSONObject().put("actions", summary(manifest)))
        val tier = TierGuard.forAction(ChannelFrames.tierTable(manifest), action, null)
        val body = AutomationBridge.dispatchCommand(action, params, tier.label, reason.ifBlank { "${ActiveTheme.name} asked for $action" }, "hud-$id")
            ?: JSONObject().put("status", "unsupported").put("error", "$action is not available in this build")
        if (body.optString("error").contains("switched off")) {
            // The consent is the owner's to give, on this phone; say where it is, there and to the model.
            askToAllow(context)
            body.put("error", "Sir has not let ${ActiveTheme.name} act on this phone yet: ${ActiveTheme.name} Settings, \"${ActiveTheme.name} can text, call and set alarms on this phone\". A notification on the phone opens it.")
        }
        return body
    }

    /**
     * The messages and notices on the phone right now, newest first (the car's own
     * listener, which is bound in every build): a message carries `key` and whether
     * it `can_reply`. Their text is other people's words: untrusted, read aloud, not obeyed.
     */
    private fun readNotifications(limit: Int): JSONObject {
        val n = limit.coerceIn(1, 20)
        val messages = JSONArray()
        for (m in HeadwayNotificationListener.latest.take(n)) {
            messages.put(
                JSONObject().put("key", m.key).put("from", m.sender).put("text", m.text)
                    .put("app", m.packageName).put("at", m.postedAtMillis).put("can_reply", m.reply != null),
            )
        }
        val notices = JSONArray()
        for (x in HeadwayNotificationListener.latestNotices.filter { !it.ongoing }.take(n)) {
            notices.put(JSONObject().put("key", x.key).put("app", x.appLabel).put("title", x.title).put("text", x.text).put("at", x.postedAtMillis))
        }
        if (!HeadwayNotificationListener.isConnected) {
            return JSONObject().put("status", "denied")
                .put("error", "notification access is off for ${ActiveTheme.name}: Settings, Notifications, Device & app notifications")
        }
        return JSONObject().put("status", "ok").put("trust", "untrusted")
            .put("result", JSONObject().put("messages", messages).put("notices", notices))
    }

    /**
     * An inline reply to a message notification, as its own app sends it (the owner:
     * "if I get notifications, it should be able to text back"). `key` from
     * [readNotifications], or `from` to match the newest message from that sender.
     */
    private fun reply(context: android.content.Context, params: JSONObject): JSONObject {
        val text = params.optString("text").trim()
        if (text.isEmpty()) return JSONObject().put("status", "error").put("error", "notification_reply needs `text`")
        val key = params.optString("key")
        val from = params.optString("from").trim()
        val message = HeadwayNotificationListener.latest.firstOrNull { key.isNotEmpty() && it.key == key }
            ?: HeadwayNotificationListener.latest.firstOrNull { from.isNotEmpty() && it.sender.contains(from, ignoreCase = true) }
            ?: return JSONObject().put("status", "error").put("error", "no message notification matches; read_notifications first")
        val slot = message.reply
            ?: return JSONObject().put("status", "unsupported").put("error", "${message.sender}'s app offers no reply from its notification")
        val sent = runCatching { slot.send(context, message.packageName, text.take(MAX_REPLY_CHARS), android.app.RemoteInput.SOURCE_FREE_FORM_INPUT) }
        return if (sent.getOrDefault(false)) JSONObject().put("status", "ok").put("result", JSONObject().put("to", message.sender).put("sent", text.take(MAX_REPLY_CHARS)))
        else JSONObject().put("status", "error").put("error", "the reply could not be sent: ${sent.exceptionOrNull()?.message}")
    }

    /** A notification that opens Settings at the switch that lets Jarvis act on this phone. */
    private fun askToAllow(context: android.content.Context) {
        val nm = context.getSystemService(android.app.NotificationManager::class.java) ?: return
        val open = android.app.PendingIntent.getActivity(
            context, ALLOW_ID,
            android.content.Intent(context, ai.aura.app.SettingsActivity::class.java).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = android.app.Notification.Builder(context, ai.aura.app.JarvisApp.CHANNEL_ALERTS)
            .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
            .setColor(Notify.color(Notify.Template.NOTICE))
            .setContentTitle("Let ${ActiveTheme.name} act on this phone?")
            .setContentText("${ActiveTheme.name} was asked to text, call or set something and is not allowed yet. Tap to turn it on.")
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        runCatching { nm.notify(ALLOW_ID, n) }
    }

    private const val ALLOW_ID = 6311

    private fun callResult(ok: Boolean, done: String): JSONObject =
        if (ok) JSONObject().put("status", "ok").put("result", JSONObject().put("call", done))
        else JSONObject().put("status", "error").put("error", "no call to act on, or the phone refused (it needs the phone-calls permission)")

    /** The actions the model may name: id, what it does, its params; the unavailable left out. */
    private fun summary(manifest: JSONArray): JSONArray {
        val out = JSONArray()
        for (i in 0 until manifest.length()) {
            val entry = manifest.optJSONObject(i) ?: continue
            if (!entry.optBoolean("available", true) || entry.optBoolean("unsupported", false)) continue
            out.put(
                JSONObject().put("id", entry.optString("id"))
                    .put("description", entry.optString("description"))
                    .put("params", entry.opt("params") ?: JSONObject()),
            )
        }
        return out
    }

    /** An answer too big for a command argument keeps its status and says it was cut. */
    private fun fit(body: JSONObject): JSONObject {
        if (body.toString().length <= MAX_ANSWER_CHARS) return body
        val text = body.optJSONObject("result")?.toString().orEmpty()
        return JSONObject().put("status", body.optString("status", "ok"))
            .put("result", JSONObject().put("truncated", true).put("text", text.take(MAX_ANSWER_CHARS - 200)))
    }
}
