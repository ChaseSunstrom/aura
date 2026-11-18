/*
 * This file is part of Headway.
 * Copyright (C) 2026 The Headway Authors
 *
 * Headway is free software: you can redistribute it and/or modify it under the
 * terms of the GNU General Public License as published by the Free Software
 * Foundation, either version 3 of the License, or (at your option) any later
 * version.
 *
 * Headway is distributed in the hope that it will be useful, but WITHOUT ANY
 * WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
 * A PARTICULAR PURPOSE. See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along with
 * Headway. If not, see <https://www.gnu.org/licenses/>.
 */

package ai.aura.app.car.dash

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.assist.Phase
import ai.aura.app.car.theme.CarMetrics
import ai.aura.app.car.theme.Headway
import ai.aura.app.glass.ReactorView
import ai.aura.app.ui.theme.JarvisTokens
import android.content.Context
import android.util.TypedValue
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * What the car screen shows the moment the head unit takes the first frame
 * (ADR-0047): Jarvis's reactor igniting, with the wordmark under it, for
 * `motion.dur.enter` and a settle -- the same ignition the console and the phone
 * open with. Headway's travelling-bars wordmark drew here before.
 *
 * Does not wait for anything: it is a moment, not a progress bar. A shell that is
 * still connecting to Jarvis shows it on the Jarvis tab, not here.
 */
class CarStartupView(
    context: Context,
    private val metrics: CarMetrics,
    /** Called once, on the main thread, when the ignition has played. */
    private val onFinished: () -> Unit,
) : FrameLayout(context) {

    private val reactor = ReactorView(context).apply { setPhase(Phase.THINKING, linkUp = true, warming = true) }

    private var finished = false
    private val finishing = Runnable { finish() }
    private val settling = Runnable { reactor.setPhase(Phase.IDLE, linkUp = true) }

    init {
        setBackgroundColor(Headway.GROUND)
        val size = (minOf(metrics.widthPx, metrics.heightPx) * REACTOR_SHARE).toInt()
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        column.addView(reactor, LinearLayout.LayoutParams(size, size))
        column.addView(
            TextView(context).apply {
                text = WORDMARK
                setTextColor(Headway.TEXT)
                setTextSize(TypedValue.COMPLEX_UNIT_PX, metrics.unit * WORDMARK_FRACTION)
                gravity = Gravity.CENTER
            },
            LinearLayout.LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT),
        )
        addView(column, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.CENTER))
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        postDelayed(settling, JarvisTokens.Motion.Dur.ENTER.toLong())
        postDelayed(finishing, (JarvisTokens.Motion.Dur.ENTER + JarvisTokens.Motion.Dur.SLOW).toLong())
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(settling)
        removeCallbacks(finishing)
        super.onDetachedFromWindow()
    }

    private fun finish() {
        if (finished) return
        finished = true
        onFinished()
    }

    companion object {
        val WORDMARK: String get() = ActiveTheme.name

        /** How much of the car screen's short side the reactor takes while it ignites. */
        private const val REACTOR_SHARE = 0.5f
        private const val WORDMARK_FRACTION = 0.40f
    }
}
