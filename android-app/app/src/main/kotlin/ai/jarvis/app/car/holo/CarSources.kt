package ai.jarvis.app.car.holo

import ai.jarvis.app.R
import ai.jarvis.app.car.dash.tiles.CarMessage
import ai.jarvis.app.car.dash.tiles.HeadwayNotificationListener
import ai.jarvis.app.car.dash.tiles.NowPlayingTile
import ai.jarvis.app.car.holo.CarCard.Kind
import ai.jarvis.app.car.phone.CarPhone
import ai.jarvis.app.car.phone.LiveCall
import ai.jarvis.app.car.voice.CarMessageAnnouncer
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.provider.CalendarContract
import android.text.format.DateFormat
import android.util.Log
import java.util.Date
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import kotlin.math.ceil

/** An event on the calendar, as much of it as the card needs. */
data class CarEvent(val title: String, val beginMs: Long, val allDay: Boolean)

/**
 * What each source says in the one card slot, and when it has anything to say (T-0648).
 * Pure: the words come from resources, the rest from the arguments, so each mapping is
 * tested without a phone.
 */
object CarSourceCards {

    const val ANSWER = "answer"
    const val DECLINE = "decline"
    const val HANG_UP = "hang_up"
    const val REPLY = "reply"
    const val LATER = "later"
    const val PREVIOUS = "previous"
    const val PAUSE = "pause"
    const val NEXT = "next"

    /** A message older than this is news no longer; the card leaves on the next tick. */
    const val MESSAGE_FRESH_MS = 2 * 60_000L

    /** The next event shows from this far ahead. */
    const val CALENDAR_AHEAD_MS = 15 * 60_000L

    /** An event that began a minute ago still reads "now". */
    const val CALENDAR_GRACE_MS = 60_000L

    /** "Almost home": the destination is called home and is this close. */
    const val HOME_M = 1_000f

    private const val MAX_TITLE = 140

    fun call(ctx: Context, call: LiveCall): CarCard = CarCard(
        Kind.CALL,
        caption = ctx.getString(if (call.ringing) R.string.car_card_call_incoming else R.string.car_card_call_live),
        title = call.who,
        actions = if (call.ringing) {
            listOf(
                CarAction(ANSWER, ctx.getString(R.string.car_card_call_answer), primary = true),
                CarAction(DECLINE, ctx.getString(R.string.car_card_call_decline)),
            )
        } else {
            listOf(CarAction(HANG_UP, ctx.getString(R.string.car_card_call_hang_up), primary = true))
        },
    )

    /**
     * The newest message worth a card: posted since the screen started (the feed replays
     * old ones), recent, not chatter ("is typing"), not put off with Later.
     */
    fun latestMessage(feed: List<CarMessage>, since: Long, now: Long, dismissed: Set<String>): CarMessage? =
        feed.filter {
            it.key !in dismissed &&
                it.postedAtMillis >= since &&
                now - it.postedAtMillis <= MESSAGE_FRESH_MS &&
                it.text.isNotBlank() &&
                !CarMessageAnnouncer.isChatter(it.text)
        }.maxByOrNull { it.postedAtMillis }

    fun message(ctx: Context, m: CarMessage): CarCard = CarCard(
        Kind.MESSAGE,
        caption = ctx.getString(R.string.car_card_message, oneLine(m.sender)),
        title = oneLine(m.text),
        // Jarvis starts the conversation; the app's own inline reply is not typed from a car.
        actions = buildList {
            if (m.reply != null) add(CarAction(REPLY, ctx.getString(R.string.car_card_message_reply), primary = true))
            add(CarAction(LATER, ctx.getString(R.string.car_card_message_later)))
        },
    )

    /** The soonest timed event beginning within [CALENDAR_AHEAD_MS] (or just begun). */
    fun nextEvent(events: List<CarEvent>, now: Long): CarEvent? =
        events.filter {
            !it.allDay && it.title.isNotBlank() &&
                it.beginMs in (now - CALENDAR_GRACE_MS)..(now + CALENDAR_AHEAD_MS)
        }.minByOrNull { it.beginMs }

    /** [time] is the start in the driver's clock format; the minutes are rounded up. */
    fun calendar(ctx: Context, e: CarEvent, time: String, now: Long): CarCard {
        val minutes = ceil((e.beginMs - now) / 60_000.0).toInt()
        return CarCard(
            Kind.CALENDAR,
            caption = ctx.getString(R.string.car_card_calendar),
            title = oneLine(e.title),
            detail = if (minutes <= 0) {
                ctx.getString(R.string.car_card_calendar_now, time)
            } else {
                ctx.getString(R.string.car_card_calendar_detail, time, minutes)
            },
        )
    }

