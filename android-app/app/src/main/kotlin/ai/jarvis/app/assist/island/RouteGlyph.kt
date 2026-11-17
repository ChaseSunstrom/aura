package ai.jarvis.app.assist.island

import ai.jarvis.app.ui.theme.JarvisTokens
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.view.View

/**
 * The route activity's glyph (T-0695): a heading arrow in the accent, in the slot where the turn's
 * pill has the core, so a route and a conversation are told apart at a glance. Does not turn with
 * the heading: the pill says where to, not which way.
 */
class RouteGlyph(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = JarvisTokens.Color.ACCENT
        style = Paint.Style.FILL
    }
    private val arrow = Path()

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        // A navigation arrow: a point at the top, two wings, a notch at the tail.
        val s = minOf(w, h) * SPAN
        val cx = w / 2f
        val cy = h / 2f
        arrow.reset()
        arrow.moveTo(cx, cy - s / 2)
        arrow.lineTo(cx + s * WING, cy + s / 2)
        arrow.lineTo(cx, cy + s * NOTCH)
        arrow.lineTo(cx - s * WING, cy + s / 2)
        arrow.close()
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawPath(arrow, paint)
    }

    private companion object {
        /** The arrow's share of its slot, its half-width and where the tail's notch sits. */
        const val SPAN = 0.7f
        const val WING = 0.38f
        const val NOTCH = 0.22f
    }
}
