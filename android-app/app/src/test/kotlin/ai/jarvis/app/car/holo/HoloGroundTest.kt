package ai.jarvis.app.car.holo

import android.Manifest
import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.location.Location
import android.location.LocationManager
import android.os.Looper
import android.view.View
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowLog

/**
 * The wireless car's hologram ground (T-0741, T-0747). The owner, 2026-11-08: "the map
 * isn't a smooth drive like Google maps, its choppy and only updates like once a second".
 * The page's map moved once a phone state, a second apart; the ground is drawn each vsync
 * from where [CarPosition] puts the car at that instant.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class HoloGroundTest {

    // These pin the 3D drive, which the owner paused (T-1252): it is drawn only when asked for.
    @org.junit.Before fun threeD() { HoloGround.threeD = true }
    @org.junit.After fun flat() { HoloGround.threeD = false }
    private val context: Context = ApplicationProvider.getApplicationContext()
    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private var now = 0L

    private fun ground() = GroundView(context) { now }.apply {
        measure(View.MeasureSpec.makeMeasureSpec(W, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(H, View.MeasureSpec.EXACTLY))
        layout(0, 0, W, H)
        route(HoloRoute(listOf(at(0f, -40f), at(0f, 400f)), "Larch Way"))
    }

    private fun GroundView.fixAt(north: Float, t: Long) =
        at(0f, north).let { feed.fix(it.lat, it.lon, SPEED, 0f, 5f, t) }

    private fun GroundView.frame(): Bitmap =
        Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888).also { draw(Canvas(it)) }

    @Test
    fun `between fixes a second apart the car moves every frame, by what its speed carries it in one`() {
        val g = ground()
        for (i in 0..2) assertNotNull(g.fixAt(north = SPEED * i, t = i * 1_000L))
        var last = plane.north(g.position.at(2_000L)!!.lat)
        var t = 2_000L
        while (t < 3_000L) {
            t += FRAME_MS
            val north = plane.north(g.position.at(t)!!.lat)
            val step = north - last
            assertTrue("frame at $t ms did not move ($step m)", step > 0f)
            assertTrue("frame at $t ms jumped $step m", step <= SPEED * FRAME_MS / 1000f * 1.5f)
            last = north
        }
    }

    @Test
    fun `two frames a vsync apart are two different pictures`() {
        val g = ground()
        for (i in 0..2) g.fixAt(north = SPEED * i, t = i * 1_000L)
        now = 2_400L
        val a = g.frame()
        now += FRAME_MS
        val b = g.frame()
        assertFalse("the ground drew the same picture a frame later", a.sameAs(b))
    }

    @Test
    fun `before the first fix it draws the sky, not a car at nowhere`() {
        val g = ground()
        assertNull(g.position.at(now))
        val sky = g.frame()
        assertTrue("nothing drawn", sky.getPixel(W / 2, H / 4) != 0)
    }

    @Test
    fun `shown it listens to GPS, and hidden it stops and draws nothing`() {
        shadowOf(context as Application).grantPermissions(Manifest.permission.ACCESS_FINE_LOCATION)
        val lm = context.getSystemService(LocationManager::class.java)
        val g = ground()
        assertEquals(View.GONE, g.visibility)

        g.show(true)
        assertEquals(View.VISIBLE, g.visibility)
        now = 10_000L
        shadowOf(lm).simulateLocation(fix(at(0f, 0f), now))
        shadowOf(Looper.getMainLooper()).idle()
        assertNotNull("a shown ground took no GPS fix", g.position.at(now))

        g.show(false)
        assertEquals(View.GONE, g.visibility)
        val before = g.position.at(now)!!
        shadowOf(lm).simulateLocation(fix(at(0f, 200f), now + 1_000L))
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals("a hidden ground still listens to GPS", before.lat, g.position.at(now)!!.lat, 1e-9)
    }

    // The owner, 2026-11-10: "why do buildings and the map not show in the car". A phone paired
    // to the adapter (the owner's) built no maps client, so the ground asked for no buildings and
    // matched nothing; the adapter relays the house's maps now (tests/contracts/house-maps.json).
    @Test
    fun `a phone paired to the adapter feeds its ground the house's maps`() {
        shadowOf(context as Application).grantPermissions(Manifest.permission.ACCESS_FINE_LOCATION)
        ai.jarvis.app.config.JarvisConfig(context).apply {
            serverUrl = "http://adapter.example:8643"
            token = "test-token" // pragma: allowlist secret
            serverKind = ai.jarvis.app.config.ServerKind.HERMES
        }
        val g = ground()
        g.show(true)
        assertNotNull("no house maps on an adapter pairing", g.feed.maps)
        g.show(false)
    }

    // T-0980: a drive drew no buildings, and the phone's log could not say whether the house
    // answered none, answered and the scene had none near, or never answered. Each answer is
    // logged by its counts and the nearest footprint's distance, never by where the car is.
    @Test
    fun `the house's buildings and roads reach the scene and the log says how many and how near`() {
        val g = ground()
        val here = at(0f, 0f)
        val box = listOf(at(0f, 12f), at(10f, 12f), at(10f, 22f), at(0f, 22f))
        val cam = HoloCamera().apply { resize(W.toFloat(), H.toFloat(), 1f) }
        fun polys() = HoloFrame().also { f -> g.scene { it.build(f, cam, here.lat, here.lon, 0f, 1000L) } }.let { it.polyCount + it.solidCount }
        val before = polys()
        ShadowLog.clear()
        g.feed.took(NearMap(listOf(HoloBuilding(box, 9f)), listOf(HoloRoad(listOf(at(-20f, 30f), at(20f, 30f)), "residential"))), here)
        val line = ShadowLog.getLogsForTag("DriveFeed").joinToString("\n") { it.msg }
        assertTrue(line, line.contains("1 buildings (nearest 12 m), 1 roads"))
        assertFalse("no coordinates in the log: $line", line.contains("59.") || line.contains("18.0"))
        assertTrue("the building is drawn", polys() > before)
    }

    // T-0977: a first ask that timed out at a drive's start left the parked ground empty 30 s.
    @Test
    fun `a parked car asks for its buildings again once the house was away, sooner first, not every fix`() {
        assertTrue(DriveFeed.asksBuildings(null, null, 0, 0L))
        assertFalse(DriveFeed.asksBuildings(10f, null, 0, 99_000L))
        assertTrue(DriveFeed.asksBuildings(250f, null, 0, 0L))
        for ((fails, wait) in listOf(1 to 5_000L, 2 to 10_000L, 3 to 20_000L, 4 to 30_000L, 9 to 30_000L)) {
            assertFalse("after $fails failures", DriveFeed.asksBuildings(0f, 1_000L, fails, 1_000L + wait - 1))
            assertTrue("after $fails failures", DriveFeed.asksBuildings(0f, 1_000L, fails, 1_000L + wait))
        }
    }

    private fun fix(p: LatLon, t: Long) = Location(LocationManager.GPS_PROVIDER).apply {
        latitude = p.lat
        longitude = p.lon
        speed = SPEED
        bearing = 0f
        accuracy = 5f
        time = t
        elapsedRealtimeNanos = t * 1_000_000L
    }

    private companion object {
        const val W = 800
        const val H = 480
        const val SPEED = 15f
        /** One frame at 60 Hz. */
        const val FRAME_MS = 16L
    }
}
