package ai.jarvis.app.car.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ParkedTest {
    @Test
    fun noFixYetSaysWhenOneIsSaved() {
        val r = Parked.answer(null, 1_000L)
        assertFalse(r.ok)
        assertTrue(r.error!!.contains("car disconnects"))
    }

    @Test
    fun aStoredFixComesBackWithItsAge() {
        val stored = """{"latitude":41.88,"longitude":-87.63,"parked_at_ms":0}"""
        val r = Parked.answer(stored, 25 * 60_000L)
        assertTrue(r.ok)
        assertEquals(41.88, r.data!!.getDouble("latitude"), 0.0)
        assertEquals(25L, r.data!!.getLong("minutes_ago"))
    }

    @Test
    fun aCorruptStoreIsNoFixNotACrash() {
        assertFalse(Parked.answer("{not json", 0L).ok)
    }

    @Test
    fun fromWhereThePhoneIsItSaysHowFarAndWhichWay() {
        val stored = """{"latitude":41.8800,"longitude":-87.6300,"parked_at_ms":0}"""
        // About 111 m south and a little west of the car: the car is north-east-ish -> north.
        val r = Parked.answer(stored, 0L, 41.8790, -87.6301)
        assertEquals(100, r.data!!.getInt("metres_away"))
        assertEquals("north", r.data!!.getString("direction"))
        assertEquals("east", Parked.compass(Parked.bearing(41.88, -87.64, 41.88, -87.63)))
        assertEquals("southwest", Parked.compass(225.0))
        assertFalse(Parked.answer(stored, 0L).data!!.has("direction"))
    }
}
