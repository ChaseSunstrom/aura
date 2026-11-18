package ai.aura.app.channel

import ai.aura.app.BuildConfig
import ai.aura.app.config.JarvisConfig
import ai.aura.app.config.VoiceIdentityClient
import ai.aura.app.automation.AutomationRuntime
import ai.aura.app.tasks.TaskNotifier
import ai.aura.app.tasks.TaskOverlay
import ai.aura.app.tasks.TaskWatch
import ai.aura.app.ui.ApprovalBridge
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.PowerManager
import android.util.Log

/**
 * The one place in the shipping app that owns a [JarvisChannel].
 *
 * ## Why this file exists
 *
 * The device channel is the transport that carries `device_command` from
 * jarvis-core to this phone and `device_result` back. Without it the phone is
 * not answering "unsupported" to the server — it is not being asked anything at
 * all, because there is no socket to ask it on. Every one of the 48 registered
 * actions was unreachable in a shipping build for exactly this reason: nothing
 * constructed `JarvisChannel`. Its only callers were the debug-only
 * `TestHooks.startChannel` and the instrumented suite, which is precisely how a
 * green `DeviceChannelTest` and a dead device channel coexisted — the test
 * started the channel itself, so it proved the protocol and nothing about
 * startup.
 *
 * ## The wiring, and the one trap in it
 *
 * [AutomationRuntime] leaves two slots for whoever owns the socket,
 * `deviceEvents` and `askJarvis`, both defaulting to no-ops so the phone still
 * runs its automations, enforces its policy and writes its audit log with no
 * server attached. [DeviceLink] fills both.
 *
 * Because it does, the channel is started with `subscribeToBridgeEvents =
 * false`. `JarvisChannel.start` otherwise ALSO subscribes itself to
 * `AutomationBridge.publishEvent`, and every trigger event would reach the
 * server twice — once through the bridge and once through the link. This is
 * called out at `AutomationBridge.publishEvent` and on [DeviceLink]; it is the
 * kind of thing that looks like a server bug for a week.
 *
 * ## Lifetime
 *
 * Owned by `JarvisAutomationService`, which is the process's long-lived
 * component: the channel has to outlive any Activity, and a socket held by a
 * background process on GrapheneOS lives for seconds. An unconfigured phone is
 * fine to start — the channel's connect loop parks in BLOCKED and re-reads the
 * configuration on a timer, so setting a server URL in Settings brings it up
 * without restarting anything.
 */
object DeviceChannelHost {

    private const val TAG = "JarvisChannelHost"

    @Volatile
    private var channel: JarvisChannel? = null

    /**
     * The floating chip, and the notification behind it.
     *
     * Both live here rather than in an Activity for the same reason the channel
     * does: the case they exist for is a research run that starts while the
     * phone is in a pocket, and an Activity is not running then. Neither draws
     * anything until there is work to draw, so starting them costs a listener.
     */
    private var overlay: TaskOverlay? = null
    private var unlistenTasks: (() -> Unit)? = null
    private var callObserver: ai.aura.app.car.phone.CarPhone.Listener? = null
    private var mirrorObserver:((List<ai.aura.app.car.dash.tiles.CarMessage>) -> Unit)? = null

    /** The live channel, for the settings screen's status readout. */
    fun channel(): JarvisChannel? = channel

    /** True once [start] has built a channel that has not been [stop]ped. */
    val isStarted: Boolean get() = channel != null

    /**
     * Ask the server for the speaker gate's mode the moment the socket has
     * registered (M98), and cache it the way the enrolment screen does.
     *
     * `speakerGateEnforcing` defaults false and was written only after the
     * Whose-voice screen asked; a phone that never opened it, against a house
     * that enforces, sent audio-derived turns the server refused while its
     * own Settings said "speech is turned into text on this phone" (the
     * Android audit, 27 Aug 2026). Off the main thread: it is a GET.
     */
    private fun refreshSpeakerGate(context: Context) {
        Thread({
            try {
                val config = JarvisConfig(context)
                when (val result = VoiceIdentityClient(config.serverUrl, config.token).status()) {
                    is VoiceIdentityClient.Result.Ok -> {
                        val fresh = result.value
                        config.speakerGateEnforcing = fresh.mode == "enforce" && fresh.enrolled
                        Log.i(TAG, "speaker gate: enforcing=${config.speakerGateEnforcing}")
                    }
                    else -> Log.w(TAG, "speaker gate mode could not be read; keeping the cached value")
                }
            } catch (t: Throwable) {
                Log.w(TAG, "speaker gate refresh failed", t)
            }
        }, "jarvis-gate-refresh").start()
    }

