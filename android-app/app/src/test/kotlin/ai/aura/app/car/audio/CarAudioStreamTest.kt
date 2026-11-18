package ai.aura.app.car.audio

import ai.aura.app.assist.Phase
import aap_protobuf.service.media.shared.message.ConfigOuterClass.Config
import aap_protobuf.service.media.shared.message.StartOuterClass.Start
import aap_protobuf.service.media.sink.message.AudioStreamTypeOuterClass.AudioStreamType
import aap_protobuf.service.media.source.message.AckOuterClass.Ack
import android.Manifest
import android.app.Application
import android.media.projection.MediaProjection
import androidx.test.core.app.ApplicationProvider
import ai.aura.app.car.audio.CarAudioStream.Companion.MEDIA_RETRY_MILLIS
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.robolectric.Shadows.shadowOf
import dev.headway.audio.CarAudioSource
import dev.headway.audio.PcmBuffer
import dev.headway.audio.PhoneAudioFocus
import dev.headway.protocol.channel.AudioChannel
import dev.headway.protocol.channel.AudioFocus
import dev.headway.protocol.channel.AvMessageId
import dev.headway.protocol.channel.MediaAudioRoute
import dev.headway.protocol.channel.PcmFormat
import dev.headway.protocol.framing.AapMessage
import dev.headway.protocol.framing.ChannelId
import dev.headway.protocol.io.Cryptor
import dev.headway.protocol.io.MessageChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.yield
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import kotlin.coroutines.coroutineContext

/**
 * What the real [CarAudioStream] puts on the car's audio channels around a spoken line
 * (DECISIONS row 130, T-1001; row 116 for the focus that follows): the guidance (or system)
 * stream is stopped after each line, before focus goes back to the music; the next line starts
 * it again under the next session id; a line cut short does the same; media stays started.
 *
 * T-1001's review: only [AudioChannel] was tested, not the order the stream drives it in.
 * Faked: the head unit (a recording [HeadUnit] that answers Setup, acknowledges every buffer and
 * grants every focus request), and nothing else -- the stream, its channels and the phone's
 * focus are the real classes. Not covered: a head unit that refuses a Start, or a link that
 * dies mid-line (the stream logs both and never throws for them).
 */
@RunWith(RobolectricTestRunner::class)
class CarAudioStreamTest {
    private val head = HeadUnit()
    private val guidance = head.sink(ChannelId.MEDIA_SINK_GUIDANCE_AUDIO.id, AudioStreamType.AUDIO_STREAM_GUIDANCE)
    private val system = head.sink(ChannelId.MEDIA_SINK_SYSTEM_AUDIO.id, AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO)
    private val media = head.sink(ChannelId.MEDIA_SINK_MEDIA_AUDIO.id, AudioStreamType.AUDIO_STREAM_MEDIA)
    private val control = AudioFocus(head.wire(ChannelId.CONTROL.id))
    private val phone = PhoneAudioFocus(ApplicationProvider.getApplicationContext(), control)
    private val stream = CarAudioStream(
        sinks = listOf(guidance, system, media),
        untouched = emptyList(),
        focus = control,
        phoneFocus = phone,
        source = CarAudioSource(ApplicationProvider.getApplicationContext()),
    )
    private var scope: CoroutineScope? = null

    @After
    fun tearDown() {
        scope?.cancel()
        stream.stop()
        if (this::stream0.isInitialized) stream0.stop()
    }

    private suspend fun up() {
        head.stream = stream
        scope = CoroutineScope(coroutineContext + Job())
        stream.start(scope!!, sessionId = 0)
        // The pump asks for music focus once something plays; the car's answer to the line's own
        // request is what the next assertions are about.
        phone.requestForMedia()
    }

    // T-1179: a false wake opened the car's microphone, the car switched to voice, and the
    // listen's end asked nothing back. Any end (answered, given up, error) is the phase leaving
    // LISTENING while music is relayed.
    @Test
    fun `a listen that ends while music is relayed asks the car for media focus again`() {
        assertTrue(reassertMediaAfterListen(wasListening = true, listening = false, relaying = true, prompt = false))
        // Still listening, never listened, or nothing relayed: nothing to ask.
        assertEquals(false, reassertMediaAfterListen(wasListening = true, listening = true, relaying = true, prompt = false))
        assertEquals(false, reassertMediaAfterListen(wasListening = false, listening = false, relaying = true, prompt = false))
        assertEquals(false, reassertMediaAfterListen(wasListening = true, listening = false, relaying = false, prompt = false))
        // A line being said hands focus back itself and owns the single focus slot meanwhile.
        assertEquals(false, reassertMediaAfterListen(wasListening = true, listening = false, relaying = true, prompt = true))
    }

