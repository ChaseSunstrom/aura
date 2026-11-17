package ai.jarvis.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

/** The snap and the gyroscope's turn as rules (T-1101); the sensors and a real drive are not under test. */
class RoadSnapTest {
    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private fun road(kind: String, vararg en: Pair<Float, Float>) = HoloRoad(en.map { at(it.first, it.second) }, kind)

    private val main = road("residential", -100f to 0f, 100f to 0f)

    @Test
    fun `a fix beside its road is put on it, pointing the way the car goes`() {
        val hit = RoadSnap.snap(at(10f, 6f), 92f, listOf(main))!!
        assertEquals(10f, plane.east(hit.at.lon), 0.1f)
        assertEquals(0f, plane.north(hit.at.lat), 0.1f)
        assertEquals(90f, hit.bearingDeg, 0.5f)
        assertEquals(270f, RoadSnap.snap(at(10f, 6f), 265f, listOf(main))!!.bearingDeg, 0.5f)
    }

    @Test
    fun `a cross street, a footway and a road too far are not taken`() {
        val cross = road("residential", 10f to -100f, 10f to 100f)
        assertNull("heading east, the street north is not it", RoadSnap.snap(at(12f, 30f), 90f, listOf(cross)))
        assertNull(RoadSnap.snap(at(10f, 3f), 90f, listOf(road("footway", -100f to 0f, 100f to 0f))))
        assertNull(RoadSnap.snap(at(10f, 25f), 90f, listOf(main)))
    }

    @Test
    fun `between two parallel roads the car stays on the one it was on`() {
        val other = road("residential", -100f to 10f, 100f to 10f)
        val roads = listOf(main, other)
        assertSame("nearer wins with no history", other, RoadSnap.snap(at(0f, 6f), 90f, roads)!!.road)
        assertSame("a little nearer is not enough to hop", main, RoadSnap.snap(at(0f, 6f), 90f, roads, last = main)!!.road)
        assertSame("clearly on the other, it moves", other, RoadSnap.snap(at(0f, 9.5f), 90f, roads, last = main)!!.road)
    }

    @Test
    fun `the oncoming side of a divided road is not taken however near it is`() {
        val east = HoloRoad(listOf(at(-100f, 0f), at(100f, 0f)), "primary", oneway = 1)
        val west = HoloRoad(listOf(at(100f, 10f), at(-100f, 10f)), "primary", oneway = 1)
        val drawnBackwards = HoloRoad(listOf(at(100f, 10f), at(-100f, 10f)), "primary", oneway = -1)
        assertSame(east, RoadSnap.snap(at(0f, 8f), 90f, listOf(east, west))!!.road)
        assertNull(RoadSnap.snap(at(0f, 8f), 90f, listOf(west)))
        assertEquals("against its line is its way", 90f, RoadSnap.snap(at(0f, 8f), 90f, listOf(drawnBackwards))!!.bearingDeg, 0.5f)
    }

    @Test
    fun `the gyroscope's turn is read about up however the phone lies`() {
        // Flat on its back, screen up: gravity on +z; a left turn is positive about z.
        assertEquals(-5.73f, gyroTurnDeg(0f, 0f, 1f, 0f, 0f, 9.81f, 0.1f), 0.02f)
        // Upright in a mount: gravity on +y; a right turn is negative about y.
        assertEquals(5.73f, gyroTurnDeg(0f, -1f, 0f, 0f, 9.81f, 0f, 0.1f), 0.02f)
        // A nod (about the axis across the car) is no turn.
        assertEquals(0f, gyroTurnDeg(1f, 0f, 0f, 0f, 9.81f, 0f, 0.1f), 0.001f)
    }

    @Test
    fun `a paused sensor and a phone in free fall turn nothing`() {
        assertEquals(0f, gyroTurnDeg(0f, 0f, 1f, 0f, 0f, 9.81f, 2f), 0f)
        assertEquals(0f, gyroTurnDeg(0f, 0f, 1f, 0f, 0f, 0.5f, 0.1f), 0f)
    }
}
