package ai.aura.app.ui

import ai.aura.app.tailnet.TailnetLink
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * Chromium finishes its own error page: `onReceivedError` and then `onPageFinished`
 * for the same failed load. Counting that as loaded hid the phone's native screen and
 * the car's rail under a transparent, empty page whenever the console could not be
 * reached (the owner, 2026-10-06: "the screen goes blank if it can't connect").
 */
@RunWith(RobolectricTestRunner::class)
class ConsolePageLoadTest {

    private var loaded = 0
    private var failed = 0

    private fun page() = ConsolePage(
        ApplicationProvider.getApplicationContext(),
        "http://console.invalid/",
        " Test",
        onLoaded = { loaded++ },
        onFailed = { failed++ },
    ).also { it.start() }

    private fun mainFrame() = object : WebResourceRequest {
        override fun getUrl(): Uri = Uri.parse("http://console.invalid/")
        override fun isForMainFrame() = true
        override fun isRedirect() = false
        override fun hasGesture() = false
        override fun getMethod() = "GET"
        override fun getRequestHeaders(): Map<String, String> = emptyMap()
    }

    // A server error, not a lookup failure: `WebResourceError` cannot be built outside
    // the framework, and both arrive at the same `retry` before the page finishes.
    private val down = WebResourceResponse("text/html", "utf-8", 502, "Bad Gateway", emptyMap(), null)

    @Test
    fun aFailedLoadIsNotLoadedEvenWhenItsErrorPageFinishes() {
        val page = page()
        val client = page.view.webViewClient
        client.onReceivedHttpError(page.view, mainFrame(), down)
        client.onPageFinished(page.view, "http://console.invalid/")
        assertEquals(0, loaded)
        assertEquals(1, failed)
        assertEquals(0L, ConsolePage.lastReached(page.view.context))
        page.stop()
    }

    // T-0481: the offline page says how old the phone's last contact is, so nothing on it
    // reads as live. A load that finished records it; the line keeps the page's own text
    // until one has.
    @Test
    fun theOfflinePageSaysWhenJarvisWasLastReached() {
        val ctx = ApplicationProvider.getApplicationContext<android.content.Context>()
        assertNull(ConsolePage.reachedLine(ctx, ConsolePage.lastReached(ctx)))
        val page = page()
        page.view.webViewClient.onPageFinished(page.view, "http://console.invalid/")
        val at = ConsolePage.lastReached(ctx)
        assertTrue(at > 0L)
        val today = ConsolePage.reachedLine(ctx, at)!!
        assertTrue(today, today.startsWith("Last reached AURA at "))
        val before = ConsolePage.reachedLine(ctx, at - 3 * 86_400_000L)!!
        assertTrue(before, before.startsWith("Last reached AURA on "))
        page.stop()
    }

    // T-0711: away from home the phone and the car showed a one-page offline card or the old
    // Headway rail (the owner, 2026-11-08: "the entire look ... even when it can't reach
    // jarvis"). A console that has answered before comes back from the web view's own cache,
    // whole, its link down until the page's own reconnect gets through; it is not "reached".
    @Test
    fun aConsoleThatAnsweredBeforeComesBackFromTheCache() {
        val page = page()
        val client = page.view.webViewClient
        client.onPageFinished(page.view, "http://console.invalid/")
        val reached = ConsolePage.lastReached(page.view.context)
        client.onReceivedHttpError(page.view, mainFrame(), down)
        assertEquals(android.webkit.WebSettings.LOAD_CACHE_ONLY, page.view.settings.cacheMode)
        assertEquals("http://console.invalid/", shadowOf(page.view).lastLoadedUrl)
        client.onPageFinished(page.view, "http://console.invalid/")
        assertEquals(2, loaded)
        assertEquals(0, failed)
        // The page's own fetches go to the network again; only the shell came from the cache.
        assertEquals(android.webkit.WebSettings.LOAD_DEFAULT, page.view.settings.cacheMode)
        assertEquals(reached, ConsolePage.lastReached(page.view.context))
        page.stop()
    }