    /**
     * Build the channel, plug it into the automation seams, and start it.
     *
     * Idempotent and safe to call from any component's startup path.
     */
    @Synchronized
    fun start(context: Context) {
        if (channel != null) return
        val app = context.applicationContext
        val built = try {
            JarvisChannel(
                context = app,
                configProvider = { ChannelConfig.from(app, BuildConfig.VERSION_NAME) },
                afterRegistered = { refreshSpeakerGate(app); ai.aura.app.routines.RoutineWatcher.onChannelReady() },
            )
        } catch (t: Throwable) {
            // A phone that cannot open its command channel must still run its
            // local automations rather than take the service down with it.
            Log.e(TAG, "could not build the device channel", t)
            return
        }
        val link = DeviceLink(built, app)
        AutomationRuntime.deviceEvents = link
        AutomationRuntime.askJarvis = link
        channel = built
        // Untrusted: a prompt's parameters can carry text somebody else wrote (a reply's body).
        ApprovalBridge.consoleRoute = { event, data -> built.sendEvent(event, data, untrusted = true) }
        // A one-time code goes only over a live socket: sendEvent queues offline, and a queued
        // code would sit on the phone and arrive after it expired.
        ai.aura.app.routines.RoutineWatcher.codeSink = { data ->
            if (built.isRegistered) built.sendEvent("otp_received", data, untrusted = true)
        }
        // Texts and chat notifications for the desktops: also live-socket only, and only after the
        // owner's one-time yes (MessageMirror). The listener replays its feed on observe; that first
        // call is the baseline, not news.
        ai.aura.app.messages.MessageMirror.reset()
        ai.aura.app.messages.MessageMirror.ask = { c, p -> ai.aura.app.messages.MessageMirror.askOwner(c, p) }
        ai.aura.app.messages.ReplyAction.smsSender = { c, n, b -> ai.aura.app.messages.ReplyAction.sendSms(c, n, b) }
        ai.aura.app.messages.ReplyAction.appFinder = { p, s -> ai.aura.app.messages.ReplyAction.findApp(p, s) }
        ai.aura.app.messages.ReplyAction.appSender = { c, m, t -> ai.aura.app.messages.ReplyAction.sendApp(c, m, t) }
        ai.aura.app.messages.MessageMirror.sink = { data ->
            if (built.isRegistered) built.sendEvent(ai.aura.app.messages.MessageMirror.EVENT, data, untrusted = true)
        }
        // Calls: the same live-socket-only rule. The commands are the owner's words relayed by the
        // house; the real answer/hang-up/dial are CarPhone's, the number lookups the contact book's.
        ai.aura.app.calls.CallEvents.reset()
        ai.aura.app.calls.CallEvents.lookup = { c, n -> ai.aura.app.calls.CallActions.realLookup(c, n) }
        ai.aura.app.calls.CallActions.numberFor = { c, n -> ai.aura.app.calls.CallActions.realNumberFor(c, n) }
        ai.aura.app.calls.CallAnswer.answerer = { c -> ai.aura.app.car.phone.CarPhone.answer(c) }
        ai.aura.app.calls.CallDecline.hanger = { c -> ai.aura.app.car.phone.CarPhone.hangUp(c) }
        ai.aura.app.calls.CallDecline.texter = { c, n, t ->
            ai.aura.app.messages.ReplyAction.sendSms(c, n, t).ok
        }
        ai.aura.app.calls.CallBack.dialer = { c, n -> ai.aura.app.car.phone.CarPhone.dial(c, n) { } }
        ai.aura.app.calls.CallPolicy.rule = ai.aura.app.calls.CallActions.savedRule(app)
        ai.aura.app.calls.CallPolicy.store = { c, r -> ai.aura.app.calls.CallActions.saveRule(c, r) }
        ai.aura.app.calls.CallEvents.sink = { data ->
            if (built.isRegistered) built.sendEvent(ai.aura.app.calls.CallEvents.EVENT, data, untrusted = true)
        }
        val callListener = ai.aura.app.car.phone.CarPhone.Listener { ai.aura.app.calls.CallEvents.onCall(app, it) }
        callObserver = callListener
        ai.aura.app.car.phone.CarPhone.observe(callListener)
        val mirror: (List<ai.aura.app.car.dash.tiles.CarMessage>) -> Unit = { ai.aura.app.messages.MessageMirror.onMessages(app, it) }
        mirrorObserver = mirror
        ai.aura.app.car.dash.tiles.HeadwayNotificationListener.observe(mirror)
        try {
            // false: DeviceLink is the event path. See the class KDoc.
            built.start(subscribeToBridgeEvents = false)
        } catch (t: Throwable) {
            Log.e(TAG, "could not start the device channel", t)
            stop()
            return
        }
        startTaskSurfaces(app)
        listenForWake(app)
        Log.i(TAG, "device channel started")
    }

