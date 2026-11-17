package ai.jarvis.app.tailnet

import ai.jarvis.app.ui.theme.ActiveTheme
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log
import okhttp3.Dns
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketAddress
import javax.net.SocketFactory
import ai.jarvis.app.tailnet.tailnet.Tailnet as Node

/**
 * Jarvis's own link home: a scanned WireGuard tunnel run in-process (`android-app/tailnet`,
 * `wg.go`). The embedded Tailscale node that shared this library was removed (T-0604);
 * the class keeps its name because the settings and the clients already bind to it.
 *
 * The car link binds a socket to the car's Wi-Fi, and a VPN app's tunnel does not allow
 * bypass, so inside it that bind is EPERM; outside it the app cannot reach home. With the
 * tunnel in the app, the car link binds freely and requests to hosts in the config's
 * AllowedIPs ([covers]) go through it.
 *
 * How a request finds it: [dns] answers a covered host with 127.0.0.1 carrying the
 * original name, and [socketFactory]'s sockets see that marker and connect to a loopback
 * forwarder that dials the real host and port through the tunnel. TLS above the socket
 * still checks the real name. Every other host resolves and connects as it always did.
 *
 * Not covered: anything that is not OkHttp (the car link's own sockets are not tunnel
 * traffic), and a forwarder is reachable by other apps on the phone's loopback -- each
 * one reaches one Jarvis port that still asks for its own key.
 */
object TailnetLink {
    private const val TAG = "JarvisTailnet"

    @Volatile
    var running: Boolean = false
        internal set

    /** Whether the WireGuard tunnel is up (kept apart from [running] for the JVM tests). */
    @Volatile
    var wireGuard: Boolean = false
        internal set

    @Volatile
    private var wireGuardConf = ""

    /** Why the last start failed, null once one worked: Settings says it while the tunnel is down. */
    @Volatile
    var lastError: String? = null
        private set

    /** Starts the native tunnel; a seam for the JVM tests, where the library is missing. */
    internal var start: (String, String) -> Unit = { conf, dir -> Node.startWireGuard(conf, dir) }

    /** Whether the WireGuard tunnel carries [host]; a seam for the JVM tests. */
    internal var covers: (String) -> Boolean = { host -> runCatching { Node.covers(host) }.getOrDefault(false) }

    /** The node's proxy port and its allow-list call; seams for the JVM tests, like [covers]. */
    internal var proxy: () -> Int? = { runCatching { Node.proxy().toInt() }.getOrNull() }
    internal var allow: (String) -> Unit = { Node.proxyAllow(it) }