    fun music(ctx: Context, title: String, artist: String): CarCard = CarCard(
        Kind.MUSIC,
        caption = ctx.getString(R.string.car_card_music),
        title = oneLine(title),
        detail = oneLine(artist),
        actions = listOf(
            CarAction(PREVIOUS, ctx.getString(R.string.car_card_music_previous)),
            CarAction(PAUSE, ctx.getString(R.string.car_card_music_pause), primary = true),
            CarAction(NEXT, ctx.getString(R.string.car_card_music_next)),
        ),
    )

    // ponytail: no home coordinates exist in the app, so "home" is the destination's name;
    // a saved home place replaces this test when the house learns one (GAPS.md).
    fun isHome(label: String, remainingM: Float): Boolean =
        label.trim().equals("home", ignoreCase = true) && remainingM < HOME_M

    fun home(ctx: Context, label: String, distance: String): CarCard = CarCard(
        Kind.HOME,
        caption = ctx.getString(R.string.car_card_home),
        title = oneLine(label),
        detail = ctx.getString(R.string.car_card_home_detail, distance),
    )

    /** One line, brackets and guillemets made inert, cut to what a glance can use. */
    private fun oneLine(s: String): String {
        val t = CarMessageAnnouncer.inert(s).replace(Regex("\\s+"), " ").trim()
        return if (t.length <= MAX_TITLE) t else t.take(MAX_TITLE - 1).trimEnd() + "…"
    }
}

/**
 * The live sources of the car's one card slot: the call, the newest message, the next
 * event, what is playing, and the arrival at home. [start] registers them and [stop]
 * removes them and their cards; each puts its card in [cards] while it has one.
 *
 * Main thread only for the calls in; listeners that fire elsewhere post to [main].
 * Not covered: the calendar is read only with the calendar permission and music only
 * with notification access (both are granted on the phone, never from the car), and a
 * source without its grant is silent rather than asking.
 */
