package ai.aura.app.car.holo

import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * The phone's own route over a map pack's roads (T-1026): the quickest way by time, one-way
 * streets driven one way, an end off every road answering nothing, the maneuvers named, and the
 * budgets. Roads are laid on a flat grid of metres from a made-up place (Strelsau, Ruritania, 5, 5).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class PackRouterTest {
    private val plane = GeoPlane(5.0, 5.0)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))

    private fun road(name: String, kind: String, vararg p: Pair<Float, Float>, oneway: Int = 0, kph: Int = 0) =
        PackRoad(p.map { at(it.first, it.second) }, kind, name, oneway, kph)

    /** Three streets in a U: First up, Avenue B across, Second down. */
    private val u = listOf(
        road("First Street", "residential", 0f to 0f, 0f to 500f),
        road("Avenue B", "residential", 0f to 500f, 1000f to 500f),
        road("Second Street", "residential", 1000f to 500f, 1000f to 0f),
    )

    private fun route(from: LatLon, to: LatLon, roads: List<PackRoad>) = PackRouter.plan(from, to, { _, _, _, _ -> roads })

    private fun metresFrom(p: LatLon, east: Float, north: Float) =
        kotlin.math.hypot(plane.east(p.lon) - east, plane.north(p.lat) - north)

    private fun passes(r: HouseRoute, east: Float, north: Float) = r.shape.any { metresFrom(it, east, north) < 1f }

    @Test
    fun `it takes the quicker way, not the shorter one`() {
        // Both ways are 1500 m; the cross street is posted fast, the others are residential.
        val roads = listOf(
            road("First Street", "residential", 0f to 0f, 0f to 500f),
            road("Avenue B", "tertiary", 0f to 500f, 1000f to 500f, kph = 90),
            road("Avenue A", "residential", 0f to 0f, 1000f to 0f),
            road("Second Street", "residential", 1000f to 500f, 1000f to 0f),
        )
        val r = route(at(0f, 0f), at(1000f, 500f), roads)!!.route
        assertTrue("up First, along B", passes(r, 0f, 500f))
        assertEquals(1500f, r.distanceM, 5f)
        assertEquals("500 m at 30, 1000 m at 90 km/h", 60f + 40f, r.durationS, 1f)
        assertEquals("First Street", r.street)
    }

    @Test
    fun `a one-way street is not driven against`() {
        // "Short" is the straight way, open only from east to west; the loop north of it is slower.
        val roads = listOf(
            road("Short Street", "primary", 0f to 0f, 1000f to 0f, oneway = -1),
            road("Loop Road", "residential", 0f to 0f, 0f to 300f, 1000f to 300f, 1000f to 0f),
        )
        val out = route(at(0f, 0f), at(1000f, 0f), roads)!!.route
        assertTrue("round the loop", passes(out, 0f, 300f) && passes(out, 1000f, 300f))
        assertEquals(1600f, out.distanceM, 5f)
        val back = route(at(1000f, 0f), at(0f, 0f), roads)!!.route
        assertEquals("the way it is open", 1000f, back.distanceM, 5f)
        val along = route(at(0f, 0f), at(1000f, 0f), roads.map { PackRoad(it.line, it.kind, it.name, 1, it.kph) }.take(1))!!.route
        assertEquals("oneway 1 is along the line", 1000f, along.distanceM, 5f)
    }

    @Test
    fun `two ends on a one-way street are joined only the way it runs`() {
        val roads = listOf(road("Short Street", "residential", 0f to 0f, 1000f to 0f, oneway = 1))
        val fwd = route(at(200f, 5f), at(700f, 5f), roads)!!.route
        assertEquals(500f, fwd.distanceM, 3f)
        assertNull("against it, with no other road", route(at(700f, 5f), at(200f, 5f), roads))
    }

    @Test
    fun `roads join at a shared vertex inside a line, and a road of a kind a car does not drive is no road`() {
        val roads = listOf(
            road("Long Road", "residential", 0f to 0f, 500f to 0f, 1000f to 0f),
            road("Cross Road", "residential", 500f to 0f, 500f to 300f),
            road("Garden Path", "footway", 0f to 0f, 0f to 300f, 500f to 300f),
        )
        val r = route(at(0f, 0f), at(500f, 300f), roads)!!.route
        assertEquals(800f, r.distanceM, 3f)
        assertNull("only a footway leads there", route(at(0f, 0f), at(0f, 300f), roads))
    }

    @Test
    fun `no path, or an end far from every road, answers nothing`() {
        val apart = listOf(road("West Road", "residential", 0f to 0f, 300f to 0f), road("East Road", "residential", 800f to 0f, 1100f to 0f))
        assertNull("two roads that never meet", route(at(0f, 0f), at(1100f, 0f), apart))
        assertNull("a start 200 m from the road", route(at(100f, 200f), at(300f, 0f), apart))
        assertNull("an end 200 m from the road", route(at(0f, 0f), at(1100f, 200f), apart))
        assertNotNull("100 m off is on the map", route(at(100f, 100f), at(300f, 0f), apart))
        assertNull("no roads at all", route(at(0f, 0f), at(300f, 0f), emptyList()))
        assertNull("nowhere to go", route(at(10f, 0f), at(10f, 0f), apart))
        assertNull("not a place", PackRouter.plan(LatLon(Double.NaN, 5.0), at(0f, 0f), { _, _, _, _ -> apart }))
    }

    @Test
    fun `the turns are named where the street changes`() {
        val out = route(at(0f, 0f), at(1000f, 0f), u)!!
        assertEquals(listOf("Turn right onto Avenue B", "Turn right onto Second Street", "Arrive"), out.turns.map { it.instruction })
        // The side beside the words, for the console's page, which words a turn itself (T-1075).
        assertEquals(listOf("right", "right", ""), out.turns.map { it.modifier })
        assertEquals(listOf("Avenue B", "Second Street", ""), out.turns.map { it.street })
        assertEquals("the route holds the next one", out.turns[0], out.route.turn)
        assertEquals(0f, metresFrom(out.route.turn!!.at!!, 0f, 500f), 1f)
        assertEquals(0f, metresFrom(out.turns.last().at!!, 1000f, 0f), 6f)
        val back = route(at(1000f, 0f), at(0f, 0f), u)!!
        assertEquals(listOf("Turn left onto Avenue B", "Turn left onto First Street", "Arrive"), back.turns.map { it.instruction })
        assertEquals(listOf("left", "left", ""), back.turns.map { it.modifier })
    }

    @Test
    fun `a street that changes its name straight on is continued, one that only bends is not a turn`() {
        val roads = listOf(
            road("Quarry Road", "residential", 0f to 0f, 400f to 0f),
            road("Mill Road", "residential", 400f to 0f, 800f to 0f),
            // A bend in one street, with no junction: not a maneuver.
            road("Mill Road", "residential", 800f to 0f, 1000f to 100f, 1000f to 500f),
        )
        val out = route(at(0f, 0f), at(1000f, 500f), roads)!!
        assertEquals(listOf("Continue onto Mill Road", "Arrive"), out.turns.map { it.instruction })
        assertEquals(listOf("straight", ""), out.turns.map { it.modifier })
    }

    @Test
    fun `a turn with no street name is just a turn`() {
        val roads = listOf(road("", "service", 0f to 0f, 0f to 300f), road("", "service", 0f to 300f, 300f to 300f))
        val out = route(at(0f, 0f), at(300f, 300f), roads)!!
        assertEquals(listOf("Arrive"), out.turns.map { it.instruction })
        val named = listOf(road("Dock Lane", "service", 0f to 0f, 0f to 300f), road("", "service", 0f to 300f, 300f to 300f))
        assertEquals(listOf("Turn right", "Arrive"), route(at(0f, 0f), at(300f, 300f), named)!!.turns.map { it.instruction })
    }

    @Test
    fun `the search is bounded by nodes and by time, and the box by the pack's own cap`() {
        // A long straight road of many vertices: the goal is beyond a one-node budget.
        val line = road("Long Road", "residential", *(0..100).map { it * 10f to 0f }.toTypedArray())
        val fetch = { _: Double, _: Double, _: Double, _: Double -> listOf(line) }
        assertNotNull(PackRouter.plan(at(0f, 0f), at(1000f, 0f), fetch))
        assertNull("out of nodes", PackRouter.plan(at(0f, 0f), at(1000f, 0f), fetch, maxNodes = 5))
        assertNull("out of time", PackRouter.plan(at(0f, 0f), at(1000f, 0f), fetch, budgetMs = 0))
        var asked: DoubleArray? = null
        PackRouter.plan(LatLon(5.0, 5.0), LatLon(5.03, 5.04), { w, s, e, n -> asked = doubleArrayOf(w, s, e, n); emptyList() })
        val box = asked!!
        assertTrue("both ends are inside, with room", box[0] < 5.0 && box[2] > 5.04 && box[1] < 5.0 && box[3] > 5.03)
        assertTrue("never wider than a pack read", box[2] - box[0] <= MapPack.MAX_SPAN_DEG && box[3] - box[1] <= MapPack.MAX_SPAN_DEG)
        var called = false
        assertNull("a trip over the cap is not tried", PackRouter.plan(LatLon(5.0, 5.0), LatLon(5.0, 5.2), { _, _, _, _ -> called = true; emptyList() }))
        assertTrue("and no pack is read for it", !called)
    }

    @Test
    fun `an end on a vertex of a one-way street keeps every road that meets there`() {
        // One Way Street begins at the junction with Side Road and ends at the one with Exit Road.
        val roads = listOf(
            road("One Way Street", "residential", 0f to 0f, 500f to 0f, oneway = 1),
            road("Side Road", "residential", 0f to 0f, 0f to 300f),
            road("Exit Road", "residential", 500f to 300f, 500f to 0f),
        )
        val out = route(at(0f, 0f), at(0f, 300f), roads)!!.route
        assertEquals("from the junction, up Side Road", 300f, out.distanceM, 3f)
        val into = route(at(500f, 300f), at(500f, 0f), roads)!!.route
        assertEquals("to the far end of the one-way, down Exit Road", 300f, into.distanceM, 3f)
        val along = route(at(0f, 0f), at(500f, 0f), roads)!!.route
        assertEquals("and still along the one-way", 500f, along.distanceM, 3f)
    }

    @Test
    fun `the nearest road is not the only one tried`() {
        // A service stub nothing joins is 0 m from the start; the street that goes on is 60 m away.
        val roads = listOf(
            road("Stub", "service", 0f to 0f, 0f to 200f),
            road("Main Street", "residential", 60f to -100f, 60f to 100f, 60f to 400f),
        )
        val out = route(at(0f, 100f), at(60f, 400f), roads)!!.route
        assertEquals(300f, out.distanceM, 5f)
        assertEquals("Main Street", out.street)
    }

    @Test
    fun `a read that hit the pack's row cap has holes and answers nothing`() {
        val line = road("Long Road", "residential", 0f to 0f, 1000f to 0f)
        assertNotNull(PackRouter.plan(at(0f, 0f), at(1000f, 0f), { _, _, _, _ -> List(MapPack.LIMIT - 1) { line } }))
        assertNull(PackRouter.plan(at(0f, 0f), at(1000f, 0f), { _, _, _, _ -> List(MapPack.LIMIT) { line } }))
    }

    @Test
    fun `the time budget is spent building the graph too`() {
        var read = 0
        val counting = object : AbstractList<LatLon>() {
            override val size = 2
            override fun get(index: Int): LatLon { read++; return at(index * 100f, 0f) }
        }
        assertNull(PackRouter.plan(at(0f, 0f), at(100f, 0f), { _, _, _, _ -> listOf(PackRoad(counting, "residential", "X", 0, 0)) }, budgetMs = -1))
        assertEquals("no road was built once the time was up", 0, read)
    }

    @Test
    fun `it routes over a real pack file, from the packs the phone holds`() {
        val dir = File(ApplicationProvider.getApplicationContext<android.content.Context>().cacheDir, "router-${System.nanoTime()}")
        fun d(m: Double) = m / GeoPlane.M_PER_DEG
        // Rose Street runs one way, east; Elm Road is the way back round.
        MapPackFixture.write(
            File(dir, "a.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 1L,
            listOf(
                MapPackFixture.road("Rose Street", 5.0 to 5.0, 5.0 + d(1000.0) to 5.0, extra = """{"oneway":1,"kph":50}"""),
                MapPackFixture.road("Elm Road", 5.0 to 5.0, 5.0 to 5.0 + d(400.0), 5.0 + d(1000.0) to 5.0 + d(400.0), 5.0 + d(1000.0) to 5.0),
            ),
        )
        val packs = MapPacks(dir)
        val east = PackRouter.plan(LatLon(5.0, 5.0), LatLon(5.0, 5.0 + d(1000.0)), { w, s, e, n -> packs.roads(w, s, e, n) })!!
        assertEquals("along Rose Street", 1000f, east.route.distanceM, 10f)
        assertEquals("Rose Street", east.route.street)
        val west = PackRouter.plan(LatLon(5.0, 5.0 + d(1000.0)), LatLon(5.0, 5.0), { w, s, e, n -> packs.roads(w, s, e, n) })!!
        assertEquals("Rose Street is shut that way: up, across and down Elm Road", 1800f, west.route.distanceM, 10f)
        assertNull("outside every pack", PackRouter.plan(LatLon(6.0, 6.0), LatLon(6.0, 6.005), { w, s, e, n -> packs.roads(w, s, e, n) }))
        packs.close()
    }

    // T-1029: the first pair of ends that found any path won, so a start 5 m from a long way
    // round beat a direct road 30 m off.
    @Test
    fun `the quickest way over every pair of nearby roads wins, not the first that connects`() {
        val roads = listOf(
            road("Long Lane", "residential", 0f to 5f, 0f to 1505f, 1000f to 1505f, 1000f to 0f),
            road("Direct Road", "residential", 0f to -30f, 1000f to -30f, 1000f to 0f),
        )
        val out = route(at(0f, 0f), at(1000f, 0f), roads)!!
        assertEquals("Direct Road", out.route.street)
        assertEquals(1030f, out.route.distanceM, 10f)
        // The nearest road still wins when it is the quick way: getting to a road counts, at a crawl.
        val near = listOf(
            road("Near Street", "residential", 0f to 5f, 1000f to 5f, 1000f to 0f),
            road("Far Street", "residential", 0f to -100f, 1000f to -100f, 1000f to 0f),
        )
        assertEquals("Near Street", route(at(0f, 0f), at(1000f, 0f), near)!!.route.street)
    }

    // T-1029 review: a start and an end put on the same corner are no drive, and that empty
    // path cost nothing to drive, so it beat the short real one and the answer was no route.
    @Test
    fun `two ends on one corner do not beat a real way`() {
        val roads = listOf(
            road("Main Street", "residential", -500f to 0f, 0f to 0f, 500f to 0f),
            road("West Lane", "residential", 0f to 0f, -300f to -300f),
        )
        assertEquals("Main Street", route(at(0f, 0.2f), at(10f, 40f), roads)!!.route.street)
    }

    // T-1088: one search from every start counted a goal on the start's own node when a road led
    // back to it, so "route to where I already am" became a drive round the block.
    @Test
    fun `a destination where the car already is on a two-way road is no drive, not a loop`() {
        // Both ends snap onto the vertex at 500: one node (off a vertex each end gets a node of its own).
        val main = road("Main Street", "residential", -1000f to 0f, 500f to 0f, 1000f to 0f)
        assertNull(route(at(500f, 3f), at(500f, 3f), listOf(main)))
    }

    @Test
    fun `an end on the start's own node goes to the other nearby road, not round the block`() {
        // The start snaps to Main only; the end (30 m off) to Main, on the start's node, and to Side 130 m away.
        // The loop out along Main and back (about 135 s) is quicker than the real way to Side (about 204 s).
        val roads = listOf(
            road("Main Street", "residential", -1000f to 0f, 500f to 0f, 1000f to 0f),
            road("Connector", "residential", 1000f to 0f, 1000f to -160f),
            road("Side Street", "residential", 1000f to -160f, -1000f to -160f),
        )
        val r = route(at(500f, 0f), at(500f, -30f), roads)!!.route
        assertTrue("it ends on Side Street", passes(r, 500f, -160f))
        assertEquals("500 m along Main, 160 down, 500 back along Side", 1160f, r.distanceM, 10f)
    }

    @Test
    fun `roads read short are no route, since the missing ones may be the way`() {
        assertNotNull(route(at(0f, 0f), at(1000f, 0f), u))
        assertNull(route(at(0f, 0f), at(1000f, 0f), CutRoads(u)))
    }

    // T-1047: a destination beyond the span (0.1 degree, about 11 km) was refused outright, so a
    // wrong turn on a long drive left the car with no guidance. The kept line already answers
    // the far end; only the way back onto it is missing.

    /** A made-up 30 km road east, a vertex every 500 m, and a side street joining it at 2000 m. */
    private val longRoad = road("Long Road", "secondary", *(0..60).map { it * 500f to 0f }.toTypedArray())
    private val side = road("Side Street", "residential", 2000f to 80f, 2000f to 0f)

    /** The route held before the wrong turn: 30 km along Long Road, a maneuver at 15 km. */
    private fun kept(turnAt: Float = 15_000f) = HouseRoute(
        longRoad.line, 30_000f, 1_800f, "Long Road",
        HouseRoute.Turn("Turn left onto Quarry Road", "Quarry Road", at(turnAt, 0f), "left"),
    )

    @Test
    fun `a far destination joins the kept line ahead of the car and keeps the rest of it`() {
        val far = at(30_000f, 0f)
        val roads = listOf(longRoad, side)
        assertNull("no kept line: refused as before", PackRouter.plan(at(2000f, 80f), far, { _, _, _, _ -> roads }))
        val out = PackRouter.plan(at(2000f, 80f), far, { _, _, _, _ -> roads }, kept = kept())!!
        assertTrue("down the side street first", passes(out.route, 2000f, 0f))
        assertTrue("then the kept line's far vertices", passes(out.route, 15_000f, 0f) && passes(out.route, 29_500f, 0f))
        assertEquals("ends where the kept line ends", 0f, metresFrom(out.route.shape.last(), 30_000f, 0f), 1f)
        assertTrue("the piece back is short, the rest is the kept line's", out.route.distanceM in 27_900f..28_300f)
        assertEquals(
            listOf("Turn left onto Long Road", "Turn left onto Quarry Road", "Arrive"),
            out.turns.map { it.instruction },
        )
        assertEquals("the route holds the next maneuver, the one back onto the road", out.turns[0], out.route.turn)
        assertEquals(0f, metresFrom(out.turns[1].at!!, 15_000f, 0f), 1f)
        assertEquals(0f, metresFrom(out.turns.last().at!!, 30_000f, 0f), 1f)
    }

    @Test
    fun `a kept maneuver behind the join is not kept`() {
        val out = PackRouter.plan(at(2000f, 80f), at(30_000f, 0f), { _, _, _, _ -> listOf(longRoad, side) }, kept = kept(turnAt = 1_000f))!!
        assertEquals(listOf("Turn left onto Long Road", "Arrive"), out.turns.map { it.instruction })
    }

    @Test
    fun `a far destination with no kept point ahead within the span is still refused`() {
        // The car is on a road 20 km from the kept line (an ocean away in span terms).
        val elsewhere = road("Other Road", "residential", 0f to 20_000f, 500f to 20_000f)
        val roads = listOf(longRoad, elsewhere)
        assertNull(PackRouter.plan(at(0f, 20_000f), at(30_000f, 0f), { _, _, _, _ -> roads }, kept = kept()))
        // And the car beyond the kept line's end has nothing ahead of it.
        assertNull(PackRouter.plan(at(30_000f, 80f), at(60_000f, 0f), { _, _, _, _ -> roads }, kept = kept()))
    }

    // T-1048: the pack's coverage row. The case is the contract's: the house writes its row for its roads.
    private val coverageContract = MapPackFixture.contract.getJSONObject("pack").getJSONObject("coverage").getJSONObject("case")

    private fun <T> withCase(row: String?, bbox: DoubleArray? = null, body: (MapPacks) -> T): T {
        val dir = File(ApplicationProvider.getApplicationContext<android.content.Context>().cacheDir, "cover-${System.nanoTime()}")
        try {
            val box = bbox ?: coverageContract.getJSONArray("bbox").let { a -> DoubleArray(4) { a.getDouble(it) } }
            val roads = coverageContract.getJSONArray("roads").let { rs ->
                (0 until rs.length()).map { i ->
                    val line = rs.getJSONArray(i)
                    MapPackFixture.road("Road $i", *(0 until line.length()).map { line.getJSONArray(it).getDouble(0) to line.getJSONArray(it).getDouble(1) }.toTypedArray())
                }
            }
            MapPackFixture.write(File(dir, "a.pack"), box, 1L, roads, coverage = row)
            val packs = MapPacks(dir)
            try {
                return body(packs)
            } finally {
                packs.close()
            }
        } finally {
            dir.deleteRecursively()
        }
    }

    private fun MapPacks.has(lon: Double, lat: Double) = covers(lon, lat, lon, lat)

    @Test
    fun `a pack covers the points and boxes the contract says, from its coverage row`() {
        withCase(coverageContract.getString("row")) { packs ->
            val points = coverageContract.getJSONArray("points")
            for (i in 0 until points.length()) {
                val p = points.getJSONObject(i)
                val at = p.getJSONArray("at")
                assertEquals(p.getString("why"), p.getBoolean("covered"), packs.has(at.getDouble(0), at.getDouble(1)))
            }
            val boxes = coverageContract.getJSONArray("boxes")
            for (i in 0 until boxes.length()) {
                val b = boxes.getJSONObject(i)
                val r = b.getJSONArray("box")
                assertEquals(b.getString("why"), b.getBoolean("covered"), packs.covers(r.getDouble(0), r.getDouble(1), r.getDouble(2), r.getDouble(3)))
            }
        }
    }

    @Test
    fun `a pack with no row, a row that is not base64 or one of the wrong size answers the bbox as before`() {
        val hole = coverageContract.getJSONArray("points").getJSONObject(3).getJSONArray("at") // in the bbox, in a hole
        val outside = coverageContract.getJSONArray("points").getJSONObject(6).getJSONArray("at")
        for (row in listOf(null, "", "!!!!", "AA==", "AAAAAAAA")) {
            withCase(row) { packs ->
                assertTrue("row=$row: the bbox answer", packs.has(hole.getDouble(0), hole.getDouble(1)))
                assertFalse("row=$row: outside the bbox", packs.has(outside.getDouble(0), outside.getDouble(1)))
            }
        }
    }

    @Test
    fun `a route to an end in a hole of the extract is refused and covered ends route`() {
        withCase(coverageContract.getString("row")) { packs ->
            val roads = { w: Double, s: Double, e: Double, n: Double -> packs.roads(w, s, e, n) }
            val inPack = { p: LatLon -> packs.has(p.lon, p.lat) }
            val a = LatLon(5.005, 5.007) // on the first road
            val b = LatLon(5.005, 5.023)
            assertNotNull("both ends covered", PackRouter.plan(a, b, roads, covered = inPack))
            val hole = LatLon(5.034, 5.01) // 3.2 km from any road, in an unset cell
            assertNull(PackRouter.plan(a, hole, roads, covered = inPack))
            assertNull("the start too", PackRouter.plan(hole, b, roads, covered = inPack))
        }
        // The same ends with a pack that carries no row are not refused for coverage.
        withCase(null) { packs ->
            val roads = { w: Double, s: Double, e: Double, n: Double -> packs.roads(w, s, e, n) }
            assertNotNull(PackRouter.plan(LatLon(5.005, 5.007), LatLon(5.005, 5.023), roads, covered = { p -> packs.has(p.lon, p.lat) }))
        }
    }

    @Test
    fun `an end the extract does not cover is refused even where a road would snap`() {
        // The roads say there is a way; the coverage says the pack does not know that place.
        assertNull(PackRouter.plan(at(0f, 0f), at(1000f, 500f), { _, _, _, _ -> u }, covered = { it != at(1000f, 500f) }))
        assertNull(PackRouter.plan(at(0f, 0f), at(1000f, 500f), { _, _, _, _ -> u }, covered = { false }))
        assertNotNull(PackRouter.plan(at(0f, 0f), at(1000f, 500f), { _, _, _, _ -> u }, covered = { true }))
        assertNotNull("no ask is refused by default", PackRouter.plan(at(0f, 0f), at(1000f, 500f), { _, _, _, _ -> u }))
    }

    private fun closed(name: String, vararg p: Pair<Float, Float>) =
        PackRoad(p.map { at(it.first, it.second) }, "service", name, 0, 0, closed = true)

    @Test
    fun `a closed road is not a way through, but the pack route that opens it is the same without the mark (T-1049)`() {
        // Short Lane is the straight way; the loop north of it is longer. The ends are where the lane meets the loop's streets.
        val loop = road("Loop Road", "residential", 0f to 0f, 0f to 300f, 1000f to 300f, 1000f to 0f)
        val lane = closed("Short Lane", 0f to 0f, 1000f to 0f)
        val open = PackRoad(lane.line, "service", "Short Lane", 0, 0)
        assertEquals("unmarked, it is the way", 1000f, route(at(0f, 0f), at(1000f, 0f), listOf(loop, open))!!.route.distanceM, 5f)
        val around = route(at(0f, 0f), at(1000f, 0f), listOf(loop, lane))!!.route
        assertEquals("marked, round the loop", 1600f, around.distanceM, 5f)
        assertTrue(passes(around, 0f, 300f) && passes(around, 1000f, 300f))
    }

    @Test
    fun `a closed road that is the only way between two public streets answers nothing (T-1049)`() {
        val a = road("West Street", "residential", 0f to 0f, 500f to 0f)
        val b = road("East Street", "residential", 1000f to 0f, 1500f to 0f)
        val lane = closed("Private Lane", 500f to 0f, 1000f to 0f)
        assertNull(route(at(0f, 0f), at(1500f, 0f), listOf(a, b, lane)))
        val open = PackRoad(lane.line, "service", "Private Lane", 0, 0)
        assertNotNull("the control: unmarked it is driven", route(at(0f, 0f), at(1500f, 0f), listOf(a, b, open)))
    }

    @Test
    fun `a destination, or a start, on a closed road is still reached (T-1049)`() {
        val street = road("Main Street", "residential", 0f to 0f, 500f to 0f, 1000f to 0f) // roads join at shared vertices
        val drive = closed("Private Drive", 500f to 0f, 500f to 200f)
        val there = route(at(0f, 0f), at(500f, 120f), listOf(street, drive))!!.route
        assertTrue("up the drive, to its door", passes(there, 500f, 0f) && passes(there, 500f, 120f))
        assertEquals(620f, there.distanceM, 5f)
        val out = route(at(500f, 120f), at(1000f, 0f), listOf(street, drive))!!.route
        assertEquals(620f, out.distanceM, 5f)
    }

    @Test
    fun `an end at the foot of a closed road is on the street, so the road is not a way for it (T-1049)`() {
        // The end sits at the junction: the street is as near as the drive, and the drive is not the end's own road.
        // Up the drive and along Back Street is 700 m; the street way round is longer.
        val street = road("Main Street", "residential", 500f to 0f, 1000f to -300f, 1000f to 200f)
        val drive = closed("Private Drive", 500f to 0f, 500f to 200f)
        val back = road("Back Street", "residential", 500f to 200f, 1000f to 200f)
        val r = route(at(500f, 0f), at(1000f, 200f), listOf(street, drive, back))!!.route
        assertEquals("by the street, not up the drive", 583f + 500f, r.distanceM, 5f)
        assertFalse(passes(r, 500f, 200f))
        // The control: the same drive unmarked is the way.
        val open = PackRoad(drive.line, "service", "Private Drive", 0, 0)
        assertEquals(700f, route(at(500f, 0f), at(1000f, 200f), listOf(street, open, back))!!.route.distanceM, 5f)
    }

    @Test
    fun `a near destination ignores the kept line`() {
        val out = PackRouter.plan(at(2000f, 80f), at(3000f, 0f), { _, _, _, _ -> listOf(longRoad, side) }, kept = kept())!!
        assertEquals(1080f, out.route.distanceM, 10f)
    }
}
