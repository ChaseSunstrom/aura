package ai.jarvis.app.ui

import ai.jarvis.app.R
import ai.jarvis.app.ui.theme.JarvisTokens
import android.content.Context
import android.view.animation.AnimationUtils
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The native screens the console opens slide in from the end and out on Back, as the
 * console's side panel does (T-0725), on the design system's motion. Before, the app
 * theme named no window animations and each screen came in with the platform's default.
 */
@RunWith(RobolectricTestRunner::class)
class PanelTransitionTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun `the app theme slides a screen in over the console and back out`() {
        val theme = context.resources.newTheme().apply { applyStyle(R.style.Theme_Jarvis, true) }
        val window = theme.obtainStyledAttributes(intArrayOf(android.R.attr.windowAnimationStyle))
        val style = window.getResourceId(0, 0)
        window.recycle()
        // Ascending ids, as obtainStyledAttributes needs: open enter, open exit, close enter, close exit.
        val attrs = intArrayOf(
            android.R.attr.activityOpenEnterAnimation,
            android.R.attr.activityOpenExitAnimation,
            android.R.attr.activityCloseEnterAnimation,
            android.R.attr.activityCloseExitAnimation,
        )
        val a = context.obtainStyledAttributes(style, attrs)
        val anims = attrs.indices.map { a.getResourceId(it, 0) }
        a.recycle()
        assertEquals(listOf(R.anim.jv_panel_in, R.anim.jv_panel_hold, R.anim.jv_panel_hold, R.anim.jv_panel_out), anims)
    }

    @Test
    fun `the slide takes the design system's appear and exit`() {
        assertEquals(JarvisTokens.Motion.Dur.APPEAR.toLong(), AnimationUtils.loadAnimation(context, R.anim.jv_panel_in).duration)
        assertEquals(JarvisTokens.Motion.Dur.EXIT.toLong(), AnimationUtils.loadAnimation(context, R.anim.jv_panel_out).duration)
        assertEquals(JarvisTokens.Motion.Dur.APPEAR.toLong(), AnimationUtils.loadAnimation(context, R.anim.jv_panel_hold).duration)
    }
}
