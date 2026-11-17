package ai.jarvis.app.car.dash.tiles

import ai.jarvis.app.car.voice.CarVoice
import ai.jarvis.app.car.voice.CarVoiceEvent
import ai.jarvis.app.glass.ReactorModel
import ai.jarvis.app.glass.ReactorView
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * AUDIT B9 / brief §1 car: the car's reactor never moved with the voice, on a touch turn
 * (onAmplitude was dropped) or a steering-wheel one (the car mic had no level at all).
 */
@RunWith(RobolectricTestRunner::class)
class JarvisTileLevelTest {

    @After fun quiet() = CarVoice.reset()

    private fun reactorIn(view: View): ReactorView =
        if (view is ReactorView) view else (0 until (view as ViewGroup).childCount).firstNotNullOf {
            runCatching { reactorIn(view.getChildAt(it)) }.getOrNull()
        }

    @Test
    fun `the reactor follows the car microphone while a steering-wheel turn listens`() {
        val tile = JarvisTile(ApplicationProvider.getApplicationContext())
        val reactor = reactorIn(tile.createView(ApplicationProvider.getApplicationContext()))
        tile.start()
        CarVoice.post(CarVoiceEvent.Listening)
        CarVoice.level(0.05f)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0.05f * ReactorModel.AMPLITUDE_GAIN, reactor.model.level, 1e-4f)
        // Heard: the mic is closed, the swell goes with it.
        CarVoice.post(CarVoiceEvent.Heard("lights off"))
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0f, reactor.model.level, 1e-4f)
        tile.stop()
    }

    @Test
    fun `a touch turn's level reaches the reactor too`() {
        val tile = JarvisTile(ApplicationProvider.getApplicationContext())
        val reactor = reactorIn(tile.createView(ApplicationProvider.getApplicationContext()))
        tile.onAmplitude(0.1f)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0.1f * ReactorModel.AMPLITUDE_GAIN, reactor.model.level, 1e-4f)
    }
}
