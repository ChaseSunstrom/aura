package ai.jarvis.app.assist.island

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisThemes
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.RectF
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * T-0898: under a pack whose ground is a starfield (AURA) the island's ground is her space,
 * the void, the pack's nebulae and still stars, as on the console (the owner, 2026-11-09:
 * "have it be the nice glass effect with the space stuff from the core screen"); under JARVIS
 * there is no space and the island keeps its flat fill.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34])
class IslandGlassTest {

    private val w = 720
    private val h = 280

    private fun pixels(theme: JarvisThemes.Theme): IntArray {
        val space = checkNotNull(IslandSpace.of(theme, density = 2f)) { "${theme.id} has no space" }
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        space.draw(Canvas(bmp), RectF(0f, 0f, w.toFloat(), h.toFloat()), radius = 40f)
        return IntArray(w * h).also { bmp.getPixels(it, 0, w, 0, 0, w, h) }
    }

    private fun lum(c: Int) = (299 * ((c shr 16) and 0xFF) + 587 * ((c shr 8) and 0xFF) + 114 * (c and 0xFF)) / 1000

    @Test
    fun `under AURA the island draws her space behind its words`() {
        val px = pixels(JarvisThemes.AURA)
        val void = checkNotNull(ActiveTheme.parse(JarvisThemes.AURA.colors["bg.void"]))
        assertEquals("opaque: the app behind never shows through", 0xFF, px[w * (h / 2) + w / 2] ushr 24)
        assertEquals("the corners are round, not painted", 0, px[0] ushr 24)
        val shades = px.filter { it ushr 24 == 0xFF }.toSet().size
        assertTrue("nebulae wash the void, not one flat fill ($shades shades)", shades > 50)
        val stars = px.count { lum(it) > lum(void) + 100 }
        assertTrue("a field of stars ($stars bright pixels)", stars in 20..4000)
    }

    @Test
    fun `under JARVIS the island has no space and keeps its fill`() {
        assertNull(IslandSpace.of(JarvisThemes.JARVIS, density = 2f))
    }
}