class CarSources(
    context: Context,
    private val cards: CarCards,
    private val main: Handler,
    private val units: CarUnits,
    /** The Reply action: the screen starts its conversation. */
    private val onReply: () -> Unit,
) {
    private val ctx: Context = context.applicationContext
    private var running = false
    private var since = 0L
    private var feed: List<CarMessage> = emptyList()
    private val dismissed = HashSet<String>()
    private var executor: ExecutorService? = null
    private var calendarAsk = 0
    private var watched: List<MediaController> = emptyList()

    private val callListener = CarPhone.Listener { call -> main.post { if (running) onCall(call) } }
    private val messageObserver: (List<CarMessage>) -> Unit = { list ->
        main.post {
            if (running) {
                feed = list
                refreshMessage()
            }
        }
    }
    private val sessionsListener = MediaSessionManager.OnActiveSessionsChangedListener { watch(it.orEmpty()) }
    private val mediaCallback = object : MediaController.Callback() {
        override fun onPlaybackStateChanged(state: PlaybackState?) = refreshMusic()
        override fun onMetadataChanged(metadata: MediaMetadata?) = refreshMusic()
    }
    private val tick = object : Runnable {
        override fun run() {
            refreshMessage()
            refreshCalendar()
            main.postDelayed(this, TICK_MS)
        }
    }

    fun start() {
        if (running) return
        running = true
        since = System.currentTimeMillis()
        feed = emptyList()
        dismissed.clear()
        executor = Executors.newSingleThreadExecutor()
        CarPhone.observe(callListener)
        HeadwayNotificationListener.observe(messageObserver)
        startMusic()
        tick.run()
    }

    fun stop() {
        if (!running) return
        running = false
        main.removeCallbacks(tick)
        CarPhone.unobserve(callListener)
        HeadwayNotificationListener.unobserve(messageObserver)
        stopMusic()
        executor?.shutdownNow()
        executor = null
        for (k in SOURCE_KINDS) cards.clear(k)
    }

    // --- call -------------------------------------------------------------------

    private fun onCall(call: LiveCall?) {
        if (call == null) return cards.clear(Kind.CALL)
        cards.set(CarSourceCards.call(ctx, call)) {
            if (it.id == CarSourceCards.ANSWER) CarPhone.answer(ctx) else CarPhone.hangUp(ctx)
        }
    }

    // --- message ----------------------------------------------------------------

    private fun refreshMessage() {
        val m = CarSourceCards.latestMessage(feed, since, System.currentTimeMillis(), dismissed)
        if (m == null) return cards.clear(Kind.MESSAGE)
        cards.set(CarSourceCards.message(ctx, m)) {
            dismissed.add(m.key)
            if (it.id == CarSourceCards.REPLY) onReply()
            refreshMessage()
        }
    }

    // --- calendar ---------------------------------------------------------------

    private fun refreshCalendar() {
        if (ctx.checkSelfPermission(Manifest.permission.READ_CALENDAR) != PackageManager.PERMISSION_GRANTED) {
            return cards.clear(Kind.CALENDAR)
        }
        val ask = ++calendarAsk
        val now = System.currentTimeMillis()
        try {
            // The provider can be slow: never on the main thread, never two at once.
            executor?.execute {
                val events = readEvents(now)
                main.post { if (running && ask == calendarAsk) showEvent(events, now) }
            }
        } catch (e: RejectedExecutionException) {
            // Stopped between the check and here.
        }
    }

    private fun showEvent(events: List<CarEvent>, now: Long) {
        val e = CarSourceCards.nextEvent(events, now)
        if (e == null) return cards.clear(Kind.CALENDAR)
        val time = DateFormat.getTimeFormat(ctx).format(Date(e.beginMs))
        cards.set(CarSourceCards.calendar(ctx, e, time, now))
    }

    private fun readEvents(now: Long): List<CarEvent> {
        val out = ArrayList<CarEvent>()
        try {
            CalendarContract.Instances.query(
                ctx.contentResolver,
                arrayOf(
                    CalendarContract.Instances.TITLE,
                    CalendarContract.Instances.BEGIN,
                    CalendarContract.Instances.ALL_DAY,
                ),
                now - CarSourceCards.CALENDAR_GRACE_MS,
                now + CarSourceCards.CALENDAR_AHEAD_MS,
            )?.use { c ->
                while (c.moveToNext()) {
                    out.add(CarEvent(c.getString(0).orEmpty(), c.getLong(1), c.getInt(2) == 1))
                }
            }
        } catch (e: SecurityException) {
            Log.i(TAG, "calendar permission revoked")
        } catch (e: Exception) {
            Log.w(TAG, "calendar read failed", e)
        }
        return out
    }

    // --- music ------------------------------------------------------------------

    private fun startMusic() {
        val manager = ctx.getSystemService(MediaSessionManager::class.java) ?: return
        val component = NowPlayingTile.listenerComponent(ctx)
        try {
            manager.addOnActiveSessionsChangedListener(sessionsListener, component, main)
            watch(manager.getActiveSessions(component))
        } catch (e: SecurityException) {
            // Notification access is not granted: no music card, said once.
            Log.i(TAG, "media sessions unavailable: notification access not granted")
        }
    }

    private fun stopMusic() {
        runCatching { ctx.getSystemService(MediaSessionManager::class.java)?.removeOnActiveSessionsChangedListener(sessionsListener) }
        watch(emptyList())
    }

    /** Every session is watched, so a paused one that resumes is heard. */
    private fun watch(sessions: List<MediaController>) {
        watched.forEach { runCatching { it.unregisterCallback(mediaCallback) } }
        watched = sessions
        watched.forEach { runCatching { it.registerCallback(mediaCallback, main) } }
        refreshMusic()
    }

    private fun refreshMusic() {
        val c = watched.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PLAYING }
        val meta = c?.metadata
        val title = meta?.getString(MediaMetadata.METADATA_KEY_TITLE).orEmpty()
        if (c == null || title.isBlank()) return cards.clear(Kind.MUSIC)
        val artist = meta?.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty()
        cards.set(CarSourceCards.music(ctx, title, artist)) {
            when (it.id) {
                CarSourceCards.PREVIOUS -> c.transportControls.skipToPrevious()
                CarSourceCards.PAUSE -> c.transportControls.pause()
                CarSourceCards.NEXT -> c.transportControls.skipToNext()
            }
        }
    }

    // --- home -------------------------------------------------------------------

    /** Called with each fix's remaining distance (null with no route). */
    fun home(label: String, remainingM: Float?) {
        if (remainingM == null || !CarSourceCards.isHome(label, remainingM)) return cards.clear(Kind.HOME)
        cards.set(CarSourceCards.home(ctx, label, units.distance(remainingM)))
    }

    companion object {
        private const val TICK_MS = 30_000L
        private const val TAG = "CarSources"
        private val SOURCE_KINDS = listOf(Kind.CALL, Kind.MESSAGE, Kind.HOME, Kind.CALENDAR, Kind.MUSIC)
    }
}
