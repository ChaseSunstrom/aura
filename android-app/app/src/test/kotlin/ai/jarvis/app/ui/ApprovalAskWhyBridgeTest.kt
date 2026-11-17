package ai.jarvis.app.ui

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment

/** T-1054: the deny-with-a-question path through the bridge. */
@RunWith(RobolectricTestRunner::class)
class ApprovalAskWhyBridgeTest {
    private val sent = mutableListOf<String>()
    private var whys = 0

    @After fun settle() {
        for (id in listOf("w1", "w2", "w3", "w4")) ApprovalBridge.dismissResolved(id)
    }

    private fun raise(id: String, why: (() -> Unit)?) = ApprovalBridge.raiseHermesRequest(
        RuntimeEnvironment.getApplication(), id, "mail_send", "{}", "", listOf("once", "deny"), 60,
        why = why,
    ) { sent.add(it) }

    @Test fun `ask why denies on the wire, then asks, once`() {
        assertTrue(raise("w1") { whys++ })
        assertTrue(ApprovalBridge.canAskWhy("w1"))
        ApprovalBridge.deliverAskWhy("w1")
        assertEquals(listOf("deny"), sent)
        assertEquals(1, whys)
        // Answered once: a second tap, or the countdown behind it, sends and asks nothing more.
        ApprovalBridge.deliverAskWhy("w1")
        ApprovalBridge.deliverTimeout("w1")
        assertEquals(listOf("deny"), sent)
        assertEquals(1, whys)
        assertFalse(ApprovalBridge.canAskWhy("w1"))
    }

    @Test fun `a request raised without a way to ask offers none and Ask why is only a deny`() {
        assertTrue(raise("w2", null))
        assertFalse(ApprovalBridge.canAskWhy("w2"))
        ApprovalBridge.deliverAskWhy("w2")
        assertEquals(listOf("deny"), sent)
        assertEquals(0, whys)
    }

    @Test fun `the countdown's auto-deny never asks why`() {
        assertTrue(raise("w3") { whys++ })
        ApprovalBridge.deliverTimeout("w3")
        assertEquals(listOf("deny"), sent)
        assertEquals(0, whys)
    }

    @Test fun `a plain deny and a resolved ask never ask why`() {
        assertTrue(raise("w4") { whys++ })
        ApprovalBridge.deliver("w4", false)
        assertEquals(listOf("deny"), sent)
        assertEquals(0, whys)
        assertTrue(raise("w1") { whys++ })
        ApprovalBridge.dismissResolved("w1")
        ApprovalBridge.deliverAskWhy("w1")
        assertEquals(0, whys)
    }

    @Test fun `an unknown id asks nothing`() {
        ApprovalBridge.deliverAskWhy("nobody")
        assertEquals(0, whys)
        assertTrue(sent.isEmpty())
    }
}
