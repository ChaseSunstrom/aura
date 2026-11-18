package ai.aura.app.car.dash

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.car.dash.tiles.CarApps
import ai.aura.app.car.dash.tiles.CarMessage
import ai.aura.app.car.dash.tiles.CarNotice
import ai.aura.app.car.dash.tiles.HeadwayNotificationListener
import ai.aura.app.car.dash.tiles.NowPlayingTile
import ai.aura.app.car.dash.tiles.send
import ai.aura.app.car.holo.HoloRoute
import ai.aura.app.car.holo.LatLon
import ai.aura.app.car.holo.MapPacks
import ai.aura.app.car.holo.PackRouter
import ai.aura.app.car.phone.CarPhone
import ai.aura.app.car.phone.LiveCall
import ai.aura.app.car.video.AppPaneHost
import ai.aura.app.car.video.CarAppDisplay
import ai.aura.app.car.voice.CarVoice
import ai.aura.app.car.voice.CarVoiceView
import ai.aura.app.car.voice.PhoneMediaControl
import ai.aura.app.config.JarvisConfig
import ai.aura.app.config.ServerEndpoint
import ai.aura.app.ui.Notify
import android.Manifest
import android.annotation.SuppressLint
import android.app.ActivityOptions
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.media.MediaMetadata
import android.media.session.PlaybackState
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebView
import dev.headway.voice.MediaAction
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import kotlin.math.roundToInt
import org.json.JSONArray
import org.json.JSONObject

/**
 * The car screen is the console's own HUD (the owner, 2026-09-25: "just Jarvis,
 * the reactor as the main thing, and the same orbit, tiling, tabs options from
 * the console on the head for the maps, phone, messages"): the console's `/car`
 * in a web view on the car display, fed what only the phone knows through
 * `window.JarvisCar` (`aura-web/src/lib/hud/car.svelte.ts` is the other half).
 *
 * The phone hands over what is playing, the live call, the recent messages,
 * where it is, which navigation apps are installed, and the car's own voice turn
 * so the reactor walks it. The page asks back: play/pause/next/previous, answer
 * and hang up, reply through the messaging app's own inline reply, navigate in
 * Organic Maps, Google Maps or HERE WeGo (opened in the car's app pane), and
 * talk -- a turn on the head unit's microphone, answered on its speakers.
 *
 * Polled once a second rather than observed: a song's position moves anyway,
 * and diffing one small JSON string is cheaper than five listener lifecycles.
 * Only a changed state crosses into the page.
 *
 * Does not cover a console the phone does not know. Only a pairing made at the
 * console (a RELAY server) names it; [consoleUrl] is null otherwise and the shell
 * keeps its own screens.
 */
