package ai.aura.app.automation.actions.builtin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The 2026-10-08 texts refused with "getGroupIdLevel1": an em dash and over 70 characters. */
class SmsTextTest {
    private val refused = "This is Jarvis, Devon's assistant. He's in Port Larch now and is on the ride — " +
        "you and Ren can get that acia bowl for lunch."

    @Test fun `the typography a model writes becomes the carrier's plain alphabet`() {
        assertEquals(
            "This is Jarvis, Devon's assistant. He's in Port Larch now and is on the ride - you and Ren can get that acia bowl for lunch.",
            SmsText.plain(refused),
        )
        assertEquals("\"Yes\" - it's 5...6", SmsText.plain("“Yes” – it’s 5…6"))
        // Plain GSM-7 now: every character is in the basic alphabet the carrier sends at 160 a part.
        assertTrue(SmsText.plain(refused).all { it.code < 128 })
    }

    @Test fun `a text that still needs UCS-2 is split at 67 without breaking an emoji`() {
        val body = "a".repeat(66) + "😀" + "b".repeat(70)
        val parts = SmsText.ucs2Parts(body)
        // The emoji's pair would straddle the first cut, so that part stops one short.
        assertEquals(listOf(66, 67, 5), parts.map { it.length })
        assertEquals(body, parts.joinToString(""))
        assertTrue(parts.all { it.length <= SmsText.UCS2_PART })
        assertTrue(parts.none { it.isNotEmpty() && Character.isHighSurrogate(it.last()) })
        assertEquals(listOf("short"), SmsText.ucs2Parts("short"))
    }
}