    @Test
    fun aStartWithACachedConsoleShowsItAfterTheShortFirstWatchdog() {
        // T-0787: with no route home the first load hangs; the car stood on an empty ground for 20 s.
        val first = page()
        first.view.webViewClient.onPageFinished(first.view, "http://console.invalid/")
        first.stop()
        val page = page()
        shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofSeconds(7))
        assertEquals(android.webkit.WebSettings.LOAD_CACHE_ONLY, page.view.settings.cacheMode)
        assertEquals(0, failed)
        page.stop()
    }

    @Test
    fun aStartWithNoCachedConsoleKeepsTheLongWatchdog() {
        val page = page()
        shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofSeconds(7))
        assertEquals(0, failed)
        org.junit.Assert.assertNotEquals(android.webkit.WebSettings.LOAD_CACHE_ONLY, page.view.settings.cacheMode)
        shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofSeconds(14))
        assertEquals(1, failed)
        page.stop()
    }

    @Test
    fun aCacheMissFallsBackToTheOfflinePath() {
        val page = page()
        val client = page.view.webViewClient
        client.onPageFinished(page.view, "http://console.invalid/")
        client.onReceivedHttpError(page.view, mainFrame(), down)
        // Nothing cached for this page: the miss is one more failure.
        client.onReceivedHttpError(page.view, mainFrame(), down)
        client.onPageFinished(page.view, "http://console.invalid/")
        assertEquals(1, loaded)
        assertEquals(1, failed)
        assertEquals(android.webkit.WebSettings.LOAD_DEFAULT, page.view.settings.cacheMode)
        page.stop()
    }

    // T-0913: the cache was tried once per live load, so after the cached console painted the
    // next main-frame failure (a reload, a navigation) dropped the car and the phone to the
    // offline page for the rest of the drive. Only a miss of the cache itself falls through.
    @Test
    fun aLaterFailureAfterTheCachedConsoleShowsTheCacheAgain() {
        val page = page()
        val client = page.view.webViewClient
        client.onPageFinished(page.view, "http://console.invalid/")
        client.onReceivedHttpError(page.view, mainFrame(), down)
        client.onPageFinished(page.view, "http://console.invalid/")
        shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofSeconds(3))
        client.onReceivedHttpError(page.view, mainFrame(), down)
        assertEquals(android.webkit.WebSettings.LOAD_CACHE_ONLY, page.view.settings.cacheMode)
        client.onPageFinished(page.view, "http://console.invalid/")
        assertEquals(3, loaded)
        assertEquals(0, failed)
        page.stop()
    }

    // A cached page that fails again the moment it paints would reload itself in a loop.
    @Test
    fun aCachedConsoleThatFailsAtOnceIsNotShownAgain() {
        val page = page()
        val client = page.view.webViewClient
        client.onPageFinished(page.view, "http://console.invalid/")
        client.onReceivedHttpError(page.view, mainFrame(), down)
        client.onPageFinished(page.view, "http://console.invalid/")
        client.onReceivedHttpError(page.view, mainFrame(), down)
        assertEquals(android.webkit.WebSettings.LOAD_DEFAULT, page.view.settings.cacheMode)
        assertEquals(1, failed)
        page.stop()
    }

    // T-0387: the console refuses its relays to anyone not signed in, and the phone
    // and the car have no password to type. The page load carries the adapter key
    // the app was paired with; the console answers it with a session cookie, which
    // the view's own fetches and sockets then carry.
    @Test
    fun thePageLoadCarriesTheAdapterKey() {
        val page = ConsolePage(ApplicationProvider.getApplicationContext(), "http://console.invalid/", " Test", key = "the-key")
        page.start()
        assertEquals("Bearer the-key", shadowOf(page.view).lastAdditionalHttpHeaders?.get("Authorization"))
        page.stop()
    }

    @Test
    fun noKeyNoHeader() {
        val page = page()
        assertNull(shadowOf(page.view).lastAdditionalHttpHeaders?.get("Authorization"))
        page.stop()
    }

    @Test
    fun aLoadThatWorksIsLoaded() {
        val page = page()
        page.view.webViewClient.onPageFinished(page.view, "http://console.invalid/")
        assertEquals(1, loaded)
        assertEquals(0, failed)
        page.stop()
    }

    // T-0399: a restarted node starts with an empty allow-list and may hand back the
    // same proxy port, so the page re-allows its origin on every route, not only when
    // the override changes; otherwise the car's console is refused after a reconnect.
    @Test
    fun everyRouteReAllowsTheConsoleOrigin() {
        val was = Triple(TailnetLink.proxy, TailnetLink.allow, TailnetLink.running)
        val wasWg = TailnetLink.wireGuard
        val allowed = mutableListOf<String>()
        try {
            TailnetLink.running = true
            TailnetLink.wireGuard = true
            TailnetLink.proxy = { 4242 }
            TailnetLink.allow = { allowed += it }
            val page = page()
            page.resume()
            page.resume()
            assertEquals(List(3) { "console.invalid:80" }, allowed)
            page.stop()
        } finally {
            TailnetLink.proxy = was.first
            TailnetLink.allow = was.second
            TailnetLink.running = was.third
            TailnetLink.wireGuard = wasWg
        }
    }
}
