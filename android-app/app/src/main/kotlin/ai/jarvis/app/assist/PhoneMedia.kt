package ai.jarvis.app.assist

import ai.jarvis.app.car.voice.PhoneMediaControl
import android.app.SearchManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.media.session.PlaybackState
import android.os.Bundle
import android.provider.MediaStore
import android.support.v4.media.MediaBrowserCompat
import android.support.v4.media.session.MediaControllerCompat
import android.support.v4.media.session.MediaSessionCompat
import android.util.Log
import dev.headway.voice.MediaAction
import org.json.JSONObject

/**
 * `hud.phone.media` (the `phone_media` tool, aura-hud/places.py): music on this
 * phone, not the house's speakers (the owner, 2026-09-25: "I asked it to play
 * music and it didn't for some reason, it kept doing HA calls").
 *
 * A request to play names a song, an album, an artist, a playlist or a genre
 * field by field, and goes to the music app as Android's structured
 * play-from-search: the session that is playing, when it takes searches, else the
 * `MEDIA_PLAY_FROM_SEARCH` intent aimed at that app. Symfonium answered "No
 * results" to the whole sentence as its query (the owner, same day); it matches
 * `EXTRA_MEDIA_ARTIST` as an artist. A queue request searches the app's browse
 * tree and adds the first playable hit after what is playing. Play, pause, next
 * and previous go to the active session, with the media key as the fallback.
 *
 * Does not cover an app that offers no search (the request is then its query to
 * whichever app answers the intent) or one that refuses to be browsed by Jarvis
 * (a queue request then says so in the log and plays nothing).
 */
object PhoneMedia {
    const val EVENT = "hud.phone.media"
    private const val TAG = "JarvisPhoneMedia"
    private const val BROWSE_ACTION = "android.media.browse.MediaBrowserService"
    private const val FOCUS_ANY = "vnd.android.cursor.item/*"
    private const val FOCUS_PLAYLIST = "vnd.android.cursor.item/playlist"
    private const val FOCUS_GENRE = "vnd.android.cursor.item/genre"

    fun handle(context: Context, payload: JSONObject) {
        val app = context.applicationContext
        val action = payload.optString("action", "play")
        val query = payload.optString("query")
        val extras = searchExtras(payload)
        val named = query.isNotBlank() || extras.size() > 1
        // "Shuffle my liked songs" (the owner, 2026-09-25): once the app has started it.
        if (payload.has("shuffle")) shuffleSoon(app, payload.optBoolean("shuffle"))
        if (action == "queue" && named) return queue(app, query, extras)
        if (action == "play" && named) {
            if (play(app, query, extras)) return
            Log.w(TAG, "no music app took the search; resuming instead")
        }
        val media = when (action) {
            "pause" -> MediaAction.PAUSE
            "next" -> MediaAction.NEXT
            "previous" -> MediaAction.PREVIOUS
            else -> MediaAction.PLAY
        }
        PhoneMediaControl(app).perform(media)
    }

    /** The structured search Android defines for music (MediaStore.EXTRA_MEDIA_*). */
    internal fun searchExtras(payload: JSONObject): Bundle = Bundle().apply {
        putString(
            MediaStore.EXTRA_MEDIA_FOCUS,
            when (payload.optString("kind")) {
                "song" -> MediaStore.Audio.Media.ENTRY_CONTENT_TYPE
                "album" -> MediaStore.Audio.Albums.ENTRY_CONTENT_TYPE
                "artist" -> MediaStore.Audio.Artists.ENTRY_CONTENT_TYPE
                "playlist" -> FOCUS_PLAYLIST
                "genre" -> FOCUS_GENRE
                else -> FOCUS_ANY
            },
        )
        payload.optString("title").takeIf { it.isNotBlank() }?.let { putString(MediaStore.EXTRA_MEDIA_TITLE, it) }
        payload.optString("artist").takeIf { it.isNotBlank() }?.let { putString(MediaStore.EXTRA_MEDIA_ARTIST, it) }
        payload.optString("album").takeIf { it.isNotBlank() }?.let { putString(MediaStore.EXTRA_MEDIA_ALBUM, it) }
        payload.optString("playlist").takeIf { it.isNotBlank() }?.let { putString(MediaStore.EXTRA_MEDIA_PLAYLIST, it) }
        payload.optString("genre").takeIf { it.isNotBlank() }?.let { putString(MediaStore.EXTRA_MEDIA_GENRE, it) }
    }

