package ai.aura.app.assist

import ai.aura.app.assist.hermes.HermesClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** A refused voice in the car comes back claimable (the owner, 2026-10-07: six refusals of six). */
class VoiceClaimTest {

    @Test
    fun `a refusal carries the adapter's claim and only a claim id reaches the path`() {
        val heard = HermesClient.heardOf(
            """{"transcript":"","speaker":{"dropped":true,"nearest":"Casey","similarity":0.41,"claim":"Ab3_x-Yz9Qw1"}}""",
        )!!
        assertTrue(heard.dropped)
        assertEquals("Ab3_x-Yz9Qw1", heard.claim)
        assertTrue(VoiceClaim.valid(heard.claim))
        assertNull(HermesClient.heardOf("""{"transcript":"hi","speaker":{"dropped":false}}""")!!.claim)
        assertFalse(VoiceClaim.valid("../../api/pair"))
        assertFalse(VoiceClaim.valid(null))
    }
}
