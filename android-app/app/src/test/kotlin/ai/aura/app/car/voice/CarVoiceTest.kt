package ai.aura.app.car.voice

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.assist.Phase
import aap_protobuf.service.media.shared.message.ConfigOuterClass.Config
import dev.headway.protocol.channel.MicrophoneChannel
import org.junit.Assert.assertEquals
import org.junit.Test

class CarVoiceTest {
    @Test fun `a steering-wheel turn walks the reactor through listening, thinking, speaking and back`() {
        var v = CarVoiceView()
        v = reduce(v, CarVoiceEvent.Listening)
        assertEquals(Phase.LISTENING, v.phase)
        v = reduce(v, CarVoiceEvent.Heard("turn on the bed light"))
        assertEquals("turn on the bed light", v.heard)
        v = reduce(v, CarVoiceEvent.Turn(CarTurnState.Thinking, null))
        assertEquals(Phase.THINKING, v.phase)
        v = reduce(v, CarVoiceEvent.Turn(CarTurnState.Speaking, "The bed light is on."))
        assertEquals(Phase.SPEAKING, v.phase)
        assertEquals("The bed light is on.", v.answer)
        v = reduce(v, CarVoiceEvent.Turn(CarTurnState.Idle, null))
        assertEquals(Phase.IDLE, v.phase)
        // What was said and answered stays on the tab after the turn.
        assertEquals("turn on the bed light", v.heard)
        assertEquals("The bed light is on.", v.answer)
    }

    @Test fun `an approval shows on the reactor as waiting for the driver`() {
        val v = reduce(CarVoiceView(), CarVoiceEvent.Turn(CarTurnState.Approval, null))
        assertEquals(Phase.THINKING, v.phase)
        assertEquals("Waiting for you on the car screen", v.caption)
    }

    @Test fun `a line for the driver is said in the caption and leaves the phase alone`() {
        CarVoice.reset()
        CarVoice.post(CarVoiceEvent.Listening)
        CarVoice.note("Reconnect for this to take effect")
        assertEquals("Reconnect for this to take effect", CarVoice.view.value.caption)
        assertEquals(Phase.LISTENING, CarVoice.view.value.phase)
        // The turn's next event replaces it, as it replaces any caption.
        CarVoice.post(CarVoiceEvent.Heard("navigate home"))
        assertEquals("", CarVoice.view.value.caption)
        CarVoice.reset()
    }

    @Test fun `a new turn clears the last answer`() {
        val v = reduce(CarVoiceView(answer = "old"), CarVoiceEvent.Listening)
        assertEquals("", v.answer)
    }

    @Test fun `a refused voice and no link are said, not silent`() {
        assertEquals("${ActiveTheme.name} did not recognise your voice", reduce(CarVoiceView(), CarVoiceEvent.Refused).caption)
        val off = reduce(CarVoiceView(), CarVoiceEvent.Offline)
        assertEquals(Phase.ERROR, off.phase)
        assertEquals("${ActiveTheme.name} is offline", off.caption)
        assertEquals("Heard nothing", reduce(CarVoiceView(), CarVoiceEvent.Nothing).caption)
    }

    @Test fun `a transcribe that failed with the network up is not said as offline`() {
        val failed = reduce(CarVoiceView(), CarVoiceEvent.Failed)
        assertEquals(Phase.ERROR, failed.phase)
        assertEquals("${ActiveTheme.name} could not hear that", failed.caption)
    }

    // T-1179 (build 1840): a false wake opened the car's microphone through the press-time probe,
    // and the car showed voice mode. A press may probe; a wake's turn never does.
    @Test fun `a wake word's turn never probes the car's microphone, a press may`() {
        assertEquals(true, probesCarMicrophone(carSetupUnanswered = true, byWake = false))
        assertEquals(false, probesCarMicrophone(carSetupUnanswered = true, byWake = true))
        assertEquals(false, probesCarMicrophone(carSetupUnanswered = false, byWake = false))
    }

    // T-1218: a head unit that needs no setup (it answered it 0x00ff) is used without one; if its
    // open is then refused the press goes to the phone's microphone rather than to a dead button.
    @Test fun `a refused open on a no-setup head unit falls back to the phone, one that did its setup does not`() {
        val config = Config.Status.STATUS_READY
        val noSetup = MicrophoneChannel.SetupResponse(config, null, emptyList(), noSetupNeeded = true)
        val normal = MicrophoneChannel.SetupResponse(config, 1, listOf(0))
        val refused = MicrophoneChannel.CaptureResponse(-7, -1, opening = true)
        val ok = MicrophoneChannel.CaptureResponse(0, 1, opening = true)
        assertEquals(true, openFallsBackToPhone(noSetup, refused))
        assertEquals(false, openFallsBackToPhone(noSetup, ok))
        assertEquals(false, openFallsBackToPhone(normal, refused))
        assertEquals(true, openFallsBackToPhone(normal, null))
        assertEquals(true, openFallsBackToPhone(noSetup, null))
    }

    @Test fun `a wake arms exactly one turn, within its window`() {
        ai.aura.app.assist.WakePersona.wakeAt = 1_000L
        assertEquals(true, ai.aura.app.assist.WakePersona.takeWake(1_500L))
        assertEquals(false, ai.aura.app.assist.WakePersona.takeWake(1_600L))
        // A wake whose turn never began must not mark a press made long after.
        ai.aura.app.assist.WakePersona.wakeAt = 1_000L
        assertEquals(false, ai.aura.app.assist.WakePersona.takeWake(1_000L + ai.aura.app.assist.WakePersona.VERIFY_WINDOW_MS + 1))
    }

    // Build 1447: the speaker gate refused the phone-microphone turns, the phone's path
    // posted nothing, and the car sat on Listening for good.
    @Test fun `a listening session that ends without an event goes idle, and a live turn is left alone`() {
        assertEquals(Phase.IDLE, reduce(reduce(CarVoiceView(), CarVoiceEvent.Listening), CarVoiceEvent.Ended).phase)
        val thinking = reduce(reduce(CarVoiceView(), CarVoiceEvent.Listening), CarVoiceEvent.Heard("text dad"))
        assertEquals(Phase.THINKING, reduce(thinking, CarVoiceEvent.Ended).phase)
    }
}
