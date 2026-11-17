package ai.jarvis.app.ui.v2

import ai.jarvis.app.a11y.ComposeAudit
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisThemes
import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.ui.theme.PersonaAccent
import android.graphics.Bitmap
import android.graphics.Color
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.dp
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * T-0949: under a pack whose ground is a starfield (AURA) a screen standing on the v2 [Stage]
 * draws her space, the void, the pack's nebulae and the still sky the island draws
 * (`IslandSpace`), over the whole window, and a [Projection] is the pack's panel glass over it;
 * under JARVIS the stage is the radial it was and a projection has no glass.
 *
 * Does not cover the exact picture (the goldens: `phone-settings`, `phone-system-check`,
 * `v2-kit` and their `-jarvis` twins) or a pack switch on a screen already up.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w411dp-h1600dp-xhdpi")
class StageSpaceTest {

    @get:Rule
    val compose = createAndroidComposeRule<ComponentActivity>()

    @After
    fun back() = wear(JarvisThemes.DEFAULT_ID)

    // In a snapshot: a bare write reaches Compose's global observer and later Compose tests
    // never get idle (IslandRestGoldenTest).
    private fun wear(pack: String) = Snapshot.withMutableSnapshot { PersonaAccent.set(compose.activity, pack) }

    private fun drawn(pack: JarvisThemes.Theme, content: @Composable () -> Unit): Bitmap {
        wear(pack.id)
        compose.setContent(content)
        compose.waitForIdle()
        return ComposeAudit.drawn(compose.activity.findViewById<ViewGroup>(android.R.id.content))
    }

    private fun lum(c: Int) = (299 * Color.red(c) + 587 * Color.green(c) + 114 * Color.blue(c)) / 1000

    private fun void(pack: JarvisThemes.Theme) = checkNotNull(ActiveTheme.parse(pack.colors["bg.void"]))

    /** The bright pixels of rows [from] until [to]: a star is far lighter than a void or a nebula. */
    private fun stars(bmp: Bitmap, pack: JarvisThemes.Theme, from: Int = 0, to: Int = bmp.height): Int {
        val row = IntArray(bmp.width)
        var n = 0
        for (y in from until to) {
            bmp.getPixels(row, 0, bmp.width, 0, y, bmp.width, 1)
            n += row.count { lum(it) > lum(void(pack)) + 100 }
        }
        return n
    }

    @Test
    fun `under AURA a stage is her space from top to bottom`() {
        val bmp = drawn(JarvisThemes.AURA) { Stage {} }
        val glow = checkNotNull(ActiveTheme.parse(JarvisThemes.AURA.colors["bg.nebula"]))
        assertNotEquals("the radial's lit core is gone", glow, bmp.getPixel(bmp.width / 2, (bmp.height * 0.44f).toInt()))
        assertTrue("a field of stars", stars(bmp, JarvisThemes.AURA) > 100)
        // The island's sky is one 480 dp square round the centre: a 1600 dp window needs more of it.
        assertTrue("stars at the top of a tall window", stars(bmp, JarvisThemes.AURA, to = bmp.height / 8) > 5)
        assertTrue("stars at the bottom of a tall window", stars(bmp, JarvisThemes.AURA, from = bmp.height * 7 / 8) > 5)
        val shades = HashSet<Int>()
        for (y in 0 until bmp.height step 4) for (x in 0 until bmp.width step 4) shades += bmp.getPixel(x, y)
        assertTrue("nebulae wash the void, not one flat fill (${shades.size} shades)", shades.size > 50)
    }

    @Test
    fun `under JARVIS a stage is the radial it was, with no star`() {
        val bmp = drawn(JarvisThemes.JARVIS) { Stage {} }
        assertEquals("no star", 0, stars(bmp, JarvisThemes.JARVIS))
        assertNotEquals("lit from the core", void(JarvisThemes.JARVIS), bmp.getPixel(bmp.width / 2, (bmp.height * 0.44f).toInt()))
        // A radial about the middle is the same left and right (to a rounding step); a sky is not.
        for (y in 0 until bmp.height step 16) for (x in 0 until bmp.width / 2 step 16) {
            assertEquals("mirrored at $x,$y", lum(bmp.getPixel(x, y)).toFloat(), lum(bmp.getPixel(bmp.width - 1 - x, y)).toFloat(), 2f)
        }
    }

