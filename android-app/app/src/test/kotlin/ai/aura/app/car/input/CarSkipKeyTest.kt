package ai.aura.app.car.input

import dev.headway.protocol.channel.InputKeyCodes
import dev.headway.voice.MediaAction
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The wheel's skip keys stop Jarvis while she talks or listens, and skip the track otherwise (the owner, 2026-11-10). */
class CarSkipKeyTest {
    @Test
    fun `a skip while Jarvis talks or listens stops her and leaves the music`() {
        var asked = 0
        val jarvis = { _: Boolean -> asked++; true }
        assertNull(CarInputStream.transport(InputKeyCodes.MEDIA_NEXT, jarvis))
        assertNull(CarInputStream.transport(InputKeyCodes.MEDIA_PREVIOUS, jarvis))
        assertEquals(2, asked)
    }

    @Test
    fun `a skip while she is quiet skips the track`() {
        val quiet = { _: Boolean -> false }
        assertEquals(MediaAction.NEXT, CarInputStream.transport(InputKeyCodes.MEDIA_NEXT, quiet))
        assertEquals(MediaAction.PREVIOUS, CarInputStream.transport(InputKeyCodes.MEDIA_PREVIOUS, quiet))
    }

    @Test
    fun `she is told which skip it was, so previous can repeat her and next cannot`() {
        val seen = mutableListOf<Boolean>()
        val repeats = { previous: Boolean -> seen += previous; previous }
        assertEquals(MediaAction.NEXT, CarInputStream.transport(InputKeyCodes.MEDIA_NEXT, repeats))
        assertNull(CarInputStream.transport(InputKeyCodes.MEDIA_PREVIOUS, repeats))
        assertEquals(listOf(false, true), seen)
    }

    @Test
    fun `play and pause always go to the music and never ask her`() {
        val never: (Boolean) -> Boolean = { error("play and pause are not offered to Jarvis") }
        assertEquals(MediaAction.PLAY_PAUSE, CarInputStream.transport(InputKeyCodes.MEDIA_PLAY_PAUSE, never))
        assertEquals(MediaAction.PLAY, CarInputStream.transport(InputKeyCodes.MEDIA_PLAY, never))
        assertEquals(MediaAction.PAUSE, CarInputStream.transport(InputKeyCodes.MEDIA_PAUSE, never))
        assertNull(CarInputStream.transport(InputKeyCodes.SEARCH, never))
    }
}