    /** The re-asks a run of phases causes, as the pump computes them (prompt = a line is being said). */
    private fun reasks(vararg steps: Pair<Phase, Boolean>): Int {
        var was = false
        var asks = 0
        for ((phase, prompt) in steps) {
            if (reassertMediaAfterListen(was, phase == Phase.LISTENING, true, prompt || phase == Phase.THINKING)) asks++
            was = listenOwed(was, phase, prompt)
        }
        return asks
    }

    @Test
    fun `an answered turn asks for no extra focus, and a listen given up asks once`() {
        // Answered: her reply takes and hands back focus itself.
        assertEquals(0, reasks(Phase.LISTENING to false, Phase.THINKING to false, Phase.SPEAKING to true, Phase.IDLE to false, Phase.IDLE to false))
        // Given up straight from listening, and after thinking with nothing said.
        assertEquals(1, reasks(Phase.LISTENING to false, Phase.IDLE to false, Phase.IDLE to false))
        assertEquals(1, reasks(Phase.LISTENING to false, Phase.THINKING to false, Phase.ERROR to false, Phase.IDLE to false))
    }

    @Test
    fun `a spoken line is started, played, stopped, and only then is focus handed back to the music`() = runBlocking {
        up()
        assertTrue(stream.play(line(), AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO))

        assertEquals(
            listOf(
                "control:GAIN",
                "system:START#0",
                "control:GAIN_TRANSIENT_MAY_DUCK",
                "system:PCM#0",
                "system:STOP",
                "control:GAIN",
            ),
            head.trace(),
        )
    }

    @Test
    fun `the next line starts the channel again under the next session id`() = runBlocking {
        up()
        repeat(3) { assertTrue(stream.play(line(), AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO)) }

        assertEquals(listOf(0, 1, 2), head.startedSessions("system"))
        // Every line's buffers carry its own session, and a Stop closes each one.
        val lines = head.trace().filter { it.startsWith("system:") }
        assertEquals(
            listOf("START#0", "PCM#0", "STOP", "START#1", "PCM#1", "STOP", "START#2", "PCM#2", "STOP")
                .map { "system:$it" },
            lines,
        )
    }

    @Test
    fun `a streamed line on guidance ends with a Stop as well`() = runBlocking {
        up()
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        pieces.trySend(ByteArray(GUIDANCE_RATE * 2))
        pieces.close()
        assertTrue(stream.playStream(GUIDANCE_RATE, pieces))

        val lines = head.trace().filter { it.startsWith("guidance:") || it.startsWith("control:") }
        assertEquals(
            listOf(
                "control:GAIN",
                "guidance:START#0",
                "control:GAIN_TRANSIENT_MAY_DUCK",
                "guidance:PCM#0",
                "guidance:STOP",
                "control:GAIN",
            ),
            lines,
        )
    }

    // T-1171: a line that ran dry grows the head start; the clean lines after it bring it back.
    @Test
    fun `the head start grown by a dry line comes back down over the clean lines after it`() = runBlocking {
        up()
        val oneSecond = GUIDANCE_RATE * 2
        val dry = Channel<ByteArray>(Channel.UNLIMITED)
        dry.trySend(ByteArray(oneSecond))
        val line = launch { stream.playStream(GUIDANCE_RATE, dry) }
        waitFor { head.trace().contains("guidance:PCM#0") }
        delay(300) // the first stretch is acknowledged and the chunker is waiting on an empty queue
        // The queue was empty after the first second: the line waits, doubled, for the next two.
        dry.trySend(ByteArray(oneSecond * 2))
        dry.close()
        line.join()
        assertEquals(2.0, stream.streamHeadStartSeconds, 1e-9)

        val expected = listOf(1.5, 1.25, 1.125)
        for (want in expected) {
            val clean = Channel<ByteArray>(Channel.UNLIMITED)
            clean.trySend(ByteArray(oneSecond))
            clean.close()
            assertTrue(stream.playStream(GUIDANCE_RATE, clean))
            assertEquals(want, stream.streamHeadStartSeconds, 1e-9)
        }
    }