    /** Through the playing app's own session when it takes searches, else the intent aimed at it. */
    private fun play(context: Context, query: String, extras: Bundle): Boolean {
        val session = PhoneMediaControl(context).boundSession()
        val searches = (session?.playbackState?.actions ?: 0L) and PlaybackState.ACTION_PLAY_FROM_SEARCH != 0L
        if (session != null && searches) {
            session.transportControls.playFromSearch(query, extras)
            return true
        }
        val intent = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
            .putExtras(extras)
            .putExtra(SearchManager.QUERY, query)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        session?.packageName?.let { pkg ->
            if (runCatching { context.startActivity(Intent(intent).setPackage(pkg)) }.isSuccess) return true
        }
        return runCatching { context.startActivity(intent) }.isSuccess
    }

    /**
     * Shuffle on (or off) in the playing app's session, a moment after the request: a
     * search starts the playlist first, and shuffling the song that was on before it
     * would shuffle the wrong thing.
     */
    private fun shuffleSoon(context: Context, on: Boolean) {
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            val session = PhoneMediaControl(context).boundSession() ?: return@postDelayed
            runCatching {
                val compat = MediaControllerCompat(context, android.support.v4.media.session.MediaSessionCompat.Token.fromToken(session.sessionToken))
                compat.transportControls.setShuffleMode(
                    if (on) android.support.v4.media.session.PlaybackStateCompat.SHUFFLE_MODE_ALL
                    else android.support.v4.media.session.PlaybackStateCompat.SHUFFLE_MODE_NONE,
                )
            }.onFailure { Log.w(TAG, "${session.packageName} would not take a shuffle mode", it) }
        }, SHUFFLE_AFTER_MS)
    }

    private const val SHUFFLE_AFTER_MS = 1_500L

    /** Searches the playing app's browse tree and adds the first playable hit to its queue. */
    private fun queue(context: Context, query: String, extras: Bundle) {
        val pkg = PhoneMediaControl(context).boundSession()?.packageName
            ?: return run { Log.w(TAG, "nothing is playing, so there is no queue to add to") }
        val service = context.packageManager.queryIntentServices(Intent(BROWSE_ACTION).setPackage(pkg), 0)
            .firstOrNull()?.serviceInfo
            ?: return run { Log.w(TAG, "$pkg offers no browse service to search") }
        val words = query.ifBlank { extras.getString(MediaStore.EXTRA_MEDIA_TITLE).orEmpty() }
        var browser: MediaBrowserCompat? = null
        browser = MediaBrowserCompat(
            context,
            ComponentName(service.packageName, service.name),
            object : MediaBrowserCompat.ConnectionCallback() {
                override fun onConnected() {
                    val b = browser ?: return
                    b.search(words, extras, object : MediaBrowserCompat.SearchCallback() {
                        override fun onSearchResult(q: String, e: Bundle?, items: List<MediaBrowserCompat.MediaItem>) {
                            addFirst(context, b, pkg, q, items)
                            b.disconnect()
                        }

                        override fun onError(q: String, e: Bundle?) {
                            Log.w(TAG, "$pkg refused the search for \"$q\"")
                            b.disconnect()
                        }
                    })
                }

                override fun onConnectionFailed() {
                    Log.w(TAG, "$pkg would not let the app browse it")
                }
            },
            null,
        )
        browser.connect()
    }

    private fun addFirst(context: Context, browser: MediaBrowserCompat, pkg: String, q: String, items: List<MediaBrowserCompat.MediaItem>) {
        val hit = items.firstOrNull { it.isPlayable }
            ?: return run { Log.w(TAG, "$pkg found nothing playable for \"$q\"") }
        val controller = runCatching { MediaControllerCompat(context, browser.sessionToken) }.getOrNull()
            ?: return run { Log.w(TAG, "$pkg's session would not take a controller") }
        if (controller.flags and MediaSessionCompat.FLAG_HANDLES_QUEUE_COMMANDS.toLong() != 0L) {
            controller.addQueueItem(hit.description)
        } else {
            Log.w(TAG, "$pkg keeps no queue the app can add to")
        }
    }
}
