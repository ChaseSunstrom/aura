package ai.aura.app.car.dash

import ai.aura.app.car.theme.CarMetrics
import android.content.Context
import android.hardware.display.DisplayManager
import android.media.AudioManager
import android.os.Looper
import android.view.KeyEvent
import android.webkit.WebView
import androidx.test.core.app.ApplicationProvider
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowWebView

/**
 * T-1078: `CarHud.perform`'s acts driven from the page's door, the `JarvisCar.act(json)` method the
 * console's /car calls, down to their effect. The readers have their own tests (`CarHudTest`); this
 * is the wiring: that `"say"` still reaches the car's voice, that `"media"` still reaches the
 * transport, that `"packroute"` still answers on the page's event.
 *
 * Does not cover the page's own JavaScript calling the bridge: Robolectric's web view runs no script,
 * so that needs a device (T-0675). The door is found where the web view recorded it
 * ([ShadowWebView.getJavascriptInterface]) and called by reflection, as the platform would.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarHudBridgeTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private lateinit var shell: CarShell
    private lateinit var hud: CarHud
    private val audio get() = shadowOf(context.getSystemService(AudioManager::class.java))
    private val keys: List<KeyEvent> get() = audio.dispatchedMediaKeyEvents
    private val said = mutableListOf<String>()
    private val prepared = mutableListOf<List<String>>()

    @Before
    fun open() {
        CarShell.onSay = { said += it }
        CarShell.onPrepare = { prepared += it }
        val display = context.getSystemService(DisplayManager::class.java).getDisplay(android.view.Display.DEFAULT_DISPLAY)
        shell = CarShell(context, display, CarMetrics(widthPx = 800, heightPx = 480, densityDpi = 160))
        hud = CarHud(context, "http://console.test", shell)
        // No notification access in a test: the transport takes the media-key road, which the shadow records.
        audio.clearDispatchedMediaKeyEvents()
    }

    @After
    fun close() {
        CarShell.onSay = null
        CarShell.onPrepare = null
    }

    /** What the page does: `window.JarvisCar.act(json)`, which the platform runs off the main thread. */
    private fun act(json: String) {
        val door = shadowOf(hud.view as WebView).getJavascriptInterface("JarvisCar")
        assertNotNull("the page's door is not registered as JarvisCar", door)
        door!!.javaClass.getDeclaredMethod("act", String::class.java).apply { isAccessible = true }.invoke(door, json)
        shadowOf(Looper.getMainLooper()).idle()
    }

    private fun downKeys() = keys.filter { it.action == KeyEvent.ACTION_DOWN }.map { it.keyCode }

    @Test
    fun `a media act reaches the transport as the key for its op`() {
        for ((op, key) in listOf(
            "play" to KeyEvent.KEYCODE_MEDIA_PLAY,
            "pause" to KeyEvent.KEYCODE_MEDIA_PAUSE,
            "next" to KeyEvent.KEYCODE_MEDIA_NEXT,
            "previous" to KeyEvent.KEYCODE_MEDIA_PREVIOUS,
        )) {
            audio.clearDispatchedMediaKeyEvents()
            act("""{"kind":"media","op":"$op"}""")
            assertEquals(op, listOf(key), downKeys())
            // Each key is a down and an up (PhoneMediaControl's contract).
            assertEquals(op, listOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP), keys.map { it.action })
        }
    }

    @Test
    fun `a media act with an op the phone does not know sends no key`() {
        // It skipped back a track: the last branch was "else", so a typo or a new op was PREVIOUS (T-1216).
        for (op in listOf("rewind", "prev", "")) {
            audio.clearDispatchedMediaKeyEvents()
            act("""{"kind":"media","op":"$op"}""")
            assertEquals(op, emptyList<Int>(), downKeys())
        }
    }

    @Test
    fun `a say act is spoken on the car's voice and a blank one is not`() {
        act("""{"kind":"say","text":"Turn left in two hundred metres"}""")
        assertEquals(listOf("Turn left in two hundred metres"), said)
        act("""{"kind":"say","text":"   "}""")
        act("""{"kind":"say"}""")
        assertEquals(1, said.size)
    }

    @Test
    fun `a prepare act hands the voice at most five lines, each cut to 200 characters`() {
        val lines = (1..7).joinToString(",") { "\"line $it ${"x".repeat(300)}\"" }
        act("""{"kind":"prepare","texts":[$lines]}""")
        val got = prepared.single()
        assertEquals(5, got.size)
        assertTrue(got.all { it.length == 200 })
        act("""{"kind":"prepare","texts":["",""]}""")
        assertEquals(1, prepared.size)
    }

    @Test
    fun `a pack route ask is answered on the page's route event with the ask's id`() {
        val web = shadowOf(hud.view as WebView)
        act("""{"kind":"packroute","id":7,"from":[59.33,18.06],"to":[59.3308,18.0638]}""")
        // The plan is built on its own thread and posted back to the main one.
        val until = System.currentTimeMillis() + 10_000
        while (web.lastEvaluatedJavascript?.contains("jarvis:car-route") != true && System.currentTimeMillis() < until) {
            Thread.sleep(20)
            shadowOf(Looper.getMainLooper()).idle()
        }
        val js = web.lastEvaluatedJavascript
        assertTrue("no answer reached the page: $js", js?.contains("new CustomEvent('jarvis:car-route',{detail:") == true)
        // No pack under a test: the answer is the contract's "none", for this ask.
        val detail = JSONObject(js!!.substringAfter("{detail:").substringBeforeLast("}))"))
        assertEquals(7, detail.getInt("id"))
        assertTrue(detail.isNull("route"))
    }

    @Test
    fun `a pack route ask with an end missing is not answered`() {
        val web = shadowOf(hud.view as WebView)
        act("""{"kind":"packroute","id":8,"from":[59.33,18.06]}""")
        Thread.sleep(200)
        shadowOf(Looper.getMainLooper()).idle()
        assertTrue(web.lastEvaluatedJavascript?.contains("jarvis:car-route") != true)
    }

    @Test
    fun `an unknown act and malformed json do nothing`() {
        val web = shadowOf(hud.view as WebView)
        act("""{"kind":"dance","op":"next","text":"hello","id":7,"from":[1,2],"to":[3,4]}""")
        act("""{"op":"next"}""")
        act("not json {")
        act("")
        act("[1,2]")
        assertTrue(keys.isEmpty())
        assertTrue(said.isEmpty())
        assertTrue(prepared.isEmpty())
        assertTrue(web.lastEvaluatedJavascript?.contains("jarvis:car-route") != true)
    }
}
