package ai.jarvis.app.tailnet

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.net.InetSocketAddress

/** The embedded tailnet's routing rules (TailnetLink), off the device. */
class TailnetLinkTest {

    @Test
    fun `with no tunnel no host is routed`() {
        assertFalse(TailnetLink.routes("jarvis.example.ts.net"))
        assertFalse(TailnetLink.routes("100.64.0.51"))
    }

    @Test
    fun `while the link is off every host resolves and connects as before`() {
        assertFalse(TailnetLink.running)
        // The car's head unit, and the marker a tailnet name would get: both untouched.
        val car = InetSocketAddress(InetAddress.getByAddress(byteArrayOf(192.toByte(), 168.toByte(), 5, 1)), 7001)
        assertEquals(car, TailnetLink.route(car))
        val marker = InetSocketAddress(InetAddress.getByAddress("jarvis.example.ts.net", byteArrayOf(127, 0, 0, 1)), 8643)
        assertEquals(marker, TailnetLink.route(marker))
        assertEquals(listOf(InetAddress.getLoopbackAddress()).map { it.hostAddress }, TailnetLink.dns.lookup("localhost").map { it.hostAddress }.take(1))
    }

    @Test
    fun `the shared builder builds a client with the tailnet's resolver and sockets`() {
        // 1.0.1416 shipped a builder that called itself: every client was a stack overflow.
        val client = JarvisHttp.builder().build()
        assertTrue(client.dns === TailnetLink.dns)
        assertTrue(client.socketFactory === TailnetLink.socketFactory)
    }

    @Test
    fun `a WireGuard QR is told apart from a pairing token`() {
        assertTrue(TailnetLink.isWireGuardConfig("[Interface]\nPrivateKey = abc=\nAddress = 10.8.0.2/32\n[Peer]\n"))
        assertTrue(TailnetLink.isWireGuardConfig("  [interface]\r\n  privatekey=abc="))
        assertFalse(TailnetLink.isWireGuardConfig("jarvis://pair?url=https://jarvis.home&key=abc"))
        assertFalse(TailnetLink.isWireGuardConfig("[Interface]\nAddress = 10.8.0.2/32"))
    }

    @Test
    fun `in WireGuard mode the tunnel's AllowedIPs decide`() {
        // The adapter's LAN address (10.20.30.59) is inside the tunnel; ts.net is not.
        val native = TailnetLink.covers
        TailnetLink.covers = { it == "jarvis.home" || it == "10.20.30.59" }
        TailnetLink.wireGuard = true
        TailnetLink.running = true
        try {
            assertTrue(TailnetLink.routes("10.20.30.59"))
            assertFalse(TailnetLink.routes("jarvis.example.ts.net"))
            val home = TailnetLink.dns.lookup("jarvis.home").single()
            assertEquals("jarvis.home", home.hostName)
            assertEquals("127.0.0.1", home.hostAddress)
            // The car's head unit is outside the tunnel: its socket is left alone.
            val car = InetSocketAddress(InetAddress.getByAddress(byteArrayOf(192.toByte(), 168.toByte(), 5, 1)), 7001)
            assertEquals(car, TailnetLink.route(car))
        } finally {
            TailnetLink.running = false
            TailnetLink.wireGuard = false
            TailnetLink.covers = native
        }
    }

    @Test
    fun `a WireGuard config that will not start leaves the tunnel that was up`() {
        // Here the native library is missing, so every start fails: the stand-in for a
        // scan the tunnel refuses. The reviewer's case: tunnel A up, a bad QR scanned.
        TailnetLink.wireGuard = true
        TailnetLink.running = true
        try {
            val reason = TailnetLink.startWireGuard("[Interface]\nPrivateKey = x\n", "")
            assertTrue(reason != null)
            assertTrue(TailnetLink.running)
            assertTrue(TailnetLink.wireGuard)
        } finally {
            TailnetLink.running = false
            TailnetLink.wireGuard = false
        }
    }

    // T-0986, T-0992: wg.go took 1420 for a config with no MTU; on mobile data the house's
    // full-size packets were lost, so no map answer and no APK reached the phone away from
    // home. 1280 still lost them (1797's log): a 1280-byte mobile path less the tunnel's 80.
    @Test
    fun `the tunnel starts at a mobile-safe MTU, and the saved config is left as it was`() {
        val was = TailnetLink.start
        val starts = mutableListOf<String>()
        try {
            TailnetLink.start = { c, _ -> starts += c }
            val bare = "[Interface]\r\nPrivateKey = x=\r\nAddress = 10.0.0.2/32\r\n[Peer]\r\nPublicKey = y=\r\n"
            assertEquals(null, TailnetLink.startWireGuard(bare, ""))
            assertEquals(
                "[Interface]\nMTU = 1200\r\nPrivateKey = x=\r\nAddress = 10.0.0.2/32\r\n[Peer]\r\nPublicKey = y=\r\n",
                starts.single(),
            )
            // The same saved config again is the tunnel already up, not a restart.
            assertEquals(null, TailnetLink.startWireGuard(bare, ""))
            assertEquals(1, starts.size)

            assertEquals("[Interface]\nmtu = 1200\nPrivateKey = x=\n", TailnetLink.mobileMtu("[Interface]\nmtu = 1420\nPrivateKey = x=\n"))
            assertEquals("[Interface]\nMTU=1100\n", TailnetLink.mobileMtu("[Interface]\nMTU=1100\n"))
            assertEquals("not a config", TailnetLink.mobileMtu("not a config"))
        } finally {
            TailnetLink.start = was
            TailnetLink.stop()
        }
    }

