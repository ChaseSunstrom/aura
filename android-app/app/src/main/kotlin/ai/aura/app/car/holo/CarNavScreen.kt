package ai.aura.app.car.holo

import ai.aura.app.R
import ai.aura.app.assist.JarvisConversation
import ai.aura.app.assist.Phase
import ai.aura.app.assist.ToolRun
import ai.aura.app.assist.hermes.HudState
import ai.aura.app.car.voice.HermesCarJarvis
import ai.aura.app.config.JarvisConfig
import ai.aura.app.glass.ReactorFeed
import ai.aura.app.reactor.ReactorInputs
import android.Manifest
import android.content.pm.PackageManager
import android.location.Location
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.car.app.AppManager
import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.SurfaceCallback
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.Template
import androidx.car.app.navigation.model.NavigationTemplate
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import org.json.JSONObject
import java.util.Date
import java.util.Locale

/**
 * Jarvis on the head unit as a navigation app (T-0613): a NavigationTemplate whose map
 * is the hologram drive, drawn by this app on the host's surface.
 *
 * ## The template and the surface
 *
 * The template is only the frame the host owns: the action strip (Talk, and the
 * actions of whatever card is showing, so every drawn button is also a host button on
 * a car that never sends taps). Everything else, the drive, the zones, the core and
 * the card, is drawn on [surface]. No `RoutingInfo` is set, because the host would
 * paint its own turn card over the drawn turn zone.
 *
 * ## Where the car is and where it goes
 *
 * The phone's GPS feeds [CarSurface.position] while the screen is started, with the
 * house's map-matching and buildings ([DriveFeed], which the wireless car's ground shares).
 * The destination is Jarvis's (`car_navigate`, [onNavigate]); [RouteGuide] decides
 * when to ask the house for a route, and [HouseMaps] asks. With no house maps (a Hermes-only
 * pairing, or a house without the endpoint) the drive still shows the car and says the
 * maps are away.
 *
 * ## Voice
 *
 * Every action is also words: the card's buttons by their label or what people say for
 * them, "reroute" and "next turn" ([DriveWords], [onHeard]), done here without a turn to
 * Jarvis. An approval (a tool Jarvis wants to run) is the card, answered by touch or
 * voice, and refused after [APPROVAL_MS] on screen or when the conversation or the screen ends.
 * The countdown runs only while the card holds the slot: behind a call it waits, and it starts
 * over each time it comes back (T-0663). A request hidden for a whole call can still be ended
 * by Hermes' own approval timeout, which this does not cover.
 *
 * ## The microphone
 *
 * Not opened by arriving here. Talk starts a conversation, the wake word still works
 * from the phone, and leaving the screen stops anything this screen started: a car
 * that disconnects must not leave a microphone open.
 *
 * ## The refresh budget
 *
 * Template pushes are still rationed by the host, so [render] pushes only when the
 * strip would change; words and state reach the driver through the surface, which is
 * not rationed.
 *
 * Not covered: an approval answered on another surface leaves its card here until it
 * times out; the turn zone's distance moves once a fix, not once a frame.
 */
