package ai.jarvis.app.automation.notify

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

/**
 * The phone's side of `tests/contracts/notification-fence.json`: the PC's
 * `aura_desktop/notifications.py` answers the same cases (T-0143), so the two fences cannot drift.
 */
@RunWith(RobolectricTestRunner::class)
class NotificationFenceContractTest {
    private val contract = JSONObject(File("../../tests/contracts/notification-fence.json").readText())

    private fun JSONObject.str(key: String): String? = if (isNull(key) || !has(key)) null else getString(key)

    @Test
    fun `the caps are the table's`() {
        assertEquals(contract.getInt("max_title"), NotificationFence.MAX_TITLE_CHARS)
        assertEquals(contract.getInt("max_text"), NotificationFence.MAX_TEXT_CHARS)
        assertEquals(contract.getString("truncation_mark"), NotificationFence.TRUNCATION_MARK)
    }

    @Test
    fun `clean answers every case`() {
        val cases = contract.getJSONArray("clean")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            assertEquals(c.getString("why"), c.str("out"), NotificationFence.clean(c.getString("in"), c.getInt("max")))
        }
    }

    @Test
    fun `sanitize answers every case`() {
        val cases = contract.getJSONArray("sanitize")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val n = c.getJSONObject("in")
            val got = NotificationFence.sanitize(
                packageName = n.str("package"),
                title = n.str("title"),
                text = n.str("text"),
                subText = n.str("sub_text"),
                category = n.str("category"),
                ongoing = n.optBoolean("ongoing"),
                groupSummary = n.optBoolean("group_summary"),
                postedAtMs = n.optLong("posted_at"),
                ourPackage = c.str("ours"),
            )
            val want = if (c.isNull("out")) null else c.getJSONObject("out").let { o -> o.keys().asSequence().associateWith { o.get(it) } }
            assertEquals(c.getString("why"), want, got?.mapValues { (_, v) -> if (v is Long && v <= Int.MAX_VALUE) v.toInt() else v })
        }
    }

    @Test
    fun `wanted answers every case`() {
        val cases = contract.getJSONArray("wanted")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val allowed = c.getJSONArray("allowed").let { a -> (0 until a.length()).map { a.getString(it) }.toSet() }
            assertEquals(c.getString("why"), c.getBoolean("out"), NotificationFence.isWanted(c.getString("package"), allowed))
        }
    }

    @Test
    fun `the deduper refuses a repeat inside the window and restarts it`() {
        val d = NotificationDeduper(windowMs = contract.getLong("dedupe_window_ms"))
        val steps = contract.getJSONArray("dedupe")
        for (i in 0 until steps.length()) {
            val s = steps.getJSONArray(i)
            assertEquals("step $i", s.getBoolean(2), d.accept(s.getString(1), s.getLong(0)))
        }
    }
}