    @Test
    fun `a line cut short is not a clean line, the head start stays where it was`() = runBlocking {
        up()
        val oneSecond = GUIDANCE_RATE * 2
        val dry = Channel<ByteArray>(Channel.UNLIMITED)
        dry.trySend(ByteArray(oneSecond))
        val first = launch { stream.playStream(GUIDANCE_RATE, dry) }
        waitFor { head.trace().contains("guidance:PCM#0") }
        delay(300) // as above: the chunker is waiting on an empty queue
        dry.trySend(ByteArray(oneSecond * 2))
        dry.close()
        first.join()
        assertEquals(2.0, stream.streamHeadStartSeconds, 1e-9)

        val cut = Channel<ByteArray>(Channel.UNLIMITED) // never closed: the voice is still being made
        cut.trySend(ByteArray(oneSecond * 2)) // the grown head start of two seconds
        val line = launch { stream.playStream(GUIDANCE_RATE, cut) }
        waitFor { head.trace().contains("guidance:PCM#1") }
        line.cancelAndJoin()
        assertEquals(2.0, stream.streamHeadStartSeconds, 1e-9)
    }

    @Test
    fun `without music under the line focus is released after the Stop, not before`() = runBlocking {
        up()
        phone.release() // the pump's ten seconds of silence: no GAIN to give back
        head.clearTrace()
        assertTrue(stream.play(line(), AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO))

        assertEquals(
            listOf(
                "system:START#0",
                "control:GAIN_TRANSIENT_MAY_DUCK",
                "system:PCM#0",
                "system:STOP",
                "control:RELEASE",
            ),
            head.trace(),
        )
    }

    @Test
    fun `a line cancelled between its pieces still sends the Stop and hands focus back`() = runBlocking {
        up()
        val pieces = Channel<ByteArray>(Channel.UNLIMITED) // never closed: the voice is still being made
        pieces.trySend(ByteArray(GUIDANCE_RATE * 2))
        val line = launch { stream.playStream(GUIDANCE_RATE, pieces) }
        waitFor { head.trace().contains("guidance:PCM#0") }
        line.cancelAndJoin()

        assertEquals(
            listOf(
                "control:GAIN",
                "guidance:START#0",
                "control:GAIN_TRANSIENT_MAY_DUCK",
                "guidance:PCM#0",
                "guidance:STOP",
                "control:GAIN",
            ),
            head.trace().filter { it.startsWith("guidance:") || it.startsWith("control:") },
        )
        assertNextLineStartsSession(1)
    }

    @Test
    fun `a line cancelled while the car has stopped acknowledging still sends the Stop`() = runBlocking {
        up()
        head.acknowledging = false
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        pieces.trySend(ByteArray(GUIDANCE_RATE * 2))
        val line = launch { stream.playStream(GUIDANCE_RATE, pieces) }
        waitFor { head.trace().contains("guidance:PCM#0") }
        line.cancelAndJoin()

        val trace = head.trace()
        assertEquals("guidance:STOP", trace[trace.size - 2])
        assertEquals("control:GAIN", trace.last())
        head.acknowledging = true
        assertNextLineStartsSession(1)
    }

    @Test
    fun `media stays started for the whole drive, whatever the lines do`() = runBlocking {
        up()
        repeat(2) { assertTrue(stream.play(line(), AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO)) }
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        pieces.trySend(ByteArray(GUIDANCE_RATE * 2))
        val cut = launch { stream.playStream(GUIDANCE_RATE, pieces) }
        waitFor { head.trace().contains("guidance:PCM#0") }
        cut.cancelAndJoin()

        // Started once at bring-up, never stopped or started again, no session after the first.
        assertEquals(listOf("media:SETUP", "media:START#0"), head.all("media"))
        // The deferred sinks said nothing until a line needed them.
        assertEquals(listOf("system:SETUP", "system:START#0"), head.all("system").take(2))
        assertEquals(listOf("guidance:SETUP", "guidance:START#0"), head.all("guidance").take(2))
    }

    // --- the media pump's retry and its focus hold (T-1113) ----------------------
    //
    // Driven on virtual time, so the stream's own delay() is the pause under test. The capture
    // never gets as far as an AudioRecord: RECORD_AUDIO is denied, which makes every attempt
    // fail at once and say so, and that is what the retry and the hold react to. Not covered: a
    // capture that opens and then dies, and the read loop itself.

    @Test
    fun `a failed capture is retried after the pause, gives up after three, and a new grant starts over`() = runTest {
        val s = pumpStream(grant())
        runCurrent()
        assertEquals(1, attempts().size)

        advanceTimeBy(MEDIA_RETRY_MILLIS - 1)
        runCurrent()
        assertEquals("retried before the pause was over", 1, attempts().size)
        advanceTimeBy(1)
        runCurrent()
        assertEquals(2, attempts().size)

        advanceTimeBy(MEDIA_RETRY_MILLIS)
        runCurrent()
        val times = attempts().map { it.first }
        assertEquals(3, times.size)
        assertEquals(listOf(MEDIA_RETRY_MILLIS, MEDIA_RETRY_MILLIS), times.zipWithNext { a, b -> b - a })
        assertTrue("no giving up", steps.any { "giving up" in it.second })

        // The grant is dropped: nothing more is tried until the driver shares again.
        advanceTimeBy(10 * MEDIA_RETRY_MILLIS)
        runCurrent()
        assertEquals(3, attempts().size)
        s.adoptProjection(grant())
        runCurrent()
        assertEquals(4, attempts().size)
    }

