package ai.jarvis.app.a11y

import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.View
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.text.TextLayoutResult
import ai.jarvis.app.ui.JarvisUi
import kotlin.math.max
import kotlin.math.min

/**
 * The Compose half of the phone's accessibility gate, shared by [AccessibilityTest] (the
 * theme's controls and the pure composables) and [ScreenAccessibilityTest] (the real
 * Activities the screenshot suite draws), so there is one bar and one place it is written.
 *
 * Every clickable is 48 dp and named; every text clears AA against the pixels at the corners
 * of its own bounds in the rendered image. It does not cover: a name that is present but
 * wrong (a screen reader reads it, nobody here listens), focus order, or a device's text
 * shaping and ripple. Anything outside the drawn image is counted, not skipped, so a screen
 * taller than its window fails loudly instead of passing on pixels it never sampled.
 */
internal object ComposeAudit {

    /** GLASS_HUD §12: touch targets are 48 dp. The gate's bar, not a design token. */
    const val MIN_TOUCH_DP = 48
    const val AA_TEXT = 4.5f
    const val AA_LARGE = 3f

    /** WCAG's "large text": 18 pt (24 px) or 14 pt bold (18.66 px). */
    const val LARGE_TEXT_PX = 24f
    const val LARGE_BOLD_TEXT_PX = 18.66f

    /** A view drawn onto a bitmap over the room colour: the picture the contrast checks sample. */
    fun drawn(view: View): Bitmap {
        val bitmap = Bitmap.createBitmap(view.width.coerceAtLeast(1), view.height.coerceAtLeast(1), Bitmap.Config.ARGB_8888)
        Canvas(bitmap).also { canvas ->
            canvas.drawColor(JarvisUi.BG)
            view.draw(canvas)
        }
        return bitmap
    }

    /** How many different colours a bitmap holds, up to a small cap: a blank draw is one. */
    fun distinctColours(bitmap: Bitmap, cap: Int = 4): Int {
        val seen = HashSet<Int>()
        for (y in 0 until bitmap.height step 8) for (x in 0 until bitmap.width step 8) {
            if (seen.add(bitmap.getPixel(x, y)) && seen.size >= cap) return seen.size
        }
        return seen.size
    }

    private fun walk(node: SemanticsNode, visit: (SemanticsNode) -> Unit) {
        visit(node)
        node.children.forEach { walk(it, visit) }
    }

    private fun hasNamedChild(node: SemanticsNode): Boolean = node.children.any { child ->
        !child.config.getOrNull(SemanticsProperties.Text).isNullOrEmpty() ||
            !child.config.getOrNull(SemanticsProperties.ContentDescription).isNullOrEmpty() ||
            hasNamedChild(child)
    }

    /** WCAG 2.x relative-luminance contrast, the same formula the web gate uses. */
    fun contrast(a: Color, b: Color): Float {
        val la = a.luminance()
        val lb = b.luminance()
        return (max(la, lb) + 0.05f) / (min(la, lb) + 0.05f)
    }

    private fun hex(c: Color) = "#%06x".format(c.toArgb() and 0xffffff)

    /**
     * What [roots] get wrong, one line each, every line starting with [name]. Empty is a pass.
     * A screen can hold several compose roots (a view screen with a ComposeView in it): each is
     * walked, and its text is sampled where it sits in the window ([pixels] is the window).
     */
    fun problems(name: String, roots: List<SemanticsNode>, pixels: Bitmap, partial: Partial? = null): List<String> {
        val problems = mutableListOf<String>()
        val counts = IntArray(3)
        for (root in roots) problems += problems(name, root, pixels, counts, partial)
        if (roots.isEmpty() || roots.all { it.boundsInRoot.width <= 0 || it.boundsInRoot.height <= 0 }) problems += "$name rendered nothing"
        if (counts[0] + counts[1] == 0) problems += "$name has no clickable and no text: nothing was checked"
        return problems
    }

    /**
     * One screen audited a window at a time (T-1275): a list too tall for one bitmap (the policy
     * screen, ~25000 dp) is scrolled by the test and [problems] called once per window. A node
     * not wholly inside a window reports clipped (0 x 0) touch bounds, which is the window's
     * limit, not the node's, so it is left to a window that holds it and recorded in [skipped].
     * [unaudited] is what no window ever held: those fail, so no row is silently skipped.
     */
    class Partial {
        val audited = HashSet<Int>()
        val skipped = HashMap<Int, String>()

