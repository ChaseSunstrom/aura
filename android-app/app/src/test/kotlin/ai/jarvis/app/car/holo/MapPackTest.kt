package ai.jarvis.app.car.holo

import androidx.test.core.app.ApplicationProvider
import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * The phone's reader of a map pack (T-1017): the file `tests/contracts/map-pack.json` describes,
 * which the house builds (T-0979) and the phone copies. Reads what the house's buildings call
 * would answer, so the ground draws the same from either.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class MapPackTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private lateinit var dir: File

    // Strelsau, Ruritania: made up, near 5, 5.
    private val bbox = doubleArrayOf(4.9, 4.9, 5.1, 5.1)
    private val near = building(5.001, 5.001, 5.002, 5.002, h = 12.0)

    private fun building(w: Double, s: Double, e: Double, n: Double, h: Double? = 9.0) = MapPackFixture.building(w, s, e, n, h)

    @Before
    fun fresh() {
        dir = File(context.cacheDir, "mappack-${System.nanoTime()}").also { it.mkdirs() }
    }

    private fun pack(vararg f: MapPackFixture.Feature, name: String = "a.pack", box: DoubleArray = bbox, built: Long = 100L) =
        MapPackFixture.write(File(dir, name), box, built, f.toList())

    @Test
    fun `the cell numbers are the contract's`() {
        val rows = MapPackFixture.contract.getJSONObject("pack").getJSONArray("cells")
        assertTrue(rows.length() >= 8)
        for (i in 0 until rows.length()) {
            val r = rows.getJSONArray(i)
            assertEquals("cell of ${r.getDouble(0)}, ${r.getDouble(1)}", r.getLong(2), MapPack.cell(r.getDouble(0), r.getDouble(1)))
        }
    }

    @Test
    fun `the format number is the contract's`() {
        assertEquals(MapPackFixture.format, MapPack.FORMAT)
    }

    @Test
    fun `it reads the footprints in a box, with their height, and leaves out those beyond it`() {
        val far = building(5.05, 5.05, 5.051, 5.051)
        val flat = building(5.003, 5.003, 5.004, 5.004, h = null)
        val p = MapPack.open(pack(near, far, flat))!!
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!
        assertEquals(2, got.buildings.size)
        // Nearest the box's centre first (T-1198): the flat one stands closer than the one by the corner.
        assertEquals(12f, got.buildings[1].heightM)
        assertNull(got.buildings[0].heightM)
        // [lon, lat] on the wire, a LatLon in the scene.
        assertEquals(5.001, got.buildings[1].ring[0].lon, 1e-9)
        assertEquals(5.001, got.buildings[1].ring[0].lat, 1e-9)
        assertEquals(5, got.buildings[1].ring.size)
        p.close()
    }

    @Test
    fun `it reads the roads with their kind and name, whole, where the box touches them`() {
        val rose = MapPackFixture.road("Rose Street", 4.995 to 5.005, 5.005 to 5.005, 5.015 to 5.005, extra = """{"oneway":-1,"kph":30}""")
        val lane = MapPackFixture.road("", 5.002 to 5.002, 5.003 to 5.003, kind = "service")
        val gone = MapPackFixture.road("Elsewhere Road", 5.06 to 5.06, 5.07 to 5.07)
        val p = MapPack.open(pack(near, rose, lane, gone))!!
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!
        assertEquals(listOf("residential", "service"), got.roads.map { it.kind })
        assertEquals("a road is whole, not cut at the box", 3, got.roads[0].line.size)
        assertEquals(4.995, got.roads[0].line[0].lon, 1e-9)

        val roads = p.roads(5.0, 5.0, 5.01, 5.01)
        assertEquals(listOf("Rose Street", ""), roads.map { it.name })
        assertEquals(-1, roads[0].oneway)
        assertEquals(30, roads[0].kph)
        assertEquals("no oneway and no limit are 0", 0, roads[1].oneway)
        assertEquals(0, roads[1].kph)
        assertEquals("service", roads[1].kind)
        p.close()
    }

    @Test
    fun `a road carries its route number, bridge, tunnel and layer, and a pack from before has none (T-1167)`() {
        val deck = MapPackFixture.road(
            "Spur Road", 5.002 to 5.002, 5.003 to 5.003,
            extra = """{"ref":" X 9;Y 12 ","bridge":true,"layer":1,"lanes":2}""",
        )
        val under = MapPackFixture.road("", 5.002 to 5.004, 5.003 to 5.004, extra = """{"tunnel":true,"layer":-1}""")
        // Wrong types are not read: a string for a flag, a blank or number ref, a layer out of range is clamped.
        val odd = MapPackFixture.road("", 5.002 to 5.006, 5.003 to 5.006, extra = """{"ref":5,"bridge":"yes","tunnel":1,"layer":"2"}""")
        val high = MapPackFixture.road("", 5.002 to 5.007, 5.003 to 5.007, extra = """{"ref":"  ","layer":99}""")
        val old = MapPackFixture.road("Rose Street", 5.002 to 5.008, 5.003 to 5.008, extra = """{"oneway":1,"kph":30}""")
        val p = MapPack.open(pack(near, deck, under, odd, high, old))!!
        // Read nearest first (T-1198); this test is about what each road carries, south to north.
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!.roads.sortedBy { it.line[0].lat }
        assertEquals(listOf("X 9;Y 12", null, null, null, null), got.map { it.ref })
        assertEquals(listOf(true, false, false, false, false), got.map { it.bridge })
        assertEquals(listOf(false, true, false, false, false), got.map { it.tunnel })
        assertEquals(listOf(1, -1, 0, MapsJson.MAX_LAYER, 0), got.map { it.layer })
        val roads = p.roads(5.0, 5.0, 5.01, 5.01).sortedBy { it.line[0].lat }
        assertEquals("X 9;Y 12", roads[0].ref)
        assertTrue(roads[0].bridge && roads[1].tunnel && roads[4].ref == null && !roads[4].bridge && roads[4].layer == 0)
        p.close()
    }

    @Test
    fun `a road a car may not pass is marked by the contract's values, stays in the pack and is drawn as before (T-1049)`() {
        val access = MapPackFixture.contract.getJSONObject("pack").getJSONObject("access")
        val closed = access.getJSONArray("closed").let { a -> (0 until a.length()).map { a.getString(it) } }
        assertEquals(closed, MapPack.ACCESS_CLOSED)
        assertEquals(access.getString("key"), MapPack.ACCESS_KEY)
        val priv = MapPackFixture.road("Drive", 5.002 to 5.002, 5.003 to 5.003, extra = """{"access":"private"}""")
        val no = MapPackFixture.road("", 5.002 to 5.004, 5.003 to 5.004, extra = """{"access":"no"}""")
        // Any other value, or a wrong type, is a road like any other.
        val dest = MapPackFixture.road("", 5.002 to 5.006, 5.003 to 5.006, extra = """{"access":"destination"}""")
        val odd = MapPackFixture.road("", 5.002 to 5.007, 5.003 to 5.007, extra = """{"access":true}""")
        val old = MapPackFixture.road("Rose Street", 5.002 to 5.008, 5.003 to 5.008, extra = """{"oneway":1}""")
        val p = MapPack.open(pack(near, priv, no, dest, odd, old))!!
        val roads = p.roads(5.0, 5.0, 5.01, 5.01).sortedBy { it.line[0].lat }
        assertEquals(listOf(true, true, false, false, false), roads.map { it.closed })
        // Marked, not dropped: the ground draws all five, as it did.
        assertEquals(5, p.near(5.0, 5.0, 5.01, 5.01)!!.roads.size)
        p.close()
    }

    @Test
    fun `a road carries what its guide sign says, cut at the contract's caps, from the pack and from the house (T-1183)`() {
        val tags = MapPackFixture.contract.getJSONObject("pack").getJSONObject("roadTags")
        assertEquals(tags.getInt("destination"), MapsJson.MAX_DESTINATION)
        assertEquals(tags.getInt("destination:ref"), MapsJson.MAX_REF)
        assertEquals(tags.getInt("junction:ref"), MapsJson.MAX_JUNCTION_REF)
        assertEquals(tags.getInt("traffic_sign"), MapsJson.MAX_TRAFFIC_SIGN)
        val said = """{"destination":" Northtown;Eastville ","destination:ref":"I 99;US 4","junction:ref":"12B","traffic_sign":"city_limit"}"""
        val link = MapPackFixture.road("", 5.002 to 5.002, 5.003 to 5.003, kind = "motorway_link", extra = said)
        // A number is no text, a long one is cut, and a name that hides characters is left out.
        val odd = MapPackFixture.road(
            "", 5.002 to 5.004, 5.003 to 5.004, kind = "motorway_link",
            extra = """{"destination":"${"N".repeat(500)}","destination:ref":7,"junction:ref":"1‮2"}""",
        )
        val old = MapPackFixture.road("Rose Street", 5.002 to 5.008, 5.003 to 5.008, extra = """{"oneway":1}""")
        val p = MapPack.open(pack(near, link, odd, old))!!
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!.roads.sortedBy { it.line[0].lat } // south to north: the read is nearest first (T-1198)
        assertEquals(listOf("Northtown;Eastville", "N".repeat(120), null), got.map { it.destination })
        assertEquals(listOf("I 99;US 4", null, null), got.map { it.destinationRef })
        assertEquals(listOf("12B", null, null), got.map { it.junctionRef })
        assertEquals(listOf("city_limit", null, null), got.map { it.trafficSign })
        p.close()
        val house = MapsJson.roads(org.json.JSONObject("""{"roads":[{"line":[[5.0,5.0],[5.001,5.0]],"kind":"motorway_link",
            "destination":"Northtown","destination:ref":"I 99","junction:ref":"12B","traffic_sign":"x"},
            {"line":[[5.0,5.1],[5.001,5.1]],"kind":"residential","destination":5}]}"""))
        assertEquals(listOf("Northtown", null), house.map { it.destination })
        assertEquals(listOf("I 99", null), house.map { it.destinationRef })
        assertEquals("12B", house[0].junctionRef)
    }

    @Test
    fun `a road carries the turns of its lanes whole or not at all, by the contract's cases, from the pack and from the house (T-1235)`() {
        val lanes = MapPackFixture.contract.getJSONObject("pack").getJSONObject("turnLanes")
        assertEquals(lanes.getInt("max"), MapsJson.MAX_TURN_LANES)
        assertEquals(lanes.getInt("max_lanes"), MapsJson.MAX_TURN_LANES_LANES)
        val keys = lanes.getJSONArray("keys")
        assertEquals(listOf("turn:lanes", "turn:lanes:forward", "turn:lanes:backward"), (0 until keys.length()).map { keys.getString(it) })
        val cases = lanes.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val want = if (case.isNull("out")) null else case.getString("out")
            assertEquals("$case", want, MapsJson.turnLanesOf(org.json.JSONObject().put("turn:lanes", case.opt("in")), "turn:lanes"))
        }
        // From the pack: the three keys, each its own road's field, and a pack from before has none.
        val said = """{"oneway":1,"turn:lanes":"left|through","turn:lanes:forward":"through|right","turn:lanes:backward":"Left"}"""
        val laned = MapPackFixture.road("Rose Street", 5.002 to 5.002, 5.003 to 5.003, extra = said)
        val old = MapPackFixture.road("Elm Street", 5.002 to 5.008, 5.003 to 5.008, extra = """{"oneway":1}""")
        val p = MapPack.open(pack(near, laned, old))!!
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!.roads.sortedBy { it.line[0].lat }
        assertEquals(listOf("left|through", null), got.map { it.turnLanes })
        assertEquals(listOf("through|right", null), got.map { it.turnLanesForward })
        assertEquals(listOf<String?>(null, null), got.map { it.turnLanesBackward })
        p.close()
        val house = MapsJson.roads(org.json.JSONObject("""{"roads":[{"line":[[5.0,5.0],[5.001,5.0]],"kind":"primary","turn:lanes":"left|right"},
            {"line":[[5.0,5.1],[5.001,5.1]],"kind":"primary","turn:lanes":5}]}"""))
        assertEquals(listOf("left|right", null), house.map { it.turnLanes })
    }

    @Test
    fun `a box a pack does not cover answers nothing, one it covers with nothing in it answers empty`() {
        val p = MapPack.open(pack(near))!!
        assertNull("beyond the pack's box", p.near(6.0, 6.0, 6.01, 6.01))
        val empty = p.near(5.08, 5.08, 5.09, 5.09)!!
        assertTrue(empty.buildings.isEmpty() && empty.roads.isEmpty())
        p.close()
    }

    @Test
    fun `a coverage grid over the contract's cap is not read, so the bbox answers (T-1048)`() {
        val cap = MapPackFixture.contract.getJSONObject("pack").getJSONObject("coverage").getLong("max_cells")
        assertEquals(4_000_000L, cap)
        // 2001 by 2000 cells, one over the cap, with a row of exactly the right size that says "nothing is covered".
        val big = doubleArrayOf(5.0, 5.0, 25.0, 24.99)
        val row = android.util.Base64.encodeToString(ByteArray((2001 * 2000 + 7) / 8), android.util.Base64.NO_WRAP)
        val file = MapPackFixture.write(File(dir, "big.pack"), big, 100L, listOf(near), coverage = row)
        val p = MapPack.open(file)!!
        assertTrue("the grid is over the cap: the bbox answers", p.covers(10.0, 10.0, 10.0, 10.0))
        p.close()
    }

    @Test
    fun `a footprint over several cells is read once, whichever rows the box spans`() {
        val wide = building(5.001, 5.001, 5.034, 5.034)
        val p = MapPack.open(pack(wide))!!
        assertEquals(1, p.near(5.0, 5.0, 5.04, 5.04)!!.buildings.size)
        assertEquals(1, p.near(5.02, 5.02, 5.025, 5.025)!!.buildings.size)
        p.close()
    }

    @Test
    fun `it answers at most as many as the house does`() {
        val many = (0 until 5003).map { i -> building(5.0 + i * 1e-5, 5.0, 5.0 + i * 1e-5 + 5e-6, 5.00001) }
        val p = MapPack.open(pack(*many.toTypedArray()))!!
        assertEquals(5000, p.near(5.0, 5.0, 5.06, 5.01)!!.buildings.size)
        p.close()
    }

    // A pack is a file the phone was sent: no row of it may take the heap.

    private fun spread(kind: String, pts: Int, tag: String = "") = MapPackFixture.Feature(
        kind, (0 until pts).map { doubleArrayOf(5.003 + (it % 2) * 0.001, 5.003) }, if (kind == "b") 9.0 else null, tag,
    )

    @Test
    fun `a row with a geometry over the size cap is skipped, and the others still answer`() {
        val huge = spread("b", 5000) // about 70 KB of text
        val hugeRoad = spread("r", 5000, "residential")
        val p = MapPack.open(pack(near, huge, hugeRoad, MapPackFixture.road("Rose Street", 5.0 to 5.005, 5.005 to 5.005)))!!
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!
        assertEquals(1, got.buildings.size)
        assertEquals(1, got.roads.size)
        assertEquals(listOf("Rose Street"), p.roads(5.0, 5.0, 5.01, 5.01).map { it.name })
        p.close()
    }

    @Test
    fun `the road limits are the contract's, which the house's writer reads too (T-1219)`() {
        val lim = MapPackFixture.contract.getJSONObject("pack").getJSONObject("roadLimits")
        assertEquals(lim.getInt("max_points"), MapPack.MAX_POINTS)
        assertEquals(lim.getInt("max_geom_chars"), MapPack.MAX_GEOM_CHARS)
        // numbers per point and range: the edge is read, a third number or a step past it skips the road.
        val n = lim.getInt("numbers_per_point")
        val lon = lim.getDouble("max_lon")
        val lat = lim.getDouble("max_lat")
        fun road(vararg pts: String) = MapPackFixture.Feature(
            "r", listOf(doubleArrayOf(5.003, 5.003), doubleArrayOf(5.004, 5.004)), null, "path", geom = pts.joinToString(",", "[", "]"),
        )
        val ok = road("[${"5.003,".repeat(n - 1)}5.003]", "[${"5.004,".repeat(n - 1)}5.004]")
        val three = road("[${"5.003,".repeat(n)}5.003]", "[${"5.004,".repeat(n)}5.004]")
        val short = road("[${"5.003,".repeat(n - 2)}5.003]", "[${"5.004,".repeat(n - 2)}5.004]")
        val pastLon = road("[${lon + 0.001},5.003]", "[5.004,5.004]")
        val pastLat = road("[5.003,${lat + 0.001}]", "[5.004,5.004]")
        for ((i, f) in listOf(ok, three, short, pastLon, pastLat).withIndex()) {
            val p = MapPack.open(MapPackFixture.write(File(dir, "lim$i.pack"), bbox, 100L, listOf(f)))!!
            assertEquals("case $i", i == 0, p.roads(5.0, 5.0, 5.01, 5.01).isNotEmpty())
            p.close()
        }
    }

    @Test
    fun `a line with more points than the cap is skipped, not cut`() {
        val p = MapPack.open(pack(near, spread("b", MapPack.MAX_POINTS + 1), spread("r", MapPack.MAX_POINTS + 1, "path")))!!
        val got = p.near(5.0, 5.0, 5.01, 5.01)!!
        assertEquals(1, got.buildings.size)
        assertTrue(got.roads.isEmpty())
        // At the cap it is read.
        val ok = MapPack.open(pack(spread("r", MapPack.MAX_POINTS, "path"), name = "b.pack"))!!
        assertEquals(MapPack.MAX_POINTS, ok.near(5.0, 5.0, 5.01, 5.01)!!.roads.single().line.size)
        p.close()
        ok.close()
    }

    @Test
    fun `one ask reads no more points than the budget, however many rows are in the box`() {
        val roads = (0 until MapPack.MAX_POINTS_ASK / MapPack.MAX_POINTS + 20).map { spread("r", MapPack.MAX_POINTS, "path") }
        val p = MapPack.open(pack(*roads.toTypedArray()))!!
        val got = p.roads(5.0, 5.0, 5.01, 5.01)
        assertTrue("some are read: ${got.size}", got.size > 100)
        assertTrue("the budget held: ${got.size}", got.size * MapPack.MAX_POINTS <= MapPack.MAX_POINTS_ASK + MapPack.MAX_POINTS)
        p.close()
    }

    @Test
    fun `a box wider than the house answers is refused, so one ask cannot read a state`() {
        val p = MapPack.open(pack(near))!!
        assertNull(p.near(4.9, 4.9, 5.1, 5.1))
        assertNull(p.near(Double.NaN, 5.0, 5.01, 5.01))
        assertNull(p.near(5.01, 5.0, 5.0, 5.01))
        p.close()
    }

    @Test
    fun `a file that is not a pack this reads is refused`() {
        val junk = File(dir, "junk.pack").also { it.writeBytes(ByteArray(4096) { (it * 7).toByte() }) }
        assertNull("garbage", MapPack.open(junk))
        assertNull("missing", MapPack.open(File(dir, "none.pack")))
        assertNull("empty", MapPack.open(File(dir, "empty.pack").also { it.writeBytes(ByteArray(0)) }))
        assertNull("another format", MapPack.open(MapPackFixture.write(File(dir, "f2.pack"), bbox, 1L, listOf(near), format = MapPackFixture.format + 1)))
        assertNotNull("this format", MapPack.open(MapPackFixture.write(File(dir, "f1.pack"), bbox, 1L, listOf(near)))?.also { it.close() })
    }

    @Test
    fun `a pack cut short answers nothing rather than throwing`() {
        val file = pack(near)
        val cut = File(dir, "cut.pack").also { it.writeBytes(file.readBytes().copyOf(file.length().toInt() / 2)) }
        val p = MapPack.open(cut)
        // Cut in the middle, the file may refuse to open or fail its first read; neither throws.
        assertNull(p?.near(5.0, 5.0, 5.01, 5.01))
        p?.close()
    }

    @Test
    fun `it keeps what the pack says of itself and reads from another thread`() {
        val p = MapPack.open(pack(near, built = 1234L))!!
        assertEquals(1234L, p.built)
        assertEquals("Ruritania", p.name)
        var got: NearMap? = null
        Thread { got = p.near(5.0, 5.0, 5.01, 5.01) }.also { it.start() }.join()
        assertEquals(1, got!!.buildings.size)
        p.close()
    }

    // MapPacks: the folder the sync fills.

    @Test
    fun `a folder of packs answers from the ones that cover the box, and from none when none does`() {
        pack(near, name = "a.pack")
        pack(building(5.5, 5.5, 5.501, 5.501), name = "b.pack", box = doubleArrayOf(5.4, 5.4, 5.6, 5.6))
        File(dir, "c.pack.part").writeBytes(ByteArray(10))
        File(dir, "notes.txt").writeText("x")
        val packs = MapPacks(dir)
        assertEquals(1, packs.near(5.0, 5.0, 5.01, 5.01)!!.buildings.size)
        assertEquals(1, packs.near(5.5, 5.5, 5.51, 5.51)!!.buildings.size)
        assertNull(packs.near(7.0, 7.0, 7.01, 7.01))
        packs.close()
    }

    @Test
    fun `a pack copied in later, or replaced, is read without a restart`() {
        val packs = MapPacks(dir)
        assertNull("nothing yet", packs.near(5.0, 5.0, 5.01, 5.01))
        pack(near)
        assertEquals(1, packs.near(5.0, 5.0, 5.01, 5.01)!!.buildings.size)
        pack(near, building(5.004, 5.004, 5.005, 5.005), built = 200L)
        assertEquals(2, packs.near(5.0, 5.0, 5.01, 5.01)!!.buildings.size)
        File(dir, "a.pack").delete()
        assertNull("deleted", packs.near(5.0, 5.0, 5.01, 5.01))
        packs.close()
    }

    @Test
    fun `an unreadable file among the packs does not spoil the others, and closing is repeatable`() {
        File(dir, "bad.pack").writeBytes(ByteArray(2048) { 9 })
        pack(near)
        val packs = MapPacks(dir)
        assertEquals(1, packs.near(5.0, 5.0, 5.01, 5.01)!!.buildings.size)
        packs.close()
        packs.close()
        // A reader that was mid-ask when the feed stopped must not fill the held packs again.
        assertNull("nothing opens after a close", packs.near(5.0, 5.0, 5.01, 5.01))
        packs.reopen()
        assertEquals("usable again after reopen", 1, packs.near(5.0, 5.0, 5.01, 5.01)!!.buildings.size)
        packs.close()
    }

    @Test
    fun `a folder that is not there has no packs`() {
        assertNull(MapPacks(File(dir, "nowhere")).near(5.0, 5.0, 5.01, 5.01))
        assertFalse(MapPacks(File(dir, "nowhere")).roads(5.0, 5.0, 5.01, 5.01).isNotEmpty())
    }

    // T-1029: a read that left roads out looked whole to the router, which then planned a long
    // way round over the hole.
    @Test
    fun `a read that must drop features keeps the nearest, the contract's case, whatever their ids (T-1198)`() {
        val case = MapPackFixture.contract.getJSONObject("pack").getJSONObject("capped")
        val box = case.getJSONArray("box").let { b -> DoubleArray(4) { b.getDouble(it) } }
        val feats = case.getJSONArray("features").let { f ->
            (0 until f.length()).map { i -> f.getJSONArray(i).let { r -> building(r.getDouble(0), r.getDouble(1), r.getDouble(2), r.getDouble(3)) } }
        }
        val p = MapPack.open(MapPackFixture.write(File(dir, "capped.pack"), box, 100L, feats))!!
        val got = p.near(box[0], box[1], box[2], box[3], case.getInt("limit"))!!.buildings
        val want = case.getJSONArray("want").let { w -> (0 until w.length()).map { w.getInt(it) } }
        val firsts = case.getJSONArray("features").let { f -> (0 until f.length()).map { f.getJSONArray(it).getDouble(0) to f.getJSONArray(it).getDouble(1) } }
        assertEquals(want, got.map { b -> firsts.indexOf(b.ring[0].lon to b.ring[0].lat) + 1 })
        p.close()
    }

    @Test
    fun `roads read short say so, whether a line was skipped, too big to read, or the budget ran out`() {
        val whole = MapPack.open(pack(spread("r", 10, "path")))!!
        assertTrue(whole.roads(5.0, 5.0, 5.01, 5.01) !is CutRoads)
        whole.close()
        val skipped = MapPack.open(pack(spread("r", 10, "path"), spread("r", MapPack.MAX_POINTS + 1, "path"), name = "b.pack"))!!
        val got = skipped.roads(5.0, 5.0, 5.01, 5.01)
        assertEquals(1, got.size)
        assertTrue("a line over the point cap", got is CutRoads)
        skipped.close()
        // 2000 points of 20 characters is under the point cap and over the geometry's size cap.
        val wide = MapPackFixture.Feature("r", (0 until MapPack.MAX_POINTS).map { doubleArrayOf(5.003 + it * 1e-9 + 0.000000123456, 5.003000123456) }, null, "path")
        val big = MapPack.open(pack(spread("r", 10, "path"), wide, name = "c.pack"))!!
        assertTrue("a geometry too big to read", big.roads(5.0, 5.0, 5.01, 5.01) is CutRoads)
        big.close()
        val many = (0 until MapPack.MAX_POINTS_ASK / MapPack.MAX_POINTS + 20).map { spread("r", MapPack.MAX_POINTS, "path") }
        val full = MapPack.open(pack(*many.toTypedArray(), name = "d.pack"))!!
        assertTrue("the points budget", full.roads(5.0, 5.0, 5.01, 5.01) is CutRoads)
        full.close()
        // A folder of packs passes it on.
        val packs = MapPacks(dir)
        assertTrue(packs.roads(5.0, 5.0, 5.01, 5.01) is CutRoads)
        packs.close()
    }
}