@SuppressLint("SetJavaScriptEnabled")
class CarHud(
    private val context: Context,
    private val url: String,
    private val shell: CarShell,
) {
    private val main = Handler(Looper.getMainLooper())
    private val packRouting = AtomicBoolean(false)
    private val media = PhoneMediaControl(context)
    private val locations = context.getSystemService(LocationManager::class.java)
    @Volatile private var place: Location? = null
    private var last = ""
    private var running = false

    /**
     * The driver's apps for the Apps panel (`CarApps`, the launcher tile's own list), read once
     * a car session: the state is pushed on every media tick, and a package query each time was
     * not worth it. ponytail: pins changed mid-drive show on the next drive; re-read on a pin
     * change if that ever matters.
     */
    private val apps by lazy { CarApps.pinned(context).map { it.packageName to it.label } }

    /**
     * The console's `/car` (the console's own page, `ConsolePage`): loaded until it works,
     * rebuilt through the shell when its renderer dies (the owner, 2026-09-27: "freezes on
     * a blank screen on open sometimes"), and given the whole snapshot again each time it
     * loads, since a fresh page has no state yet.
     */
    private val page = ai.aura.app.ui.ConsolePage(
        context,
        page(url),
        // A user-agent mark only: the console reads neither this nor the phone's " JarvisPhone".
        " JarvisCar",
        onLoaded = {
            last = ""
            push()
            shell.hudReachable(true)
        },
        onFailed = { shell.hudReachable(false) },
        // A reload or the offline page never ran the old page's cleanup, which would
        // leave the ground drawing and listening to GPS under a page that never asked.
        onStarted = { shell.askGround(false) },
        onRendererGone = { shell.rebuildHud() },
        microphone = false,
        // Unreachable with nothing cached: the console's offline page, not the Headway rail (the
        // owner, 2026-11-08: "it should show the nice looking entire JARVIS UI regardless").
        offlinePage = true,
        key = JarvisConfig(context).hermesKey,
    ).also { it.view.addJavascriptInterface(Bridge(), "JarvisCar") }

    val view: WebView get() = page.view

    private val location = LocationListener { place = it }

    private val tick = object : Runnable {
        override fun run() {
            push()
            // Faster while the reactor moves with the music: the level is only news at ~15 Hz.
            if (running) main.postDelayed(this, if (JarvisConfig(context).carMusicPulse) MUSIC_TICK_MS else TICK_MS)
        }
    }

    fun start() {
        if (running) return
        running = true
        watchLocation()
        page.start()
        main.post(tick)
    }

    fun stop() {
        running = false
        main.removeCallbacks(tick)
        CarMusicLevel.stop()
        runCatching { locations?.removeUpdates(location) }
        page.stop()
    }

    @SuppressLint("MissingPermission")
    private fun watchLocation() {
        val lm = locations ?: return
        val granted = listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
            .any { context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }
        if (!granted) return
        for (provider in listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)) {
            if (!runCatching { lm.isProviderEnabled(provider) }.getOrDefault(false)) continue
            runCatching { lm.getLastKnownLocation(provider)?.let { if (place == null) place = it } }
            runCatching { lm.requestLocationUpdates(provider, LOCATION_MS, LOCATION_M, location, Looper.getMainLooper()) }
        }
    }

    /**
     * The newest notification, drawn by the page in the console's own notice card
     * (the owner, 2026-09-25: "fix the weird notification pop up in the car hud, it
     * looks weird as its the old headway thing"). The shell hands it here instead of
     * raising its native card while this page is on the car screen.
     */
    @Volatile
    private var notice: CarNotice? = null
    @Volatile
    private var noticeAt = 0L

    /**
     * Sent for [NOTICE_LIVE_MS] and then no longer. It was kept for good and went out in
     * every snapshot, and the page's memory of which notices it had shown lives only as
     * long as the page: each reconnect reloaded it (three in the owner's 1447 drive) and
     * the last notification came back, closed or not ("still having notifications be
     * stuck").
     */
    fun notify(fresh: CarNotice) {
        notice = fresh
        noticeAt = android.os.SystemClock.elapsedRealtime()
        main.removeCallbacks(expireNotice)
        main.post { push() }
        main.postDelayed(expireNotice, NOTICE_LIVE_MS)
    }

    private val expireNotice = Runnable {
        notice = null
        push()
    }

    private fun liveNotice(): CarNotice? =
        notice?.takeIf { android.os.SystemClock.elapsedRealtime() - noticeAt < NOTICE_LIVE_MS }

    /**
     * An approval asked on the car screen as a small card, not a sheet over the whole
     * screen (the owner, 2026-09-25: "the approve shouldn't take up the whole screen,
     * only a tiny notification, or verbally approve"). [onAnswer] hears the tap.
     */
    @Volatile
    private var asking: Triple<String, String, String>? = null
    @Volatile
    private var onAnswer: ((Boolean) -> Unit)? = null

    fun ask(id: String, title: String, detail: String, answer: (Boolean) -> Unit) {
        asking = Triple(id, title, detail)
        onAnswer = answer
        main.post { push() }
    }

    fun clearAsk() {
        asking = null
        onAnswer = null
        main.post { push() }
    }

    private fun push() {
        val now = snapshot()
        if (now == last) return
        last = now
        view.evaluateJavascript("window.dispatchEvent(new CustomEvent('jarvis:car',{detail:$now}))", null)
    }

    private fun snapshot(): String {
        val session = runCatching { media.boundSession() }.getOrNull()
        val meta = session?.metadata
        val playback = session?.playbackState
        val now = meta?.getString(MediaMetadata.METADATA_KEY_TITLE)?.let { title ->
            Playing(
                title = title,
                artist = meta.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty(),
                app = appLabel(session.packageName),
                playing = playback?.state == PlaybackState.STATE_PLAYING,
                positionMs = playback?.position ?: 0L,
                durationMs = meta.getLong(MediaMetadata.METADATA_KEY_DURATION),
            )
        }
        val car = ai.aura.app.car.sensor.CarSensorStream.latest
        val phoneFix = place?.takeIf { android.os.SystemClock.elapsedRealtimeNanos() - it.elapsedRealtimeNanos < PHONE_FIX_STALE_NS }
        // The phone's fix while it is fresh, the car's GPS otherwise: a locked phone's
        // location stops, and the Speed panel's limit and the map went with it (2026-09-27).
        val here = phoneFix?.let {
            Place(it.latitude, it.longitude, if (it.hasBearing() && it.speed > 1f) it.bearing.toDouble() else null, it.speed.toDouble())
        } ?: car.latitude?.let { lat ->
            val lon = car.longitude ?: return@let null
            val mps = car.speedMetersPerSecond ?: 0.0
            Place(lat, lon, car.bearingDegrees?.takeIf { mps > 1.0 }, mps)
        }
        return state(now, CarPhone.call, HeadwayNotificationListener.latest, here, installedNavApps(), CarVoice.view.value, liveNotice(), CarMusicLevel.snapshot(context), asking,
            ai.aura.app.car.sensor.CarSensorStream.latest.speedMetersPerSecond,
            // The car panels' own listener, not "any listener of this app": the app has two, and
            // with only the plain "Jarvis" one on, media and messages stay null (the review of T-0326).
            notificationAccess = NowPlayingTile.notificationAccessGranted(context),
            capture = AppPaneHost.available,
            apps = apps)
    }

    private fun appLabel(pkg: String): String = runCatching {
        context.packageManager.run { getApplicationLabel(getApplicationInfo(pkg, 0)).toString() }
    }.getOrDefault(pkg)

    private fun installedNavApps(): List<String> = installedNavApps(context)

    private fun perform(act: JSONObject) {
        when (act.optString("kind")) {
            // An op this build does not know does nothing: the last branch was "else", so a typo
            // or a newer page's op skipped back a track (T-1216).
            "media" -> when (act.optString("op")) {
                "play" -> MediaAction.PLAY
                "pause" -> MediaAction.PAUSE
                "next" -> MediaAction.NEXT
                "previous" -> MediaAction.PREVIOUS
                else -> null
            }?.let { media.perform(it) }
            "call" -> if (act.optString("op") == "answer") CarPhone.answer(context) else CarPhone.hangUp(context)
            "reply" -> {
                val message = HeadwayNotificationListener.latest.firstOrNull { it.key == act.optString("id") }
                message?.reply?.send(context, message.packageName, act.optString("text"), RemoteInput.SOURCE_FREE_FORM_INPUT)
            }
            "navigate" -> navigate(act.optString("app"), act.optDouble("lat"), act.optDouble("lon"), act.optString("label"))
            "talk" -> CarShell.onVoiceRequested?.invoke() ?: shell.showVoiceMessage("Voice is not available on this connection")
            "answer" -> if (asking?.first == act.optString("id")) onAnswer?.invoke(act.optBoolean("ok"))
            // A turn, said on the car's own voice at once: no model, no thinking (route.ts).
            "say" -> act.optString("text").takeIf { it.isNotBlank() }?.let { CarShell.onSay?.invoke(it) }
            // The coming turns' words, to voice before they are due. From our own page, and still
            // bounded: a few lines of a sentence each.
            "prepare" -> act.optJSONArray("texts")?.let { arr ->
                (0 until minOf(arr.length(), PREPARE_MAX)).map { arr.optString(it).take(PREPARE_CHARS) }.filter { it.isNotBlank() }
            }?.takeIf { it.isNotEmpty() }?.let { CarShell.onPrepare?.invoke(it) }
            "arrived" -> destination = null
            "voice" -> voiceOf(act)?.let { CarShell.carVoice = it }
            // The page over the phone's hologram (T-0741): it asks for the ground, and hands it its route.
            "ground" -> groundOf(act)?.let(shell::askGround)
            "route" -> shell.groundRoute(routeOf(act))
            // The house's router did not answer the page: the route comes from the phone's own
            // packs (T-1075), off the main thread, and goes back as an event of its own.
            "packroute" -> packAskOf(act)?.let { (id, from, to) ->
                fun answer(json: String) = main.post {
                    runCatching { view.evaluateJavascript("window.dispatchEvent(new CustomEvent('jarvis:car-route',{detail:$json}))", null) }
                }
                // One plan at a time: a page that asks faster than a plan takes is told none, so
                // a script in a loop cannot pile up threads each building a road graph.
                if (!packRouting.compareAndSet(false, true)) {
                    answer(packRouteJson(id, null))
                } else {
                    thread(isDaemon = true, name = "pack-route") {
                        // Its own pack set, closed after: no SQLite handle outlives the ask.
                        val packs = MapPacks.of(context)
                        val plan = try {
                            runCatching { PackRouter.plan(from, to, { w, s, e, n -> packs.roads(w, s, e, n) }, covered = { p -> packs.covers(p.lon, p.lat, p.lon, p.lat) }) }.getOrNull()
                        } finally {
                            runCatching { packs.close() }
                            packRouting.set(false)
                        }
                        answer(packRouteJson(id, plan))
                    }
                }
            }
            // An app from the Apps panel: the shell's own route, which holds the allow-list and
            // says on the car screen when there is no screen-sharing grant to show it with.
            "app" -> appOf(act)?.let { shell.openApp(it) }
            // The one switch that lets the phone see music, messages and notices (T-0326). It
            // opens on the phone: Android shows that setting nowhere else.
            "access" -> if (wantsNotificationAccess(act)) {
                runCatching {
                    context.startActivity(NowPlayingTile.notificationAccessIntent())
                    shell.showVoiceMessage("Turn on ${ActiveTheme.name} car panels in the setting on the phone")
                }
            }
        }
        push()
    }

    private fun navigate(app: String, lat: Double, lon: Double, label: String) = navigate(context, shell, app, lat, lon, label)

    private inner class Bridge {
        @JavascriptInterface
        fun state(): String = snapshot()

        @JavascriptInterface
        fun act(json: String) {
            val act = runCatching { JSONObject(json) }.getOrNull() ?: return
            main.post { perform(act) }
        }
    }

    internal data class Playing(
        val title: String,
        val artist: String,
        val app: String,
        val playing: Boolean,
        val positionMs: Long,
        val durationMs: Long,
    )

    internal data class Place(val lat: Double, val lon: Double, val heading: Double?, val speed: Double)

    companion object {
        /** The house's `hud.car_voice` as the page hands it over, or null for a mode this app does not know. */
        internal fun voiceOf(act: JSONObject): String? =
            act.optString("mode").takeIf { act.optString("kind") == "voice" && it in setOf("auto", "house", "phone") }

        /** The most lines one `prepare` act may carry, and the longest a line may be. */
        private const val PREPARE_MAX = 5
        private const val PREPARE_CHARS = 200

        private const val TAG = "CarHud"
        private const val TICK_MS = 1_000L
        /** How long a notification is offered to the page: a little past its card's 8 s (`popupMood.ts` NOTICE_MS). */
        private const val NOTICE_LIVE_MS = 10_000L
        /** A phone fix older than this gives way to the car's GPS. */
        private const val PHONE_FIX_STALE_NS = 10_000_000_000L
        private const val MUSIC_TICK_MS = 66L
        // Every second and every 5 m: a turn's word is timed from the fix, and at 70 km/h
        // two seconds was forty metres late.
        private const val LOCATION_MS = 1_000L
        private const val LOCATION_M = 5f

        /** The page's names for the navigation apps it offers, and their packages, in offer order. */
        internal val NAV_APPS = listOf(
            "organic" to "app.organicmaps",
            "google" to "com.google.android.apps.maps",
            "here" to "com.here.app.maps",
        )

        /** Where the car screen's HUD is: the console the phone was paired at, or null. */
        fun consoleUrl(context: Context): String? {
            val config = JarvisConfig(context)
            return config.serverUrl.takeIf { it.isNotEmpty() && ServerEndpoint.servesConsole(config.serverKind) }
        }

        internal fun page(console: String): String = console.trimEnd('/') + "/car"

        fun installedNavApps(context: Context): List<String> = NAV_APPS.filter { (_, pkg) ->
            runCatching { context.packageManager.getPackageInfo(pkg, 0) }.isSuccess
        }.map { it.first }

        /** Where Jarvis was last asked to go; the page routes it or asks for an app (`hud.car_navigation`). */
        @Volatile
        var destination: JSONObject? = null
            private set

        /**
         * A destination Jarvis chose (`car_navigate`): handed to the car page, which draws
         * the route on its own map and says the turns, or asks for the app the owner
         * chose (the owner, 2026-09-25: "it doesn't update the map on the car HUD and
         * gets stuck on a white screen in my map app"). With no car page up, the app.
         */
        fun route(context: Context, shell: CarShell?, app: String, lat: Double, lon: Double, label: String): JSONObject {
            val onCar = shell?.hasHud() == true
            hold(lat, lon, label, app, onCar)
            // Off the car the route lives on the phone's island until it arrives or ends (T-0695).
            ai.aura.app.assist.island.RouteIsland.update(context, destination, onCar)
            if (onCar) return opened("car", app)
            return navigate(context, shell, app, lat, lon, label)
        }

        internal fun hold(lat: Double, lon: Double, label: String, app: String, onCar: Boolean) {
            destination = JSONObject().put("lat", lat).put("lon", lon).put("label", label).put("app", app)
                .put("at", System.currentTimeMillis())
            onCarMap = onCar
        }

        /** Whether the held route is on the car page's own map, which goes with the car. */
        @Volatile
        private var onCarMap = false
        private var handOff: Runnable? = null
        private val handOffs by lazy { Handler(Looper.getMainLooper()) }

        /**
         * The car screen went with Jarvis's route on its own map: unless the car is back within
         * [HANDOFF_DELAY_MS] (the owner's link drops and comes back mid-drive), the route opens in
         * the phone's maps app, so the drive goes on where the person is (the owner, 2026-11-09:
         * "it needs to cleanly transfer from both"). Not covered: a route the driver set on the
         * car page's own Map panel (the page holds it, the phone never sees it), and a maps app
         * already routing on the car's display, which Android moves to the phone with the display.
         * Main thread.
         */
        fun carGone(context: Context) {
            val held = destination ?: return
            if (!onCarMap) return
            val app = context.applicationContext
            carBack()
            handOff = Runnable {
                handOff = null
                if (destination !== held) return@Runnable
                onCarMap = false
                ai.aura.app.assist.island.RouteIsland.update(app, held, onCar = false)
                val label = held.optString("label")
                val opened = navigate(app, null, held.optString("app"), held.getDouble("lat"), held.getDouble("lon"), label)
                if (opened.optString("status") == "ok") continueNote(app, opened.optString("app"), held.getDouble("lat"), held.getDouble("lon"), label)
            }.also { handOffs.postDelayed(it, HANDOFF_DELAY_MS) }
        }

        /**
         * The hand-off's route as a tap: Android 15 blocks an activity start from the background
         * unless a window of ours is showing, silently, and a car that just went away usually
         * leaves none (the review of T-0806). Posted beside the start, which may have worked.
         */
        private fun continueNote(context: Context, chosen: String, lat: Double, lon: Double, label: String) {
            val pkg = NAV_APPS.firstOrNull { it.first == chosen }?.second ?: return
            val intent = Intent(Intent.ACTION_VIEW, navUri(chosen, lat, lon, label)).setPackage(pkg).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val tap = android.app.PendingIntent.getActivity(context, HANDOFF_NOTE_ID, intent,
                android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT)
            val note = androidx.core.app.NotificationCompat.Builder(context, ai.aura.app.JarvisApp.CHANNEL_ALERTS)
                .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
                .setColor(Notify.color(Notify.Template.TASK))
                .setContentTitle("Continue to ${label.ifEmpty { "the destination" }}")
                .setContentText("The car disconnected. Tap to keep the route on the phone.")
                .setContentIntent(tap)
                .setAutoCancel(true)
                .build()
            runCatching { context.getSystemService(android.app.NotificationManager::class.java)?.notify(HANDOFF_NOTE_ID, note) }
        }

        private const val HANDOFF_NOTE_ID = 4_806

        /** A car screen came up: the route stays on the car, which reads it from [destination]. */
        fun carBack() {
            handOff?.let(handOffs::removeCallbacks)
            handOff = null
        }

        internal const val HANDOFF_DELAY_MS = 30_000L

        /**
         * A route Jarvis sent (`hud.car.navigate`) to a screen that does not draw routes itself --
         * the phone's popup and assist screen, its home screen, the car's dash tile -- opened where
         * the person is ([route]); the answer is what the waiting `car_navigate` call tells Jarvis.
         * The popup dropped it while Jarvis said the phone had the route open (the owner,
         * 2026-11-09). `cancel` lets go of the held route; a maps app's own route is the driver's
         * to end.
         */
        fun routeEvent(context: Context, payload: JSONObject): JSONObject {
            if (payload.optBoolean("cancel")) {
                destination = null
                ai.aura.app.assist.island.RouteIsland.update(context, null, onCar = false)
                return JSONObject().put("status", "ok")
            }
            val lat = payload.optDouble("lat")
            val lon = payload.optDouble("lon")
            // NaN (a missing field) is outside both ranges too.
            if (lat !in -90.0..90.0 || lon !in -180.0..180.0) return refused("The route had no place to go to")
            val label = if (payload.isNull("label")) "" else payload.optString("label").trim()
            return route(context, CarShell.active(), payload.optString("app"), lat, lon, label)
        }

        private fun opened(where: String, app: String) = JSONObject().put("status", "ok").put("opened", where).put("app", app)

        private fun refused(why: String) = JSONObject().put("status", "error").put("error", why)

        /**
         * Into the car's app pane, then the route: the app opens where the driver
         * can see it, and the intent hands it the destination. From the page's Map
         * panel and from Jarvis (`hud.car.navigate`, `car_navigate`); a new one is
         * a changed route. With no car screen up it opens on the phone.
         */
        fun navigate(context: Context, shell: CarShell?, app: String, lat: Double, lon: Double, label: String): JSONObject {
            val installed = installedNavApps(context)
            val chosen = app.takeIf { it in installed } ?: installed.firstOrNull()
            if (chosen == null) {
                // Said on the phone too: with no car screen up this was silent, and the
                // route looked "sent" to Jarvis and to the driver alike.
                return refused(NO_MAPS_APP).also { say(context, shell, NO_MAPS_APP) }
            }
            val pkg = NAV_APPS.first { it.first == chosen }.second
            shell?.openApp(pkg)
            val intent = Intent(Intent.ACTION_VIEW, navUri(chosen, lat, lon, label)).setPackage(pkg).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val options = ActivityOptions.makeBasic().apply { if (shell != null) setLaunchDisplayId(CarAppDisplay.displayId) }
            return runCatching { context.applicationContext.startActivity(intent, options.toBundle()) }.fold(
                onSuccess = { opened(if (shell != null) "car" else "phone", chosen) },
                onFailure = { refused(MAPS_REFUSED).also { say(context, shell, MAPS_REFUSED) } },
            )
        }

        private const val NO_MAPS_APP = "No maps app is installed: Organic Maps, Google Maps or HERE WeGo"
        private const val MAPS_REFUSED = "That maps app would not take the route"

        private fun say(context: Context, shell: CarShell?, text: String) {
            if (shell != null) {
                shell.showVoiceMessage(text)
            } else {
                android.widget.Toast.makeText(context.applicationContext, text, android.widget.Toast.LENGTH_LONG).show()
            }
        }

        /**
         * Where the car is, for a car turn's prompt, so Jarvis can find the places
         * nearest (`place_search` near it): the phone's last fix, if one is under
         * ten minutes old. Null with no fix or no permission; Jarvis then asks.
         */
        fun whereNow(context: Context): String? {
            val fix = lastLocation(context) ?: return null
            return whereLine(fix.latitude, fix.longitude, if (fix.hasBearing() && fix.speed > 1f) fix.bearing else null, fix.speed)
        }

        /** The phone's last fix under ten minutes old, as (lat, lon): what the route's island measures from. */
        fun lastFix(context: Context): Pair<Double, Double>? = lastLocation(context)?.let { it.latitude to it.longitude }

        /** The newest fix the phone already has, never a new request; null when stale or not allowed. */
        @SuppressLint("MissingPermission")
        private fun lastLocation(context: Context): android.location.Location? {
            val lm = context.getSystemService(LocationManager::class.java) ?: return null
            val granted = listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
                .any { context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }
            if (!granted) return null
            val fix = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                .mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
                .maxByOrNull { it.time } ?: return null
            return fix.takeIf { System.currentTimeMillis() - it.time <= FIX_MAX_AGE_MS }
        }

        private const val FIX_MAX_AGE_MS = 10 * 60_000L

        /** The line a car turn carries: position to about 10 m, and the heading when moving. */
        internal fun whereLine(lat: Double, lon: Double, bearing: Float?, speedMs: Float): String {
            val at = "%.4f,%.4f".format(java.util.Locale.ROOT, lat, lon)
            val moving = bearing?.let { ", heading ${it.toInt()}°, ${(speedMs * 3.6f).toInt()} km/h" } ?: ", parked"
            return "(In the car, at $at$moving. For places, place_search near $at; to go, car_navigate.)"
        }

        /** Google Maps starts turn-by-turn from its own scheme; the others take a geo: point with a label. */
        internal fun navUri(app: String, lat: Double, lon: Double, label: String): Uri = if (app == "google") {
            Uri.parse("google.navigation:q=$lat,$lon")
        } else {
            Uri.parse("geo:$lat,$lon?q=$lat,$lon(${Uri.encode(label)})")
        }

        /** The package an `app` act asks to open (`tests/contracts/car-state.json` `acts`), or null. */
        internal fun appOf(act: JSONObject): String? =
            act.optString("pkg").takeIf { act.optString("kind") == "app" && it.isNotBlank() }

        /** Whether an act is the car HUD asking for the notification-access setting. */
        internal fun wantsNotificationAccess(act: JSONObject): Boolean =
            act.optString("kind") == "access" && act.optString("op") == "notifications"

        /** Whether the page wants the hologram ground under it, or null when the act is not that. */
        internal fun groundOf(act: JSONObject): Boolean? =
            if (act.optString("kind") == "ground") act.optBoolean("on") else null

        /** The page's ask for a route over the packs (`askPackRoute` in car.svelte.ts): its id, from and to; null when either end is missing. */
        internal fun packAskOf(act: JSONObject): Triple<Int, LatLon, LatLon>? {
            fun end(key: String) = act.optJSONArray(key)?.let { LatLon(it.optDouble(0), it.optDouble(1)) }?.takeIf { it.lat.isFinite() && it.lon.isFinite() }
            return Triple(act.optInt("id"), end("from") ?: return null, end("to") ?: return null)
        }

        /**
         * The answer to a pack route ask, as `readPackRoute` reads it (`packRoute` in
         * `tests/contracts/car-state.json`): the path as `[lat, lon]` and each maneuver's place,
         * side and street, which the page words itself. A null [plan] is `route: null`.
         */
        internal fun packRouteJson(id: Int, plan: PackRouter.Plan?): String {
            val out = JSONObject().put("id", id)
            if (plan == null) return out.put("route", JSONObject.NULL).toString()
            val steps = JSONArray()
            plan.turns.forEachIndexed { i, t ->
                val at = t.at ?: return@forEachIndexed
                val last = i == plan.turns.lastIndex
                steps.put(
                    JSONObject().put("lat", at.lat).put("lon", at.lon).put("type", if (last) "arrive" else "turn")
                        .put("modifier", if (last) "" else t.modifier).put("name", t.street),
                )
            }
            val points = JSONArray().also { p -> plan.route.shape.forEach { p.put(JSONArray().put(it.lat).put(it.lon)) } }
            val route = JSONObject().put("points", points).put("steps", steps)
                .put("distance", plan.route.distanceM.roundToInt()).put("duration", plan.route.durationS.roundToInt())
            return out.put("route", route).toString()
        }

        /**
         * The page's route for the ground (`groundRoute` in car.svelte.ts): its points as
         * `[lat, lon]`, the street the car is on and the next turn. Null clears the ground's
         * route: no points (arrived, cancelled), or fewer than the two a ribbon needs.
         */
        internal fun routeOf(act: JSONObject): HoloRoute? {
            val points = act.optJSONArray("points") ?: return null
            val shape = (0 until points.length()).mapNotNull { i ->
                points.optJSONArray(i)?.let { p -> LatLon(p.optDouble(0), p.optDouble(1)) }?.takeIf { !it.lat.isNaN() && !it.lon.isNaN() }
            }
            if (shape.size < 2) return null
            val turn = act.optJSONObject("turn")
            val turnAt = turn?.let { LatLon(it.optDouble("lat"), it.optDouble("lon")) }?.takeIf { !it.lat.isNaN() && !it.lon.isNaN() }
            return HoloRoute(shape, act.optString("street"), turnAt, turn?.optString("street")?.ifBlank { null })
        }

        /** The JSON `car.svelte.ts` `readCarState` reads; pure, so a unit test holds the two to one shape. */
        internal fun state(
            media: Playing?,
            call: LiveCall?,
            messages: List<CarMessage>,
            place: Place?,
            navApps: List<String>,
            voice: CarVoiceView,
            notice: CarNotice? = null,
            music: CarMusicLevel.Snapshot? = null,
            ask: Triple<String, String, String>? = null,
            /** The car's own speedometer, m/s, when its sensor channel reports one. */
            carSpeed: Double? = null,
            /** Notification access: without it media, messages and the notice are blind, not empty. */
            notificationAccess: Boolean = true,
            /** A screen-sharing grant, which an app opened on the car needs to be seen there. */
            capture: Boolean = true,
            /** The driver's apps, package to label (`CarApps`). */
            apps: List<Pair<String, String>> = emptyList(),
            /** This phone draws the hologram ground under the page (GroundView). */
            ground: Boolean = true,
        ): String = JSONObject().apply {
            put("carSpeed", carSpeed ?: JSONObject.NULL)
            put("media", media?.let {
                JSONObject().put("title", it.title).put("artist", it.artist).put("app", it.app).put("playing", it.playing)
                    .put("position", it.positionMs / 1000).put("duration", it.durationMs / 1000)
            } ?: JSONObject.NULL)
            put("call", call?.let {
                JSONObject().put("state", if (it.ringing) "ringing" else "active").put("name", it.who).put("number", "")
                    .put("photo", it.photo).put("since", it.since ?: JSONObject.NULL)
            } ?: JSONObject.NULL)
            put("messages", JSONArray().apply {
                messages.forEach { m ->
                    put(JSONObject().put("id", m.key).put("from", m.sender).put("app", m.packageName).put("text", m.text).put("at", m.postedAtMillis))
                }
            })
            put("place", place?.let {
                JSONObject().put("lat", it.lat).put("lon", it.lon).put("heading", it.heading ?: JSONObject.NULL).put("speed", it.speed)
            } ?: JSONObject.NULL)
            put("navApps", JSONArray(navApps))
            put("voice", JSONObject().put("phase", voice.phase.name.lowercase()).put("heard", voice.heard)
                .put("answer", voice.answer).put("caption", voice.caption))
            put("notice", notice?.let {
                JSONObject().put("id", it.key).put("app", it.appLabel).put("title", it.title).put("text", it.text).put("at", it.postedAtMillis)
            } ?: JSONObject.NULL)
            put("destination", destination ?: JSONObject.NULL)
            put("ask", ask?.let { JSONObject().put("id", it.first).put("title", it.second).put("detail", it.third) } ?: JSONObject.NULL)
            put("music", music?.let { JSONObject().put("pulse", it.pulse).put("level", it.level.toDouble()) } ?: JSONObject.NULL)
            put("access", JSONObject().put("notifications", notificationAccess).put("capture", capture).put("ground", ground))
            put("apps", JSONArray().apply { apps.forEach { (pkg, label) -> put(JSONObject().put("pkg", pkg).put("label", label)) } })
        }.toString()
    }
}
