package ai.aura.app.assist.hermes

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Every glyph in `docs/hermes-hud-protocol.md`'s goal table, and the reason
 * text kept verbatim. Mirrored by `android-app/tools/hermes_goal_glyph_test.py`.
 */
class GoalStatusTest {

    @Test
    fun `set - the budget and the goal text`() {
        val g = GoalStatus.parse("⊙ Goal set (20-turn budget): tidy the notes folder")!!
        assertEquals(GoalStatus.State.SET, g.state)
        assertEquals(20, g.budget)
        assertNull(g.turn)
        assertEquals("tidy the notes folder", g.reason)
        assertEquals("0/20", g.progress)
        assertTrue(g.active)
    }

    @Test
    fun `continuing - turn of budget and the judge's reason`() {
        val g = GoalStatus.parse("↻ Continuing toward goal (3/20): two folders remain untagged")!!
        assertEquals(GoalStatus.State.CONTINUING, g.state)
        assertEquals(3, g.turn)
        assertEquals(20, g.budget)
        assertEquals("two folders remain untagged", g.reason)
        assertEquals("3/20", g.progress)
        assertTrue(g.active)
    }

    @Test
    fun `parked - the whole line after the glyph`() {
        val g = GoalStatus.parse("⏳ Goal (parked until pid 4242 exits) — waiting on the build")!!
        assertEquals(GoalStatus.State.PARKED, g.state)
        assertEquals("Goal (parked until pid 4242 exits) — waiting on the build", g.reason)
        assertFalse(g.active)
        assertTrue(g.present)
    }

    @Test
    fun `paused - turns used`() {
        val g = GoalStatus.parse("⏸ Goal paused — 5/20 turns used. /goal resume to continue")!!
        assertEquals(GoalStatus.State.PAUSED, g.state)
        assertEquals(5, g.turn)
        assertEquals(20, g.budget)
        assertEquals("5/20", g.progress)
        assertFalse(g.active)
    }

    @Test
    fun `achieved - the judge's reason`() {
        val g = GoalStatus.parse("✓ Goal achieved: every note carries a tag and the index links them")!!
        assertEquals(GoalStatus.State.ACHIEVED, g.state)
        assertEquals("every note carries a tag and the index links them", g.reason)
        assertFalse(g.active)
        assertTrue(g.present)
    }

    @Test
    fun `cleared - nothing to show`() {
        val g = GoalStatus.parse("Goal cleared")!!
        assertEquals(GoalStatus.State.CLEARED, g.state)
        assertFalse(g.present)
        assertEquals("", g.progress)
    }

    @Test
    fun `a reason with a colon of its own survives whole`() {
        val g = GoalStatus.parse("↻ Continuing toward goal (1/5): judge says: not yet: two tests red")!!
        assertEquals("judge says: not yet: two tests red", g.reason)
    }

    @Test
    fun `a line with no glyph is shown as it came`() {
        val g = GoalStatus.parse("Something new the backend says")!!
        assertEquals(GoalStatus.State.UNKNOWN, g.state)
        assertEquals("Something new the backend says", g.reason)
    }

    @Test
    fun `blank is nothing`() {
        assertNull(GoalStatus.parse(""))
        assertNull(GoalStatus.parse("   "))
        assertNull(GoalStatus.parse(null))
    }

    @Test
    fun `the glyph table is the doc's five, in order`() {
        assertEquals(listOf("⊙", "↻", "⏳", "⏸", "✓"), GoalStatus.GLYPHS.keys.toList())
        assertEquals(
            listOf(GoalStatus.State.SET, GoalStatus.State.CONTINUING, GoalStatus.State.PARKED, GoalStatus.State.PAUSED, GoalStatus.State.ACHIEVED),
            GoalStatus.GLYPHS.values.toList(),
        )
        for ((glyph, state) in GoalStatus.GLYPHS) assertEquals(glyph, state.glyph)
    }
}
