package ai.aura.app.assist.island

import android.animation.ValueAnimator
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * What a screen reader says for the island (T-0699): the view carries the description of the
 * state it is in, and no description once hidden. The words themselves are held to
 * `tests/contracts/island.json` by [IslandStateTest]. Does not cover how TalkBack voices them
 * (no device here) or the card's own text, which its views carry.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class IslandSpokenTest {
    @Test
    fun `the view says the state it is in`() {
        val view = IslandView(ApplicationProvider.getApplicationContext())
        for (state in Island.State.entries.filter { it != Island.State.HIDDEN } + Island.State.HIDDEN) {
            view.show(state)
            assertEquals(state.key, state.said(ai.aura.app.ui.theme.ActiveTheme.spokenName).ifEmpty { null }, view.contentDescription?.toString())
        }
    }

    /**
     * Reduced motion (T-0699): with the system's animator scale at 0 no state change leaves the
     * island's spring running. The phone's home is the console in a WebView, which Robolectric
     * cannot run, so its own reduced-motion handling is the web gate's (`prefers-reduced-motion`
     * in jarvis-web); this covers the one native moving surface.
     */
    @Test
    fun `animator scale 0 leaves no spring running in any state`() {
        val scale = ValueAnimator::class.java.getMethod("setDurationScale", Float::class.javaPrimitiveType)
        scale.invoke(null, 0f)
        try {
            val view = IslandView(ApplicationProvider.getApplicationContext())
            for (state in Island.State.entries) {
                view.show(state)
                assertFalse("${state.key} left the island ticking with animations off", view.tickingForTest)
            }
        } finally {
            scale.invoke(null, 1f)
        }
    }
}
