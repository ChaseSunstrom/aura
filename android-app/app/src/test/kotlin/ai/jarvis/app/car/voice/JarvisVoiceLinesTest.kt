package ai.jarvis.app.car.voice

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class JarvisVoiceLinesTest {
    @get:Rule val tmp = TemporaryFolder()

    /** What reached the car: a Jarvis clip by its size, or the phone engine's line by its text. */
    private val heard = mutableListOf<String>()

    private fun lines() = JarvisVoiceLines(
        dir = tmp.root,
        play = { pcm, rate -> heard += "jarvis:${pcm.size}@$rate"; true },
        engine = { text -> heard += "engine:$text" },
    )

    @Test fun `a line Jarvis said online is said offline in Jarvis's voice, not the phone's`() = runTest {
        lines().remember(CarTurn.OFFLINE, 24_000, ByteArray(960))
        lines().say(CarTurn.OFFLINE)
        assertEquals(listOf("jarvis:960@24000"), heard)
    }

    @Test fun `a line never said online is announced once in Jarvis's voice before the phone engine says it`() = runTest {
        val voice = lines()
        voice.remember(JarvisVoiceLines.NOTICE, 22_050, ByteArray(480))
        voice.say("Navigating to Home Depot")
        voice.say("Navigating to the office")
        assertEquals(
            listOf("jarvis:480@22050", "engine:Navigating to Home Depot", "engine:Navigating to the office"),
            heard,
        )
    }

    @Test fun `the notice is said once a drive, however often the house comes and goes`() = runTest {
        // It was re-armed by every line the house voiced; on a link that came and went the
        // owner heard it "throughout the entire drive" (2026-11-11).
        val voice = lines()
        voice.remember(JarvisVoiceLines.NOTICE, 24_000, ByteArray(480))
        voice.say("one")
        voice.remember("a line the house just voiced", 24_000, ByteArray(8))
        voice.say("three")
        assertEquals(listOf("jarvis:480@24000", "engine:one", "engine:three"), heard)
    }

    @Test fun `with no notice cached the phone engine says the notice itself, so the switch is never silent`() = runTest {
        lines().say("Pausing")
        assertEquals(listOf("engine:${JarvisVoiceLines.NOTICE}", "engine:Pausing"), heard)
    }

    @Test fun `a re-render replaces the clip, so a persona switch is picked up`() = runTest {
        val voice = lines()
        voice.remember("Pausing", 24_000, ByteArray(1))
        voice.remember("Pausing", 22_050, ByteArray(3))
        assertEquals(22_050 to 3, voice.clip("Pausing")?.let { it.first to it.second.size })
        assertNull(voice.clip("Playing"))
    }

    @Test fun `case and surrounding space do not make a second clip`() = runTest {
        val voice = lines()
        voice.remember("Pausing ", 24_000, ByteArray(4))
        assertEquals(4, voice.clip("pausing")?.second?.size)
    }

    @Test fun `a missing or day-old clip is stale, so the drive start renders only those`() = runTest {
        val voice = lines()
        assertTrue(voice.stale("Pausing", maxAgeMs = 1_000))
        voice.remember("Pausing", 24_000, ByteArray(2))
        assertFalse(voice.stale("Pausing", maxAgeMs = 60_000))
        assertTrue(voice.stale("Pausing", maxAgeMs = 1_000, nowMs = System.currentTimeMillis() + 5_000))
    }

    @Test fun `a phone with no room keeps the drive going and the notice armed`() = runTest {
        val voice = JarvisVoiceLines(tmp.newFile("full"), { _, _ -> true }, { heard += "engine:$it" })
        assertFalse(voice.remember(JarvisVoiceLines.NOTICE, 24_000, ByteArray(4)))
        voice.say("Pausing")
        assertEquals(listOf("engine:${JarvisVoiceLines.NOTICE}", "engine:Pausing"), heard)
    }

    @Test fun `a clip with a corrupt rate is no clip`() = runTest {
        val voice = lines()
        voice.remember("Pausing", 0, ByteArray(4))
        assertNull(voice.clip("Pausing"))
    }

    @Test fun `a second link in the same drive does not say the notice again`() = runTest {
        // 2026-11-12: the car link re-formed several times in one drive and each new link made
        // its own lines, so "I can't reach the server" came back with every one.
        val drive = java.util.concurrent.atomic.AtomicBoolean(false)
        val link = { JarvisVoiceLines(tmp.root, { _, _ -> true }, { heard += "engine:$it" }, announced = drive) }
        link().say("one")
        link().say("two")
        assertEquals(listOf("engine:${JarvisVoiceLines.NOTICE}", "engine:one", "engine:two"), heard)
    }

    @Test fun `while the house answers, a line it was late with is said with no notice, and the notice stays armed`() = runTest {
        // The same drive: home=true in every connect check, yet a render 2.5 s late sent the
        // line to the phone's voice behind "I can't reach the server".
        var up = true
        val voice = JarvisVoiceLines(tmp.root, { _, _ -> true }, { heard += "engine:$it" }, houseUp = { up })
        voice.say("The console isn't loading")
        assertEquals(listOf("engine:The console isn't loading"), heard)
        up = false
        voice.say("Pausing")
        assertEquals("engine:${JarvisVoiceLines.NOTICE}", heard[1])
    }

    @Test fun `every line the connect check can say is kept as a clip`() {
        for (home in listOf(true, false)) for (console in listOf(true, false)) for (grant in listOf(true, false)) {
            ai.jarvis.app.car.service.CarConnectCheck.line(home, console, grant)?.let { assertTrue(it, it in JarvisVoiceLines.FIXED) }
        }
        assertTrue(HermesCarJarvis.CONFIRM_ON_PHONE in JarvisVoiceLines.FIXED)
    }

    @Test fun `every fixed line the car says is one the drive start keeps`() {
        listOf(CarTurn.OFFLINE, "pause", "play pause", "Sorry, I did not catch that", "Yes or no?").forEach {
            assertTrue(it, it in JarvisVoiceLines.FIXED)
        }
    }

    @Test fun `clips are kept per voice, so a persona switch never plays the old voice's line`() = runTest {
        // T-0807: keyed by text alone, Ada's drive played JARVIS's "Pausing" for up to a day.
        var persona = "jarvis"
        val voice = JarvisVoiceLines(
            dir = tmp.root,
            play = { pcm, rate -> heard += "jarvis:${pcm.size}@$rate"; true },
            engine = { text -> heard += "engine:$text" },
            voice = { persona },
        )
        voice.remember("Pausing", 24_000, ByteArray(7))
        persona = "aura"
        assertNull(voice.clip("Pausing"))
        assertTrue(voice.stale("Pausing", Long.MAX_VALUE))
        voice.remember("Pausing", 24_000, ByteArray(9))
        assertEquals(9, voice.clip("Pausing")?.second?.size)
        persona = "jarvis"
        assertEquals(7, voice.clip("Pausing")?.second?.size)
    }
}