    /** Of the columns across a projection's top, the share lighter just inside it than just outside. */
    private fun litInside(pack: JarvisThemes.Theme): Float {
        val bmp = drawn(pack) {
            Stage {
                Column(Modifier.fillMaxWidth().padding(JarvisTokens.Space.WIDE.dp)) {
                    Projection(Modifier.fillMaxWidth().testTag("p")) { Body("The lights are on.") }
                }
            }
        }
        val box = compose.onNodeWithTag("p").fetchSemanticsNode().boundsInRoot
        val step = JarvisTokens.Space.STEP
        // From past the bracket to the right corner, a few pixels either side of the top edge.
        val columns = (box.left.toInt() + bmp.width / 4) until (box.right.toInt() - step)
        val lit = columns.count { x -> lum(bmp.getPixel(x, box.top.toInt() + step)) > lum(bmp.getPixel(x, box.top.toInt() - step)) }
        return lit.toFloat() / columns.count()
    }

    @Test
    fun `under AURA a projection is the pack's panel glass over her space`() {
        val share = litInside(JarvisThemes.AURA)
        assertTrue("the panel fill lights the box from its top edge ($share of the columns)", share > 0.9f)
    }

    @Test
    fun `under JARVIS a projection has no glass`() {
        val share = litInside(JarvisThemes.JARVIS)
        assertTrue("the backing reaches past the box, nothing starts at its edge ($share of the columns)", share < 0.5f)
    }

    // The accessibility gate samples the pixels at a text's corners (ComposeAudit): a star at
    // full strength there read 1.13:1 under "FIND_FREE_TIME" at a panel's edge, where the old
    // round backing had faded to nothing.
    @Test
    fun `under AURA no star is as bright as a word anywhere inside a projection`() {
        val bmp = drawn(JarvisThemes.AURA) {
            Stage {
                Column(Modifier.fillMaxWidth().padding(JarvisTokens.Space.WIDE.dp)) {
                    Projection(Modifier.fillMaxWidth().height(JarvisTokens.Size.SHEET_MIN.dp * 3).testTag("p")) {}
                }
            }
        }
        val box = compose.onNodeWithTag("p").fetchSemanticsNode().boundsInRoot
        val step = JarvisTokens.Space.STEP
        // Clear of the bracket (the left quarter) and of the hairline edge.
        val left = box.left.toInt() + bmp.width / 4
        val inside = Bitmap.createBitmap(bmp, left, box.top.toInt() + step, box.right.toInt() - step - left, box.height.toInt() - 2 * step)
        assertTrue("the stage round it has stars", stars(bmp, JarvisThemes.AURA) > 100)
        assertEquals("stars at full strength inside the glass", 0, stars(inside, JarvisThemes.AURA))
    }

    @Test
    fun `a pack's rgba colours are read in order and anything else is not a colour`() {
        assertEquals(
            listOf(Color.argb(20, 183, 156, 255), Color.argb(0, 10, 5, 24)),
            ActiveTheme.rgbas("rgba(183,156,255,.08) rgba(10,5,24,0)"),
        )
        assertEquals(listOf(Color.argb(36, 236, 220, 255)), ActiveTheme.rgbas("rgba(236, 220, 255, 0.14)"))
        assertEquals(emptyList<Int>(), ActiveTheme.rgbas("#05030D"))
        assertEquals(emptyList<Int>(), ActiveTheme.rgbas("rgba(1,2,3) rgba(999,0,0,1) linear-gradient(red, blue)"))
        assertEquals(emptyList<Int>(), ActiveTheme.rgbas(null))
    }
}
