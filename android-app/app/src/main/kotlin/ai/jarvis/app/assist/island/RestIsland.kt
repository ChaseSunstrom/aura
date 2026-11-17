package ai.jarvis.app.assist.island

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.assist.AssistOverlay
import ai.jarvis.app.assist.island.IslandRest.Network
import ai.jarvis.app.assist.island.IslandRest.Presence
import ai.jarvis.app.car.dash.tiles.HeadwayNotificationListener
import ai.jarvis.app.car.dash.tiles.IslandIcon
import ai.jarvis.app.car.input.HeadwayAccessibilityService
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisTokens
import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.net.ConnectivityManager
import android.net.Network as Link
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.text.format.DateFormat
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import java.util.Date
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/**
 * The resting island's window (T-0890), held by the wake service for as long as it runs. It
 * shows while its setting is on, the conversation's island is not up, the status bar shows
 * (another app's full screen hides it, and it comes back with the bar) and the screen is on.
 * It is never touched: the status bar's pull goes through it.
 *
 * Battery: it draws only when what it says changes, and with the status option on it reads the
 * phone from the system's own broadcasts and one network callback, all of them dropped while the
 * screen is off. Nothing here polls or runs a timer.
 *
 * An app overlay, so the system's status icons still draw over the bar either side of it. With the
 * cover on (T-0969, off by default, and only with the status shown) and the car's touch service
 * granted, it is an accessibility overlay instead, over a band in its ground that hides the bar's
 * icons: the band leaves the corner where Android draws its camera and microphone dot open, and
 * both step aside while System UI (the shade, the lock screen) is in front. Does not cover a phone
 * whose maker keeps app overlays out of the status bar's strip (there it rests just under it), a dot
 * drawn outside that corner, or a shade that announces no closing (the cover then comes back with
 * the next app).
 */
