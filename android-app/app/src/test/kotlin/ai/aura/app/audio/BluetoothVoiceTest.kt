package ai.aura.app.audio

import ai.aura.app.car.dash.CarShell
import android.content.Intent
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * T-0744: the car's own voice button, while the car's screen is up, is the car's turn over
 * Android Auto (its microphone, its guidance voice), not hands-free voice recognition, which
 * puts the car in a voice call that sounds wrong and does not let go.
 */
@RunWith(RobolectricTestRunner::class)
class BluetoothVoiceTest {

    @After
    fun reset() {
        CarShell.onVoiceRequested = null
    }

    @Test
    fun `the car's voice button goes to the car's own turn while the car is up`() {
        var turns = 0
        CarShell.onVoiceRequested = { turns++ }
        assertTrue(BluetoothVoice.handToCar(Intent(Intent.ACTION_VOICE_COMMAND)))
        assertEquals(1, turns)
    }

    @Test
    fun `without the car up, or for another request, it is not handed over`() {
        assertFalse(BluetoothVoice.handToCar(Intent(Intent.ACTION_VOICE_COMMAND)))
        var turns = 0
        CarShell.onVoiceRequested = { turns++ }
        assertFalse(BluetoothVoice.handToCar(Intent(Intent.ACTION_ASSIST)))
        assertFalse(BluetoothVoice.handToCar(null))
        assertEquals(0, turns)
    }
}
