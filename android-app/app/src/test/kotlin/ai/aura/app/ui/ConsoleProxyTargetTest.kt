package ai.aura.app.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * T-0394: the node's proxy is a loopback port every app on the phone can reach, so the
 * web view sends it only the console's host and the proxy carries only its host:port.
 * Before, a WireGuard link sent every request to the proxy and the proxy dialled anything.
 */
class ConsoleProxyTargetTest {

    @Test
    fun `a WireGuard link routes the console's own origin, the scheme's port written out`() {
        assertEquals("jarvis.home.example:443" to "jarvis.home.example", ConsolePage.proxyTarget("https://Jarvis.Home.Example/", wireGuard = true))
        assertEquals("10.20.30.59:8199" to "10.20.30.59", ConsolePage.proxyTarget("http://10.20.30.59:8199/car", wireGuard = true))
        assertEquals("jarvis.lan:80" to "jarvis.lan", ConsolePage.proxyTarget(" http://jarvis.lan/ ", wireGuard = true))
        assertEquals("jarvis_box.lan:8199" to "jarvis_box.lan", ConsolePage.proxyTarget("http://jarvis_box.lan:8199/", wireGuard = true))
    }

    @Test
    fun `with no tunnel the console loads direct`() {
        assertNull(ConsolePage.proxyTarget("http://jarvis.tail1.ts.net:8199/", wireGuard = false))
        assertNull(ConsolePage.proxyTarget("http://10.20.30.59:8199/", wireGuard = false))
    }

    @Test
    fun `a console with no host routes nothing`() {
        assertNull(ConsolePage.proxyTarget("", wireGuard = true))
        assertNull(ConsolePage.proxyTarget("not a url", wireGuard = true))
        assertNull(ConsolePage.proxyTarget("file:///android_asset/x.html", wireGuard = true))
    }
}