    /** The forwarders' address: IPv4 loopback, as `tailnet.go` listens. */
    internal val FORWARDER: InetAddress = InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1))

    /** Whether [host] goes through the tunnel: only what its AllowedIPs cover. */
    internal fun routes(host: String): Boolean = wireGuard && covers(host)

    /** Whether a scanned QR is a wg-quick config (what a WireGuard server hands a new peer). */
    fun isWireGuardConfig(text: String): Boolean =
        Regex("(?im)^\\s*\\[Interface]").containsMatchIn(text) && Regex("(?im)^\\s*PrivateKey\\s*=").containsMatchIn(text)

    /**
     * Brings up the tunnel the settings ask for. Null when it is up (or none is asked for);
     * the reason when it would not start.
     */
    @Synchronized
    fun apply(context: Context, config: ai.jarvis.app.config.JarvisConfig): String? = when {
        config.wireGuardConf.isNotBlank() -> startWireGuard(config.wireGuardConf, stateDir(context))
        else -> {
            if (running) stop()
            null
        }
    }

    /**
     * Start the WireGuard tunnel [conf] describes; the same one already up is left alone.
     * One that will not start leaves whatever link was up as it was (`wg.go` swaps only
     * a tunnel that came up), so a bad scan does not cut the phone off from home.
     * [dir] keeps the home names' addresses the tunnel learns (`wg.go` pin).
     */
    @Synchronized
    fun startWireGuard(conf: String, dir: String): String? {
        if (running && wireGuard && conf == wireGuardConf) return null
        return begin(conf, dir)
    }

    @Synchronized
    private fun begin(conf: String, dir: String): String? =
        try {
            val sent = mobileMtu(conf)
            start(sent, dir)
            wireGuardConf = conf
            wireGuard = true
            running = true
            lastError = null
            // T-0992: 1280 still lost the house's answers, and no log said which MTU was in use.
            Log.i(TAG, "the WireGuard tunnel is up at MTU ${MTU_LINE.find(sent)?.groupValues?.get(2) ?: "1420 (wg.go's default)"}")
            null
        } catch (t: Throwable) {
            Log.w(TAG, "the WireGuard tunnel did not start", t)
            (t.message ?: t.javaClass.simpleName).also { lastError = it }
        }

    /**
     * Starts the tunnel that is up again, so its Endpoint is resolved on the network the phone
     * is on now (T-0738, T-0855): one resolved on home Wi-Fi, where the house's DNS answers
     * its home address, went nowhere on mobile data, and only an app restart brought the
     * phone and the car back. A network is announced before its resolver answers, so it is
     * tried [RENEW_TRIES] times, 2 s doubling; one that fails keeps the link that was up.
     * Blocks: run it on its own thread.
     *
     * Not covered: a config with a ListenPort, which the old link holds while the new one starts.
     */
    fun renew(dir: String, sleep: (Long) -> Unit = Thread::sleep) {
        var wait = 2_000L
        for (attempt in 1..RENEW_TRIES) {
            val conf = synchronized(this) { wireGuardConf.takeIf { wireGuard } } ?: return
            if (begin(conf, dir) == null) return
            if (attempt == RENEW_TRIES) break
            sleep(wait)
            wait = (wait * 2).coerceAtMost(30_000L)
        }
        Log.w(TAG, "the WireGuard tunnel was not renewed on the new network: $lastError")
    }

    /**
     * [conf] with the tunnel's MTU at most [MOBILE_MTU] (T-0986, T-0992). `wg.go` takes 1420 when the
     * config names none, which fits only a full 1500-byte path. Mobile data is often smaller, so
     * away from home the house's full-size packets were lost while pings and small answers got
     * through: every map answer timed out in the car, and an APK never finished downloading. The
     * phone's TCP advertises its segment size from this MTU, so the house sends smaller packets.
     *
     * Not covered: a mobile path under 1280 bytes, or UDP in the tunnel (none is sent).
     */
    internal fun mobileMtu(conf: String): String {
        if (MTU_LINE.containsMatchIn(conf)) {
            return MTU_LINE.replace(conf) { m -> m.groupValues[1] + (m.groupValues[2].toIntOrNull()?.coerceAtMost(MOBILE_MTU) ?: MOBILE_MTU) }
        }
        val head = Regex("(?im)^[ \\t]*\\[Interface][^\\r\\n]*").find(conf) ?: return conf
        val at = head.range.last + 1
        return conf.substring(0, at) + "\nMTU = $MOBILE_MTU" + conf.substring(at)
    }

    /**
     * A 1280-byte mobile path (IPv6's minimum, common on IPv6-only carriers) less the tunnel's own
     * 80 bytes (outer IPv6, UDP and WireGuard's header). 1280, WireGuard's own pick for a phone,
     * still lost the house's answers on the owner's carrier (1797, T-0992).
     */
    internal const val MOBILE_MTU = 1200

    private val MTU_LINE = Regex("(?im)^([ \\t]*MTU[ \\t]*=[ \\t]*)(\\d+)")

    /** Whether the phone's default network changed; the first one heard is the one it started on. */
    internal fun moved(was: Any?, now: Any): Boolean = was != null && was != now

    /** Renews the tunnel ([renew]) whenever the phone's default network changes. */
    fun watchNetworks(context: Context, dir: String) {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return
        runCatching {
            cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
                private var last: Network? = null

                override fun onAvailable(network: Network) {
                    val was = last
                    last = network
                    if (moved(was, network)) Thread({ renew(dir) }, "jarvis-tunnel-renew").start()
                }
            })
        }.onFailure { Log.w(TAG, "could not watch the network; the tunnel is renewed only at start", it) }
    }

    /**
     * Keeps the saved tunnel coming up until it starts or [conf] is forgotten, 2 s doubling
     * to 30 s apart (T-0913). The start resolves the config's Endpoint, so one made while the
     * phone could not resolve it (an update installing in the car, on its no-internet Wi-Fi)
     * failed, nothing tried again, and away from home the phone and the car could not reach
     * the house until SAVE in Settings. Blocks: run it on its own thread.
     *
     * Not covered: an Endpoint whose address moves while the tunnel is up.
     */
    fun keepUp(dir: String, conf: () -> String, sleep: (Long) -> Unit = Thread::sleep) {
        var wait = 2_000L
        while (true) {
            val c = conf()
            if (c.isBlank() || startWireGuard(c, dir) == null) return
            Log.w(TAG, "the WireGuard tunnel is not up: $lastError; trying again in ${wait / 1000}s")
            sleep(wait)
            wait = (wait * 2).coerceAtMost(30_000L)
        }
    }

    private const val RENEW_TRIES = 5

    /** Where the tunnel keeps what it learns across restarts. */
    fun stateDir(context: Context): String = context.filesDir.resolve("tailnet").apply { mkdirs() }.path

    @Synchronized
    fun stop() {
        running = false
        wireGuard = false
        wireGuardConf = ""
        runCatching { Node.stop() }
    }

    /**
     * The node's loopback HTTP proxy (`tailnet.go` Proxy), for the car's web view:
     * Chromium makes its own connections, which [socketFactory] never sees. Null
     * when the node is not running here, or would not start one.
     */
    fun proxyPort(): Int? = if (!running) null else proxy()

    /** The one host:port the proxy carries (`tailnet.go` ProxyAllow); anything else is refused. */
    fun allowProxy(origin: String) {
        runCatching { allow(origin) }.onFailure { Log.w(TAG, "the proxy allow-list was refused", it) }
    }

    /** "Running" once the server has answered, "Starting", "Stopped" or a reason. */
    fun state(): String = if (!running) "Stopped" else runCatching { Node.state() }.getOrDefault("Stopped")

    /** The tunnel to [host]:[port] checked step by step (`wg.go` Diagnose); off the main thread, up to 8 s. */
    fun diagnose(host: String, port: Int): String =
        // A binding fault is shown as itself, not as "scan again": the config may be fine.
        runCatching { Node.diagnose(host, port.toLong()) }.getOrElse { "error|${it.message}" }

    /**
     * Settings' sentence for a [diagnose] report, `step|detail`: the step that broke
     * (the owner, 2026-10-30: "the WG tunnel wont connect to jarvis"). Not covered:
     * a step after the one that failed is not known to work.
     */
    fun wireGuardWords(report: String, host: String): String {
        val part = report.split("|", limit = 3)
        val at = part.getOrElse(1) { "" }
        return when (part[0]) {
            "ok" -> "Connected home through WireGuard: ${ActiveTheme.name} answers at $at."
            "down" -> "The WireGuard tunnel isn't running. Scan its QR code again."
            "name" -> "WireGuard is connected, but nothing here knows $host's address. Open ${ActiveTheme.name} once on home " +
                "Wi-Fi and the phone remembers it, or set the WireGuard config's DNS to your home DNS server."
            "covered" -> "WireGuard is connected, but $host is at $at, outside the config's AllowedIPs, so it isn't " +
                "reached through the tunnel. If that's a home address, add it to AllowedIPs."
            "handshake" -> "WireGuard is on, but its server hasn't answered. Check the config's Endpoint is " +
                "reachable from this network and the server is running."
            "answer" -> "WireGuard reaches $at, but ${ActiveTheme.name} doesn't answer there (${part.getOrElse(2) { "no reply" }}). " +
                "Check ${ActiveTheme.name} is running and the address in Settings has the right port."
            else -> report
        }
    }

    /** A covered name resolves to loopback carrying the name; the socket factory does the rest. */
    val dns: Dns = object : Dns {
        override fun lookup(hostname: String): List<InetAddress> =
            if (running && routes(hostname)) {
                listOf(InetAddress.getByAddress(hostname, byteArrayOf(127, 0, 0, 1)))
            } else {
                Dns.SYSTEM.lookup(hostname)
            }
    }

    val socketFactory: SocketFactory = object : SocketFactory() {
        override fun createSocket(): Socket = Routed()
        override fun createSocket(host: String, port: Int): Socket = Routed().apply { connect(InetSocketAddress(host, port)) }
        override fun createSocket(host: String, port: Int, local: InetAddress, localPort: Int): Socket =
            Routed().apply { bind(InetSocketAddress(local, localPort)); connect(InetSocketAddress(host, port)) }
        override fun createSocket(host: InetAddress, port: Int): Socket = Routed().apply { connect(InetSocketAddress(host, port)) }
        override fun createSocket(host: InetAddress, port: Int, local: InetAddress, localPort: Int): Socket =
            Routed().apply { bind(InetSocketAddress(local, localPort)); connect(InetSocketAddress(host, port)) }
    }

    /** A socket that, for the [dns] marker, connects to the tunnel forwarder instead. */
    private class Routed : Socket() {
        override fun connect(endpoint: SocketAddress, timeout: Int) {
            super.connect(route(endpoint), timeout)
        }
        override fun connect(endpoint: SocketAddress) {
            super.connect(route(endpoint))
        }
    }

    internal fun route(endpoint: SocketAddress): SocketAddress {
        val a = endpoint as? InetSocketAddress ?: return endpoint
        val address = a.address ?: return endpoint
        if (!running) return endpoint
        // A covered name comes as the [dns] marker; an address never meets the
        // resolver -- OkHttp takes an IP literal as it is -- so a home address in
        // WireGuard's AllowedIPs (the adapter's 10.20.30.59) is caught here, or it is
        // dialled on the phone's own network and fails.
        val target = when {
            address.isLoopbackAddress -> address.hostName.takeIf { routes(it) }
            routes(address.hostAddress.orEmpty()) -> address.hostAddress
            else -> null
        } ?: return endpoint
        val port = Node.forward("$target:${a.port}").toInt()
        // 127.0.0.1 by address, where the forwarder listens: getLoopbackAddress()
        // is ::1 on the owner's phone, and every connect was refused (1.0.1417).
        return InetSocketAddress(FORWARDER, port)
    }
}
