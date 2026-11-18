package ai.aura.app.assist

import ai.aura.app.car.voice.JarvisVoiceLines
import kotlinx.coroutines.test.runTest
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** The choice order is `tests/contracts/offline-voice.json`, which the Python mirror runs too. */
class OfflineVoiceTest {
    @get:Rule val tmp = TemporaryFolder()

    private val contract = JSONObject(File("../../tests/contracts/offline-voice.json").readText())

    @Test fun `each pack's order names only speakers there are, and together all of them`() {
        val orders = contract.getJSONObject("speech").getJSONObject("orders")
        val named = orders.keys().asSequence().flatMap { k ->
            orders.getJSONArray(k).let { o -> List(o.length()) { o.getString(it) } }
        }.toSet()
        assertEquals(OfflineVoice.Speaker.entries.map { it.name.lowercase() }.toSet(), named)
        assertEquals(setOf(OfflineVoice.BUNDLED_VOICE, OfflineVoice.CLIPS_ONLY), orders.keys().asSequence().toSet())
    }

    @Test fun `every case in the table`() {
        val cases = contract.getJSONObject("speech").getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            assertEquals(
                c.getString("name"),
                c.getString("expect"),
                OfflineVoice.choose(
                    c.getBoolean("hasClip"), c.getBoolean("bundledReady"), c.getString("fallback"),
                    c.optBoolean("ownVoiceRealtime", false),
                )
                    .name.lowercase(),
            )
        }
    }

    private val heard = mutableListOf<String>()

    private fun lines(
        bundledWorks: Boolean,
        fallback: String = OfflineVoice.BUNDLED_VOICE,
        own: Boolean? = null,
    ) = JarvisVoiceLines(
        dir = tmp.root,
        play = { pcm, rate -> heard += "clip:${pcm.size}@$rate"; true },
        engine = { heard += "system:$it" },
        bundled = { heard += "bundled:$it"; bundledWorks },
        fallback = { fallback },
        ownRealtime = { own != null },
        own = { heard += "own:$it"; own == true },
    )

    @Test fun `clips only, her own voice on the phone says what no clip has, with no notice`() = runTest {
        val voice = lines(true, OfflineVoice.CLIPS_ONLY, own = true)
        voice.remember(JarvisVoiceLines.WORKING_OFFLINE, 24_000, ByteArray(6))
        voice.say("Turning left")
        assertEquals(listOf("own:Turning left"), heard)
    }

    @Test fun `clips only, her voice failing on the phone falls to the notice, never another voice`() = runTest {
        val voice = lines(true, OfflineVoice.CLIPS_ONLY, own = false)
        voice.remember(JarvisVoiceLines.WORKING_OFFLINE, 24_000, ByteArray(6))
        voice.say("Turning left")
        voice.say("Pausing")
        assertEquals(listOf("own:Turning left", "clip:6@24000", "own:Pausing"), heard)
    }

    @Test fun `clips only, a line with no clip is unsaid and her notice says why, once`() = runTest {
        val voice = lines(true, OfflineVoice.CLIPS_ONLY)
        voice.remember(JarvisVoiceLines.WORKING_OFFLINE, 24_000, ByteArray(6))
        voice.say("Navigating to Home Depot")
        voice.say("Turning left")
        assertEquals(listOf("clip:6@24000"), heard)
    }

    @Test fun `clips only, with no notice clip either, nothing else speaks`() = runTest {
        lines(false, OfflineVoice.CLIPS_ONLY).say("Pausing")
        assertEquals(emptyList<String>(), heard)
    }

    @Test fun `a free-form line is spoken by the bundled voice and never the system engine`() = runTest {
        lines(true).say("Navigating to Home Depot")
        assertEquals(listOf("bundled:Navigating to Home Depot"), heard)
    }

    @Test fun `a cached clip beats the bundled voice`() = runTest {
        val voice = lines(true)
        voice.remember("Pausing", 24_000, ByteArray(8))
        voice.say("Pausing")
        assertEquals(listOf("clip:8@24000"), heard)
    }

    @Test fun `with the bundled voice failing the system engine speaks after the notice`() = runTest {
        lines(false).say("Pausing")
        assertEquals(
            listOf("bundled:Pausing", "system:${JarvisVoiceLines.NOTICE}", "system:Pausing"),
            heard,
        )
    }
}
