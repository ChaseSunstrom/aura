package ai.aura.app.assist

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.config.JarvisConfig
import ai.aura.app.config.ServerUrl
import ai.aura.app.tailnet.JarvisHttp
import ai.aura.app.ui.Notify
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * "That was me", from the phone's tray. The owner, 2026-10-07: enrolled on the console,
 * then refused every time in the car -- six of six that morning. A microphone the voice
 * was never enrolled on sounds different enough to fall under the line, and the only
 * place to claim a refused voice was the console, fifteen minutes at most.
 *
 * A refusal close to someone comes back with the adapter's claim id; the phone offers
 * it here, and one tap files that utterance as a print for the microphone it came from
 * (the car's, the phone's). Three and that microphone is judged by its own prints.
 *
 * Not on the car screen: a control there appears under a driver's finger unasked
 * (`CarShell.showNoticePopup`). The tray waits for a stop. Only an unlocked phone claims:
 * the action asks for the screen lock first, the notification's body claims nothing, and
 * the lock screen shows no text. A passenger holding the locked phone could otherwise
 * file their own refused voice as the owner's car prints, three taps and they were him.
 * Does not prove who unlocked it: that is the trust the console's own button has.
 */
object VoiceClaim {
    private const val TAG = "JarvisVoiceClaim"
    private const val CHANNEL_ID = "jarvis_voice_claim"
    private const val NOTIFICATION_ID = 7341
    internal const val ACTION_CLAIM = "ai.aura.app.VOICE_CLAIM"
    internal const val EXTRA_CLAIM = "claim"

    /** The adapter's claim ids (`secrets.token_urlsafe`): spliced into a path, so nothing else. */
    private val CLAIM_RE = Regex("^[A-Za-z0-9_-]{6,64}$")

    internal fun valid(claim: String?): Boolean = claim != null && CLAIM_RE.matches(claim)

    /** Offers [claim] in the tray, replacing the last offer: the newest refusal is the one to claim. */
    fun offer(context: Context, claim: String?, where: String) {
        if (!valid(claim)) return
        val app = context.applicationContext
        val manager = app.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Voice not recognised", NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = "When ${ActiveTheme.name} did not know your voice, so you can say it was you" },
        )
        val tap = PendingIntent.getBroadcast(
            app,
            claim.hashCode(),
            Intent(app, VoiceClaimReceiver::class.java).setAction(ACTION_CLAIM).putExtra(EXTRA_CLAIM, claim),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val text = "${ActiveTheme.name} did not recognise your voice $where. If it was you, tap That was me: " +
            "it learns your voice on this microphone (three times, and it knows you)."
        post(
            app,
            Notification.Builder(app, CHANNEL_ID)
                .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
                .setColor(Notify.color(Notify.Template.REMINDER))
                .setContentTitle("Was that you?")
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .addAction(
                    Notification.Action.Builder(null, "That was me", tap).setAuthenticationRequired(true).build(),
                )
                .setAutoCancel(true)
                // The adapter keeps a refused voice two hours (`Unrecognised.TTL_S`).
                .setTimeoutAfter(2 * 60 * 60_000L)
                .build(),
        )
    }

    internal fun settle(context: Context, text: String) {
        val app = context.applicationContext
        post(
            app,
            Notification.Builder(app, CHANNEL_ID)
                .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
                .setColor(Notify.color(Notify.Template.REMINDER))
                .setContentTitle("Voice")
                .setContentText(text)
                .setAutoCancel(true)
                .setTimeoutAfter(60_000L)
                .build(),
        )
    }

    private fun post(app: Context, notification: Notification) {
        runCatching { app.getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, notification) }
            .onFailure { Log.w(TAG, "could not post: ${it.javaClass.simpleName}") }
    }

    /** `POST /api/speaker/unrecognised/<claim>/mine`; blocking. The adapter's answer, in a sentence. */
    internal fun claim(context: Context, claim: String): String {
        val config = JarvisConfig(context)
        if (config.hermesUrl.isEmpty()) return "${ActiveTheme.name} is not paired"
        val request = Request.Builder()
            .url(ServerUrl.normalize(config.hermesUrl) + "/api/speaker/unrecognised/$claim/mine")
            .header("Authorization", "Bearer ${config.hermesKey}")
            .post("{}".toRequestBody("application/json".toMediaType()))
            .build()
        return runCatching {
            JarvisHttp.builder().readTimeout(30, TimeUnit.SECONDS).build().newCall(request).execute().use { r ->
                when {
                    r.isSuccessful -> "Got it: that voice is yours on this microphone now"
                    r.code == 404 -> "That one is gone (they keep two hours); the next refusal can be claimed"
                    else -> "${ActiveTheme.name} could not learn that one (${r.code})"
                }
            }
        }.getOrElse { "${ActiveTheme.name} could not be reached" }
    }
}

/** The tray's "That was me". Not exported: only this app's own PendingIntent reaches it. */
class VoiceClaimReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != VoiceClaim.ACTION_CLAIM) return
        val claim = intent.getStringExtra(VoiceClaim.EXTRA_CLAIM)?.takeIf { VoiceClaim.valid(it) } ?: return
        // Belt to the action's own lock requirement: a broadcast that somehow arrives
        // while the phone is locked claims nothing.
        if (context.getSystemService(android.app.KeyguardManager::class.java)?.isKeyguardLocked == true) return
        val pending = goAsync()
        thread(name = "jarvis-voice-claim", isDaemon = true) {
            try {
                VoiceClaim.settle(context, VoiceClaim.claim(context, claim))
            } finally {
                pending.finish()
            }
        }
    }
}
