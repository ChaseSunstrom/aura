package ai.aura.app.car.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The decision and its words only (T-1572); the notification and the profile read are CarLinkService's. */
class A2dpDuringProjectionTest {
    @Test
    fun `a2dp connected at a session start warns once, with the remedy`() {
        val notice = A2dpDuringProjection()
        val text = notice.check("connected")!!
        assertTrue(text, "Media audio" in text)
        assertTrue(text, "Phone calls" in text)
        assertTrue(text, "on" in text && "off" in text)
        assertNull("the second session of the same drive says nothing", notice.check("connected"))
    }

    @Test
    fun `a2dp not connected says nothing and does not spend the warning`() {
        val notice = A2dpDuringProjection()
        for (state in listOf("disconnected", "connecting", "disconnecting", "unknown")) {
            assertNull(state, notice.check(state))
        }
        assertEquals(true, notice.check("connected") != null)
    }

    @Test
    fun `the log line names the state and the measured cost`() {
        val line = A2dpDuringProjection.LOG_LINE
        assertTrue(line, "a2dp connected" in line)
        assertTrue(line, "reset" in line)
    }
}
