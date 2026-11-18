package ai.aura.app.assist

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The follow-up window's silent close is an end, not a fault (T-0430).
 *
 * After a reply both engines listen again for the inactivity window; when the owner
 * said nothing more, the turn took the no-speech branch and the wake overlay showed
 * "I did not hear anything" under an Error caption over the reply. The mirror is
 * `android-app/tools/silence_diagnosis_test.py`.
 */
class FollowUpCloseTest {

    private fun vad(peak: Float) = VoiceActivity().also { it.onLevel(1L, peak) }

    @Test fun `a turn that heard speech ends quietly`() {
        assertTrue(vad(0.3f).silenceIsAnEnd(sawSpeech = true, exchanged = false))
    }

    @Test fun `the first turn hearing nothing is still diagnosed`() {
        assertFalse(vad(0.4f).silenceIsAnEnd(sawSpeech = false, exchanged = false))
    }

    @Test fun `silence after a submitted turn is the owner being done`() {
        assertTrue(vad(0.4f).silenceIsAnEnd(sawSpeech = false, exchanged = true))
        assertTrue(vad(0.002f).silenceIsAnEnd(sawSpeech = false, exchanged = true))
    }

    @Test fun `a dead mic after a typed turn still says so`() {
        assertFalse(vad(0f).silenceIsAnEnd(sawSpeech = false, exchanged = true))
    }
}
