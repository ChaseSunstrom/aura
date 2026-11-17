package ai.jarvis.app.ui

import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** "It just shows an unformatted json object" (2026-10-08): fields, every value whole. */
@RunWith(RobolectricTestRunner::class)
class ConsentTextTest {
    @Test fun `parameters read as lines and nothing is cut`() {
        val body = "Hi, this is Jarvis. He is in Port Larch now and would like the ride. ".repeat(4).trim()
        assertEquals(
            "to: +15555550123\nbody: $body",
            ConsentText.fields("""{"to": "+15555550123", "body": "$body"}"""),
        )
        assertEquals(
            "action: send_sms\nparams.to: Mom\nparams.tags: a, b\nparams.extra: (none)",
            ConsentText.fields("""{"action": "send_sms", "params": {"to": "Mom", "tags": ["a", "b"], "extra": null}}"""),
        )
        // Not an object: shown exactly as given.
        assertEquals("rm -rf /tmp/x", ConsentText.fields("rm -rf /tmp/x"))
        assertEquals("[1, 2]", ConsentText.fields("[1, 2]"))
    }
}
