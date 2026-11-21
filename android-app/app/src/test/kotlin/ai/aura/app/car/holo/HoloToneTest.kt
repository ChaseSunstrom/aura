package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The sun's phase as a tone (T-1584; the owner, 2026-11-20: "match the current time of day
 * correctly with accurate colors/time of year … including sunset"). Pinned: golden hour and the
 * sun on the horizon are warm, the blue hour after sunset and before sunrise is blue, full day and
 * deep night are neither, and the season moves it: at the goldens' place a December noon is low
 * and golden, a June one high and plain. Not pinned: the colours the map makes of it (FlatMapTest)
 * or the weather, which HoloLight does not know.
 */
class HoloToneTest {
    private val lat = 59.33
    private val lon = 18.06

    private fun utc(y: Int, m: Int, d: Int, hh: Int, mm: Int) =
        java.time.LocalDateTime.of(y, m, d, hh, mm).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()

    /** The sun's highest point that day, minute by minute (local noon is about 10:48 UTC here). */
    private fun noonElevation(y: Int, m: Int, d: Int): Double =
        (0 until 24 * 60).maxOf { HoloLight.sun(utc(y, m, d, 0, 0) + it * 60_000L, lat, lon).elevationDeg }

    @Test
    fun `full day and deep night are neither warm nor blue`() {
        for (e in listOf(40.0, 25.0, -20.0, -40.0)) {
            val t = HoloLight.tone(e)
            assertEquals("warm at $e", 0f, t.warm, 0.01f)
            assertEquals("blue at $e", 0f, t.blue, 0.01f)
        }
        assertEquals(1f, HoloLight.tone(40.0).daylight, 0f)
        assertEquals(0f, HoloLight.tone(-20.0).daylight, 0f)
    }

    @Test
    fun `golden hour and the sun on the horizon are warm, the blue hour is blue`() {
        val golden = HoloLight.tone(4.0)
        val sunset = HoloLight.tone(0.0)
        val blueHour = HoloLight.tone(-4.5)
        assertTrue("golden hour warm ${golden.warm}", golden.warm > 0.7f)
        assertTrue("sunset warm ${sunset.warm}", sunset.warm > 0.7f)
        assertTrue("the blue hour blue ${blueHour.blue}", blueHour.blue > 0.8f)
        assertTrue("the blue hour is not golden ${blueHour.warm}", blueHour.warm < 0.15f)
        assertTrue("golden hour is not blue ${golden.blue}", golden.blue < 0.05f)
        // The sky is still bright as the sun sets and dark only near civil twilight's end: a
        // half-lit sunset drew the night's navy under the gold, a brown map (the first goldens).
        assertTrue("sunset is mostly lit ${sunset.daylight}", sunset.daylight in 0.7f..0.95f)
        assertTrue("the blue hour is mostly dark ${blueHour.daylight}", blueHour.daylight < 0.35f)
    }

    @Test
    fun `the tone moves smoothly with the sun, with no step between phases`() {
        var last = HoloLight.tone(-30.0)
        var e = -30.0
        while (e <= 60.0) {
            val t = HoloLight.tone(e)
            assertTrue("warm jumps at $e", kotlin.math.abs(t.warm - last.warm) < 0.06f)
            assertTrue("blue jumps at $e", kotlin.math.abs(t.blue - last.blue) < 0.06f)
            assertTrue("daylight jumps at $e", kotlin.math.abs(t.daylight - last.daylight) < 0.06f)
            last = t
            e += 0.1
        }
    }

    @Test
    fun `the season moves it - a winter noon is low and golden, a summer one high and plain`() {
        val winter = noonElevation(2024, 12, 21)
        val summer = noonElevation(2024, 6, 21)
        assertTrue("winter noon at $winter degrees", winter in 5.0..9.0)
        assertTrue("summer noon at $summer degrees", summer in 52.0..56.0)
        assertTrue("a winter noon is golden", HoloLight.tone(winter).warm > 0.3f)
        assertEquals("a summer noon is not", 0f, HoloLight.tone(summer).warm, 0.01f)
        // Midsummer here never gets darker than the blue hour (the white nights); midwinter does.
        val summerLow = (0 until 24 * 60).minOf { HoloLight.sun(utc(2024, 6, 21, 0, 0) + it * 60_000L, lat, lon).elevationDeg }
        assertTrue("a midsummer midnight is the blue hour", HoloLight.tone(summerLow).blue > 0.5f)
    }
}
