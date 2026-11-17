package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.policy.ActionTier
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `media_search` (T-1474): the music request that reaches the phone over the house's channel
 * when it was asked for somewhere the phone is not. What a JVM can prove: which requests are
 * handed to the music app and which are refused, and that the action is registered beside
 * the media keys at their tier. That a music app plays is a handset's to show.
 */
class MediaSearchTest {

    private fun request(vararg pairs: Pair<String, Any>) = JSONObject().apply { pairs.forEach { (k, v) -> put(k, v) } }

    @Test
    fun `a named play, a queue and a shuffled play are handed on`() {
        assertNull(MediaSearch.problem(request("query" to "Daft Punk")))
        assertNull(MediaSearch.problem(request("action" to "play", "kind" to "album", "album" to "Discovery")))
        assertNull(MediaSearch.problem(request("action" to "queue", "title" to "Starlight")))
        assertNull(MediaSearch.problem(request("action" to "play", "query" to "", "shuffle" to true)))
        assertNull(MediaSearch.problem(request("action" to " Play ", "playlist" to "Liked songs", "shuffle" to false)))
    }

    @Test
    fun `the media keys' verbs and a request that names nothing are refused`() {
        for (verb in listOf("pause", "next", "previous", "stop", "rewind")) {
            assertTrue(MediaSearch.problem(request("action" to verb, "query" to "x"))!!.contains("media_control"))
        }
        assertNotNull(MediaSearch.problem(request()))
        assertNotNull(MediaSearch.problem(request("action" to "play", "query" to "  ")))
        // Queueing nothing is refused even with shuffle set: there is nothing to add.
        assertTrue(MediaSearch.problem(request("action" to "queue", "shuffle" to true))!!.startsWith("queue needs"))
    }

    @Test
    fun `it is registered with the media keys, runs unasked and belongs to the media capability`() {
        assertTrue(MediaActions.all.contains(MediaSearch))
        assertEquals(1, Builtins.all().count { it.id == "media_search" })
        assertEquals(ActionTier.AUTO, MediaSearch.tier)
        assertEquals("media", MediaSearch.capability)
    }
}
