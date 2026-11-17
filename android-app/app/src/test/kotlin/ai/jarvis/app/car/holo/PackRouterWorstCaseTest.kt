package ai.jarvis.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What [PackRouter.plan] costs on the worst pack a read can hand it (T-1088): a square grid of
 * residential streets, all one speed, so A* has no quick way to prefer one and expands close to
 * [PackRouter.MAX_NODES]; four parallel roads within [PackRouter.SNAP_M] of each end, which
 * made a corner-to-corner drive run all 16 (start, end) searches, as its straight-line bound cannot
 * rule any pair out. Synthetic coordinates around 10, 20.
 *
 * Measured on a loaded 16-core server JVM (load about 7), three runs in one JVM, the first cold:
 * 16 per-pair searches 1275, 992 and 1085 ms (all 16 finished, 1021 ms with no budget); the one
 * search from every start 419, 270 and 327 ms (220 ms with no budget). A phone is slower than
 * this by some factor not measured here.
 *
 * Timing on a shared machine: the assertions are loose (a route, and inside twice the budget);
 * the numbers are printed.
 */
class PackRouterWorstCaseTest {
    private val plane = GeoPlane(10.0, 20.0)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))

    /** [n] rows and [n] columns, [step] metres apart, one road each, a vertex at every crossing. */
    private fun grid(n: Int, step: Float): List<PackRoad> {
        val rows = (0 until n).map { r -> PackRoad((0 until n).map { c -> at(c * step, r * step) }, "residential", "Row $r", 0, 0) }
        val cols = (0 until n).map { c -> PackRoad((0 until n).map { r -> at(c * step, r * step) }, "residential", "Column $c", 0, 0) }
        return rows + cols
    }

    private fun timed(from: LatLon, to: LatLon, roads: List<PackRoad>, budgetMs: Long): Pair<PackRouter.Plan?, Long> {
        val t0 = System.nanoTime()
        val plan = PackRouter.plan(from, to, { _, _, _, _ -> roads }, budgetMs = budgetMs)
        return plan to (System.nanoTime() - t0) / 1_000_000L
    }

    @Test
    fun `a corner-to-corner drive over a grid near the node cap with four roads at each end is a route inside the budget`() {
        val n = 400
        val step = 10f
        val roads = grid(n, step)
        // 4 km a side: inside one pack read's span, 160 000 nodes, 800 roads (a read returns 5 000 at most).
        val from = at(3f, 3f)
        val to = at((n - 1) * step - 3f, (n - 1) * step - 3f)
        val runs = (1..3).map { timed(from, to, roads, PackRouter.BUDGET_MS) }
        val free = timed(from, to, roads, 600_000L)
        println("PackRouter worst case: ${n * n} nodes, ${roads.size} roads, 4 snaps each end; default budget ms per run = ${runs.map { it.second }}, unbounded budget ms = ${free.second}")
        runs.forEach { assertNotNull("a route inside the budget", it.first) }
        assertNotNull(free.first)
        assertTrue("inside the full budget twice over: ${runs.map { it.second }}", runs.all { it.second < 2 * PackRouter.BUDGET_MS })
        // 2 x 3 984 m along the grid, at 30 km/h, whichever of the many equal ways it takes.
        assertEquals(2 * ((n - 1) * step - 6f), runs[0].first!!.route.distanceM, 60f)
    }
}
