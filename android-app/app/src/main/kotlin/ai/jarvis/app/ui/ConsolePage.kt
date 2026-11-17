package ai.jarvis.app.ui

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.car.log.SessionLog
import ai.jarvis.app.tailnet.TailnetLink
import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.format.DateUtils
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.ProxyConfig
import androidx.webkit.ProxyController
import androidx.webkit.WebViewFeature

/**
 * The console's own page in a web view, kept loaded: the car screen's HUD (`CarHud`) and
 * the phone's home screen (`MainActivity`, the owner, 2026-09-27: "the mobile app UI is
 * still weird and doesn't really match the look/feel of the console, so fix it") are the
 * same console, not a copy of it.
 *
 * What it owns: the web view and its user agent (the page tells the car from the phone
 * by it), the tailnet proxy for a `ts.net` console when the tailnet runs inside the app,
 * and loading until it works -- a failed main-frame load, a 5xx, or no finished page in
 * [LOAD_WATCHDOG_MS] reloads with a growing pause (2..30 s); a dead renderer is reported
 * to [onRendererGone], which builds a new page (this view cannot be revived). Each is in
 * the drive log. The microphone is granted to the page when the app holds it, so the
 * console's own voice works on the phone.
 *
 * The console's password (T-0387): the page load carries [key], the adapter key the
 * app was paired with, and the console answers it with a session cookie that the
 * page's own fetches and sockets then carry. Does not cover a console whose
 * adapter key differs from the app's: that console shows its password lock.
 */
