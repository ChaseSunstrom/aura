package ai.aura.app.car.voice

import ai.aura.app.assist.hermes.HudState
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class CarTurnTest {
    private class FakeJarvis : CarJarvis {
        var reply: String? = "You have two meetings."
        var gate: CompletableDeferred<Unit>? = null
        var approval: HudState.Approval? = null
        var approved: Boolean? = null
        val asked = mutableListOf<Pair<String, String?>>()
        var interrupted = 0
        val spoken = mutableListOf<Boolean>()
        override suspend fun ask(text: String, ticket: String?, voice: Boolean, onApproval: suspend (HudState.Approval) -> Boolean): String? {
            asked += text to ticket
            spoken += voice
            approval?.let { approved = onApproval(it) }
            gate?.await()
            return reply
        }
        override fun interrupt() { interrupted++ }
        override fun speak(text: String, onStart: (Int) -> Unit, onPiece: (ByteArray) -> Unit): Boolean {
            onStart(24_000); onPiece(ByteArray(480)); onPiece(ByteArray(480)); return true
        }
    }

    private class FakeSpeaker : CarSpeaker {
        val played = mutableListOf<Pair<Int, Int>>()
        var gate: CompletableDeferred<Unit>? = null
        override suspend fun play(pcm: ByteArray, rateHz: Int): Boolean { gate?.await(); played += pcm.size to rateHz; return true }
        override suspend fun say(text: String) { played += -1 to 0 }
    }

    @Test fun `a question is asked with its ticket and the answer is spoken in the car`() = runTest {
        val jarvis = FakeJarvis(); val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("what's on my calendar", "t-1")
        advanceUntilIdle()
        assertEquals(listOf("what's on my calendar" to "t-1"), jarvis.asked)
        assertEquals(listOf(960 to 24_000), speaker.played)
        assertEquals(CarTurnState.Idle, turn.state.value)
    }

    @Test fun `the last reply is said again for a few seconds, and never asked again`() = runTest {
        // The wheel's previous key, just after she has spoken (the owner, 2026-11-11).
        val jarvis = FakeJarvis(); val speaker = FakeSpeaker()
        var now = 1_000L
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler), clock = { now })
        // Nothing said yet: the key is the music's.
        assertEquals(false, turn.repeat())
        turn.ask("what's on my calendar", null)
        advanceUntilIdle()
        now += CarTurn.REPEAT_WINDOW_MILLIS - 1
        assertEquals(true, turn.repeat())
        advanceUntilIdle()
        assertEquals(listOf(960 to 24_000, 960 to 24_000), speaker.played)
        assertEquals(1, jarvis.asked.size)
        assertEquals(CarTurnState.Idle, turn.state.value)
        // The window runs from the end of the repeat, and then closes.
        now += CarTurn.REPEAT_WINDOW_MILLIS
        assertEquals(false, turn.repeat())
        advanceUntilIdle()
        assertEquals(2, speaker.played.size)
    }

    @Test fun `the reply is played while its voice is still being made`() = runTest {
        val firstHeard = CompletableDeferred<Unit>()
        var heardBeforeTheEnd = false
        val jarvis = object : CarJarvis {
            override suspend fun ask(text: String, ticket: String?, voice: Boolean, onApproval: suspend (HudState.Approval) -> Boolean) = "Two meetings."
            override fun interrupt() = Unit
            override fun speak(text: String, onStart: (Int) -> Unit, onPiece: (ByteArray) -> Unit): Boolean {
                onStart(24_000); onPiece(ByteArray(480))
                // The speaker has the first piece before the second is made.
                heardBeforeTheEnd = kotlinx.coroutines.runBlocking { kotlinx.coroutines.withTimeoutOrNull(2_000) { firstHeard.await() } } != null
                onPiece(ByteArray(480)); return true
            }
        }
        val speaker = object : CarSpeaker {
            var bytes = 0
            override suspend fun play(pcm: ByteArray, rateHz: Int) = true
            override suspend fun stream(rateHz: Int, pieces: kotlinx.coroutines.channels.ReceiveChannel<ByteArray>): Boolean {
                for (p in pieces) { bytes += p.size; firstHeard.complete(Unit) }
                return bytes > 0
            }
            override suspend fun say(text: String) = Unit
        }
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.Dispatchers.IO)
        turn.ask("what's on", null)
        while (turn.state.value != CarTurnState.Idle || speaker.bytes == 0) kotlinx.coroutines.yield().also { Thread.sleep(5) }
        assertTrue(heardBeforeTheEnd)
        assertEquals(960, speaker.bytes)
    }

    // T-1161: on the 2026-11-12 drive every reply voiced at the house arrived choppy over
    // mobile data; her voice is on the phone too.
    private class Counting : CarJarvis {
        val voiced = mutableListOf<String>()
        override suspend fun ask(text: String, ticket: String?, voice: Boolean, onApproval: suspend (HudState.Approval) -> Boolean) =
            "Two meetings today. The first is at nine! Is that all right?"
        override fun interrupt() = Unit
        override fun speak(text: String, onStart: (Int) -> Unit, onPiece: (ByteArray) -> Unit): Boolean {
            voiced += text; onStart(24_000); onPiece(ByteArray(100)); return true
        }
    }

    @Test fun `with her voice on the phone a reply is said from it sentence by sentence, and the house is not asked`() = runTest {
        val jarvis = Counting(); val speaker = FakeSpeaker()
        val rendered = mutableListOf<String>()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { text -> rendered += text; 24_000 to ByteArray(10 * text.length) })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("Two meetings today.", "The first is at nine!", "Is that all right?"), rendered)
        assertTrue(jarvis.voiced.isEmpty())
        assertEquals(listOf(10 * rendered.sumOf { it.length } to 24_000), speaker.played)
    }

    @Test fun `with no voice on the phone the house says the whole reply`() = runTest {
        val jarvis = Counting(); val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { null })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("Two meetings today. The first is at nine! Is that all right?"), jarvis.voiced)
        assertEquals(listOf(100 to 24_000), speaker.played)
    }

    @Test fun `a phone voice that fails part way leaves the rest to the house, and nothing is said twice`() = runTest {
        val jarvis = Counting(); val speaker = FakeSpeaker()
        var n = 0
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { if (n++ == 0) 24_000 to ByteArray(40) else null })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("The first is at nine! Is that all right?"), jarvis.voiced)
        assertEquals(listOf(40 to 24_000, 100 to 24_000), speaker.played)
    }

    @Test fun `a phone voice that gets slow part way leaves the rest to the house instead of a silence`() = runTest {
        // From the review: realtime() is measured once, at the fetch; a hot phone is slower.
        val jarvis = Counting(); val speaker = FakeSpeaker()
        var n = 0
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { kotlinx.coroutines.delay(if (n++ > 0) 60_000L else 0L); 24_000 to ByteArray(48_000) })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("The first is at nine! Is that all right?"), jarvis.voiced)
        assertEquals(listOf(48_000 to 24_000, 100 to 24_000), speaker.played)
    }

    @Test fun `a phone voice too slow with its first sentence leaves the whole reply to the house`() = runTest {
        val jarvis = Counting(); val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { kotlinx.coroutines.delay(CarTurn.FIRST_SENTENCE_MILLIS + 1); 24_000 to ByteArray(40) })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("Two meetings today. The first is at nine! Is that all right?"), jarvis.voiced)
        assertEquals(listOf(100 to 24_000), speaker.played)
    }

    @Test fun `a sentence at another rate, and a phone voice the car would not play, go to the house`() = runTest {
        val jarvis = Counting(); val speaker = FakeSpeaker()
        var n = 0
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { (if (n++ == 0) 24_000 else 16_000) to ByteArray(40) })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("The first is at nine! Is that all right?"), jarvis.voiced)

        // The car refuses the phone's stream (nothing played): the whole reply is the house's.
        val house = Counting()
        val deaf = object : CarSpeaker {
            val streams = mutableListOf<Int>()
            override suspend fun play(pcm: ByteArray, rateHz: Int) = true
            override suspend fun stream(rateHz: Int, pieces: kotlinx.coroutines.channels.ReceiveChannel<ByteArray>): Boolean {
                var bytes = 0
                for (p in pieces) bytes += p.size
                streams += bytes
                return streams.size > 1
            }
            override suspend fun say(text: String) = Unit
        }
        val again = CarTurn(house, deaf, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler),
            own = { 24_000 to ByteArray(40) })
        again.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(listOf("Two meetings today. The first is at nine! Is that all right?"), house.voiced)
        assertEquals(listOf(120, 100), deaf.streams)
    }

    @Test fun `a reply is cut at its sentence ends, and a number or an initial is not an end`() {
        assertEquals(listOf("The meeting is at 9 p.m. tomorrow.", "J. Smith is in."), CarTurn.sentences("The meeting is at 9 p.m. tomorrow. J. Smith is in."))
        assertEquals(listOf("It is 3.5 miles.", "Turn left at St. Mary's?", "Yes"), CarTurn.sentences("It is 3.5 miles. Turn left at St. Mary's?  Yes"))
        assertEquals(listOf("One line"), CarTurn.sentences("One line"))
        assertEquals(emptyList<String>(), CarTurn.sentences("  "))
    }

    @Test fun `which voice says a car reply is the contract's`() {
        val c = org.json.JSONObject(java.io.File("../../tests/contracts/car-state.json").readText()).getJSONObject("voice")
        val rows = c.getJSONArray("uses")
        assertTrue(rows.length() >= 6)
        for (i in 0 until rows.length()) {
            val r = rows.getJSONObject(i)
            assertEquals(r.toString(), r.getBoolean("phone"), CarTurn.usesPhone(r.getString("mode"), r.getBoolean("ready"), r.getBoolean("realtime")))
        }
    }

    @Test fun `an empty transcript is not a question`() = runTest {
        val jarvis = FakeJarvis(); val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("  ", null)
        advanceUntilIdle()
        assertTrue(jarvis.asked.isEmpty())
        assertTrue(speaker.played.isEmpty())
    }

    @Test fun `the turn reports its states and the answer as it goes`() = runTest {
        val seen = mutableListOf<Pair<CarTurnState, String?>>()
        val turn = CarTurn(FakeJarvis(), FakeSpeaker(), approve = { false }, scope = this,
            io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler), onUpdate = { s, a -> seen += s to a })
        turn.ask("what's on", null)
        advanceUntilIdle()
        assertEquals(
            listOf(CarTurnState.Thinking to null, CarTurnState.Speaking to "You have two meetings.", CarTurnState.Idle to null),
            seen,
        )
    }

    @Test fun `only a spoken question says it was spoken`() = runTest {
        val jarvis = FakeJarvis(); val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("lights off", null, voice = true)
        advanceUntilIdle()
        turn.ask(CarTurn.DRIVE_START, null)
        advanceUntilIdle()
        assertEquals(listOf(true, false), jarvis.spoken)
    }

    @Test fun `a drive start with no routine is answered (quiet) and nothing is said`() = runTest {
        val jarvis = FakeJarvis().apply { reply = CarTurn.QUIET }; val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask(CarTurn.DRIVE_START, null)
        advanceUntilIdle()
        assertEquals(listOf(CarTurn.DRIVE_START to null), jarvis.asked)
        assertTrue(speaker.played.isEmpty())
        assertEquals(CarTurnState.Idle, turn.state.value)
    }

    @Test fun `no link says so in the car instead of going quiet`() = runTest {
        val jarvis = FakeJarvis().apply { reply = null }; val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("hello", null)
        advanceUntilIdle()
        assertEquals(listOf(-1 to 0), speaker.played)
        assertEquals(CarTurnState.Idle, turn.state.value)
    }

    @Test fun `a turn cancelled while Jarvis is thinking is never spoken, and Jarvis is told to stop`() = runTest {
        val jarvis = FakeJarvis().apply { gate = CompletableDeferred() }; val speaker = FakeSpeaker()
        val turn = CarTurn(jarvis, speaker, approve = { false }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("tell me a long story", null)
        advanceTimeBy(100)
        assertEquals(CarTurnState.Thinking, turn.state.value)
        turn.cancel()
        jarvis.gate!!.complete(Unit)
        advanceUntilIdle()
        assertTrue(speaker.played.isEmpty())
        assertEquals(1, jarvis.interrupted)
        assertEquals(CarTurnState.Idle, turn.state.value)
    }

    @Test fun `an approval nobody answers is denied when the time runs out`() = runTest {
        val jarvis = FakeJarvis().apply { approval = HudState.Approval("r1", "terminal", "rm -rf /tmp/x", "", listOf("once", "deny")) }
        val never = CompletableDeferred<Boolean>()
        val turn = CarTurn(jarvis, FakeSpeaker(), approve = { never.await() }, scope = this, approvalTimeoutMs = 20_000, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("clean up", null)
        advanceTimeBy(19_000)
        assertEquals(CarTurnState.Approval, turn.state.value)
        advanceUntilIdle()
        assertEquals(false, jarvis.approved)
    }

    @Test fun `an approval the driver gives goes through`() = runTest {
        val jarvis = FakeJarvis().apply { approval = HudState.Approval("r1", "lights", "", "", listOf("once", "deny")) }
        val turn = CarTurn(jarvis, FakeSpeaker(), approve = { true }, scope = this, io = kotlinx.coroutines.test.StandardTestDispatcher(testScheduler))
        turn.ask("lights on", null)
        advanceUntilIdle()
        assertEquals(true, jarvis.approved)
    }
}

class ApprovalChoiceTest {
    private val offer = HudState.Approval("r", "t", "", "", listOf("once", "session", "always", "deny"))

    @Test fun `yes is once, never always`() = assertEquals("once", HermesCarJarvis.choiceFor(offer, true))

    @Test fun `no is deny`() = assertEquals("deny", HermesCarJarvis.choiceFor(offer, false))

    @Test fun `with no deny offered, no is the last choice`() =
        assertEquals("cancel", HermesCarJarvis.choiceFor(HudState.Approval("r", "t", "", "", listOf("ok", "cancel")), false))
}