    @Test
    fun `a closed stream makes no further capture attempt`() = runTest {
        val s = pumpStream(grant())
        runCurrent()
        assertEquals(1, attempts().size)

        s.stop()
        advanceTimeBy(10 * MEDIA_RETRY_MILLIS)
        runCurrent()
        assertEquals(1, attempts().size)
    }

    @Test
    fun `focus is held while the grant is replaced and released when none comes`() = runTest {
        // The grant goes away during the first attempt, as Android 15's lock does.
        var dropped = false
        val s = pumpStream(grant()) {
            if ("microphone permission" in it && !dropped) {
                dropped = true
                stream0.adoptProjection(null)
            }
        }
        s.mediaFocusHeld = true
        runCurrent()
        assertTrue(dropped)

        advanceTimeBy(GRANT_GAP_HOLD_MILLIS - 1)
        runCurrent()
        assertTrue("focus given back before the hold was over", s.mediaFocusHeld)
        assertTrue(steps.none { "focus released" in it.second })

        advanceTimeBy(1)
        runCurrent()
        assertEquals(false, s.mediaFocusHeld)
        assertEquals(1, steps.count { "focus released" in it.second })
    }

    @Test
    fun `a new grant inside the hold keeps focus and ends the wait`() = runTest {
        var dropped = false
        val s = pumpStream(grant()) {
            if ("microphone permission" in it && !dropped) {
                dropped = true
                stream0.adoptProjection(null)
            }
        }
        s.mediaFocusHeld = true
        runCurrent()
        advanceTimeBy(GRANT_GAP_HOLD_MILLIS / 2)
        s.adoptProjection(grant())
        // Well past where the hold would have run out, and past the new grant's own retries.
        advanceTimeBy(5 * GRANT_GAP_HOLD_MILLIS)
        runCurrent()

        assertTrue("the replaced grant still lost the car its focus", s.mediaFocusHeld)
        assertTrue(steps.none { "focus released" in it.second })
        assertTrue("the new grant was never tried", attempts().size >= 2)
    }

    // --- helpers --------------------------------------------------------------

    private val steps = java.util.Collections.synchronizedList(mutableListOf<Pair<Long, String>>())
    private lateinit var stream0: CarAudioStream

    private fun attempts() = steps.toList().filter { "microphone permission" in it.second }

    /** A grant is only ever compared by identity here, so no platform object stands behind it. */
    private fun grant(): MediaProjection {
        // By name: the Android bootclasspath the tests compile against does not list sun.misc.
        val unsafe = Class.forName("sun.misc.Unsafe")
        val field = unsafe.getDeclaredField("theUnsafe").apply { isAccessible = true }
        return unsafe.getMethod("allocateInstance", Class::class.java)
            .invoke(field.get(null), MediaProjection::class.java) as MediaProjection
    }

    /** A stream on the same fake head unit with media pumping on [initial], steps stamped with virtual time. */
    private suspend fun TestScope.pumpStream(initial: MediaProjection?, extra: (String) -> Unit = {}): CarAudioStream {
        val app = ApplicationProvider.getApplicationContext<Application>()
        shadowOf(app).denyPermissions(Manifest.permission.RECORD_AUDIO)
        val s = CarAudioStream(
            sinks = listOf(guidance, system, media),
            untouched = emptyList(),
            focus = control,
            phoneFocus = phone,
            source = CarAudioSource(app),
            projection = initial,
            appContext = app,
            onStep = { steps += testScheduler.currentTime to it; extra(it) },
        )
        stream0 = s
        head.stream = s
        s.start(backgroundScope, sessionId = 0)
        return s
    }

    private suspend fun assertNextLineStartsSession(next: Int) {
        assertTrue(stream.play(line(), AudioStreamType.AUDIO_STREAM_GUIDANCE))
        assertEquals(next, head.startedSessions("guidance").last())
        assertTrue("the next line did not play", head.trace().contains("guidance:PCM#$next"))
    }

    private fun line(): PcmBuffer = PcmBuffer(GUIDANCE_FORMAT, ByteArray(GUIDANCE_RATE / 10 * 2))

    private suspend fun waitFor(condition: () -> Boolean) = withTimeout(5_000) {
        while (!condition()) delay(5)
    }