        fun unaudited(name: String): List<String> =
            skipped.filterKeys { it !in audited }.values.map { "$name: \"$it\" was never wholly inside a scroll window, so never audited" }
    }

    private fun problems(name: String, root: SemanticsNode, pixels: Bitmap, counts: IntArray, partial: Partial?): List<String> {
        val problems = mutableListOf<String>()
        val density = root.layoutInfo.density.density
        val minTouchPx = MIN_TOUCH_DP * density
        walk(root) { node ->
            val text = node.config.getOrNull(SemanticsProperties.Text)?.joinToString { it.text }
            val description = node.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString()
            val label = text ?: description ?: node.config.getOrNull(SemanticsProperties.Role)?.toString() ?: "node ${node.id}"
            val checked = node.config.contains(SemanticsActions.OnClick) || text != null
            if (partial != null && checked) {
                // Two px of slack: the contrast sample reads one pixel below the text box.
                if (node.positionInRoot.y < 0 || node.positionInRoot.y + node.size.height + 2 > root.size.height) {
                    counts[2]++
                    partial.skipped[node.id] = label
                    return@walk
                }
                partial.audited += node.id
            }
            if (node.config.contains(SemanticsActions.OnClick)) {
                counts[0]++
                // The touch bounds, not the drawn size: Material's 40 dp button
                // is a 48 dp target through minimumInteractiveComponentSize.
                val w = node.touchBoundsInRoot.width
                val h = node.touchBoundsInRoot.height
                if (w < minTouchPx || h < minTouchPx) {
                    problems += "$name: \"$label\" is ${w / density} x ${h / density} dp, under $MIN_TOUCH_DP dp " +
                        "(bounds ${node.boundsInWindow}, layout ${node.size} at ${node.positionInRoot}, root ${root.size})"
                }
                if (text.isNullOrBlank() && description.isNullOrBlank() && !hasNamedChild(node)) {
                    problems += "$name: clickable node ${node.id} has no text or content description"
                }
            }
            if (text != null && node.config.contains(SemanticsActions.GetTextLayoutResult)) {
                val layouts = mutableListOf<TextLayoutResult>()
                node.config[SemanticsActions.GetTextLayoutResult].action?.invoke(layouts)
                val layout = layouts.firstOrNull() ?: return@walk
                val colour = layout.layoutInput.style.color.takeIf { it != Color.Unspecified } ?: return@walk
                // WCAG's 18 pt / 14 pt bold are CSS px, one per dp: the text's size in dp, with
                // the user's font scale. (Run 3 found this scaled by the screen density instead,
                // which made every 8 sp text "large" at xxhdpi and held it to 3:1 only.)
                val fontPx = layout.layoutInput.style.fontSize.value * layout.layoutInput.density.fontScale
                val large = fontPx >= LARGE_TEXT_PX || (fontPx >= LARGE_BOLD_TEXT_PX && (layout.layoutInput.style.fontWeight?.weight ?: 400) >= 700)
                val required = if (large) AA_LARGE else AA_TEXT
                val b = node.boundsInWindow
                if (b.right >= pixels.width || b.bottom >= pixels.height) {
                    // Off the drawn image (a scrolled-away row): nothing to sample, so say so.
                    problems += "$name: \"$text\" lies outside the drawn ${pixels.width} x ${pixels.height} image, contrast not sampled"
                    return@walk
                }
                counts[1]++
                // The corners of the text's own box, one pixel OUTSIDE it, are backdrop. Inside,
                // a glyph can reach the corner (a "D" stem, a descender), and on a small-density
                // window (the 25000 dp policy list is drawn at ldpi) run 8 sampled the text's
                // own pixels as "backdrop" and reported 1.4:1 over white-on-dark chips.
                val xs = listOf(b.left - 1, b.right + 1).map { it.toInt().coerceIn(0, pixels.width - 1) }
                val ys = listOf(b.top - 1, b.bottom + 1).map { it.toInt().coerceIn(0, pixels.height - 1) }
                for (x in xs) for (y in ys) {
                    val behind = Color(pixels.getPixel(x, y))
                    val ratio = contrast(colour.compositeOver(behind), behind)
                    if (ratio < required) {
                        problems += "$name: \"$text\" ${hex(colour)} over ${hex(behind)} at ($x,$y) is %.2f:1, needs $required".format(ratio)
                    }
                }
            }
        }
        return problems
    }
}