class CarNavScreen(
    carContext: CarContext,
    /** How the surface reaches the host; a seam because no host is bound in a unit test. */
    private val registerSurface: (SurfaceCallback?) -> Unit = {
        carContext.getCarService(AppManager::class.java).setSurfaceCallback(it)
    },
    /** Where a route ask goes when the house gave none: (from, to, the kept line, answer). A seam so a test sees what the router is asked (T-1088); null is the feed's pack router. */
    private val packRoute: ((LatLon, LatLon, HouseRoute?, (HouseRoute?) -> Unit) -> Unit)? = null,
) : Screen(carContext), JarvisConversation.Ui, DefaultLifecycleObserver {

    private val config = JarvisConfig(carContext.applicationContext)

    /** The road signs of the driver's locale, read once: a drive does not change country. */
    private val units = CarUnits.of(carContext.resources.configuration.locales[0])
    private val main = Handler(Looper.getMainLooper())
    private var convo: JarvisConversation? = null

    private var reactor: ReactorInputs = ReactorFeed.inputsFrom(Phase.IDLE)
    private var transcript = ""
    private var response = ""
    private var card: CarCard? = null
    private var onCardAction: (CarAction) -> Unit = {}
    private var approvalReply: ((Boolean) -> Unit)? = null

    /** Every card, the approval included, reaches the one slot through here. */
    private val cards = CarCards { next, onAction ->
        card = next
        onCardAction = onAction
        render()
    }

    /** The call, message, next event, music and arrival; live from [onStart] to [onStop]. */
    private val sources = CarSources(carContext, cards, main, units) { if (!isTalking()) toggleTalking() }

    /** The map surface. Claimed in [onCreate], released in [onDestroy]. */
    val surface = CarSurface { act(it) }

    private val guide = RouteGuide(store = RouteGuide.storeIn(carContext)) { route ->
        surface.scene { it.setRoute(route) }
        feed.ahead(route?.shape)
    }
    private val feed = DriveFeed(carContext, surface.position, { edit -> surface.scene(edit) }, ::onFix)
    /** GPS was asked for and refused (no permission, no provider); not before the first start. */
    private var noLocation = false
    private var speedMps: Float? = null

    /**
     * The last strip actually handed to the host: an identical template is a no-op
     * rather than a spent refresh.
     */
    private var lastRendered: List<Any?> = signature()

    init {
        lifecycle.addObserver(this)
    }

    override fun onCreate(owner: LifecycleOwner) {
        registerSurface(surface)
        // A head-unit reconnect or an app restart makes a new screen: the drive's route comes back
        // from the phone, so guidance does not wait on a house that may be away.
        guide.restore()
        render()
    }

    override fun onStart(owner: LifecycleOwner) {
        val maps = HouseMaps.of(config)
        noLocation = !feed.start(maps)
        sources.start()
        render()
    }

    override fun onStop(owner: LifecycleOwner) {
        // The screen is gone; nothing it started may outlive it.
        stopTalking()
        // Their cards go with them: a call card left up would outlive the call.
        sources.stop()
        feed.stop()
        // Parked at the pin: the next session must not restore a drive that is over.
        guide.stopped()
        speedMps = null
    }

    override fun onDestroy(owner: LifecycleOwner) {
        stopTalking()
        // A second session registers its own; this one must not be drawn through.
        registerSurface(null)
        // An answer still on its way must not write the route store the next session reads.
        guide.close()
        surface.release()
    }

    /**
     * Put [card] in the one card slot (null empties it). Its actions are drawn on it, put
     * on the strip, and each comes back to [onAction] however the driver chose it.
     */
    fun show(card: CarCard?, onAction: (CarAction) -> Unit = {}) {
        if (card == null) cards.clearAll() else cards.set(card, onAction = onAction)
    }

    override fun onGetTemplate(): Template {
        val strip = ActionStrip.Builder()
            .addAction(
                Action.Builder()
                    .setTitle(text(if (isTalking()) R.string.car_screen_stop else R.string.car_screen_talk))
                    .setOnClickListener { toggleTalking() }
                    .build()
            )
        for (a in cardActions()) {
            strip.addAction(Action.Builder().setTitle(a.label).setOnClickListener { act(a) }.build())
        }
        return NavigationTemplate.Builder().setActionStrip(strip.build()).build()
    }

    /** The strip holds four, and Talk is one of them. */
    private fun cardActions() = card?.actions.orEmpty().take(CarDrive.MAX_ACTIONS)

    private fun act(action: CarAction) {
        // A tap that lands after the card changed is for a card no longer shown.
        if (action in cardActions()) onCardAction(action)
    }

    private fun isTalking(): Boolean = convo?.isRunning == true

    private fun text(id: Int): String = carContext.getString(id)

    private fun toggleTalking() {
        if (isTalking()) {
            stopTalking()
            render()
            return
        }
        // The reason Talk did nothing is drawn in the subtitle zone with the replies (T-1335), not in
        // a pop-up a head unit may hide; the next talk that starts clears it with the last reply.
        if (!config.isConfigured) return refuse(R.string.car_screen_not_set_up_body)
        // The car host cannot grant this and must not pretend to. RECORD_AUDIO is
        // granted on the phone, and a driver who has not done that is told so rather
        // than given a button that silently does nothing.
        if (carContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) return refuse(R.string.car_screen_needs_mic)
        transcript = ""
        response = ""
        convo = JarvisConversation(
            carContext.applicationContext,
            config,
            this,
            inactivityMs = CAR_INACTIVITY_MS,
        ).also { it.start() }
        render()
    }

    /** Talk did nothing: the reason replaces the last exchange, so a stale quote does not sit above it. */
    private fun refuse(reason: Int) {
        transcript = ""
        response = text(reason)
        render()
    }

    private fun stopTalking() {
        // An approval on the card would wait on a conversation that is gone: refuse it.
        approvalReply?.invoke(false)
        convo?.stop()
        convo = null
    }

    // --- position, route, buildings ------------------------------------------

    /** A fix the feed took: the turn ahead, the way home and the zones, from it. */
    private fun onFix(loc: Location, here: LatLon, now: Long) {
        speedMps = if (loc.hasSpeed()) loc.speed else null
        guide.onPosition(here, now)?.let(::ask)
        sources.home(guide.label, guide.progress(here)?.remainingM)
        render()
    }

    private fun here(now: Long): LatLon? = surface.position.at(now)?.let { LatLon(it.lat, it.lon) }

    /** The house first, then the roads of the packs the phone holds when it does not answer (T-1026, [RouteAsk]). */
    private val routeAsk = RouteAsk(
        clock = SystemClock::uptimeMillis,
        house = { from, to, answer, nothing -> feed.maps?.let { it.route(from, to, answer, nothing); true } ?: false },
        packs = { from, to, answer ->
            val kept = guide.route
            if (packRoute != null) packRoute.invoke(from, to, kept, answer) else feed.routeFromPacks(from, to, kept, answer)
        },
    )

    private fun ask(request: RouteGuide.Request) {
        routeAsk.ask(
            request.from, request.to,
            onRoute = { guide.onRoute(request.id, it); render() },
            onFailed = { guide.onFailed(request.id, noRoute = it); render() },
        )
    }

    // --- JarvisConversation.Ui ----------------------------------------------

    /** The core follows [onReactor], not the phase; the caption has no zone on the drive. */
    override fun onMode(mode: Phase, label: String) = Unit

    override fun onReactor(inputs: ReactorInputs) {
        reactor = inputs
        render()
    }

    /** Per audio buffer: read by the next frame, never a template push. */
    override fun onAmplitude(level: Float) {
        surface.level = level
    }

    override fun onTranscript(text: String) {
        transcript = text
        render()
    }

    override fun onResponse(text: String) {
        response = text
        render()
    }

    override fun onError(message: String) {
        reactor = ReactorFeed.inputsFrom(Phase.ERROR)
        response = message
        render()
    }

    override fun onIdle() {
        approvalReply?.invoke(false)
        convo = null
        reactor = ReactorFeed.inputsFrom(Phase.IDLE)
        surface.level = 0f
        render()
    }

    /** A list that grows while the car moves is what the distraction rules forbid. */
    override fun onTools(run: ToolRun) = Unit

    /** The card, not the phone's full-screen consent door: the driver cannot read that. */
    override fun onApproval(approval: HudState.Approval, answer: (String) -> Unit): Boolean {
        // A newer question replaces the card; the older one is refused, not left waiting.
        approvalReply?.invoke(false)
        lateinit var reply: (Boolean) -> Unit
        val timeout = Runnable { reply(false) }
        reply = { allowed ->
            if (approvalReply === reply) {
                approvalReply = null
                main.removeCallbacks(timeout)
                cards.clear(CarCard.Kind.APPROVAL)
                answer(HermesCarJarvis.choiceFor(approval, allowed))
            }
        }
        approvalReply = reply
        cards.set(
            CarCard(
                CarCard.Kind.APPROVAL,
                caption = text(R.string.car_card_approval),
                title = approval.tool,
                detail = approval.reason,
                actions = listOf(
                    CarAction(ALLOW, text(R.string.car_card_allow), primary = true),
                    CarAction(DENY, text(R.string.car_card_deny)),
                ),
            ),
            onShown = { onScreen ->
                main.removeCallbacks(timeout)
                if (onScreen && approvalReply === reply) main.postDelayed(timeout, APPROVAL_MS)
            },
        ) { reply(it.id == ALLOW) }
        return true
    }

    /** `text` is the parameter here, so the replies use [CarContext.getString] directly. */
    override fun onHeard(text: String): String? {
        DriveWords.action(text, cardActions())?.let {
            act(it)
            return ""
        }
        return when (DriveWords.command(text)) {
            DriveWords.Command.REROUTE -> {
                if (guide.destination == null) return carContext.getString(R.string.car_drive_no_route_set)
                val now = SystemClock.uptimeMillis()
                val here = here(now) ?: return carContext.getString(R.string.car_drive_no_fix)
                guide.reroute(here, now)?.let(::ask)
                render()
                carContext.getString(R.string.car_drive_rerouting)
            }
            DriveWords.Command.NEXT_TURN -> {
                val turn = turn(SystemClock.uptimeMillis())
                    ?: return carContext.getString(R.string.car_drive_no_route_set)
                carContext.getString(
                    R.string.car_drive_next_turn,
                    turn.distance,
                    turn.instruction.replaceFirstChar { it.lowercase(Locale.ROOT) },
                )
            }
            null -> null
        }
    }

    override fun onNavigate(payload: JSONObject): Boolean {
        val lat = payload.optDouble("lat")
        val lon = payload.optDouble("lon")
        // NaN (a missing field) is outside both ranges too.
        if (lat !in -90.0..90.0 || lon !in -180.0..180.0) return false
        val label = if (payload.isNull("label")) "" else payload.optString("label").trim()
        val now = SystemClock.uptimeMillis()
        guide.go(LatLon(lat, lon), label, here(now), now)?.let(::ask)
        render()
        return true
    }

    // --- drawing --------------------------------------------------------------

    /** The turn zone's next maneuver; with no maneuver known, the destination itself. */
    private fun turn(now: Long): DriveView.Turn? {
        val route = guide.route ?: return null
        val progress = here(now)?.let { guide.progress(it) } ?: return null
        return DriveView.Turn(
            distance = units.distance(progress.turnM ?: progress.remainingM),
            instruction = route.turn?.instruction?.ifBlank { null } ?: guide.label,
            sign = surface.scene { it.turnSign },
        )
    }

    private fun trip(now: Long): DriveView.Trip? {
        if (guide.route == null) return null
        val progress = here(now)?.let { guide.progress(it) } ?: return null
        val eta = android.text.format.DateFormat.getTimeFormat(carContext)
            .format(Date(System.currentTimeMillis() + (progress.remainingS * 1000f).toLong()))
        val detail = carContext.getString(
            R.string.car_drive_trip_detail,
            RouteGuide.duration(progress.remainingS),
            units.distance(progress.remainingM),
        )
        return DriveView.Trip(guide.label, eta, detail)
    }

    /** Said in the turn zone when there is no turn. */
    private fun status(fixed: Boolean): String = when {
        noLocation -> text(R.string.car_drive_needs_location)
        !fixed -> text(R.string.car_drive_no_fix)
        guide.destination == null || guide.route != null -> ""
        guide.failure == RouteGuide.Failure.NO_ROUTE && !guide.routing -> text(R.string.car_drive_no_route)
        guide.failure == RouteGuide.Failure.UNAVAILABLE && !guide.routing -> text(R.string.car_drive_maps_away)
        else -> text(R.string.car_drive_routing)
    }

    /** Hand the surface what to draw; push a template only if the strip would differ. */
    private fun render() {
        val now = SystemClock.uptimeMillis()
        surface.view = DriveView(
            reactor = reactor,
            turn = turn(now),
            status = status(surface.position.at(now) != null),
            trip = trip(now),
            speed = speedMps?.let(units::speed),
            speedUnit = text(if (units.imperial) R.string.car_drive_mph else R.string.car_drive_kmh),
            heard = transcript,
            said = response,
            card = card,
        )
        val signature = signature()
        if (signature == lastRendered) return
        lastRendered = signature
        invalidate()
    }

    /** What the strip shows: equal signatures are the same template. */
    private fun signature(): List<Any?> = listOf(isTalking(), cardActions())

    companion object {
        /**
         * The car API this screen is written against, and the manifest's
         * `minCarApiLevel`. NavigationTemplate and the surface are level 1; taps on the
         * surface (`onClick`, level 5) are optional because the strip mirrors them.
         */
        const val MIN_CAR_API = 1

        /** Longer than the phone's, because a driver takes longer to answer; still bounded. */
        private const val CAR_INACTIVITY_MS = 15_000L

        /** CarTurn's: an unanswered approval is a refusal, never a silent yes. */
        const val APPROVAL_MS = 30_000L

        private const val ALLOW = "allow"
        private const val DENY = "deny"
    }
}
