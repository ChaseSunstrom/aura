package ai.jarvis.app.assist.island

import ai.jarvis.app.assist.island.Island.Action
import ai.jarvis.app.assist.island.Island.Gesture
import ai.jarvis.app.assist.island.Island.State
import ai.jarvis.app.ui.theme.JarvisTokens
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** The island's gestures, its window's frame and its spring (T-0695), as pure rules. */
class IslandMotionTest {

    @Test
    fun `a flick puts it out of the way, a tap or pull opens it, an approval takes only its answers`() {
        val table = mapOf(
            (State.CARD to Gesture.FLICK_UP) to Action.COLLAPSE,
            (State.CARD to Gesture.TAP) to Action.COLLAPSE,
            (State.CARD to Gesture.PULL_DOWN) to Action.NONE,
            (State.PILL to Gesture.TAP) to Action.EXPAND,
            (State.PILL to Gesture.PULL_DOWN) to Action.EXPAND,
            (State.PILL to Gesture.FLICK_UP) to Action.DISMISS,
            (State.ACTIVITY to Gesture.TAP) to Action.EXPAND,
            (State.ACTIVITY to Gesture.PULL_DOWN) to Action.EXPAND,
            (State.ACTIVITY to Gesture.FLICK_UP) to Action.COLLAPSE,
        )
        for ((given, action) in table) assertEquals("$given", action, Island.act(given.first, given.second))
        for (g in Gesture.entries) {
            assertEquals(Action.NONE, Island.act(State.APPROVAL, g))
            assertEquals(Action.NONE, Island.act(State.HIDDEN, g))
        }
    }

    @Test
    fun `the window grows before a morph and shrinks after it`() {
        val pill = 212 to 44
        val card = 400 to 260
        assertEquals(card, Island.frame(pill, card))
        assertEquals(card, Island.frame(card, pill))
        // An activity's pill is wider than the word's: one dimension grows while the other holds.
        assertEquals(300 to 44, Island.frame(pill, 300 to 44))
    }

    @Test
    fun `the glass spring is all but there at its settle time and comes to rest`() {
        val s = Island.Spring.glass(rest = 0.5f).apply { snap(0f); target = 100f }
        val frame = 1f / 60
        var t = 0f
        while (t < JarvisTokens.Spring.Glass.SETTLE / 1000f) { s.step(frame); t += frame }
        assertTrue("at settle: ${s.value}", abs(s.value - 100f) < 2f)
        var frames = 0
        while (s.step(frame)) frames++
        assertEquals(100f, s.value)
        assertFalse(s.moving)
        assertTrue("came to rest in $frames more frames", frames < 30)
    }

    @Test
    fun `a new target mid-flight keeps the value and the velocity`() {
        val s = Island.Spring.glass(rest = 0.5f).apply { snap(0f); target = 100f }
        repeat(6) { s.step(1f / 60) }
        val (value, velocity) = s.value to s.velocity
        s.target = 0f
        assertEquals(value, s.value)
        assertEquals(velocity, s.velocity)
        assertTrue(velocity > 0f)
        // A fling's velocity is the spring's start: it carries on before it turns back.
        s.step(1f / 60)
        assertTrue(s.value > value)
    }
}
