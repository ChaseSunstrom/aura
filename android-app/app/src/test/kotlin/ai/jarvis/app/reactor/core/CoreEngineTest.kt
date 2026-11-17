package ai.jarvis.app.reactor.core

import ai.jarvis.app.ui.theme.JarvisTokens
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlin.math.abs

/**
 * The phone's port of the console's core against the console's own numbers (T-0712).
 *
 * `tests/contracts/core_frames.json` is written from `jarvis-web/src/lib/ds/core/engine.ts` by
 * `contract.test.ts`, which fails when it is stale; this checks [CoreEngine] against the same
 * file, frame by frame, to 1e-4. Does not cover the drawing ([CoreCanvas]): that is held to
 * pictures, not numbers.
 */
class CoreEngineTest {
    private val contract = JSONObject(File("../../tests/contracts/core_frames.json").readText())

    private fun near(expected: Double, actual: Double, what: String) =
        assertTrue("$what: expected $expected, got $actual", abs(expected - actual) <= TOLERANCE)

    private fun near(expected: JSONArray, actual: DoubleArray, what: String) {
        assertEquals("$what: length", expected.length(), actual.size)
        for (i in actual.indices) near(expected.getDouble(i), actual[i], "$what[$i]")
    }

    @Test
    fun `the view, the rings and the timings are the console's`() {
        val view = contract.getJSONObject("view")
        near(view.getDouble("TILT"), CoreEngine.TILT, "TILT")
        near(view.getDouble("CAMERA"), CoreEngine.CAMERA, "CAMERA")
        near(view.getDouble("SPREAD"), CoreEngine.SPREAD, "SPREAD")
        near(view.getDouble("BOARD_WIDTH"), CoreEngine.BOARD_WIDTH, "BOARD_WIDTH")
        near(view.getDouble("STRUT_IN"), CoreEngine.STRUT_IN, "STRUT_IN")
        val ms = contract.getJSONObject("ms")
        assertEquals(ms.getInt("beat"), JarvisTokens.Motion.Dur.BEAT)
        assertEquals(ms.getInt("shock"), JarvisTokens.Motion.Dur.SHOCK)
        assertEquals(ms.getInt("kick"), JarvisTokens.Motion.Dur.KICK)
        val rings = contract.getJSONArray("RINGS")
        assertEquals(rings.length(), CoreEngine.RINGS.size)
        CoreEngine.RINGS.forEachIndexed { i, r ->
            val o = rings.getJSONObject(i)
            near(o.getDouble("r"), r.r, "ring $i r")
            assertEquals(o.getInt("seg"), r.seg)
            near(o.getDouble("a"), r.a, "ring $i a")
            near(o.getDouble("b"), r.b, "ring $i b")
            near(o.getDouble("dir"), r.dir, "ring $i dir")
            near(o.getDouble("w"), r.w, "ring $i w")
            near(o.getDouble("d"), r.d, "ring $i d")
            near(o.getDouble("gap"), r.gap, "ring $i gap")
            // The scaled segment, as engine.ts segmentOf computes it.
            val s = contract.getJSONArray("segments").getJSONObject(i)
            near(s.getDouble("inner"), r.inner, "ring $i inner")
            near(s.getDouble("outer"), r.outer, "ring $i outer")
            near(s.getDouble("depth"), r.depth, "ring $i depth")
        }
        val seg = contract.getJSONObject("SEGMENT")
        near(seg.getDouble("inner"), CoreEngine.Segment.INNER, "inner")
        near(seg.getDouble("outer"), CoreEngine.Segment.OUTER, "outer")
        near(seg.getDouble("depth"), CoreEngine.Segment.DEPTH, "depth")
        assertEquals(seg.getInt("windings"), CoreEngine.Segment.WINDINGS)
        val flow = contract.getJSONObject("FLOW")
        assertEquals(flow.getInt("dots"), CoreEngine.Flow.DOTS)
        near(flow.getDouble("trail"), CoreEngine.Flow.TRAIL, "trail")
        assertEquals(flow.getInt("comet"), CoreEngine.Flow.COMET)
        near(flow.getDouble("lift"), CoreEngine.Flow.LIFT, "lift")
        for (s in contract.getJSONArray("shockRing").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }) {
            val r = CoreEngine.shockRing(s.getDouble("f"))
            near(s.getDouble("radius"), r.radius, "shock radius")
            near(s.getDouble("width"), r.width, "shock width")
            near(s.getDouble("alpha"), r.alpha, "shock alpha")
        }
    }

    @Test
    fun `the nine looks, the palette and the reactor mapping are the console's`() {
        val looks = contract.getJSONObject("LOOKS")
        assertEquals(looks.length(), CoreState.entries.size)
        for (state in CoreState.entries) {
            val o = looks.getJSONObject(state.key)
            val l = CoreEngine.LOOKS.getValue(state)
            for ((name, value) in l.params()) near(o.getDouble(name), value, "${state.key}.$name")
            near(o.getJSONArray("color"), l.color, "${state.key}.color")
        }
        val palette = contract.getJSONObject("PALETTE")
        for ((name, rgb) in CoreEngine.PALETTE) near(palette.getJSONArray(name), rgb, "palette.$name")
        assertEquals(palette.length(), CoreEngine.PALETTE.size)
        val map = contract.getJSONObject("REACTOR_TO_CORE")
        for (key in map.keys()) assertEquals(key, map.getString(key), coreStateFor(key).key)
        assertEquals(CoreState.IDLE, coreStateFor("constructor"))
        assertEquals(CoreState.IDLE, coreStateFor("no such state"))
    }

    @Test
    fun `the shell's points are the console's`() {
        val fib = contract.getJSONObject("fibonacci")
        val shell = CoreEngine.fibonacci(fib.getInt("n"))
        near(fib.getJSONArray("pos"), DoubleArray(24) { shell.pos[it].toDouble() }, "pos")
        val hot = fib.getJSONArray("hot").let { a -> (0 until a.length()).map { a.getInt(it) } }
        assertEquals(hot, (0 until shell.n).filter { shell.hot[it] })
        near(fib.getJSONArray("speed"), DoubleArray(8) { shell.speed[it].toDouble() }, "speed")
        near(fib.getJSONArray("phase"), DoubleArray(8) { shell.phase[it].toDouble() }, "phase")
    }

    @Test
    fun `every frozen frame matches the console's, state by state`() {
        val heard = contract.getJSONObject("heard").let {
            Inputs(level = it.getDouble("level"), envelope = it.getDouble("envelope"), music = it.getDouble("music"))
        }
        val frames = contract.getJSONArray("frames")
        assertTrue("a contract with frames", frames.length() >= CoreState.entries.size)
        for (i in 0 until frames.length()) {
            val f = frames.getJSONObject(i)
            val state = CoreState.of(f.getString("state"))
            val from = CoreState.of(f.getString("from"))
            val at = f.getDouble("at")
            val inputs = if (f.getString("inputs") == "heard") heard else Inputs()
            val w = "${state.key} from ${from.key} at $at (${f.getString("inputs")})"
            val core = CoreEngine.frozenAt(state, from, at, inputs)

            val look = f.getJSONObject("look")
            for ((name, value) in core.look.params()) near(look.getDouble(name), value, "$w look.$name")
            near(look.getJSONArray("color"), core.look.color, "$w color")
            near(f.getDouble("t"), core.t, "$w t")
            near(f.getDouble("spinA"), core.spinA, "$w spinA")
            near(f.getDouble("gyroA"), core.gyroA, "$w gyroA")
            near(f.getDouble("shellA"), core.shellA, "$w shellA")
            near(f.getDouble("flowA"), core.flowA, "$w flowA")
            near(f.getDouble("amp"), core.amp, "$w amp")
            near(f.getDouble("beat"), core.beat, "$w beat")
            assertEquals("$w beats", f.getInt("beats"), core.beats)
            assertEquals("$w changes", f.getInt("changes"), core.changes)
            near(f.getDouble("kick"), CoreEngine.kick(core, core.t), "$w kick")
            near(f.getJSONArray("shocks"), CoreEngine.shocks(core, core.t), "$w shocks")
            val glow = CoreEngine.coreGlow(core)
            near(f.getJSONObject("glow").getDouble("glow"), glow.glow, "$w glow")
            near(f.getJSONObject("glow").getDouble("radius"), glow.radius, "$w radius")
            val rings = f.getJSONArray("rings")
            for (ri in 0 until rings.length()) {
                val r = rings.getJSONObject(ri)
                val pose = CoreEngine.ringPose(core, ri)
                near(r.getDouble("a"), pose.a, "$w ring $ri a")
                near(r.getDouble("b"), pose.b, "$w ring $ri b")
                near(r.getDouble("pulse"), pose.pulse, "$w ring $ri pulse")
                near(r.getJSONArray("flow"), doubleArrayOf(0.0, 5.0, 25.0).map { CoreEngine.flowAngle(core, ri, it.toInt()) }.toDoubleArray(), "$w ring $ri flow")
                near(r.getDouble("comet"), CoreEngine.cometAngle(core, ri), "$w ring $ri comet")
            }
            near(f.getJSONArray("lit"), DoubleArray(4) { CoreEngine.segmentLit(core, it) }, "$w lit")
            val n = contract.getJSONObject("fibonacci").getInt("n")
            val out = FloatArray(n * 4)
            CoreEngine.shellInto(core, CoreEngine.fibonacci(n), out)
            val every8th = (0 until n * 4).filter { (it / 4) % 8 == 0 }.map { out[it].toDouble() }.toDoubleArray()
            near(f.getJSONArray("shell"), every8th, "$w shell")
        }
    }

    private companion object {
        const val TOLERANCE = 1e-4
    }
}
