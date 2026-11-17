package ai.jarvis.app.car.theme

import ai.jarvis.app.ui.theme.JarvisTokens
import dev.headway.dash.CarTheme
import dev.headway.dash.ThemeAccent
import dev.headway.dash.ThemeBase
import dev.headway.dash.ThemeChoice
import org.junit.Assert.assertEquals
import org.junit.Test

class JarvisCarPaletteTest {
    @Test fun `the default car screen is drawn in Jarvis's own colours`() {
        val p = carPalette(ThemeChoice.DEFAULT)
        assertEquals(JarvisTokens.Color.BG, p.ground)
        assertEquals(JarvisTokens.Color.PANEL, p.surface)
        assertEquals(JarvisTokens.Color.ACCENT, p.accent)
        assertEquals(JarvisTokens.Color.ACCENT_INK, p.onAccent)
        assertEquals(JarvisTokens.Color.TEXT, p.text)
        assertEquals(JarvisTokens.Color.LINE, p.outline)
    }

    @Test fun `a theme the driver chose is still theirs`() {
        val light = ThemeChoice(ThemeBase.LIGHT, ThemeAccent.AMBER)
        assertEquals(CarTheme.palette(light), carPalette(light))
        val midnight = ThemeChoice(ThemeBase.MIDNIGHT, ThemeAccent.HEADWAY)
        assertEquals(CarTheme.palette(midnight), carPalette(midnight))
    }
}
