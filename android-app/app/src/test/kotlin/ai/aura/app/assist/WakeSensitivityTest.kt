package ai.aura.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The per-phone wake sensitivity (T-0316): three levels, NORMAL is what the phone always did. */
class WakeSensitivityTest {

    @Test
    fun `each level maps to its threshold and run length`() {
        assertEquals(0.65f, WakeSensitivity.STRICT.threshold)
        assertEquals(3, WakeSensitivity.STRICT.frames)
        assertEquals(0.5f, WakeSensitivity.NORMAL.threshold)
        assertEquals(2, WakeSensitivity.NORMAL.frames)
        assertEquals(0.4f, WakeSensitivity.EAGER.threshold)
        assertEquals(2, WakeSensitivity.EAGER.frames)
    }

    @Test
    fun `normal is exactly the scorer's defaults`() {
        assertEquals(WakeScore.DEFAULT_THRESHOLD, WakeSensitivity.NORMAL.threshold)
        assertEquals(WakeScore.DEFAULT_FRAMES, WakeSensitivity.NORMAL.frames)
    }

    @Test
    fun `an unknown or missing stored value is normal`() {
        assertEquals(WakeSensitivity.NORMAL, WakeSensitivity.fromStored(null))
        assertEquals(WakeSensitivity.NORMAL, WakeSensitivity.fromStored(""))
        assertEquals(WakeSensitivity.NORMAL, WakeSensitivity.fromStored("PARANOID"))
        assertEquals(WakeSensitivity.NORMAL, WakeSensitivity.fromStored("strict"))
        WakeSensitivity.entries.forEach { assertEquals(it, WakeSensitivity.fromStored(it.name)) }
    }

    @Test
    fun `strict asks for more than normal, which asks for more than eager`() {
        assertTrue(WakeSensitivity.STRICT.threshold > WakeSensitivity.NORMAL.threshold)
        assertTrue(WakeSensitivity.NORMAL.threshold > WakeSensitivity.EAGER.threshold)
        assertTrue(WakeSensitivity.STRICT.frames >= WakeSensitivity.NORMAL.frames)
        assertTrue(WakeSensitivity.NORMAL.frames >= WakeSensitivity.EAGER.frames)
    }

    // T-1179: music from the car's speakers set off a wake nobody asked for.
    @Test
    fun `while car music plays one weak hit or a short run does not fire`() {
        fun run(music: Boolean, score: Float, frames: Int): Boolean {
            val s = WakeSensitivity.NORMAL.scorer(music)
            return (1..frames).any { s.onScore(1_000L + it * 80L, score) }
        }
        // Two frames at 0.55 fire in silence (normal's own rule), not over music.
        assertTrue(run(music = false, score = 0.55f, frames = 2))
        assertFalse(run(music = true, score = 0.55f, frames = 2))
        // A long run at 0.55 is still under the line the music raised.
        assertFalse(run(music = true, score = 0.55f, frames = 10))
        // A strong, sustained hit still wakes it: saying the word over music is the point.
        assertTrue(run(music = true, score = 0.9f, frames = 2 + WakeSensitivity.MUSIC_EXTRA_FRAMES))
        assertFalse(run(music = true, score = 0.9f, frames = 1 + WakeSensitivity.MUSIC_EXTRA_FRAMES))
    }

    @Test
    fun `a pack's head needs the longer run over music too, at its own threshold`() {
        val word = ai.aura.app.ui.theme.JarvisThemes.ALL.first { it.wakeThreshold > 0f }
        val s = WakeSensitivity.NORMAL.scorerFor(word.id, musicPlaying = true)
        val hits = (1..4).map { s.onScore(1_000L + it * 80L, 1f) }
        assertEquals(listOf(false, false, false, true), hits)
    }

    @Test
    fun `the scorer a level builds fires where that level says`() {
        // A 0.55 run: over normal's line, under strict's.
        fun fires(level: WakeSensitivity, score: Float): Boolean {
            val s = level.scorer()
            return (1..5).any { s.onScore(1_000L + it * 80L, score) }
        }
        assertTrue(fires(WakeSensitivity.NORMAL, 0.55f))
        assertFalse(fires(WakeSensitivity.STRICT, 0.55f))
        assertFalse(fires(WakeSensitivity.NORMAL, 0.45f))
        assertTrue(fires(WakeSensitivity.EAGER, 0.45f))
    }
}