@SuppressLint("SetJavaScriptEnabled")
class ConsolePage(
    context: Context,
    private val url: String,
    agent: String,
    private val onLoaded: () -> Unit = {},
    private val onRendererGone: () -> Unit = {},
    private val onFailed: () -> Unit = {},
    /**
     * A new document began loading in the main frame. What the old one asked of the
     * native side through a bridge is void now: its own cleanup never ran.
     */
    private val onStarted: () -> Unit = {},
    /**
     * Whether the page may have the microphone. Not on the car page: a web view's
     * microphone is call audio to Android, and the car has its own voice path.
     */
    private val microphone: Boolean = true,
    /**
     * While the console cannot be reached, show the console's own offline page (an
     * APK asset prerendered from it, `jarvis-web/scripts/offline-asset.mjs`) instead
     * of handing the screen back to the native one (the owner, 2026-10-08: "a base UI
     * that looks like the console"). Only when the web view has no cached console to show
     * ([showCached]); the phone home and, since T-0711, the car.
     */
    private val offlinePage: Boolean = false,
    /** The adapter key, sent only on the page load itself; blank sends nothing. Never logged. */
    private val key: String = "",
    /**
     * Opens the system picker for the page's file input (the chat's Attach, T-0497) and
     * answers with what was picked, or null. Null here: the page gets no picker (the car).
     */
    private val chooseFile: ((Intent, (Array<Uri>?) -> Unit) -> Unit)? = null,
) {
    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private var running = false
    private var attempts = 0
    private var firstTry = true
    private var loaded = false
    /**
     * The main frame failed on this attempt. Chromium still calls `onPageFinished` for
     * its own error page, and counting that as loaded hid the native screen under a
     * transparent, empty page: the blank car and phone screens whenever the console
     * could not be reached.
     */
    private var failed = false
    /** The offline page is what the view is showing, between two tries. */
    private var showingOffline = false
    /** When the next try starts, for the offline page's countdown line. */
    private var nextTryAt = 0L
    /** The view shows the last console this phone loaded, from its cache, while the server is away. */
    private var fromCache = false
    /**
     * The cache had no copy since the console last answered: the next failure goes to the offline
     * page. Only a miss: a failure after the cached console painted shows it again (T-0913).
     */
    private var cacheMissed = false
    /** When the cached console last painted: a page that fails again at once would reload in a loop. */
    private var cachedAt = 0L

    val view: WebView = WebView(context).apply {
        setBackgroundColor(Color.TRANSPARENT)
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        // The console speaks replies itself; nobody taps first.
        settings.mediaPlaybackRequiresUserGesture = false
        settings.userAgentString = settings.userAgentString + agent
        webViewClient = Loader()
        webChromeClient = object : WebChromeClient() {
            // The console's own push-to-talk and open mic: granted when the app holds
            // the microphone, and only the microphone.
            override fun onPermissionRequest(request: PermissionRequest) {
                val mic = PermissionRequest.RESOURCE_AUDIO_CAPTURE
                val held = app.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                if (microphone && held && mic in request.resources) request.grant(arrayOf(mic)) else request.deny()
            }

            // Without this a web view ignores a file input's tap. Returning true is a
            // promise to answer the callback exactly once, or the input never opens again.
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                val choose = chooseFile ?: return false
                choose(params.createIntent()) { callback.onReceiveValue(it) }
                return true
            }
        }
    }

    private val watchdog = Runnable {
        if (running && !loaded) retry("the console page did not finish loading in time")
    }

    fun start() {
        if (running) return
        running = true
        routeThenLoad()
    }

    fun stop() {
        running = false
        main.removeCallbacks(watchdog)
        runCatching { view.destroy() }
    }

    fun resume() {
        runCatching { view.onResume() }
        // Settings may have started, swapped or forgotten the link: the page's own
        // reconnects must not keep going to a proxy port that closed.
        if (running) routeThen {}
    }

    fun pause() {
        runCatching { view.onPause() }
    }

    private fun load() {
        loaded = false
        failed = false
        showingOffline = false
        fromCache = false
        main.removeCallbacks(watchdog)
        // The first try of a start with a cached console waits less (T-0787): with no route home the
        // load hangs on a SYN timeout, and the car stood on an empty ground for the whole 20 s.
        val first = firstTry && lastReached(app) > 0L
        firstTry = false
        main.postDelayed(watchdog, if (first) FIRST_LOAD_WATCHDOG_MS else LOAD_WATCHDOG_MS)
        if (key.isNotBlank()) view.loadUrl(url, mapOf("Authorization" to "Bearer $key")) else view.loadUrl(url)
    }

    private fun retry(why: String) {
        if (!running) return
        view.settings.cacheMode = WebSettings.LOAD_DEFAULT
        // The cache attempt failed before it painted (a miss), or the painted page failed again
        // within RETRY_BASE_MS (a loop): both go to the offline page and its backoff.
        if (fromCache && (!loaded || SystemClock.uptimeMillis() - cachedAt < RETRY_BASE_MS)) cacheMissed = true
        if (!cacheMissed && lastReached(app) > 0L) {
            SessionLog.shared.info(TAG, "$why; showing the last console this phone loaded, from its cache")
            showCached()
            return
        }
        fromCache = false
        failed = true
        loaded = false
        attempts++
        val wait = (RETRY_BASE_MS shl (attempts - 1).coerceAtMost(4)).coerceAtMost(RETRY_MAX_MS)
        SessionLog.shared.info(TAG, "$why; loading again in ${wait / 1000}s (attempt $attempts)")
        main.removeCallbacks(watchdog)
        nextTryAt = System.currentTimeMillis() + wait
        if (!showOffline()) runCatching { onFailed() }
        // Routed again each time: a console opened before the link came up at launch was
        // loaded direct and failed, and retrying direct never reached home.
        main.postDelayed({ if (running) routeThenLoad() }, wait)
    }

    /**
     * The last console this phone loaded, whole, from the web view's own cache (T-0711): its page
     * and its bundles (cached for a year, `immutable`) with its link down, which reconnects by
     * itself when home answers. Only the page is read cache-only; once it paints, its own fetches
     * go to the network again. A miss is the next failure and falls through to the offline page.
     *
     * Does not cover: a screen whose client navigation needs the server's `__data.json`, or a
     * cache Chromium has evicted; the page stays the build it was until the next real load.
     */
    private fun showCached() {
        fromCache = true
        failed = false
        loaded = false
        showingOffline = false
        main.removeCallbacks(watchdog)
        main.postDelayed(watchdog, LOAD_WATCHDOG_MS)
        view.settings.cacheMode = WebSettings.LOAD_CACHE_ONLY
        view.loadUrl(url)
    }

    /** While the cached console is up, the link may start or move: its reconnects follow the proxy. */
    private val reroute = object : Runnable {
        override fun run() {
            if (!running || !fromCache) return
            routeThen {}
            main.postDelayed(this, RETRY_MAX_MS)
        }
    }

    /** The offline page in the view, or false when this page has none or the asset is missing. */
    private fun showOffline(): Boolean {
        if (!offlinePage) return false
        val html = offlineHtml ?: runCatching {
            app.assets.open(OFFLINE_ASSET).bufferedReader().use { it.readText() }
        }.getOrNull()?.also { offlineHtml = it } ?: return false
        showingOffline = true
        view.loadDataWithBaseURL(OFFLINE_BASE, html, "text/html", "utf-8", null)
        return true
    }

    private var offlineHtml: String? = null

    /** The attempt and the countdown, written into the offline page's ids. */
    private fun fillOffline() {
        val secs = ((nextTryAt - System.currentTimeMillis()) / 1000).coerceAtLeast(0)
        val next = if (attempts > 1) "Try $attempts. Next in ${secs}s." else "Trying again in ${secs}s."
        view.evaluateJavascript("(function(){var n=document.getElementById('next');if(n)n.textContent=${org.json.JSONObject.quote(next)};})()", null)
        reachedLine(app, lastReached(app))?.let { line ->
            view.evaluateJavascript("(function(){var d=document.getElementById('detail');if(d)d.textContent=${org.json.JSONObject.quote(line)};})()", null)
        }
        if (showingOffline && secs > 0) main.postDelayed({ if (showingOffline) fillOffline() }, 1000)
    }

    /**
     * Chromium makes its own connections, so with the tailnet or a WireGuard tunnel
     * running inside the app (`TailnetLink`) the console is unreachable from here unless
     * it goes through the node's proxy. Only the console's host does, and the proxy
     * carries only its host:port ([proxyTarget]): it is a loopback port any app on the
     * phone can reach, and with everything sent to it any of them could reach any home
     * host through Jarvis's tunnel (T-0394). Everything else (map tiles, fonts) goes
     * out as usual.
     */
    private fun routeThenLoad() = routeThen { load() }

    /** Points the web view at the link's proxy as it is now (or at nothing), then runs [then]. */
    private fun routeThen(then: () -> Unit) {
        val (origin, host) = proxyTarget(url, TailnetLink.wireGuard) ?: (null to null)
        val port = origin?.let { TailnetLink.proxyPort() }
        // Every time, not only when the override changes: a restarted node starts with
        // an empty list and may get the same port back.
        if (port != null && origin != null) TailnetLink.allowProxy(origin)
        val want = port?.let { "$it/$origin" }
        if (want == proxied || !WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE) ||
            !WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE_REVERSE_BYPASS)
        ) {
            then()
            return
        }
        runCatching {
            val controller = ProxyController.getInstance()
            if (port == null || host == null) {
                controller.clearProxyOverride({ main.post(it) }) { proxied = null; then() }
                return
            }
            val rules = ProxyConfig.Builder().addProxyRule("127.0.0.1:$port")
                .addBypassRule(host).setReverseBypassEnabled(true)
            controller.setProxyOverride(rules.build(), { main.post(it) }) { proxied = want; then() }
        }.onFailure {
            Log.w(TAG, "the tailnet proxy was refused; loading directly", it)
            then()
        }
    }

    private inner class Loader : WebViewClient() {
        override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
            runCatching { onStarted() }
        }

        // A new document has no --safe-* of its own: ask for the insets again, which tells it ([fit]).
        override fun onPageCommitVisible(view: WebView, url: String) {
            androidx.core.view.ViewCompat.requestApplyInsets(view)
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (running && showingOffline && url.startsWith(OFFLINE_BASE)) {
                runCatching { onLoaded() }
                fillOffline()
                return
            }
            if (!running || failed) return
            loaded = true
            main.removeCallbacks(watchdog)
            if (fromCache) {
                // Shown, not reached: the "last reached" line and the retry count stay as they were.
                view.settings.cacheMode = WebSettings.LOAD_DEFAULT
                cachedAt = SystemClock.uptimeMillis()
                main.removeCallbacks(reroute)
                main.postDelayed(reroute, RETRY_MAX_MS)
                onLoaded()
                return
            }
            cacheMissed = false
            if (attempts > 0) SessionLog.shared.info(TAG, "the console page loaded after $attempts retry(s)")
            attempts = 0
            app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(KEY_REACHED, System.currentTimeMillis()).apply()
            onLoaded()
        }

        /** The offline page's two links: try now, or the phone's own settings. */
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            when (request.url.toString()) {
                "jarvis-offline:reconnect" -> {
                    main.removeCallbacksAndMessages(null)
                    if (running) load()
                }
                "jarvis-offline:settings" -> runCatching {
                    view.context.startActivity(android.content.Intent(view.context, ai.jarvis.app.SettingsActivity::class.java))
                }
                else -> return false
            }
            return true
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) retry("the console page failed: ${error.description} (${error.errorCode})")
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (request.isForMainFrame && response.statusCode >= 500) retry("the console answered ${response.statusCode}")
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            SessionLog.shared.info(TAG, "the console page's renderer stopped (crashed=${detail.didCrash()}); rebuilding it")
            main.post { runCatching { onRendererGone() } }
            return true
        }
    }

    /**
     * The phone app's side of `jarvis-web/src/lib/hud/phone.ts`: what Jarvis asks of the
     * phone on this page's session -- its music (`hud.phone.media`), an action like a text
     * (`hud.phone.action`, answered back to the page as `jarvis:phone-result` for it to
     * send on), and the routines to watch (`hud.phone.routines`). The native conversation
     * did this before the console was the home screen; without it "play my playlist" from
     * the phone's own screen did nothing. Every action still goes through the phone's
     * tiers (`PhoneAction`).
     */
    fun bridgePhone() {
        view.addJavascriptInterface(PhoneBridge(), "JarvisPhone")
    }

    private inner class PhoneBridge {
        /**
         * Whether the car is here (CarPresenceReceiver.carHere): the page lets go of its open
         * mic then (`voice.setAway`), since a held web microphone is a call to the car. From
         * the car's Bluetooth, not its screen: the call screen came up in between (T-0745).
         */
        @JavascriptInterface
        fun carLinked(): Boolean = ai.jarvis.app.car.link.CarPresenceReceiver.carHere()

        /**
         * One of the phone's own screens, from the console's Settings → This phone: the
         * phone and car options stay native (the owner, 2026-09-27: "theres no options for
         * the car and phone stuff") and the console links to them. An unknown name opens
         * nothing.
         */
        @JavascriptInterface
        fun open(screen: String) {
            val target = when (screen) {
                "phone" -> ai.jarvis.app.SettingsActivity::class.java
                "car" -> ai.jarvis.app.car.setup.CarSetupActivity::class.java
                "tasks" -> ai.jarvis.app.automation.ui.AutomationsActivity::class.java
                "voice" -> ai.jarvis.app.VoiceIdentityActivity::class.java
                "check" -> SystemCheckActivity::class.java
                else -> return
            }
            main.post {
                runCatching { view.context.startActivity(android.content.Intent(view.context, target)) }
            }
        }

        /**
         * Whether this phone writes down what is said to the console (the owner, 2026-10-08:
         * "on phone transcription isnt working" -- the switch was the native screen's
         * alone): the switch is on and the model is here. The recording still goes with
         * the words, whatever this phone believes of the speaker gate: its copy of the
         * gate's mode can be stale or never fetched, and words alone would have walked a
         * stranger past an enforcing gate as the key holder. Only room tone, in which the
         * phone hears no words, stays on the phone.
         */
        @JavascriptInterface
        fun hears(): Boolean =
            ai.jarvis.app.config.JarvisConfig(app).sttOnDevice && ai.jarvis.app.assist.OnPhoneSpeech.ready(app)

        /**
         * The words in [wav] (a base64 data URL of the console's 16 kHz WAV), answered on the
         * page as `jarvis:phone-transcript` `{id, text}` or `{id, error}`. Off the binder
         * thread the page called on: opening the model takes seconds the first time.
         */
        @JavascriptInterface
        fun transcribe(id: String, wav: String) {
            SPEECH.execute {
                val detail = org.json.JSONObject().put("id", id)
                runCatching {
                    val stt = ai.jarvis.app.assist.OnPhoneSpeech.shared(app) ?: error("the speech model is not on this phone yet")
                    stt.transcribe(ai.jarvis.app.assist.OnPhoneSpeech.samplesOfWav(java.util.Base64.getDecoder().decode(wav.substringAfter(','))))
                }.onSuccess { detail.put("text", it) }
                    .onFailure { detail.put("error", it.message ?: it.javaClass.simpleName) }
                main.post {
                    view.evaluateJavascript("window.dispatchEvent(new CustomEvent('jarvis:phone-transcript',{detail:$detail}))", null)
                }
            }
        }

        /** The phone's answer to a waiting call, handed to the page to send on (`jarvis:phone-result`). */
        private fun answer(id: String, body: org.json.JSONObject) {
            val detail = org.json.JSONObject().put("id", id).put("body", body)
            main.post {
                view.evaluateJavascript("window.dispatchEvent(new CustomEvent('jarvis:phone-result',{detail:$detail}))", null)
            }
        }

        @JavascriptInterface
        fun event(type: String, json: String) {
            val payload = runCatching { org.json.JSONObject(json) }.getOrNull() ?: return
            main.post {
                when (type) {
                    ai.jarvis.app.assist.PhoneMedia.EVENT -> ai.jarvis.app.assist.PhoneMedia.handle(app, payload)
                    ai.jarvis.app.assist.PhoneAction.EVENT -> ai.jarvis.app.assist.PhoneAction.handle(app, payload, ::answer)
                    ai.jarvis.app.routines.RoutineWatcher.SYNC_EVENT -> ai.jarvis.app.routines.RoutineWatcher.sync(app, payload)
                    // A route asked for here, the car linked or not: the car screen if it is
                    // up, else the maps app on the phone; the waiting call is told which.
                    ai.jarvis.app.car.voice.HermesCarJarvis.NAVIGATE_EVENT -> {
                        val body = ai.jarvis.app.car.dash.CarHud.routeEvent(app, payload)
                        payload.optString("id").takeIf { it.isNotEmpty() }?.let { answer(it, body) }
                    }
                }
            }
        }
    }

    companion object {
        /**
         * Keep [view] (the page) clear of the system bars and the keyboard, as margins: a web
         * view's page ignores the view's own padding and drew under the bars of the edge-to-edge
         * window, the console cut off at the top and the bottom (T-0887). The window's ground
         * shows in the margins. Needs a parent whose layout params take margins.
         *
         * The camera's cutout is not a margin: the home hides the bars, so it was the only inset
         * left and its margin a black band across the top (the owner, 2026-11-10, T-0974). The
         * page draws its ground to the edge and is told, as `--safe-top`/`-right`/`-bottom`/
         * `-left` on its root, how far in to keep its content; the console's shell pads by them.
         * A page that loads again is told again ([Loader]). Not covered: a page that has no
         * shell (the offline page centres its content instead).
         */
        fun fit(view: android.view.View) {
            androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
                val clear = insets.getInsets(
                    androidx.core.view.WindowInsetsCompat.Type.systemBars() or androidx.core.view.WindowInsetsCompat.Type.ime(),
                )
                (v.layoutParams as? android.view.ViewGroup.MarginLayoutParams)?.let {
                    if (it.leftMargin != clear.left || it.topMargin != clear.top || it.rightMargin != clear.right || it.bottomMargin != clear.bottom) {
                        it.setMargins(clear.left, clear.top, clear.right, clear.bottom)
                        v.layoutParams = it
                    }
                }
                val cutout = insets.getInsets(androidx.core.view.WindowInsetsCompat.Type.displayCutout())
                val safe = androidx.core.graphics.Insets.max(androidx.core.graphics.Insets.subtract(cutout, clear), androidx.core.graphics.Insets.NONE)
                (v as? WebView)?.evaluateJavascript(safeScript(safe, v.resources.displayMetrics.density), null)
                insets
            }
        }

        /** The script that tells the page its [safe] insets (px), in CSS px. */
        internal fun safeScript(safe: androidx.core.graphics.Insets, density: Float): String {
            fun css(px: Int) = "${kotlin.math.round(px / density).toInt()}px"
            return "(function(s){s.setProperty('--safe-top','${css(safe.top)}');s.setProperty('--safe-right','${css(safe.right)}');" +
                "s.setProperty('--safe-bottom','${css(safe.bottom)}');s.setProperty('--safe-left','${css(safe.left)}')})(document.documentElement.style)"
        }

        private const val TAG = "ConsolePage"
        /** The proxy the web views were last pointed at ("port/origin"): the override is process-wide. */
        private var proxied: String? = null
        /** One utterance at a time, for the app: the phone's speech model is one model, and a rebuilt page must not leave a thread behind. */
        private val SPEECH = java.util.concurrent.Executors.newSingleThreadExecutor()
        private const val LOAD_WATCHDOG_MS = 20_000L
        /** Long enough for a live first load on a cold renderer; a slower one shows the cached console, whose link reconnects. */
        private const val FIRST_LOAD_WATCHDOG_MS = 6_000L
        private const val RETRY_BASE_MS = 2_000L
        private const val RETRY_MAX_MS = 30_000L
        /** `jarvis-web/scripts/offline-asset.mjs` writes it; CI checks it is current. */
        internal const val OFFLINE_ASSET = "console-offline.html"
        /** A base no request goes to: the page loads nothing, and its links are caught above. */
        internal const val OFFLINE_BASE = "https://offline.jarvis.invalid/"
        private const val PREFS = "console_page"
        private const val KEY_REACHED = "last_reached_ms"

        /** When the console last loaded on this phone, 0 if never: shared by the phone page and the car's. */
        internal fun lastReached(context: Context): Long =
            context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_REACHED, 0L)

        /**
         * The offline page's stale marker (T-0481): what is on screen is not live, and this
         * says how old the last contact is. Null keeps the page's own line. Does not cover
         * the house's state itself: the phone keeps none (T-0494).
         */
        internal fun reachedLine(context: Context, at: Long, now: Long = System.currentTimeMillis()): String? {
            if (at <= 0L) return null
            // Not DateUtils.isToday: it reads the framework's clock, which Robolectric fakes
            // while this app's own clock stays real, so a load a second ago read as another day.
            val day = { t: Long -> DateUtils.formatDateTime(context, t, DateUtils.FORMAT_SHOW_DATE or DateUtils.FORMAT_SHOW_YEAR) }
            val today = day(at) == day(now)
            val flags = DateUtils.FORMAT_SHOW_TIME or if (today) 0 else DateUtils.FORMAT_SHOW_DATE
            val whenText = DateUtils.formatDateTime(context, at, flags)
            return "Last reached ${ActiveTheme.name} ${if (today) "at" else "on"} $whenText. This phone is trying again on its own."
        }

        /**
         * What goes through the node's proxy for a console at [url]: its origin as the
         * proxy sees it (host:port, the scheme's port written out) and the one host the
         * web view sends there. Null loads direct: no host, or a non-tailnet host while
         * the tailnet (not a WireGuard tunnel, whose dialler sends what it does not cover
         * straight out) is the link. Does not cover a second console origin: the allow-list
         * is the node's, so the last page routed wins (the phone and the car share one).
         */
        internal fun proxyTarget(url: String, wireGuard: Boolean): Pair<String, String>? {
            // URL, not URI: URI drops a host with an underscore (jarvis_box.lan), which the web view loads.
            val u = runCatching { java.net.URL(url.trim()) }.getOrNull() ?: return null
            val host = u.host?.lowercase()?.takeIf { it.isNotEmpty() } ?: return null
            if (!wireGuard) return null
            val port = if (u.port != -1) u.port else u.defaultPort
            return "$host:$port" to host
        }
    }
}
