package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** "Hey Aura" wakes the assistant as AURA, once (2026-09-25: wake word by persona, on by default). */
class PersonaWakeTest {

    @Test
    fun `each trained name calls its persona, and Jarvis's own calls Jarvis`() {
        assertEquals("Aura", OnDeviceWakeWord.personaOf("aura"))
        assertEquals("Jarvis", OnDeviceWakeWord.personaOf(OnDeviceWakeWord.JARVIS))
        assertNull(OnDeviceWakeWord.personaOf("hey_somebody"))
        // T-0936: the three retired heads are no longer heard, even if a phone kept their files.
        assertNull(OnDeviceWakeWord.personaOf("hey_ada")) // T-0936
        assertEquals(listOf("aura.onnx"), OnDeviceWakeWord.PERSONA_MODELS.values.toList())
    }

    @Test
    fun `the woken persona is handed over once, never to a later conversation`() {
        WakePersona.pending = "Aura"
        assertEquals("Aura", WakePersona.take())
        assertNull(WakePersona.take())
    }

    @Test
    fun `Aura's head fires at her pack's measured threshold, every other head at the level's`() {
        fun fires(word: String, score: Float): Boolean {
            val s = WakeSensitivity.NORMAL.scorerFor(word)
            return listOf(0L, 80L, 160L).any { s.onScore(it, score) }
        }
        // 0.95, two frames running (themes/aura/assets/wake/aura.metrics.json).
        assertFalse(fires("aura", 0.9f))
        assertTrue(fires("aura", 0.96f))
        assertTrue(fires(OnDeviceWakeWord.JARVIS, 0.6f))
    }

    @Test
    fun `a turn Aura opened needs a recognised voice before its words are sent`() {
        WakePersona.verifyAt = 1_000L
        assertTrue(WakePersona.takeVerify(1_500L))
        assertFalse(WakePersona.takeVerify(1_600L))
        // A wake whose turn never began does not reach a turn started by hand later.
        WakePersona.verifyAt = 1_000L
        assertFalse(WakePersona.takeVerify(1_000L + WakePersona.VERIFY_WINDOW_MS + 1))
        assertEquals(0L, WakePersona.verifyAt)
        // Words with no ticket: refused. With one, or silence, or a turn no verified wake opened: not.
        assertTrue(WakePersona.refused(dropped = false, ticket = null, transcript = "lights on", unverified = true))
        assertFalse(WakePersona.refused(dropped = false, ticket = "t1", transcript = "lights on", unverified = true))
        assertFalse(WakePersona.refused(dropped = false, ticket = null, transcript = " ", unverified = true))
        assertFalse(WakePersona.refused(dropped = false, ticket = null, transcript = "lights on", unverified = false))
        assertTrue(WakePersona.refused(dropped = true, ticket = null, transcript = "", unverified = false))
    }

    @Test
    fun `every pack with a trained head is found by that head's name`() {
        // The scorer and the verification flag look a pack up by the detector's name; a head
        // filed under another name would fire at the default threshold and skip verification.
        val trained = ai.jarvis.app.ui.theme.JarvisThemes.ALL.filter { it.wakeModel.startsWith("assets/wake/") }
        assertTrue(trained.isNotEmpty())
        for (theme in trained) {
            val head = theme.wakeModel.substringAfterLast('/').removeSuffix(".onnx")
            assertEquals(theme.id, head)
            assertEquals("$head.onnx", OnDeviceWakeWord.PERSONA_MODELS[head])
        }
    }
}