class RestIsland(
    private val context: Context,
    private val config: JarvisConfig = JarvisConfig(context),
) {
    /** The worn persona's name; its pack is [ai.jarvis.app.ui.theme.ActiveTheme]'s, re-read as the island draws. */
    var persona: String = ""
        set(v) {
            field = v
            view?.invalidate()
            draw()
        }
    var presence: Presence = Presence.LISTENING
        set(v) {
            field = v
            draw()
        }

    private val main = Handler(Looper.getMainLooper())
    private var started = false
    private var conversation = false
    private var statusBar = true
    private var screenOn = context.getSystemService(PowerManager::class.java)?.isInteractive ?: true
    private var view: RestView? = null
    private var windows: WindowManager? = null

    private var reading = false
    private var battery: Int? = null
    private var charging = false
    private var network = Network.NONE
    private var wifiBars = 0
    /** The apps with a notification showing (T-0969), followed while the status is read. */
    private var icons: List<Drawable> = emptyList()
    private val iconsSeen: (List<IslandIcon>) -> Unit = { list ->
        main.post {
            icons = list.mapNotNull { it.icon }
            draw()
        }
    }
    private var params: WindowManager.LayoutParams? = null
    private var at: IslandRest.At? = null
    /** The windows are the cover's (accessibility overlays over [band]), not an app overlay. */
    private var covered = false
    private var band: View? = null
    private var follow: Job? = null

    /** The window's view, for the tests. */
    val viewForTest: View? get() = view
    val bandForTest: View? get() = band

    // Held here: SharedPreferences keeps its listeners weakly.
    private val settings = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key in JarvisConfig.ISLAND_KEYS) sync()
    }

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            // Unlocked: the cover may come back (it stays aside on the lock screen).
            if (intent.action != Intent.ACTION_USER_PRESENT) screenOn = intent.action == Intent.ACTION_SCREEN_ON
            sync()
        }
    }

    private val status = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_BATTERY_CHANGED) charge(intent)
            draw()
        }
    }

    private val link = object : ConnectivityManager.NetworkCallback() {
        override fun onCapabilitiesChanged(n: Link, caps: NetworkCapabilities) {
            val wifi = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
            val next = if (wifi) Network.WIFI else if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) Network.CELL else Network.NONE
            val bars = if (wifi) IslandRest.wifiBars(caps.signalStrength) else 0
            main.post { network = next; wifiBars = bars; draw() }
        }

        override fun onLost(n: Link) {
            main.post { network = Network.NONE; draw() }
        }
    }

    fun start() {
        if (started) return
        started = true
        config.watch(settings)
        (context.applicationContext as? JarvisApp)?.onOwnScreens = { sync() }
        context.registerReceiver(
            screen,
            IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(Intent.ACTION_SCREEN_OFF)
                addAction(Intent.ACTION_USER_PRESENT)
            },
            Context.RECEIVER_NOT_EXPORTED,
        )
        // The cover's host comes and goes with its grant, and System UI with the shade.
        follow = CoroutineScope(Dispatchers.Main.immediate).launch {
            combine(HeadwayAccessibilityService.instance, HeadwayAccessibilityService.systemInFront) { s, front -> (s != null) to front }
                .distinctUntilChanged()
                .collect { sync() }
        }
        sync()
    }

    fun stop() {
        if (!started) return
        started = false
        config.unwatch(settings)
        (context.applicationContext as? JarvisApp)?.onOwnScreens = null
        runCatching { context.unregisterReceiver(screen) }
        follow?.cancel()
        follow = null
        stopReading()
        remove()
    }

    /** The conversation's island is up (it has the top) or gone. */
    fun conversation(up: Boolean) {
        conversation = up
        sync()
    }

    private fun sync() {
        if (!started) return
        if (!config.islandRest || !AssistOverlay.canShow(context)) {
            stopReading()
            remove()
            return
        }
        val host = HeadwayAccessibilityService.instance.value?.takeIf { config.islandCover && config.islandStatus }
        // A window's kind is fixed when it is added: the cover going on or off adds it again.
        if (view != null && covered != (host != null)) remove()
        if (view == null) add(host)
        if (config.islandStatus && screenOn) startReading() else stopReading()
        val shown = IslandRest.shown(true, conversation, statusBar, screenOn, ((context.applicationContext as? JarvisApp)?.ownScreens ?: 0) > 0) &&
            !(covered && (HeadwayAccessibilityService.systemInFront.value || locked()))
        view?.visibility = if (shown) View.VISIBLE else View.INVISIBLE
        band?.visibility = if (shown) View.VISIBLE else View.INVISIBLE
        draw()
    }

    private fun draw() {
        val v = view ?: return
        v.face = IslandRest.face(persona, presence, if (reading) now() else null)
        v.presence = presence
        v.icons = if (reading) icons else emptyList()
        band?.setBackgroundColor(ActiveTheme.tint(JarvisTokens.Color.BG, "bg.void", "bg.deep"))
        // Grown for its icons, the window moves so the pill's hole stays on the camera.
        val p = params ?: return
        val x = (at?.x ?: 0) + v.shift
        if (p.x != x) {
            p.x = x
            runCatching { windows?.updateViewLayout(v, p) }.onFailure { Log.w(TAG, "the resting island did not move", it) }
        }
    }

    /** Adds the island's window: an app overlay, or with [host] (the car's touch service) the cover's. */
    private fun add(host: Context?) {
        val wm = (host ?: context).getSystemService(WindowManager::class.java) ?: return
        val metrics = wm.currentWindowMetrics
        val insets = metrics.windowInsets
        val cutout = hole(insets)
        val bar = insets.getInsetsIgnoringVisibility(WindowInsets.Type.statusBars()).top
        val screenW = metrics.bounds.width()
        val height = IslandRest.height(
            JarvisUi.dp(context, JarvisTokens.Material.Floating.REST_HEIGHT_DP), cutout, JarvisUi.dp(context, JarvisTokens.Space.SNUG), bar,
        )
        val at = IslandRest.at(screenW, cutout, bar, height)
        // Right to left, the dot's open corner is on the side the pill grows to: it stops short of it.
        val rtl = context.resources.configuration.layoutDirection == View.LAYOUT_DIRECTION_RTL
        val v = RestView(context).apply { place(cutout, height, if (host != null && rtl) bar else 0) }
        v.setOnApplyWindowInsetsListener { _, given ->
            val shown = given.isVisible(WindowInsets.Type.statusBars())
            if (shown != statusBar) {
                statusBar = shown
                sync()
            }
            given
        }
        val type = if (host != null) WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY else WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        // The band first, so the island draws over it.
        if (host != null) addBand(wm, IslandRest.band(screenW, bar, rtl))
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            x = at.x
            y = at.y
            // On the camera, not fitted below the bars: an overlay fits them by default, which is
            // the gap under the status bar the island sat in before (the owner: "slightly below it").
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            fitInsetsTypes = 0
            windowAnimations = 0
            title = "Resting island"
        }
        try {
            wm.addView(v, params)
            view = v
            windows = wm
            this.params = params
            this.at = at
            covered = host != null
        } catch (t: Throwable) {
            Log.w(TAG, "the resting island's window was refused", t)
            removeBand(wm)
        }
    }

    private fun addBand(wm: WindowManager, box: IslandRest.Box) {
        val v = View(context)
        val p = WindowManager.LayoutParams(
            box.right - box.left,
            box.bottom - box.top,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.OPAQUE,
        ).apply {
            gravity = Gravity.TOP or Gravity.LEFT
            x = box.left
            y = box.top
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            fitInsetsTypes = 0
            windowAnimations = 0
            title = "Resting island's band"
        }
        runCatching { wm.addView(v, p); band = v }.onFailure { Log.w(TAG, "the resting island's band was refused", it) }
    }

    private fun removeBand(wm: WindowManager?) {
        val b = band ?: return
        band = null
        runCatching { wm?.removeView(b) }
    }

    private fun remove() {
        val v = view ?: return
        view = null
        params = null
        runCatching { windows?.removeView(v) }.onFailure { Log.w(TAG, "the resting island's window was already gone", it) }
        removeBand(windows)
        windows = null
        covered = false
    }

    private fun startReading() {
        if (reading) return
        reading = true
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_BATTERY_CHANGED)
            addAction(Intent.ACTION_TIME_TICK)
            addAction(Intent.ACTION_TIME_CHANGED)
            addAction(Intent.ACTION_TIMEZONE_CHANGED)
            addAction(NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED)
        }
        // The battery's broadcast is sticky: registering hands back its last one.
        context.registerReceiver(status, filter, Context.RECEIVER_NOT_EXPORTED)?.let(::charge)
        runCatching { context.getSystemService(ConnectivityManager::class.java)?.registerDefaultNetworkCallback(link) }
        HeadwayNotificationListener.observeIcons(iconsSeen)
    }

    private fun stopReading() {
        if (!reading) return
        reading = false
        runCatching { context.unregisterReceiver(status) }
        runCatching { context.getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(link) }
        HeadwayNotificationListener.unobserveIcons(iconsSeen)
        icons = emptyList()
    }

    /** The lock screen is up; unknown counts as up, so the cover stays aside. */
    private fun locked() = context.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked ?: true

    private fun charge(intent: Intent) {
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        battery = if (level >= 0 && scale > 0) level * 100 / scale else null
        val plugged = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        charging = plugged == BatteryManager.BATTERY_STATUS_CHARGING || plugged == BatteryManager.BATTERY_STATUS_FULL
    }

    private fun now(): IslandRest.Status {
        // The cell's bars are read as the island is drawn (a minute's tick at most), not followed.
        val cell = if (network == Network.CELL) runCatching { context.getSystemService(android.telephony.TelephonyManager::class.java)?.signalStrength?.level }.getOrNull() ?: 0 else 0
        val quiet = context.getSystemService(NotificationManager::class.java)?.currentInterruptionFilter
            ?.let { it > NotificationManager.INTERRUPTION_FILTER_ALL } ?: false
        return IslandRest.Status(
            time = DateFormat.getTimeFormat(context).format(Date()),
            battery = battery,
            charging = charging,
            network = network,
            bars = if (network == Network.WIFI) wifiBars else cell,
            dnd = quiet,
        )
    }

    companion object {
        private const val TAG = "RestIsland"

        /** The camera's hole on this screen, px ([IslandRest.hole]); null with none. */
        fun hole(context: Context): IslandRest.Box? =
            context.getSystemService(WindowManager::class.java)?.currentWindowMetrics?.windowInsets?.let(::hole)

        private fun hole(insets: WindowInsets): IslandRest.Box? {
            val outline = insets.displayCutout?.cutoutPath?.let { path ->
                RectF().also { path.computeBounds(it, true) }.let { IslandRest.Box(it.left.toInt(), it.top.toInt(), it.right.toInt(), it.bottom.toInt()) }
            }
            val rect = insets.displayCutout?.boundingRectTop?.takeIf { !it.isEmpty }?.let { IslandRest.Box(it.left, it.top, it.right, it.bottom) }
            return IslandRest.hole(outline, rect)
        }
    }
}