    /**
     * The car's side: every message the phone sends is recorded in order, and answered the way
     * a cooperative head unit does (Setup -> Config, a buffer -> its Ack, a focus request -> the
     * grant). [trace] is what a line did on the wire, with runs of buffers collapsed.
     */
    private class HeadUnit {
        private val frames = java.util.Collections.synchronizedList(mutableListOf<Frame>())
        private val names = mutableMapOf<Int, String>()
        var acknowledging = true
        lateinit var stream: CarAudioStream

        private data class Frame(val who: String, val what: String, val session: Int?)

        private val sessions = mutableMapOf<Int, Int>()

        fun sink(id: Int, type: AudioStreamType): AudioChannel {
            names[id] = when (type) {
                AudioStreamType.AUDIO_STREAM_GUIDANCE -> "guidance"
                AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO -> "system"
                else -> "media"
            }
            val format = if (type == AudioStreamType.AUDIO_STREAM_MEDIA) MEDIA_FORMAT else GUIDANCE_FORMAT
            return AudioChannel(
                connection = wire(id),
                channelId = id,
                stream = type,
                advertisedFormats = listOf(format),
                route = MediaAudioRoute.AAP_MEDIA_CHANNEL,
            )
        }

        fun wire(id: Int): MessageChannel = object : MessageChannel {
            private val inbox = Channel<AapMessage>(Channel.UNLIMITED)
            override var cryptor: Cryptor? = null

            override suspend fun send(message: AapMessage) {
                // The real link's send queues on the connection's lock, so it can suspend, and
                // a send in a cancelled coroutine is refused: without this the cancelled-line
                // tests could not tell a Stop sent as NonCancellable from one that is not.
                yield()
                if (id == ChannelId.CONTROL.id) return onFocusRequest(message, inbox)
                val who = names.getValue(id)
                when (message.messageId) {
                    AvMessageId.SETUP -> {
                        frames += Frame(who, "SETUP", null)
                        inbox.send(reply(id, AvMessageId.CONFIG, Config.newBuilder()
                            .setStatus(Config.Status.STATUS_READY).setMaxUnacked(1)
                            .addConfigurationIndices(0).build().toByteArray()))
                    }
                    AvMessageId.START -> {
                        val session = Start.parseFrom(message.payload).sessionId
                        sessions[id] = session
                        frames += Frame(who, "START", session)
                    }
                    AvMessageId.STOP -> frames += Frame(who, "STOP", null)
                    AvMessageId.DATA -> {
                        frames += Frame(who, "PCM", sessions[id])
                        if (acknowledging) {
                            inbox.send(reply(id, AvMessageId.ACK, Ack.newBuilder()
                                .setSessionId(sessions.getValue(id)).setAck(1).build().toByteArray()))
                        }
                    }
                }
            }

            override suspend fun receive(): AapMessage = inbox.receive()
        }

        private fun onFocusRequest(message: AapMessage, inbox: Channel<AapMessage>) {
            val type = AudioFocus.parseRequest(message.payload)
            frames += Frame("control", type.name.removePrefix("AUDIO_FOCUS_"), null)
            // Delivered the way the session does: pushed in by whoever reads the control channel.
            stream.onControlMessage(
                AudioFocus.notificationMessage(AudioFocus.openautoResponseTo(type))
            )
        }

        private fun reply(id: Int, messageId: Int, payload: ByteArray) =
            AapMessage(channelId = id, control = false, encrypted = true, messageId = messageId, payload = payload)

        /** One entry per frame, `who:WHAT#session`, consecutive buffers of one session as one. */
        fun trace(): List<String> = all(null).filterNot { it.endsWith(":SETUP") || it.startsWith("media:") }

        fun all(who: String?): List<String> {
            val out = mutableListOf<String>()
            val skipped = skip
            for ((i, f) in frames.toList().withIndex()) {
                if (i < skipped) continue
                if (who != null && f.who != who) continue
                val text = f.who + ":" + f.what + (f.session?.let { "#$it" } ?: "")
                if (f.what == "PCM" && out.lastOrNull() == text) continue
                out += text
            }
            return out
        }

        fun startedSessions(who: String): List<Int> =
            frames.toList().filter { it.who == who && it.what == "START" }.map { it.session!! }

        private var skip = 0

        fun clearTrace() {
            skip = frames.size
        }
    }

    private companion object {
        const val GUIDANCE_RATE = 16_000
        val GUIDANCE_FORMAT = PcmFormat(GUIDANCE_RATE, 16, 1)
        val MEDIA_FORMAT = PcmFormat(48_000, 16, 2)
    }
}
