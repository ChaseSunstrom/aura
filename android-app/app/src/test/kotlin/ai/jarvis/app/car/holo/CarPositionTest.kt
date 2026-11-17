package ai.jarvis.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Where the car is drawn between GPS fixes (T-0613): the house's map-match puts it on the
 * road, dead reckoning carries it between fixes, and neither a fix nor a match makes it jump.
 */
class CarPositionTest {

    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private fun east(p: DriveView.Pose) = plane.east(p.lon)
    private fun north(p: DriveView.Pose) = plane.north(p.lat)

    private fun CarPosition.fixAt(east: Float, north: Float, speed: Float, bearing: Float?, timeMs: Long, accuracy: Float = 5f) =
        at(east, north).let { fix(it.lat, it.lon, speed, bearing, accuracy, timeMs) }

    @Test
    fun `there is no pose before the first fix`() {
        assertNull(CarPosition().at(0L))
    }

    @Test
    fun `between fixes the car carries on along its heading at its speed`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 90f, timeMs = 0L)
        val half = p.at(500L)!!
        assertEquals("half a second east at 10 m/s", 5f, east(half), 0.05f)
        assertEquals(0f, north(half), 0.05f)
        assertEquals(90f, half.headingDeg, 0.01f)
    }

    @Test
    fun `dead reckoning stops at its cap rather than driving off the map`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 0f, timeMs = 0L)
        val capped = p.at(CarPosition.MAX_DEAD_RECKON_MS)!!
        val later = p.at(CarPosition.MAX_DEAD_RECKON_MS + 60_000L)!!
        assertEquals(north(capped), north(later), 0.01f)
        assertEquals(10f * CarPosition.MAX_DEAD_RECKON_MS / 1000f, north(capped), 0.1f)
    }

    @Test
    fun `a parked car neither creeps nor spins`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 12f, bearing = 45f, timeMs = 0L)
        // Stopped: GPS speed jitters and its bearing points anywhere.
        p.fixAt(0f, 0f, speed = 0.3f, bearing = 200f, timeMs = 1_000L)
        val still = p.at(1_000L + CarPosition.BLEND_MS + 5_000L)!!
        assertEquals(0f, east(still), 0.05f)
        assertEquals(0f, north(still), 0.05f)
        assertEquals("the heading is the last one it had while moving", 45f, still.headingDeg, 0.01f)
    }

    @Test
    fun `a new fix is eased in, not jumped to`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 0f, timeMs = 0L)
        val before = p.at(1_000L)!!
        // The next fix says the car is 6 m to the right of where it was carried to.
        p.fixAt(6f, 10f, speed = 10f, bearing = 0f, timeMs = 1_000L)
        val same = p.at(1_000L)!!
        assertEquals("no jump at the moment of the fix", east(before), east(same), 0.05f)
        assertEquals(north(before), north(same), 0.05f)
        val mid = p.at(1_000L + CarPosition.BLEND_MS / 2)!!
        assertTrue("halfway there halfway through", east(mid) > 2f && east(mid) < 4f)
        val done = p.at(1_000L + CarPosition.BLEND_MS)!!
        assertEquals("on the fix's track once eased", 6f, east(done), 0.05f)
        assertEquals(10f + 10f * CarPosition.BLEND_MS / 1000f, north(done), 0.1f)
    }

    @Test
    fun `the house's match moves the car onto the road and later fixes keep it there`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 0f, bearing = null, timeMs = 0L)
        // The road is 4 m east of where the phone thinks it is.
        assertTrue(p.matched(at(0f, 0f), at(4f, 0f), timeMs = 100L))
        assertEquals("eased, not jumped", 0f, east(p.at(100L)!!), 0.05f)
        assertEquals(4f, east(p.at(100L + CarPosition.BLEND_MS)!!), 0.05f)
        p.fixAt(0f, 20f, speed = 0f, bearing = null, timeMs = 2_000L)
        val next = p.at(2_000L + CarPosition.BLEND_MS)!!
        assertEquals("the next fix is shifted by the same match", 4f, east(next), 0.05f)
        assertEquals(20f, north(next), 0.05f)
    }

    @Test
    fun `a snap too far away is the wrong road and is not believed`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 0f, bearing = null, timeMs = 0L)
        assertFalse(p.matched(at(0f, 0f), at(CarPosition.MAX_SNAP_M + 30f, 0f), timeMs = 100L))
        assertEquals(0f, east(p.at(100L + CarPosition.BLEND_MS)!!), 0.05f)
    }

    @Test
    fun `an old match stops shifting new fixes`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 0f, bearing = null, timeMs = 0L)
        p.matched(at(0f, 0f), at(4f, 0f), timeMs = 0L)
        val t = CarPosition.MATCH_MAX_AGE_MS + 1L
        p.fixAt(0f, 0f, speed = 0f, bearing = null, timeMs = t)
        assertEquals(0f, east(p.at(t + CarPosition.BLEND_MS)!!), 0.05f)
    }

    @Test
    fun `a poor fix is dropped`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 0f, bearing = null, timeMs = 0L)
        assertFalse(p.fixAt(300f, 0f, speed = 0f, bearing = null, timeMs = 1_000L, accuracy = CarPosition.MAX_ACCURACY_M + 1f))
        assertEquals(0f, east(p.at(1_000L + CarPosition.BLEND_MS)!!), 0.05f)
        assertEquals("a dropped fix is not sent to the house either", 1, p.trace().size)
    }

    @Test
    fun `the trace for the house is the last fixes, oldest first, and bounded`() {
        val p = CarPosition()
        repeat(CarPosition.TRACE_SIZE + 5) { p.fixAt(0f, it.toFloat(), speed = 1f, bearing = 0f, timeMs = it * 1_000L) }
        val trace = p.trace()
        assertEquals(CarPosition.TRACE_SIZE, trace.size)
        assertEquals(5f, plane.north(trace.first().lat), 0.01f)
        assertEquals(CarPosition.TRACE_SIZE + 4f, plane.north(trace.last().lat), 0.01f)
    }

    @Test
    fun `the heading eases through north the short way`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 5f, bearing = 350f, timeMs = 0L)
        p.fixAt(0f, 0f, speed = 5f, bearing = 10f, timeMs = 0L)
        val h = p.at(CarPosition.BLEND_MS / 2)!!.headingDeg
        val fromNorth = minOf(h, 360f - h)
        assertTrue("half way from 350 to 10 is about north, not south: $h", fromNorth < 1f)
    }

    @Test
    fun `a moving fix beside a drawn road is drawn on it at once, with no match from the house`() {
        val p = CarPosition()
        p.roads(listOf(HoloRoad(listOf(at(-200f, 0f), at(200f, 0f)), "residential")))
        p.fixAt(0f, 6f, speed = 10f, bearing = 90f, timeMs = 0L)
        p.fixAt(10f, 6f, speed = 10f, bearing = 90f, timeMs = 1_000L)
        val shown = p.at(2_000L)!!
        assertEquals("on the road's line, not 6 m beside it", 0f, north(shown), 0.2f)
        assertEquals(20f, east(shown), 0.3f)
        assertFalse("an answer about older fixes does not move it back", p.matched(at(10f, 6f), at(10f, 12f), 2_000L))
    }

    @Test
    fun `a parked fix is not pulled onto the nearest road`() {
        val p = CarPosition()
        p.roads(listOf(HoloRoad(listOf(at(-200f, 0f), at(200f, 0f)), "residential")))
        p.fixAt(0f, 8f, speed = 0f, bearing = null, timeMs = 0L)
        assertEquals(8f, north(p.at(1_000L)!!), 0.1f)
    }

    @Test
    fun `the gyroscope turns the car between fixes and GPS only pulls it back part of the way`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 0f, timeMs = 0L)
        for (i in 1..10) p.turned(4f, i * 50L)
        assertEquals("turning as it is driven", 40f, p.at(500L)!!.headingDeg, 0.01f)
        // GPS, a moment behind, still says 10 degrees.
        p.fixAt(0f, 5f, speed = 10f, bearing = 10f, timeMs = 600L)
        assertEquals(40f - 30f * CarPosition.BEARING_PULL, p.at(1_700L)!!.headingDeg, 0.5f)
    }

    @Test
    fun `with the gyroscope silent a bearing is taken whole, and a parked car is not turned`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 0f, timeMs = 0L)
        p.turned(20f, 100L)
        p.fixAt(0f, 50f, speed = 10f, bearing = 90f, timeMs = 5_000L)
        assertEquals(90f, p.at(6_100L)!!.headingDeg, 0.01f)
        p.fixAt(0f, 50f, speed = 0f, bearing = null, timeMs = 7_000L)
        p.turned(45f, 7_100L)
        assertEquals(90f, p.at(8_200L)!!.headingDeg, 0.01f)
    }

    @Test
    fun `a turn between fixes is driven as an arc, not swung round the last fix`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 0f, timeMs = 0L)
        p.turned(90f, 500L)
        val shown = p.at(1_000L)!!
        assertEquals("5 m north before the turn", 5f, north(shown), 0.1f)
        assertEquals("5 m east after it", 5f, east(shown), 0.1f)
    }

    @Test
    fun `the gyroscope does not carry the car past the cap in a tunnel`() {
        val p = CarPosition()
        p.fixAt(0f, 0f, speed = 10f, bearing = 0f, timeMs = 0L)
        for (t in 100L..20_000L step 100L) p.turned(0.01f, t)
        assertEquals(CarPosition.MAX_DEAD_RECKON_MS / 100f, north(p.at(20_000L)!!), 0.5f)
    }

    @Test
    fun `a new answer's roads keep the car on the road it was on`() {
        val p = CarPosition()
        fun roads() = listOf(
            HoloRoad(listOf(at(-200f, 0f), at(200f, 0f)), "residential"),
            HoloRoad(listOf(at(-200f, 10f), at(200f, 10f)), "residential"),
        )
        p.roads(roads())
        p.fixAt(0f, 2f, speed = 10f, bearing = 90f, timeMs = 0L)
        p.roads(roads())
        p.fixAt(10f, 6f, speed = 10f, bearing = 90f, timeMs = 1_000L)
        assertEquals("a little nearer the other road is not a hop", 0f, north(p.at(2_100L)!!), 0.2f)
    }
}
