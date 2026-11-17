package ai.jarvis.app.car.voice

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.car.audio.inVoiceTurn
import ai.jarvis.app.car.audio.musicHushed
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch

/**
 * How a car listen ends (T-1111). On the 2026-11-12 drive one listen kept the car on
 * Listening for about 30 s after the microphone had closed: the wait for the house's
 * transcript had no bound of its own, and all of it counted as listening, so the music was
 * withheld and the car's audio held for a turn that never came.
 */
class CarListenEndTest {

    @Test fun `a transcriber that never answers is given up on at the cap`() = runBlocking {
        val never = CountDownLatch(1)
        val began = System.nanoTime()
        val answer = bounded(50L) { never.await(); "late" }
        assertNull(answer)
        assertTrue("gave up in ${(System.nanoTime() - began) / 1_000_000} ms", System.nanoTime() - began < 2_000_000_000L)
        never.countDown()
    }

    @Test fun `a transcriber that answers in time is heard`() = runBlocking {
        assertEquals("lights on", bounded(2_000L) { "lights on" })
    }

    @Test fun `an answer that lands after the next listen began does not judge it`() {
        val release = CountDownLatch(1)
        val sent = CountDownLatch(1)
        val stt = JarvisTranscriber(16_000) { sent.countDown(); release.await(); ai.jarvis.app.assist.hermes.HermesClient.Transcribed.Offline }
        stt.accept(ByteArray(320), 320)
        val late = kotlin.concurrent.thread { stt.finish() }
        sent.await()
        stt.reset() // the next press
        release.countDown()
        late.join(2_000)
        assertFalse("the abandoned listen's failure was left on the next one", stt.offline)
        assertNull(stt.failure)
    }

    @Test fun `the phase leaves listening when the microphone closes, so the music comes back`() {
        val closed = reduce(reduce(CarVoiceView(), CarVoiceEvent.Listening), CarVoiceEvent.Hearing)
        assertEquals(Phase.THINKING, closed.phase)
        assertFalse(musicHushed(closed.phase == Phase.LISTENING, 0L))
    }

    @Test fun `a listen that ends while its words are still awaited goes idle`() {
        val closed = reduce(reduce(CarVoiceView(), CarVoiceEvent.Listening), CarVoiceEvent.Hearing)
        assertEquals(Phase.IDLE, reduce(closed, CarVoiceEvent.Ended).phase)
        // Words that did arrive start a turn, and the listen ending leaves that alone.
        val heard = reduce(closed, CarVoiceEvent.Heard("text dad"))
        assertEquals(Phase.THINKING, reduce(heard, CarVoiceEvent.Ended).phase)
    }

    @Test fun `a refusal, an empty transcript and a timeout all end outside a turn`() {
        val closed = reduce(reduce(CarVoiceView(), CarVoiceEvent.Listening), CarVoiceEvent.Hearing)
        assertTrue(inVoiceTurn(closed.phase, prompt = false))
        for (end in listOf(CarVoiceEvent.Refused, CarVoiceEvent.Nothing, CarVoiceEvent.Offline)) {
            val ended = reduce(reduce(closed, end), CarVoiceEvent.Ended)
            assertFalse("$end left the car in a turn", inVoiceTurn(ended.phase, prompt = false))
            assertFalse("$end left the music withheld", musicHushed(ended.phase == Phase.LISTENING, 0L))
        }
        // A line being said is a turn whatever the phase says.
        assertTrue(inVoiceTurn(Phase.IDLE, prompt = true))
    }
}
