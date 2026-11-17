package ai.jarvis.app.ui

import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * The phone's home is a web view in an edge-to-edge window (T-0887): the owner saw the console
 * cut off. A web view's page ignores the view's padding, so the bars' insets must be its
 * margins. Does not cover how the page lays itself out inside them (the console's e2e).
 */
@RunWith(RobolectricTestRunner::class)
class ConsoleInsetsTest {

    private fun page(): View = page(View(ApplicationProvider.getApplicationContext()))

    private fun <V : View> page(view: V): V {
        val parent = FrameLayout(view.context)
        parent.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        ConsolePage.fit(view)
        return view
    }

    private fun apply(view: View, type: Int, insets: Insets) =
        ViewCompat.dispatchApplyWindowInsets(view, WindowInsetsCompat.Builder().setInsets(type, insets).build())

    private fun margins(view: View) = (view.layoutParams as ViewGroup.MarginLayoutParams).let {
        listOf(it.leftMargin, it.topMargin, it.rightMargin, it.bottomMargin)
    }

    private fun padding(view: View) = listOf(view.paddingLeft, view.paddingTop, view.paddingRight, view.paddingBottom)

    @Test
    fun `the bars are margins round the page, not padding it would draw under`() {
        val view = page()
        apply(view, WindowInsetsCompat.Type.systemBars(), Insets.of(0, 63, 0, 42))
        assertEquals(listOf(0, 63, 0, 42), margins(view))
        assertEquals(listOf(0, 0, 0, 0), padding(view))
    }

    @Test
    fun `the keyboard keeps the page clear too`() {
        val view = page()
        apply(view, WindowInsetsCompat.Type.ime(), Insets.of(0, 0, 0, 700))
        assertEquals(700, margins(view)[3])
    }

    /**
     * The home hides the bars, so the camera's cutout was the only inset left and its margin was
     * a black band across the top (the owner, 2026-11-10, T-0974). The page draws its ground up
     * to the edge and is told how far down to start its content.
     */
    @Test
    fun `the camera cutout is the page's to keep clear, not a black band above it`() {
        val view = page(android.webkit.WebView(ApplicationProvider.getApplicationContext()))
        val density = view.resources.displayMetrics.density
        apply(view, WindowInsetsCompat.Type.displayCutout(), Insets.of(0, (40 * density).toInt(), 0, 0))
        assertEquals(listOf(0, 0, 0, 0), margins(view))
        val js = shadowOf(view).lastEvaluatedJavascript
        assertTrue(js, js.contains("'--safe-top','40px'"))
        assertTrue(js, js.contains("'--safe-left','0px'"))
    }
}
