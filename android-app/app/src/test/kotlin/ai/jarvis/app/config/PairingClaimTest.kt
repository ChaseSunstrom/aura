package ai.jarvis.app.config

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The adapter a claim at the console carries beside the house's token
 * (`aura-web/src/lib/server/pairClaim.ts`): without it a phone paired from
 * the console's panel asked the house's pipeline, which has no agent.
 */
@RunWith(RobolectricTestRunner::class)
class PairingClaimTest {

    private fun claim(adapter: String?) = JSONObject(
        """{"url":"http://100.64.0.51:8080","token":"t","name":"Pixel"${adapter?.let { ""","adapter":$it""" } ?: ""}}"""
    )

    @Test
    fun `the console's adapter is read with the adapter QR's own rules`() {
        val adapter = PairingClaim.adapterOf(
            claim("""{"url":"http://100.64.0.51:8643/","key":"devadapterkey0123456789","model":"m"}""")
        )!!
        assertEquals("http://100.64.0.51:8643", adapter.url)
        assertEquals("devadapterkey0123456789", adapter.key)
    }

    @Test
    fun `no adapter, or one the QR rules refuse, leaves the house pairing alone`() {
        assertNull(PairingClaim.adapterOf(claim(null)))
        assertNull(PairingClaim.adapterOf(claim("""{"url":"ftp://x","key":"devadapterkey0123456789"}""")))
        assertNull(PairingClaim.adapterOf(claim("""{"url":"http://x:8643","key":"short"}""")))
    }
}
