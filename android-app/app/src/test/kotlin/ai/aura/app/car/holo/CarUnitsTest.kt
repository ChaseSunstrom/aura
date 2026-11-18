package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Locale

/** The drive says feet, miles and mph where the road signs do, and metres, km and km/h elsewhere. */
class CarUnitsTest {

    @Test
    fun `the US, Liberia and Myanmar are imperial and nobody else is`() {
        for (c in listOf("US", "LR", "MM")) {
            assertTrue(c, CarUnits.of(Locale("en", c)).imperial)
        }
        for (c in listOf("GB", "SE", "DE", "CA", "")) {
            assertFalse(c, CarUnits.of(Locale("en", c)).imperial)
        }
    }

    @Test
    fun `imperial distance is feet to the nearest fifty under a fifth of a mile, then miles`() {
        val u = CarUnits(imperial = true)
        assertEquals("0 ft", u.distance(0f))
        assertEquals("150 ft", u.distance(46f)) // 151 ft
        assertEquals("300 ft", u.distance(90f)) // 295 ft
        assertEquals("950 ft", u.distance(290f))
        assertEquals("0.2 mi", u.distance(305f)) // 1001 ft is not "1000 ft"
        assertEquals("0.6 mi", u.distance(1000f))
        assertEquals("2.6 mi", u.distance(4200f))
        assertEquals("9.9 mi", u.distance(16_000f)) // one place under ten
        assertEquals("10 mi", u.distance(16_100f))
        assertEquals("12 mi", u.distance(19_300f))
    }

    @Test
    fun `metric distance is the route guide's`() {
        val u = CarUnits(imperial = false)
        assertEquals("90 m", u.distance(87f))
        assertEquals("4.2 km", u.distance(4200f))
        assertEquals("12 km", u.distance(12_400f))
    }

    @Test
    fun `speed is the metres per second in the unit of the road`() {
        assertEquals("100", CarUnits(imperial = false).speed(27.8f))
        assertEquals("62", CarUnits(imperial = true).speed(27.8f))
        assertEquals("0", CarUnits(imperial = true).speed(0f))
    }
}