    // T-0913: the start resolves the config's Endpoint, so a tunnel started while the phone
    // could not resolve it (an update installing in the car, on its no-internet Wi-Fi) stayed
    // off for good, and away from home the phone and the car could not reach the house.
    @Test
    fun `a saved tunnel that would not start is tried again until it comes up`() {
        val was = TailnetLink.start
        val waits = mutableListOf<Long>()
        var tries = 0
        try {
            TailnetLink.start = { _, _ -> if (++tries < 7) error("Endpoint: lookup home: no such host") }
            TailnetLink.keepUp("", { "[Interface]\nPrivateKey = x\n" }) { waits += it }
            assertEquals(7, tries)
            assertEquals(listOf(2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L), waits)
            assertTrue(TailnetLink.wireGuard)
            assertEquals(null, TailnetLink.lastError)
        } finally {
            TailnetLink.start = was
            TailnetLink.stop()
        }
    }

    // T-0738, T-0855: the start resolves the Endpoint once. Resolved on home Wi-Fi (where the
    // house's own DNS answers its home address), the tunnel went nowhere on mobile data, and
    // only an app restart brought the phone and the car back: a whole drive without Jarvis.
    @Test
    fun `a new network starts the same tunnel again, so its Endpoint is resolved there`() {
        val was = TailnetLink.start
        val starts = mutableListOf<String>()
        val conf = "[Interface]\nPrivateKey = x\n"
        try {
            TailnetLink.start = { c, _ -> starts += c }
            assertEquals(null, TailnetLink.startWireGuard(conf, ""))
            // The first network the watch hears is the one the tunnel started on.
            assertFalse(TailnetLink.moved(null, "wifi"))
            assertFalse(TailnetLink.moved("wifi", "wifi"))
            assertTrue(TailnetLink.moved("wifi", "cell"))
            TailnetLink.renew("") { error("no wait when the first try works") }
            assertEquals(listOf(TailnetLink.mobileMtu(conf), TailnetLink.mobileMtu(conf)), starts)
            assertTrue(TailnetLink.wireGuard)
        } finally {
            TailnetLink.start = was
            TailnetLink.stop()
        }
    }

    @Test
    fun `a renewal the new network cannot resolve yet is tried again, and keeps the link meanwhile`() {
        val was = TailnetLink.start
        val waits = mutableListOf<Long>()
        var tries = 0
        try {
            TailnetLink.start = { _, _ -> }
            TailnetLink.startWireGuard("[Interface]\nPrivateKey = x\n", "")
            TailnetLink.start = { _, _ -> if (++tries < 3) error("Endpoint: lookup home: no such host") }
            TailnetLink.renew("") { waits += it; assertTrue(TailnetLink.wireGuard) }
            assertEquals(3, tries)
            assertEquals(listOf(2_000L, 4_000L), waits)
            // No tunnel, nothing to renew.
            TailnetLink.stop()
            TailnetLink.renew("") { error("no tunnel, no tries") }
            assertEquals(3, tries)
        } finally {
            TailnetLink.start = was
            TailnetLink.stop()
        }
    }

    @Test
    fun `a forgotten tunnel is not tried again, and why it was down is kept for Settings`() {
        val was = TailnetLink.start
        var conf = "[Interface]\nPrivateKey = x\n"
        try {
            TailnetLink.start = { _, _ -> error("Endpoint: lookup home: no such host") }
            TailnetLink.keepUp("", { conf }) { conf = "" }
            assertFalse(TailnetLink.wireGuard)
            assertEquals("Endpoint: lookup home: no such host", TailnetLink.lastError)
        } finally {
            TailnetLink.start = was
            TailnetLink.stop()
        }
    }

    @Test
    fun `Settings names the WireGuard step that fails`() {
        // The owner, 2026-10-30: "the WG tunnel wont connect to jarvis". Each of
        // Diagnose's reports (wg.go) gets a sentence that says which step broke.
        val host = "jarvis.home.example"
        fun words(report: String) = TailnetLink.wireGuardWords(report, host)
        assertEquals("Connected home through WireGuard: AURA answers at 10.20.30.59.", words("ok|10.20.30.59"))
        assertTrue(words("down|").startsWith("The WireGuard tunnel isn't running."))
        assertTrue(words("handshake|10.20.30.59").contains("its server hasn't answered"))
        assertTrue(words("handshake|10.20.30.59").contains("Endpoint"))
        assertTrue(words("name|").contains("nothing here knows $host's address"))
        assertTrue(words("covered|8.8.8.8").contains("$host is at 8.8.8.8, outside the config's AllowedIPs"))
        val refused = words("answer|10.20.30.59|connect tcp 10.20.30.59:443: connection was refused")
        assertTrue(refused.contains("WireGuard reaches 10.20.30.59, but AURA doesn't answer there"))
        assertTrue(refused.contains("connection was refused"))
        // A report this build does not know is shown as it came, never dropped.
        assertEquals("route|odd", words("route|odd"))
    }

    @Test
    fun `the forwarder is dialled on IPv4 loopback, where it listens`() {
        // getLoopbackAddress() was ::1 on the owner's phone; the forwarder is 127.0.0.1 only.
        assertEquals("127.0.0.1", TailnetLink.FORWARDER.hostAddress)
    }
}
