package ai.aura.app.automation.actions.builtin

import android.content.Context
import android.media.AudioManager
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.os.SystemClock
import android.view.KeyEvent
import ai.aura.app.automation.actions.ActionEnv
import ai.aura.app.automation.actions.ActionResult
import ai.aura.app.automation.actions.JarvisAction
import ai.aura.app.automation.actions.clampPercent
import ai.aura.app.automation.actions.intOr
import ai.aura.app.automation.actions.json
import ai.aura.app.automation.policy.ActionTier
import org.json.JSONObject

/**
 * Media transport. All Tier 1 — the brief names "media play/pause" and
 * "set volume" as Tier 1, and every one of these is undone by pressing the
 * opposite button.
 *
 * Two mechanisms, in order of preference:
 *
 *  1. `MediaSessionManager` — precise (targets the actual session, tells you
 *     which app answered) but needs notification-listener access, so it is used
 *     only when [ActionEnv.notificationListener] has been registered and the
 *     user has granted it.
 *  2. `AudioManager.dispatchMediaKeyEvent` — needs no special access at all and
 *     is what the physical headset buttons do. Always available fallback.
 */
internal class MediaKeyAction(
    override val id: String,
    override val description: String,
    private val keyCode: Int,
    private val transport: (MediaController.TransportControls) -> Unit
) : JarvisAction {
    override val tier = ActionTier.AUTO
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "media"
    override val timeoutMs = 5_000L

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        viaSession(ctx, transport) ?: viaMediaKey(ctx, keyCode)

    private fun viaSession(
        ctx: Context,
        apply: (MediaController.TransportControls) -> Unit
    ): ActionResult? {
        val component = ActionEnv.notificationListener ?: return null
        val msm = ctx.getSystemService(MediaSessionManager::class.java) ?: return null
        return try {
            val controller = msm.getActiveSessions(component).firstOrNull() ?: return null
            apply(controller.transportControls)
            ActionResult.ok(json("via" to "media_session", "app" to controller.packageName))
        } catch (e: SecurityException) {
            // Notification access not actually granted — fall back silently.
            null
        } catch (e: Exception) {
            null
        }
    }

    private fun viaMediaKey(ctx: Context, code: Int): ActionResult {
        val am = ctx.getSystemService(AudioManager::class.java)
            ?: return ActionResult.error("no audio service")
        val now = SystemClock.uptimeMillis()
        return try {
            am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0))
            am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, code, 0))
            ActionResult.ok(json("via" to "media_key", "key" to code))
        } catch (e: Exception) {
            ActionResult.error("media key dispatch failed: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}

/** Tier 1 — same thing as [SetVolume] with stream=music, kept as its own id for the model. */
object SetMediaVolume : JarvisAction {
    override val id = "set_media_volume"
    override val tier = ActionTier.AUTO
    override val description = "Set the media/music volume as a percentage."
    override val paramsSchema = mapOf("level" to "int 0-100")
    override val capability = "media"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val am = ctx.getSystemService(AudioManager::class.java)
            ?: return ActionResult.error("no audio service")
        if (!params.has("level")) return ActionResult.error("level (0-100) is required")
        val percent = params.intOr("level", 0).clampPercent()
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        return try {
            am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(max * percent / 100f), 0)
            ActionResult.ok(json("level" to percent, "max" to max))
        } catch (e: SecurityException) {
            ActionResult.error("the system refused to change the media volume")
        }
    }
}

/**
 * Tier 1 — "play Daft Punk on my phone", asked somewhere the phone is not (the desk console).
 *
 * In the phone's own conversation a music request is a `hud.phone.media` frame on that
 * session ([ai.aura.app.assist.PhoneMedia]). From any other conversation that frame
 * reaches nothing that plays music, so the request comes over the house's device channel
 * as this action and is handed to the same handler (T-1474). The bare verbs (pause, next,
 * previous, a plain play) are `media_control`'s; this is for what the media keys cannot do.
 *
 * Does not say whether the music app found anything: play-from-search has no answer.
 */
object MediaSearch : JarvisAction {
    override val id = "media_search"
    override val tier = ActionTier.AUTO
    override val description =
        "Play or queue music by name in the phone's own music app: a song, an album, an artist, a playlist or a genre, optionally shuffled."
    override val paramsSchema = mapOf(
        "action" to "string (optional): play (default) | queue",
        "query" to "string: what to search for (or name it with the fields below)",
        "kind" to "string (optional): song | album | artist | playlist | genre | any",
        "title" to "string (optional)",
        "artist" to "string (optional)",
        "album" to "string (optional)",
        "playlist" to "string (optional)",
        "genre" to "string (optional)",
        "shuffle" to "bool (optional): turn shuffle on or off once playback starts"
    )
    override val capability = "media"
    override val timeoutMs = 5_000L

    private val FIELDS = listOf("query", "title", "artist", "album", "playlist", "genre")

    /** Why this request is refused, or null when it can be handed on. */
    fun problem(params: JSONObject): String? {
        val action = params.optString("action", "play").trim().lowercase()
        if (action != "play" && action != "queue") return "action is play or queue; pause, next and previous are media_control's"
        val named = FIELDS.any { params.optString(it).isNotBlank() }
        if (action == "queue" && !named) return "queue needs what to add: a query, a title, an album, an artist, a playlist or a genre"
        if (!named && !params.has("shuffle")) return "name what to play (query, title, artist, album, playlist or genre) or set shuffle; a bare play is media_control's"
        return null
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        problem(params)?.let { return ActionResult.error(it) }
        val app = ctx.applicationContext
        // The handler binds a media browser and posts delayed work: the main thread's, as its
        // HUD callers give it.
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            ai.aura.app.assist.PhoneMedia.handle(app, params)
        }
        return ActionResult.ok(
            json(
                "action" to params.optString("action", "play").trim().lowercase(),
                "query" to params.optString("query"),
                "handed_to" to "the phone's music app"
            )
        )
    }
}

/** The transport set, built once. */
object MediaActions {
    val play: JarvisAction = MediaKeyAction(
        "media_play", "Resume media playback.", KeyEvent.KEYCODE_MEDIA_PLAY
    ) { it.play() }

    val pause: JarvisAction = MediaKeyAction(
        "media_pause", "Pause media playback.", KeyEvent.KEYCODE_MEDIA_PAUSE
    ) { it.pause() }

    val next: JarvisAction = MediaKeyAction(
        "media_next", "Skip to the next track.", KeyEvent.KEYCODE_MEDIA_NEXT
    ) { it.skipToNext() }

    val previous: JarvisAction = MediaKeyAction(
        "media_previous", "Go back to the previous track.", KeyEvent.KEYCODE_MEDIA_PREVIOUS
    ) { it.skipToPrevious() }

    val stop: JarvisAction = MediaKeyAction(
        "media_stop", "Stop media playback.", KeyEvent.KEYCODE_MEDIA_STOP
    ) { it.stop() }

    val all: List<JarvisAction> = listOf(play, pause, next, previous, stop, SetMediaVolume, MediaSearch)
}
