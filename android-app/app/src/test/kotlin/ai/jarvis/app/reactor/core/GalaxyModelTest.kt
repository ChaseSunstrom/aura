package ai.jarvis.app.reactor.core

import ai.jarvis.app.ui.theme.JarvisThemes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * AURA's galaxy on the phone (T-0871): the model's eight states land on the pack's own
 * targets, its star budgets are the pack's, and its tween is the same at 60 Hz and 120 Hz.
 * Does not cover the pixels ([GalaxyCanvas]): those are held to goldens.
 */
class GalaxyModelTest {
    private val pack = JarvisThemes.AURA

    @Test
    fun `the nine core states land on the pack's eight`() {
        assertEquals(pack.coreStates.keys, GalaxyModel.GALAXY_STATE.values.toSet())
        assertEquals(CoreState.entries.toSet(), GalaxyModel.GALAXY_STATE.keys)
    }

    @Test
    fun `every state settles on the pack's targets and tint`() {
        for (mode in CoreState.entries) {
            val from = if (mode == CoreState.ALERT) CoreState.IDLE else CoreState.ALERT
            val g = GalaxyModel.at(mode, from, 20_000.0, GalaxyQuality.MINI)
            val row = pack.coreStates.getValue(GalaxyModel.GALAXY_STATE.getValue(mode))
            GalaxyModel.PARAMS.forEachIndexed { i, p ->
                val want = (row[p] ?: 0f).toDouble()
                assertTrue("$mode $p: want $want, got ${g.look[i]}", abs(want - g.look[i]) < 1e-4)
            }
            val tint = pack.coreTints[GalaxyModel.GALAXY_STATE.getValue(mode)]
            if (tint != null) {
                val want = GalaxyModel.rgb(tint)
                for (c in 0..2) assertTrue("$mode tint[$c]", abs(want[c] - g.tint[c]) < 1e-3)
            }
        }
    }

    @Test
    fun `the star budgets are the pack's`() {
        assertEquals(1200, pack.coreParams.getValue("starsPhone").toInt())
        assertEquals(360, pack.coreParams.getValue("starsMini").toInt())
        assertEquals(1200, GalaxyModel.create(GalaxyQuality.PHONE).stars.size)
        assertEquals(360, GalaxyModel.create(GalaxyQuality.MINI).stars.size)
        assertEquals(pack.coreParams.getValue("starsDesktop").toInt(), GalaxyModel.create(GalaxyQuality.DESKTOP).stars.size)
    }

    @Test
    fun `the small-size counts are the pack's, and a galaxy keeps the pack it was built from`() {
        val mini = GalaxyModel.create(GalaxyQuality.MINI)
        assertEquals(pack.coreParams.getValue("nebulaeMini").toInt(), mini.nebulae.size)
        assertEquals(pack.coreParams.getValue("jetsMini").toInt(), mini.jets.size)
        val full = GalaxyModel.create(GalaxyQuality.PHONE)
        assertEquals(pack.coreParams.getValue("nebulae").toInt(), full.nebulae.size)
        assertEquals(pack.coreParams.getValue("jets").toInt(), full.jets.size)
        // A pack that draws another core has no galaxy of its own: it wears the default's.
        assertTrue(mini.pack === GalaxyModel.packOf(pack))
        assertTrue(GalaxyModel.packOf(JarvisThemes.JARVIS) === GalaxyModel.packOf(pack))
    }

    @Test
    fun `the tween lands the same at 60 Hz and 120 Hz`() {
        val a = GalaxyModel.create(GalaxyQuality.MINI)
        val b = GalaxyModel.create(GalaxyQuality.MINI)
        GalaxyModel.setState(a, CoreState.THINKING)
        GalaxyModel.setState(b, CoreState.THINKING)
        repeat(60) { GalaxyModel.step(a, 1000.0 / 60) }
        repeat(120) { GalaxyModel.step(b, 1000.0 / 120) }
        for (i in a.look.indices) assertEquals("look[$i]", a.look[i], b.look[i], 1e-9)
        for (c in 0..2) assertEquals("tint[$c]", a.tint[c], b.tint[c], 1e-9)
        // And part way: not snapped, not still at the start.
        val idle = pack.coreStates.getValue("idle").getValue("spin").toDouble()
        val think = pack.coreStates.getValue("think").getValue("spin").toDouble()
        assertTrue(a.look[GalaxyModel.SPIN] > idle && a.look[GalaxyModel.SPIN] < think)
    }

    @Test
    fun `a change flares, a repeat does not, and reduced motion snaps`() {
        val g = GalaxyModel.create(GalaxyQuality.MINI)
        assertFalse(GalaxyModel.setState(g, CoreState.IDLE))
        assertTrue(GalaxyModel.setState(g, CoreState.SPEAKING))
        assertEquals(g.t, g.kickAt, 0.0)
        val still = GalaxyModel.create(GalaxyQuality.MINI, CoreState.IDLE, still = true)
        GalaxyModel.setState(still, CoreState.THINKING)
        assertEquals(pack.coreStates.getValue("think").getValue("spin").toDouble(), still.look[GalaxyModel.SPIN], 1e-6)
    }

    @Test
    fun `the sky is the same every load and the frame draws finite light`() {
        val a = GalaxyModel.create(GalaxyQuality.PHONE)
        val b = GalaxyModel.create(GalaxyQuality.PHONE)
        for (i in a.stars.indices) {
            assertEquals(a.stars[i].r, b.stars[i].r, 0.0)
            assertEquals(a.stars[i].th, b.stars[i].th, 0.0)
        }
        for (mode in CoreState.entries) {
            val g = GalaxyModel.at(mode, CoreState.IDLE, 400.0, GalaxyQuality.PHONE)
            val list = GalaxyDrawList()
            GalaxyFrame().build(g, list, 1080f, 1920f)
            assertTrue("$mode draws", list.n >= g.stars.size / 2)
            var counted = 0
            for (k in 0 until list.batches) counted += list.batchCount[k]
            assertEquals("$mode batches cover the list", list.n, counted)
            for (i in 0 until list.n * GalaxyDrawList.STRIDE) assertTrue("$mode [$i] finite", list.data[i].isFinite())
        }
    }

    @Test
    fun `the jets and comets show only in the states that have them`() {
        val idle = GalaxyModel.at(CoreState.IDLE, CoreState.IDLE, 20_000.0, GalaxyQuality.MINI)
        val think = GalaxyModel.at(CoreState.THINKING, CoreState.IDLE, 20_000.0, GalaxyQuality.MINI)
        val a = GalaxyDrawList()
        val b = GalaxyDrawList()
        GalaxyFrame().build(idle, a, 400f, 400f)
        GalaxyFrame().build(think, b, 400f, 400f)
        // Thinking adds two jet beams, 30 jet sparks and two comets' trails and heads.
        assertTrue("think ${b.n} > idle ${a.n}", b.n > a.n + 30)
    }
}
