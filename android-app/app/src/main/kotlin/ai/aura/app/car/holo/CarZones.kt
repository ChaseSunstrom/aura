package ai.aura.app.car.holo

import ai.aura.app.ui.theme.JarvisTokens.Size
import ai.aura.app.ui.theme.JarvisTokens.Space
import ai.aura.app.ui.theme.JarvisTokens.Type
import kotlin.math.min

/** A rectangle in surface pixels; touching edges do not count as overlap. */
data class Box(val left: Float, val top: Float, val right: Float, val bottom: Float) {
    val width get() = right - left
    val height get() = bottom - top

    fun contains(b: Box): Boolean =
        b.left >= left - EPS && b.top >= top - EPS && b.right <= right + EPS && b.bottom <= bottom + EPS

    fun intersects(b: Box): Boolean =
        b.left < right - EPS && b.right > left + EPS && b.top < bottom - EPS && b.bottom > top + EPS

    private companion object {
        /** Float rounding on a shared edge is not an overlap. */
        const val EPS = 1e-3f
    }
}

/**
 * The drive's fixed zones (Car.dc.html), laid out in the area the host leaves visible:
 * the next turn top left, the trip top right, what Jarvis says bottom left, the core
 * bottom right and the one card beside it. The zones never move with what is in them,
 * so a driver's glance always lands in the same place.
 *
 * The board is 600 dp tall; a shorter screen scales everything by [scale] except the
 * touch targets, which keep the car's 52 dp. When the host leaves no room for the
 * subtitle beside the card, the card goes above the subtitle instead.
 *
 * Does not cover a visible area under about 400 by 300 dp: zones would collapse to
 * nothing there, and no head unit the car templates allow is that small.
 */
class CarZones private constructor(
    val turn: Box,
    val trip: Box,
    val subtitle: Box,
    val card: Box,
    val core: Box,
    /** Board dp to surface px for the text and spacing: density times [scale]. */
    val unit: Float,
    val scale: Float,
    private val density: Float,
) {
    /** [count] equal buttons along the card's bottom, each at least the car's touch size. */
    fun buttons(count: Int): List<Box> {
        if (count <= 0) return emptyList()
        val pad = Space.SCREEN * unit
        val gap = Space.ROW * unit
        val bottom = card.bottom - Space.SECTION * unit
        val top = bottom - Size.CAR_TOUCH * density
        val w = (card.width - 2 * pad - gap * (count - 1)) / count
        return List(count) { i ->
            val left = card.left + pad + i * (w + gap)
            Box(left, top, left + w, bottom)
        }
    }

    companion object {
        /** The board's height, which every zone size is drawn against. */
        const val DESIGN_HEIGHT_DP = 600f

        /** The board's line height. */
        private const val LH = 1.25f

        /** A subtitle narrower than this share of the card is a column nobody can read. */
        private const val MIN_SUBTITLE_SHARE = 0.6f

        fun layout(visible: Box, density: Float): CarZones {
            val k = min(1f, visible.height / density / DESIGN_HEIGHT_DP)
            val u = density * k
            val margin = Size.CAR_MARGIN * u
            val gap = Size.CAR_CARD_GAP * u
            val pad = Space.SECTION * u

            val coreSize = Size.CAR_CORE * u
            val coreRight = visible.right - Size.CAR_CORE_RIGHT * u
            val coreBottom = visible.bottom - Size.CAR_CORE_BOTTOM * u
            val core = Box(coreRight - coreSize, coreBottom - coreSize, coreRight, coreBottom)

            val left = visible.left + margin
            val colRight = core.left - gap
            val cardW = min(Size.CAR_CARD * u, colRight - left)
            // A caption, two lines of the card's text and a row of targets.
            val cardH = (2 * pad + Type.LABEL * LH * u + 2 * Size.CAR_TEXT_LINE * LH * u + pad) +
                Size.CAR_TOUCH * density
            val subH = 2 * pad + Type.HINT * LH * u + 3 * Size.CAR_TEXT_LINE * LH * u
            val subBottom = visible.bottom - Size.CAR_BOTTOM * u

            val cardBottom = visible.bottom - Size.CAR_MARGIN * u
            var card = Box(colRight - cardW, cardBottom - cardH, colRight, cardBottom)
            var subtitle = Box(left, subBottom - subH, min(left + Size.CAR_SUBTITLE * u, card.left - gap), subBottom)
            if (subtitle.width < MIN_SUBTITLE_SHARE * cardW) {
                subtitle = Box(left, subBottom - subH, min(left + Size.CAR_SUBTITLE * u, colRight), subBottom)
                val bottom = subtitle.top - gap
                card = Box(left, bottom - cardH, left + cardW, bottom)
            }

            val topW = min(Size.CAR_CARD * u, (visible.width - 2 * margin - gap) / 2)
            val top = visible.top + Size.CAR_TOP * u
            val turnH = 2 * pad + (Size.CAR_TEXT_TURN + Type.FIELD + Type.HINT) * LH * u
            val tripH = 2 * pad + (Type.LABEL + Size.CAR_TEXT_ETA + Type.HINT + Size.CAR_TEXT_SPEED) * LH * u +
                Space.ROW * u
            val turn = Box(left, top, left + topW, top + turnH)
            val tripRight = visible.right - margin
            val trip = Box(tripRight - topW, top, tripRight, top + tripH)

            return CarZones(turn, trip, subtitle, card, core, u, k, density)
        }
    }
}
