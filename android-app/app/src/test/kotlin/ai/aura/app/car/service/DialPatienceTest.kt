package ai.aura.app.car.service

import org.junit.Assert.assertEquals
import org.junit.Test

/** The dial's cutoff (T-0997). The loop that uses it, in CarLinkService, is not under test. */
class DialPatienceTest {
    @Test
    fun `a head unit that refuses before it has named its endpoint is waited on for a minute`() {
        assertEquals(60_000L, DialPatience.millis(refused = true, named = false, totalMillis = 20_000))
    }

    @Test
    fun `a refusal after the start request, and any other failure, end when they did before`() {
        assertEquals(20_000L, DialPatience.millis(refused = true, named = true, totalMillis = 20_000))
        assertEquals(20_000L, DialPatience.millis(refused = false, named = false, totalMillis = 20_000))
        assertEquals(90_000L, DialPatience.millis(refused = true, named = false, totalMillis = 90_000))
    }

    @Test
    fun `an endpoint named in the handshake or after it both count as the start request`() {
        assertEquals(true, DialPatience.named(inHandshake = "endpoint", later = null))
        assertEquals(true, DialPatience.named(inHandshake = null, later = "endpoint"))
        assertEquals(false, DialPatience.named(inHandshake = null, later = null))
    }
}
