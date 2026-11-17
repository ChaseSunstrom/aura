package ai.jarvis.app.car.holo

import ai.jarvis.app.ui.theme.JarvisThemes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** What each pack's `map` paints on the car (T-0871): its own reading, not a substring check. */
class MapLookTest {
    @Test
    fun `JARVIS drives the hologram`() {
        assertSame(MapLook.HOLOGRAM, MapLook.of(JarvisThemes.JARVIS))
    }

    @Test
    fun `AURA drives through space in her pack's colours, one look per pack`() {
        val look = MapLook.of(JarvisThemes.AURA)
        assertEquals(MapLook.SPACE_DRIVE, look.style)
        assertEquals(320, look.stars)
        assertTrue(look.band)
        // The comet route's amber, its @35% lanes read as the colour alone.
        assertEquals(0xFFFFC36B.toInt(), look.inks[Ink.WARN])
        assertEquals(0xFFF2EAFF.toInt(), look.inks[Ink.BRIGHT])
        assertEquals(3, look.sky.size)
        assertNotNull(look.ground)
        assertSame(look, MapLook.of(JarvisThemes.AURA))
    }
}
