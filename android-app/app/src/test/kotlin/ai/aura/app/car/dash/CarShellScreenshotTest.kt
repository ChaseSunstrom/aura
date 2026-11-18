package ai.aura.app.car.dash

import ai.aura.app.car.HeadwaySettings
import ai.aura.app.car.theme.CarMetrics
import ai.aura.app.config.JarvisConfig
import ai.aura.app.config.ServerKind
import ai.aura.app.car.voice.CarTurnState
import ai.aura.app.car.voice.CarVoice
import ai.aura.app.car.voice.CarVoiceEvent
import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.Duration

/**
 * The car screen (ADR-0047) as a 2021 Chevrolet Infotainment 3 unit sees it: 800x480
 * at 160 dpi, every shipped tab. Drawn by the real shell on a Robolectric display, so
 * the rail, the tabs and the panes are the ones a car gets -- with no car, no media
 * session and no link, which is what each pane's empty state is for.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w800dp-h480dp-land-mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class CarShellScreenshotTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    private fun shot(
        tab: String,
        name: String = tab.lowercase(),
        turn: List<CarVoiceEvent> = emptyList(),
    ) {
        CarVoice.reset()
        // A turn only happens on a paired phone; unpaired, the reactor is offline whatever the turn says.
        JarvisConfig(context).apply {
            serverUrl = if (turn.isEmpty()) "" else "http://jarvis.test:8643"
            token = if (turn.isEmpty()) "" else "paired"
        }
        // Robolectric runs frames synchronously: an endlessly repeating animator
        // (the link mark's sweep) would never let the looper go idle.
        android.animation.ValueAnimator::class.java.getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
        HeadwaySettings.setStartLayout(context, HeadwaySettings.START_LAYOUT_NAMED + tab)
        // The rail's clock is the time of the run, which would change every golden.
        HeadwaySettings.setRailStyle(context, dev.headway.dash.RailStyle(showClock = false))
        val display = context.getSystemService(DisplayManager::class.java).getDisplay(android.view.Display.DEFAULT_DISPLAY)
        val shell = CarShell(context, display, CarMetrics(widthPx = 800, heightPx = 480, densityDpi = 160))
        shell.show()
        // The reactor's ignition, then the startup fading away.
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1500))
        turn.forEach(CarVoice::post)
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(500))
        // Drawn by hand: Roborazzi's View capture goes through Espresso, which wants
        // an Activity, and a Presentation is a window with none.
        val root = shell.window!!.decorView
        root.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(800, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(480, android.view.View.MeasureSpec.EXACTLY),
        )
        root.layout(0, 0, 800, 480)
        val picture = android.graphics.Bitmap.createBitmap(800, 480, android.graphics.Bitmap.Config.ARGB_8888)
        root.draw(android.graphics.Canvas(picture))
        picture.captureRoboImage("src/test/screenshots/car-tab-$name.png")
        shell.teardown()
        CarVoice.reset()
    }

    @Test fun `the Jarvis tab`() = shot("Jarvis")

    @Test fun `the Jarvis tab answering a question asked on the steering wheel`() = shot(
        "Jarvis",
        name = "jarvis-speaking",
        turn = listOf(
            CarVoiceEvent.Listening,
            CarVoiceEvent.Heard("Turn on the bed light"),
            CarVoiceEvent.Turn(CarTurnState.Speaking, "The bed light is on, Sir."),
        ),
    )
    @Test fun `the Maps tab`() = shot("Maps")
    @Test fun `the Music tab`() = shot("Music")
    @Test fun `the Apps tab`() = shot("Apps")
    @Test fun `the Phone tab`() = shot("Phone")
    @Test fun `the Messages tab`() = shot("Messages")
    @Test fun `the Car tab`() = shot("Car")

    // T-0741: the owner's car showed the page's flat street map; T-0747: it moved once a second.
    @Test
    fun `the car page over the phone's hologram ground`() {
        CarVoice.reset()
        JarvisConfig(context).apply {
            serverUrl = "http://jarvis.test:8643"
            token = "paired"
            serverKind = ServerKind.RELAY
        }
        android.animation.ValueAnimator::class.java.getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
        HeadwaySettings.setRailStyle(context, dev.headway.dash.RailStyle(showClock = false))
        val display = context.getSystemService(DisplayManager::class.java).getDisplay(android.view.Display.DEFAULT_DISPLAY)
        val shell = CarShell(context, display, CarMetrics(widthPx = 800, heightPx = 480, densityDpi = 160))
        shell.show()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1500))
        val root = shell.window!!.decorView
        val ground = root.find<ai.aura.app.car.holo.GroundView>()!!
        val page = root.find<android.webkit.WebView>()!!
        val stack = ground.parent as android.view.ViewGroup
        assertTrue("the ground is not under the page", stack.indexOfChild(ground) < stack.indexOfChild(page))
        assertEquals("a ground nobody asked for is drawn", android.view.View.GONE, ground.visibility)

        // The page has loaded and asks for the ground, as Car.svelte does when the phone says access.ground.
        shell.hudReachable(true)
        shell.askGround(true)
        assertEquals(android.view.View.VISIBLE, ground.visibility)
        val plane = ai.aura.app.car.holo.GeoPlane(59.33, 18.06)
        fun at(east: Float, north: Float) = ai.aura.app.car.holo.LatLon(plane.lat(north), plane.lon(east))
        shell.groundRoute(ai.aura.app.car.holo.HoloRoute(listOf(at(0f, -40f), at(0f, 90f), at(220f, 90f)), "Larch Way", at(0f, 90f), "Quarry Road"))
        ground.scene { scene ->
            scene.setBuildings(
                (0 until 12).flatMap { i ->
                    val n = -10f + i * 22f
                    listOf(-34f to -14f, 14f to 34f).filterNot { (e0, _) -> e0 > 0 && n > 70f && n < 110f }.map { (e0, e1) ->
                        ai.aura.app.car.holo.HoloBuilding(listOf(at(e0, n), at(e1, n), at(e1, n + 16f), at(e0, n + 16f)), 10f + (i * 7 % 22))
                    }
                },
            )
        }
        val now = android.os.SystemClock.uptimeMillis()
        at(0f, 30f).let { ground.position.fix(it.lat, it.lon, 12f, 0f, 5f, now) }
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(200))

        root.measure(
            android.view.View.MeasureSpec.makeMeasureSpec(800, android.view.View.MeasureSpec.EXACTLY),
            android.view.View.MeasureSpec.makeMeasureSpec(480, android.view.View.MeasureSpec.EXACTLY),
        )
        root.layout(0, 0, 800, 480)
        val picture = android.graphics.Bitmap.createBitmap(800, 480, android.graphics.Bitmap.Config.ARGB_8888)
        root.draw(android.graphics.Canvas(picture))
        picture.captureRoboImage("src/test/screenshots/car-tab-ground.png")
        var lit = 0
        // The whole picture, not the middle of it: a new route given to a standing car is
        // first shown whole (T-1255), and its line runs wherever the route goes.
        for (y in 0 until 480 step 4) for (x in 0 until 800 step 4) {
            val c = picture.getPixel(x, y)
            if (((c shr 16) and 0xFF) + ((c shr 8) and 0xFF) + (c and 0xFF) > 180) lit++
        }
        assertTrue("no hologram drawn under the page ($lit lit)", lit > 50)

        // A reload (or the offline page) never runs the old page's cleanup: the start of
        // the next document takes the ground away until that one asks again.
        page.webViewClient.onPageStarted(page, page.url ?: "http://jarvis.test:8643/car", null)
        assertEquals("a reloaded page left the ground drawing", android.view.View.GONE, ground.visibility)
        shell.askGround(true)
        assertEquals(android.view.View.VISIBLE, ground.visibility)

        // The page leaves (or stops asking): the ground goes, and stops listening.
        shell.askGround(false)
        assertEquals(android.view.View.GONE, ground.visibility)
        shell.teardown()
        CarVoice.reset()
    }

    // T-0783: the owner's car showed Headway's rail and tiles until the console page loaded, so
    // every start, and the whole wait while home was out of reach, was the default Headway UI.
    @Test
    fun `with a console the car shows its page from the start, and the rail only when it cannot draw`() {
        CarVoice.reset()
        JarvisConfig(context).apply {
            serverUrl = "http://jarvis.test:8643"
            token = "paired"
            serverKind = ServerKind.RELAY
        }
        android.animation.ValueAnimator::class.java.getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
        val display = context.getSystemService(DisplayManager::class.java).getDisplay(android.view.Display.DEFAULT_DISPLAY)
        val shell = CarShell(context, display, CarMetrics(widthPx = 800, heightPx = 480, densityDpi = 160))
        shell.show()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1500))
        val page = shell.window!!.decorView.find<android.webkit.WebView>()!!
        // Not loaded (there is no server here): the page is on screen all the same, since it
        // draws the console, its cached copy or its offline page; the connect check still knows.
        assertEquals("the Headway rail stands in for a loading console", android.view.View.VISIBLE, page.visibility)
        assertEquals(false, shell.consoleUp())
        // A page that cannot draw even its offline page: the car's own screen, not a blank one.
        shell.hudReachable(false)
        assertEquals(android.view.View.INVISIBLE, page.visibility)
        shell.hudReachable(true)
        assertEquals(android.view.View.VISIBLE, page.visibility)
        assertEquals(true, shell.consoleUp())
        shell.teardown()
        CarVoice.reset()
    }

    private inline fun <reified T : android.view.View> android.view.View.find(): T? = find(T::class.java)

    private fun <T : android.view.View> android.view.View.find(type: Class<T>): T? {
        if (type.isInstance(this)) return type.cast(this)
        if (this is android.view.ViewGroup) for (i in 0 until childCount) getChildAt(i).find(type)?.let { return it }
        return null
    }
}
