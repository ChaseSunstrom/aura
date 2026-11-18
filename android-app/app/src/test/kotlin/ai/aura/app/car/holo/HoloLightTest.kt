package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneOffset
import kotlin.math.abs

/**
 * T-1107 (the owner, 2026-11-12: "accurate lighting depending on the time of day"): the sun's
 * place against cases worked by hand from the solstices and equinoxes, not from this code.
 * At local solar noon the sun stands due south (north, south of the tropic) at 90 degrees less
 * the latitude plus the sun's declination; the places are made-up round ones.
 *
 * Does not cover what the scene does with the light (HoloWorldTest) or how it looks (the goldens).
 */
class HoloLightTest {
    private fun at(y: Int, mo: Int, d: Int, h: Int, mi: Int, s: Int) = LocalDateTime.of(y, mo, d, h, mi, s).toInstant(ZoneOffset.UTC).toEpochMilli()

    private fun bearingOff(a: Double, b: Double) = abs(a - b).let { minOf(it, 360.0 - it) }

    @Test
    fun `the sun's height and bearing match the solstices and equinoxes to within a degree`() {
        // June solstice, solar noon at 51.48 N on the prime meridian: 90 - 51.48 + 23.44.
        HoloLight.sun(at(2024, 6, 20, 12, 1, 40), 51.48, 0.0).let {
            assertEquals(61.96, it.elevationDeg, 1.0); assertTrue(bearingOff(it.azimuthDeg, 180.0) < 1.0)
        }
        // December solstice, the same place: 90 - 51.48 - 23.44.
        HoloLight.sun(at(2024, 12, 21, 11, 58, 0), 51.48, 0.0).let {
            assertEquals(15.08, it.elevationDeg, 1.0); assertTrue(bearingOff(it.azimuthDeg, 180.0) < 1.0)
        }
        // Midnight in June there: as far under the northern horizon as December's noon is over the southern.
        HoloLight.sun(at(2024, 6, 21, 0, 1, 40), 51.48, 0.0).let {
            assertEquals(-15.08, it.elevationDeg, 1.0); assertTrue(bearingOff(it.azimuthDeg, 0.0) < 1.0)
        }
        // The March equinox on the equator: overhead at noon, rising due east six hours before.
        assertEquals(90.0, HoloLight.sun(at(2024, 3, 20, 12, 7, 30), 0.0, 0.0).elevationDeg, 1.0)
        HoloLight.sun(at(2024, 3, 20, 6, 7, 30), 0.0, 0.0).let {
            assertEquals(0.0, it.elevationDeg, 1.0); assertTrue(bearingOff(it.azimuthDeg, 90.0) < 1.0)
        }
        // South of the tropic the noon sun is in the north: 33.87 S, 151.21 E, the June solstice.
        HoloLight.sun(at(2024, 6, 20, 1, 56, 50), -33.87, 151.21).let {
            assertEquals(32.69, it.elevationDeg, 1.0); assertTrue(bearingOff(it.azimuthDeg, 0.0) < 1.0)
        }
    }

    @Test
    fun `daylight is none once civil twilight ends, full once the sun is as far up, and eases between`() {
        assertEquals(0f, HoloLight.daylight(-6.0))
        assertEquals(0f, HoloLight.daylight(-40.0))
        assertEquals(1f, HoloLight.daylight(6.0))
        assertEquals(1f, HoloLight.daylight(60.0))
        assertEquals(0.5f, HoloLight.daylight(0.0), 0.001f)
        assertTrue(HoloLight.daylight(-3.0) < HoloLight.daylight(0.0) && HoloLight.daylight(0.0) < HoloLight.daylight(3.0))
    }

    @Test
    fun `a pole and a place past the date line still give an answer`() {
        for ((lat, lon) in listOf(90.0 to 0.0, -90.0 to 10.0, 10.0 to 180.0, 10.0 to -180.0)) {
            val s = HoloLight.sun(at(2024, 9, 1, 3, 0, 0), lat, lon)
            assertTrue("$lat $lon", s.elevationDeg.isFinite() && s.azimuthDeg in 0.0..360.0)
        }
    }
}