    private var wakeReceiver: BroadcastReceiver? = null
    private var wakeContext: Context? = null

    /** [WakeNudge]: the phone woke, so the house link is checked now, not after Doze's delay. */
    private fun listenForWake(app: Context) {
        if (wakeReceiver != null) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                val idle = runCatching { context.getSystemService(PowerManager::class.java)?.isDeviceIdleMode }.getOrNull() == true
                if (WakeNudge.wanted(intent.action, idle)) channel?.nudge(intent.action ?: "wake")
            }
        }
        val filter = IntentFilter().apply { WakeNudge.actions.forEach { addAction(it) } }
        runCatching {
            if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
            else app.registerReceiver(receiver, filter)
            wakeReceiver = receiver
            wakeContext = app
        }.onFailure { Log.w(TAG, "could not listen for the phone waking", it) }
    }

    /**
     * Show long work wherever the phone can.
     *
     * Two surfaces, deliberately not exclusive. The overlay is the good one and
     * has two hard limits — it needs SYSTEM_ALERT_WINDOW, and it is never drawn
     * above the keyguard — and both of them bite in exactly the situation this
     * is for. The notification has neither limit. Running both means an OEM
     * that suppresses overlays degrades to something rather than to nothing.
     */
    private fun startTaskSurfaces(app: Context) {
        if (unlistenTasks != null) return
        val chip = TaskOverlay(app)
        overlay = chip
        runCatching { chip.start() }
            .onFailure { Log.w(TAG, "the task overlay would not start", it) }
        unlistenTasks = TaskWatch.listen { rows ->
            TaskNotifier.render(app, rows, TaskWatch.headline())
        }
    }

    /** Close the socket and clear the seams. Safe when there is no channel. */
    @Synchronized
    fun stop() {
        val existing = channel ?: return
        channel = null
        wakeReceiver?.let { r -> runCatching { wakeContext?.unregisterReceiver(r) } }
        wakeReceiver = null
        wakeContext = null
        AutomationRuntime.deviceEvents = null
        AutomationRuntime.askJarvis = null
        ApprovalBridge.consoleRoute = null
        ai.aura.app.routines.RoutineWatcher.codeSink = null
        ai.aura.app.messages.MessageMirror.sink = null
        ai.aura.app.calls.CallEvents.sink = null
        callObserver?.let { ai.aura.app.car.phone.CarPhone.unobserve(it) }
        callObserver = null
        mirrorObserver?.let { ai.aura.app.car.dash.tiles.HeadwayNotificationListener.unobserve(it) }
        mirrorObserver = null
        unlistenTasks?.invoke()
        unlistenTasks = null
        overlay?.stop()
        overlay = null
        // The board is not cleared. It is the last true thing anybody knew, and
        // a reconnect replaces it wholesale with a fresh listing.
        TaskWatch.onDisconnected()
        runCatching { existing.stop() }
            .onFailure { Log.w(TAG, "device channel stop failed", it) }
    }

    /**
     * The configuration changed (a new server URL or token). Ask the connect
     * loop to retry now rather than finish its backoff; it re-reads the
     * configuration on every attempt.
     */
    fun configChanged() {
        channel?.wake()
    }
}
