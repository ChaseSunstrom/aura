package ai.jarvis.app.assist.hermes

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** The wake overlay pulls up each notice JARVIS puts up once, and none that were up already. */
@RunWith(RobolectricTestRunner::class)
class FreshNoticesTest {
    private fun panel(id: String, kind: String = "notice", at: Long = 0) =
        Panel(id, kind, id, JSONObject().put("text", id), "agent", at, 1, null, null)

    @Test
    fun `the first look is not news, a notice after it is, once`() {
        val (seeded, first) = freshNotices(null, listOf(panel("old")))
        assertEquals(emptyList<Panel>(), first)
        val (seen, fresh) = freshNotices(seeded, listOf(panel("old"), panel("laundry", at = 2), panel("chart", kind = "chart")))
        assertEquals(listOf("laundry"), fresh.map { it.id })
        assertEquals(emptyList<Panel>(), freshNotices(seen, listOf(panel("old"), panel("laundry"))).second)
    }
}
