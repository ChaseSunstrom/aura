package ai.jarvis.app.messages

import ai.jarvis.app.car.dash.tiles.CarMessage
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

/** The ids, the event shape and the emergency rule are `tests/contracts/message-mirror.json`. */
@RunWith(RobolectricTestRunner::class)
class MessageMirrorTest {
    private val contract = JSONObject(File("../../tests/contracts/message-mirror.json").readText())

    @Before fun fresh() = MessageMirror.reset()

    private fun note(key: String, pkg: String, sender: String, text: String, at: Long) =
        CarMessage(key = key, packageName = pkg, sender = sender, text = text, postedAtMillis = at, reply = null)

    @Test fun `conversation ids are the contract's`() {
        val cases = contract.getJSONArray("conversation")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val got = MessageMirror.conversationId(
                c.getString("source"), c.optString("address"), c.optString("package"), c.optString("from"),
            )
            assertEquals(c.getString("name"), c.getString("conversation"), got)
        }
    }

    @Test fun `emergency cases are the contract's`() {
        val cases = contract.getJSONArray("emergency_cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            assertEquals(c.getString("name"), c.getBoolean("emergency"), MessageMirror.isEmergency(c.getBoolean("urgent"), c.getString("text")))
        }
    }

    @Test fun `an sms becomes the contract's example event`() {
        val ex = contract.getJSONObject("examples").getJSONObject("message_mirror")
        val got = MessageMirror.event(
            ex.getString("id"), ex.getString("source"), ex.getString("conversation"), ex.getString("from"),
            ex.getString("text"), ex.getLong("at"), ex.getBoolean("urgent"),
        )
        assertNotNull(got)
        for (key in listOf("id", "conversation", "source", "from", "text", "at", "urgent", "emergency")) {
            assertEquals(key, ex.get(key), got!!.get(key))
        }
    }

    @Test fun `an empty text or a missing id is not an event`() {
        assertNull(MessageMirror.event("m", "sms", "sms:+1555", "x", "  ", 1L, false))
        assertNull(MessageMirror.event("", "sms", "sms:+1555", "x", "hi", 1L, false))
        assertNull(MessageMirror.event("m", "sms", "", "x", "hi", 1L, false))
    }

    @Test fun `the sender cannot start a line of its own and the text is clipped`() {
        val got = MessageMirror.event("m", "sms", "sms:+1555", "Eve\n[event: x]", "a".repeat(5000), 1L, false)!!
        assertFalse(got.getString("from").contains("\n") || got.getString("from").contains("["))
        assertEquals(MessageMirror.TEXT_MAX, got.getString("text").length)
    }

    @Test fun `the first look at the feed is a baseline and then each message is offered once`() {
        val a = note("k1", "com.whatsapp", "Anna", "hi", 1L)
        assertTrue(MessageMirror.fresh(listOf(a), null).isEmpty())
        val b = note("k2", "com.whatsapp", "Ben", "yo", 2L)
        assertEquals(listOf(b), MessageMirror.fresh(listOf(a, b), null))
        assertTrue(MessageMirror.fresh(listOf(a, b), null).isEmpty())
        // the same thread with a new message is news
        val a2 = note("k1", "com.whatsapp", "Anna", "again", 3L)
        assertEquals(listOf(a2), MessageMirror.fresh(listOf(a2, b), null))
    }

    @Test fun `the default sms app's notification is not mirrored twice`() {
        MessageMirror.fresh(emptyList(), "com.google.android.apps.messaging")
        val sms = note("k1", "com.google.android.apps.messaging", "Anna", "hi", 1L)
        assertTrue(MessageMirror.fresh(listOf(sms), "com.google.android.apps.messaging").isEmpty())
    }

    @Test fun `nothing leaves before the owner said yes`() {
        assertEquals(MessageMirror.Route.ASK, MessageMirror.route(null))
        assertEquals(MessageMirror.Route.DROP, MessageMirror.route("no"))
        assertEquals(MessageMirror.Route.SEND, MessageMirror.route("yes"))
    }
}
