package ai.jarvis.app.channel

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** One persistent connection: the phone says plainly whether it is connected, and nothing asked offline is lost at once. */
@RunWith(RobolectricTestRunner::class)
class ChannelLinkTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()

    private fun channel(queue: Int = 3) = JarvisChannel(
        app,
        configProvider = {
            ChannelConfig(
                serverUrl = "http://192.168.1.2:8080", token = "t", deviceId = "d",
                deviceName = "n", appVersion = "1", offlineEventQueue = queue,
            )
        },
    )

    @Test fun `every channel state reads as connected, connecting or offline`() {
        val want = mapOf(
            JarvisChannel.State.READY to JarvisChannel.Link.CONNECTED,
            JarvisChannel.State.CONNECTING to JarvisChannel.Link.CONNECTING,
            JarvisChannel.State.AUTHENTICATING to JarvisChannel.Link.CONNECTING,
            JarvisChannel.State.REGISTERING to JarvisChannel.Link.CONNECTING,
            JarvisChannel.State.BACKING_OFF to JarvisChannel.Link.OFFLINE,
            JarvisChannel.State.OFFLINE to JarvisChannel.Link.OFFLINE,
            JarvisChannel.State.BLOCKED to JarvisChannel.Link.OFFLINE,
            JarvisChannel.State.STOPPED to JarvisChannel.Link.OFFLINE,
        )
        assertEquals(JarvisChannel.State.values().toSet(), want.keys)
        for ((state, link) in want) assertEquals(state.name, link, JarvisChannel.Link.of(state))
    }

    // T-0918: the channel built its own client, so with AURA's tunnel up the home name still went
    // to the phone's ordinary DNS and never resolved away from home.
    @Test fun `the channel reaches the house through AURA's tunnel, as every other client does`() {
        val http = channel().http
        assertSame(ai.jarvis.app.tailnet.TailnetLink.dns, http.dns)
        assertSame(ai.jarvis.app.tailnet.TailnetLink.socketFactory, http.socketFactory)
    }

    @Test fun `a channel that has not connected exposes offline`() {
        assertEquals(JarvisChannel.Link.OFFLINE, channel().link.value)
    }

    @Test fun `events made offline queue up to the cap, newest kept`() {
        val c = channel(queue = 3)
        repeat(5) { assertTrue(c.sendEvent("e$it", JSONObject(), false)) }
        assertEquals(3, c.queuedEvents)
    }

    @Test fun `a request made offline waits for the link instead of failing at once`() {
        val c = channel()
        val began = System.nanoTime()
        val answer = runBlocking { c.request("jarvis/ping", timeoutMs = 300L) }
        val tookMs = (System.nanoTime() - began) / 1_000_000
        assertEquals(null, answer)
        assertTrue("returned in $tookMs ms, before the wait", tookMs >= 250)
    }
}
