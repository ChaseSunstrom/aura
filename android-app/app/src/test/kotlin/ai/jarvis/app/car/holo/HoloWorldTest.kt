package ai.jarvis.app.car.holo

import ai.jarvis.app.ui.theme.JarvisTokens.Color
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.json.JSONObject
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
import kotlin.math.abs

/**
 * The place drawn as it is (T-1060; the owner, 2026-11-11: "instead of buildings being blocks,
 * can we have the actual shape and color of them and all of their details ... trees, road
 * lines, parking spots, light posts, trees, construction"): what the phone reads of a
 * street's look from a pack and from the house, and what the scene makes of it.
 *
 * Does not cover how it looks (the goldens in CarNavGoldenTest are looked at) or a real
 * drive's frame rate.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class HoloWorldTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private val plane = GeoPlane(5.0, 5.0)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private fun box(e0: Float, n0: Float, e1: Float, n1: Float) = listOf(at(e0, n0), at(e1, n0), at(e1, n1), at(e0, n1), at(e0, n0))

    private val cam = HoloCamera().apply { resize(1280f, 720f, 1.5f) }

    private fun frame(scene: HoloScene): HoloFrame = HoloFrame().also { scene.build(it, cam, 5.0, 5.0, 0f, 1_000L) }

    /** A tree's lit crown is one of eight greens, by its size (T-1106). */
    private val canopies = (0..7).map { HoloWorld.vary(Color.WORLD_CANOPY, it) }.toSet()

    private fun HoloFrame.solids(color: Int) = (0 until solidCount).count { solidColor[it] == color }

    // --- reading ---------------------------------------------------------------------

    @Test
    fun `a pack's building keeps its colours, roof, kind and parts, a road its lanes, and the details come by kind`() {
        val f = MapPackFixture
        val file = f.write(
            File(context.cacheDir, "world-${System.nanoTime()}/a.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 100L,
            listOf(
                MapPackFixture.Feature(
                    "b", f.building(5.001, 5.001, 5.002, 5.002).points, 12.0, "osm",
                    extra = """{"colour":"#ff0000","roof_colour":"#808080","roof_shape":"gabled","kind":"house","min_height_m":3.0,"part":true}""",
                ),
                f.building(5.003, 5.003, 5.004, 5.004),
                f.road("Mall", 5.0 to 5.0, 5.0 to 5.005, kind = "primary", extra = """{"lanes":4,"kph":50}"""),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.001)), tag = "tree"),
                MapPackFixture.Feature("d", f.building(5.005, 5.005, 5.006, 5.006).points, tag = "parking"),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.001), doubleArrayOf(5.002, 5.002)), tag = "tree_row"),
                // A kind from a newer house, and a known kind in the wrong shape: skipped, not a failure.
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.001)), tag = "ufo"),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.001)), tag = "parking"),
            ),
        )
        val got = MapPack.open(file)!!.near(5.0, 5.0, 5.01, 5.01)!!
        // The read is nearest the box's centre first (T-1198); here the buildings are taken south to north.
        val buildings = got.buildings.sortedBy { it.ring[0].lat }
        val look = buildings[0].look
        assertEquals(0xFFFF0000.toInt(), look.colour)
        assertEquals(0xFF808080.toInt(), look.roofColour)
        assertEquals("gabled", look.roofShape)
        assertEquals("house", look.kind)
        assertEquals(3f, look.minM)
        assertTrue(look.part)
        assertTrue("a plain building has no look", buildings[1].look === HoloLook.NONE)
        assertEquals(4, got.roads.single().lanes)
        assertEquals(listOf("tree", "parking", "tree_row").sorted(), got.details!!.map { it.kind }.sorted())
    }

    @Test
    fun `the kinds are the contract's`() {
        val kinds = MapPackFixture.contract.getJSONObject("pack").getJSONObject("details")
        fun set(key: String) = kinds.getJSONArray(key).let { a -> (0 until a.length()).map { a.getString(it) }.toSet() }
        assertEquals(set("point"), HoloDetail.POINTS)
        assertEquals(set("line"), HoloDetail.LINES)
        assertEquals(set("area"), HoloDetail.AREAS)
    }

    @Test
    fun `the house's answer reads the same, and one that says nothing of details does not clear them`() {
        val answer = JSONObject(
            """{"buildings":[{"ring":[[5.001,5.001],[5.002,5.001],[5.002,5.002],[5.001,5.001]],"height_m":9,"source":"osm",
               "colour":"#00ff00","roof_colour":"javascript:1","kind":"retail"}],
               "roads":[{"line":[[5.0,5.0],[5.0,5.005]],"kind":"primary","name":"Mall","lanes":400}],
               "details":[{"kind":"lamp","geom":[[5.001,5.001]]},{"kind":"lamp","geom":"x"},{"kind":"water","geom":[[5.0,5.0]]}],
               "truncated":false}""",
        )
        val near = MapsJson.near(answer)
        assertEquals(0xFF00FF00.toInt(), near.buildings[0].look.colour)
        assertEquals("a colour that is not six hex digits is no colour", 0, near.buildings[0].look.roofColour)
        assertEquals("retail", near.buildings[0].look.kind)
        assertEquals(MapsJson.MAX_LANES, near.roads[0].lanes)
        assertEquals(listOf("lamp"), near.details!!.map { it.kind })
        answer.remove("details")
        assertNull(MapsJson.near(answer).details)
    }

    @Test
    fun `the house's roads carry ref, bridge, tunnel and layer, and an answer from before has none (T-1167)`() {
        val near = MapsJson.near(JSONObject(
            """{"buildings":[],"roads":[
               {"line":[[5.0,5.0],[5.0,5.005]],"kind":"motorway","name":"Spur","ref":" X 9;Y 12 ","bridge":true,"layer":1},
               {"line":[[5.0,5.0],[5.005,5.0]],"kind":"primary","tunnel":true,"layer":-1},
               {"line":[[5.0,5.0],[5.006,5.0]],"kind":"primary","ref":5,"bridge":"yes","layer":"2"},
               {"line":[[5.0,5.0],[5.007,5.0]],"kind":"primary","ref":"  ","layer":99},
               {"line":[[5.0,5.0],[5.008,5.0]],"kind":"primary","name":"Plain"}]}""",
        ))
        assertEquals(listOf("X 9;Y 12", null, null, null, null), near.roads.map { it.ref })
        assertEquals(listOf(true, false, false, false, false), near.roads.map { it.bridge })
        assertEquals(listOf(false, true, false, false, false), near.roads.map { it.tunnel })
        assertEquals(listOf(1, -1, 0, MapsJson.MAX_LAYER, 0), near.roads.map { it.layer })
        val road = HoloRoad(near.roads[0].line, "primary")
        assertTrue(road.ref == null && !road.bridge && !road.tunnel && road.layer == 0)
    }

    // --- the solid layer -------------------------------------------------------------

    @Test
    fun `solids are ordered far to near, the ground first, and one distance keeps the order given`() {
        val f = HoloFrame()
        fun add(color: Int, depth: Float) {
            f.beginPoly(); f.point(0f, 0f); f.point(1f, 0f); f.point(1f, 1f); f.endSolid(color, depth)
        }
        add(1, 10f); add(2, HoloFrame.FLAT); add(3, 80f); add(4, 10f); add(5, HoloFrame.FLAT); add(6, 10.1f)
        f.order()
        assertEquals(listOf(2, 5, 3, 1, 4, 6), (0 until f.solidCount).map { f.solidColor[f.solidOrder[it]] })
        f.clear()
        assertEquals(0, f.solidCount)
    }

    // --- the scene -------------------------------------------------------------------

    private fun scene() = HoloScene().apply {
        world.hazy = false
        setRoads(listOf(
            HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 4),
            HoloRoad(listOf(at(-60f, 40f), at(60f, 40f)), "residential"),
            HoloRoad(listOf(at(20f, 0f), at(20f, 30f)), "service"),
        ))
        setBuildings(listOf(
            HoloBuilding(box(14f, 20f, 30f, 36f), 12f, HoloLook(colour = 0xFFFF0000.toInt(), roofColour = 0xFF0000FF.toInt())),
            HoloBuilding(box(-30f, 20f, -14f, 30f), 6f, HoloLook(kind = "house")),
            HoloBuilding(box(-30f, 50f, -14f, 66f), 9f),
        ))
        setDetails(listOf(
            HoloDetail("tree", listOf(at(-9f, 30f))),
            HoloDetail("tree_row", listOf(at(9f, 50f), at(9f, 86f))),
            HoloDetail("lamp", listOf(at(8f, 25f))),
            HoloDetail("signal", listOf(at(8f, 38f))),
            HoloDetail("crossing", listOf(at(0f, 34f))),
            HoloDetail("water", box(40f, 40f, 80f, 80f)),
            HoloDetail("parking", box(-60f, 0f, -36f, 18f)),
            HoloDetail("bay", box(-58f, 2f, -55f, 7f)),
            HoloDetail("construction", box(34f, 0f, 50f, 16f)),
            HoloDetail("roadworks", listOf(at(-40f, 44f), at(-20f, 44f))),
        ))
    }

    @Test
    fun `a roof above the camera is painted before the wall in front of it`() {
        // The 12 m block's roof is seen from below, through its front wall: the wall must cover it.
        val f = frame(scene())
        val painted = (0 until f.solidCount).map { f.solidColor[f.solidOrder[it]] }
        val nearestWall = painted.indexOfLast { c -> (c shr 16 and 0xFF) > 100 && (c shr 8 and 0xFF) == 0 && (c and 0xFF) == 0 }
        val roof = painted.indexOf(0xFF0000FF.toInt())
        assertTrue("no red wall or no blue roof", nearestWall >= 0 && roof >= 0)
        assertTrue("the roof was painted over its front wall", roof < nearestWall)
    }

    @Test
    fun `a flat roof is given before every wall of its building, so a tie cannot put it over one`() {
        // Ties in the frame's half-metre buckets keep the order given (T-1136): a side wall
        // at the roof's own distance was given first and the roof painted over its top.
        val f = frame(scene())
        val given = (0 until f.solidCount).map { f.solidColor[it] }
        val firstWall = given.indexOfFirst { c -> (c shr 16 and 0xFF) > 100 && (c shr 8 and 0xFF) == 0 && (c and 0xFF) == 0 }
        val roof = given.indexOf(0xFF0000FF.toInt())
        assertTrue("no red wall or no blue roof", firstWall >= 0 && roof >= 0)
        assertTrue("the roof was given after a wall of its building", roof < firstWall)
    }

    @Test
    fun `a building is a solid in its own colour with a roof and windows, not a wire block`() {
        val f = frame(scene())
        val reds = (0 until f.solidCount).map { f.solidColor[it] }.filter { c ->
            (c shr 16 and 0xFF) > 100 && (c shr 8 and 0xFF) == 0 && (c and 0xFF) == 0
        }
        assertTrue("no wall in the map's red", reds.isNotEmpty())
        assertTrue("walls turned from the sun are not darker", reds.toSet().size > 1 || reds.size == 1)
        assertEquals("the flat roof in the map's blue", 1, f.solids(0xFF0000FF.toInt()))
        val glass = (0 until f.solidCount).count { i ->
            val c = f.solidColor[i]
            (c and 0xFF) > (c shr 16 and 0xFF) && f.solidLen[i] == 4 && (c shr 16 and 0xFF) < 0x40 && c != Color.WORLD_WATER
        }
        assertTrue("no windows", glass > 4)
        // The house the map gives no colour is one of its kind's, and its roof rises to a point.
        val home = HoloWorld.wallFor("house")
        assertEquals(Color.WORLD_WALL_HOME, home)
        assertEquals(Color.WORLD_WALL, HoloWorld.wallFor(""))
        assertTrue("no pitched roof faces", (0 until f.solidCount).count { f.solidLen[it] == 3 } >= 3)
    }

    // --- T-1185: buildings in full detail, further out ---------------------------------

    private fun shoelace(xy: FloatArray, n: Int): Double {
        var a = 0.0
        for (k in 0 until n) { val j = (k + 1) % n; a += xy[k * 2].toDouble() * xy[j * 2 + 1] - xy[j * 2].toDouble() * xy[k * 2 + 1] }
        return Math.abs(a) / 2
    }

    /** The highest point on the screen (the least y) of everything a scene gives. */
    private fun roofPeak(scene: HoloScene): Float {
        val f = frame(scene)
        var top = Float.MAX_VALUE
        for (i in 0 until f.solidCount) for (p in f.solidStart[i] until f.solidStart[i] + f.solidLen[i]) top = minOf(top, f.pts[p * 2 + 1])
        return top
    }

    private fun lone(height: Float, look: HoloLook, north: Float = 40f) =
        HoloScene().apply { setBuildings(listOf(HoloBuilding(box(-10f, north, 10f, north + 10f), height, look))) }

    @Test
    fun `an outline with more corners than the limit keeps its shape, the least significant corners going first`() {
        // A 60 x 30 block with a notch, its walls also carrying a point every 3 m (collinear), and a
        // 400-point circle: striding down to 24 cut the block's real corners, this does not.
        val pts = ArrayList<Float>()
        fun side(x0: Float, y0: Float, x1: Float, y1: Float) { for (s in 0 until 20) { pts += x0 + (x1 - x0) * s / 20; pts += y0 + (y1 - y0) * s / 20 } }
        side(0f, 0f, 60f, 0f); side(60f, 0f, 60f, 30f); side(60f, 30f, 35f, 30f); side(35f, 30f, 35f, 18f)
        side(35f, 18f, 20f, 18f); side(20f, 18f, 20f, 30f); side(20f, 30f, 0f, 30f); side(0f, 30f, 0f, 0f)
        val block = pts.toFloatArray()
        val want = shoelace(block, block.size / 2)
        val kept = HoloWorld.thin(block, block.size / 2, HoloWorld.MAX_RING_CORNERS)
        assertTrue("kept $kept", kept <= HoloWorld.MAX_RING_CORNERS)
        assertEquals(want, shoelace(block, kept), want * 0.02)
        val corners = (0 until kept).map { block[it * 2] to block[it * 2 + 1] }
        for (c in listOf(35f to 18f, 20f to 18f, 35f to 30f, 20f to 30f)) assertTrue("lost $c", c in corners)
        val circle = FloatArray(800) { i -> val a = (i / 2) * 2 * Math.PI / 400; (50 * if (i % 2 == 0) Math.cos(a) else Math.sin(a)).toFloat() }
        val round = shoelace(circle, 400)
        assertEquals(round, shoelace(circle, HoloWorld.thin(circle, 400, HoloWorld.MAX_RING_CORNERS)), round * 0.02)
        // A ring within the limit is left alone.
        assertEquals(3, HoloWorld.thin(floatArrayOf(0f, 0f, 4f, 0f, 0f, 3f), 3, HoloWorld.MAX_RING_CORNERS))
    }

    @Test
    fun `a roof stands as high as the map says and its ridge runs across the way the slope faces`() {
        fun ridge(look: HoloLook) = lone(12f, look).also { frame(it) }.world.ridges.toList()
        fun near(want: List<Float>, got: List<Float>) = want.indices.forEach { assertEquals("ridge $got", want[it], got[it], 0.2f) }
        // Slope facing north: the ridge is the footprint's east-west middle line, a gable to its ends, a hip short of them.
        near(listOf(-10f, 45f, 10f, 45f), ridge(HoloLook(roofShape = "gabled", roofM = 3f, roofDir = 0f)))
        near(listOf(-5f, 45f, 5f, 45f), ridge(HoloLook(roofShape = "hipped", roofM = 3f, roofDir = 0f)))
        // No direction: across the longest wall (east-west here) is north-south, along it east-west.
        near(listOf(0f, 40f, 0f, 50f), ridge(HoloLook(roofShape = "gabled", roofOrient = "across")))
        near(listOf(-10f, 45f, 10f, 45f), ridge(HoloLook(roofShape = "gabled", roofOrient = "along")))
        assertTrue("a ridge with nothing mapped", ridge(HoloLook(roofShape = "gabled")).all { it.isNaN() })
        // A mapped roof height runs to the building's own top; without it the roof rose on top of it.
        val mapped = roofPeak(lone(12f, HoloLook(roofShape = "gabled", roofM = 2f, roofDir = 0f)))
        val guessed = roofPeak(lone(12f, HoloLook(roofShape = "gabled")))
        assertTrue("mapped $mapped, guessed $guessed", mapped > guessed)
        // A ridge is quads (a slope a side), not the pyramid's triangles.
        assertEquals(0, frame(lone(12f, HoloLook(roofShape = "gabled", roofM = 3f, roofDir = 0f))).let { f -> (0 until f.solidCount).count { f.solidLen[it] == 3 } })
    }

    @Test
    fun `a roof's height, direction and orientation are read from the pack and the house, and nonsense is no roof`() {
        val look = MapsJson.look(JSONObject("""{"roof_shape":"gabled","roof_height_m":4.5,"roof_dir":200,"roof_orient":"along"}"""))
        assertEquals(4.5f, look.roofM)
        assertEquals(200f, look.roofDir)
        assertEquals("along", look.roofOrient)
        val bad = MapsJson.look(JSONObject("""{"roof_height_m":900,"roof_dir":400,"roof_orient":"<script>"}"""))
        assertTrue("out-of-range roof keys are no look", bad === HoloLook.NONE)
        val file = MapPackFixture.write(
            File(context.cacheDir, "world-${System.nanoTime()}/a.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 100L,
            listOf(MapPackFixture.Feature("b", MapPackFixture.building(5.001, 5.001, 5.002, 5.002).points, 12.0, "osm", extra = """{"roof_height_m":3,"roof_dir":90}""")),
        )
        val got = MapPack.open(file)!!.near(5.0, 5.0, 5.01, 5.01)!!.buildings[0].look
        assertEquals(3f, got.roofM)
        assertEquals(90f, got.roofDir)
    }

    @Test
    fun `a building drawn in parts is not drawn twice, and a part that is only an annex leaves it`() {
        val whole = HoloBuilding(box(-20f, 60f, 20f, 100f), 12f)
        val parts = listOf(
            HoloBuilding(box(-20f, 60f, 0f, 100f), 12f, HoloLook(part = true)),
            HoloBuilding(box(0f, 60f, 20f, 100f), 20f, HoloLook(part = true)),
        )
        val covered = HoloScene().apply { setBuildings(listOf(whole) + parts) }
        frame(covered)
        assertEquals("the outline and its parts", 2, covered.world.counts[0])
        val annex = HoloScene().apply { setBuildings(listOf(whole, HoloBuilding(box(-20f, 60f, -16f, 64f), 6f, HoloLook(part = true)))) }
        frame(annex)
        assertEquals("a corner annex does not stand for the building", 2, annex.world.counts[0])
        // Parts with no outline are all there is to draw.
        val bare = HoloScene().apply { setBuildings(parts) }
        frame(bare)
        assertEquals(2, bare.world.counts[0])
    }

    @Test
    fun `a far building has its windows in rows, a floor each, and a farther one has none`() {
        fun rows(north: Float) = HoloScene().apply { setBuildings(listOf(HoloBuilding(box(-8f, north, 8f, north + 12f), 24f))) }.let { frame(it); it.world.rows }
        assertEquals("a near wall has its windows one by one", 0, rows(40f))
        assertTrue("no rows at 300 m", rows(300f) >= 7)
        assertTrue("no rows at 650 m", rows(650f) >= 7)
        assertEquals("past the rows", 0, rows(900f))
    }

    @Test
    fun `the ground has its water, parking and site, the streets their surface and paint`() {
        val f = frame(scene())
        assertEquals(1, f.solids(Color.WORLD_WATER))
        assertEquals(1, f.solids(Color.WORLD_PARKING))
        assertEquals(1, f.solids(Color.WORLD_CONSTRUCTION))
        assertEquals("two streets", 2, f.solids(Color.WORLD_ASPHALT))
        assertEquals("one driveway", 1, f.solids(Color.WORLD_ASPHALT_MINOR))
        // Two: the side street meets the four-lane road, so its centre line parts there (T-1182) and
        // is one run on either side of the mouth, not one run across the side street's surface.
        assertEquals("the line between opposing lanes of the four-lane road", 2, f.solids(Color.WORLD_MARKING_CENTRE))
        // Edges of both streets, lane dashes, the crossing's stripes and the bay's four sides.
        assertTrue("too little paint", f.solids(Color.WORLD_MARKING) > 4 + 10 + 4 + 4)
        assertTrue("cones and boards", f.solids(Color.WORLD_CONE) >= 4)
    }

    // --- T-1188: the land has its cover ------------------------------------------------

    private fun HoloFrame.first() = solidColor[solidOrder[0]]
    private fun channels(c: Int) = intArrayOf(c shr 16 and 0xFF, c shr 8 and 0xFF, c and 0xFF)

    @Test
    fun `bare ground is a land colour under everything, apart from tarmac by day and near-black at night`() {
        val night = frame(scene())
        assertEquals("one ground", 1, night.solids(Color.WORLD_GROUND))
        assertEquals("the ground is the first thing drawn", Color.WORLD_GROUND, night.first())
        assertTrue("near-black at night", channels(Color.WORLD_GROUND).all { it <= 0x30 })
        // By day the same light that lifts the tarmac lifts the land: they stay apart, and the land stays green.
        val sunny = scene().also { it.world.light(60.0) }
        val noon = frame(sunny)
        val land = HoloWorld.daylit(Color.WORLD_GROUND, HoloLight.daylight(60.0))
        val tarmac = HoloWorld.daylit(Color.WORLD_ASPHALT, HoloLight.daylight(60.0))
        assertEquals("the day's land", land, noon.first())
        assertTrue("land brighter than at night", channels(land).sum() > channels(Color.WORLD_GROUND).sum() + 60)
        assertTrue("land and tarmac are one grey by day", channels(land).zip(channels(tarmac)).maxOf { (a, b) -> Math.abs(a - b) } >= 12)
        assertTrue("land does not glow", channels(land).all { it < 0x90 } && channels(land)[1] > channels(land)[2])
        // No map of the place: nothing is drawn, the board's own grid stays what it was.
        val bare = HoloScene().apply { world.hazy = false }
        assertEquals(0, frame(bare).solids(Color.WORLD_GROUND))
    }

    @Test
    fun `farmland, residential and commercial land are tinted, under a park, and an unknown kind is ignored`() {
        val scene = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(mall))
            setDetails(listOf(
                // The park is given first: the cover is drawn under it anyway.
                HoloDetail("green", box(30f, 30f, 50f, 50f)),
                HoloDetail("field", box(20f, 20f, 100f, 100f)),
                HoloDetail("yard", box(-100f, 20f, -20f, 100f)),
                HoloDetail("paved", box(-100f, 120f, -20f, 200f)),
                HoloDetail("ufo", box(20f, 120f, 100f, 200f)),
            ))
        }
        val f = frame(scene)
        assertEquals(1, f.solids(Color.WORLD_FIELD))
        assertEquals(1, f.solids(Color.WORLD_YARD))
        assertEquals(1, f.solids(Color.WORLD_PAVED))
        assertEquals(1, f.solids(Color.WORLD_GREEN))
        assertEquals("an unknown kind is not a construction site", 0, f.solids(Color.WORLD_CONSTRUCTION))
        val painted = (0 until f.solidCount).map { f.solidColor[f.solidOrder[it]] }
        assertTrue("the field is drawn under the park", painted.indexOf(Color.WORLD_FIELD) < painted.indexOf(Color.WORLD_GREEN))
        assertEquals("the tints are each their own", 4, setOf(Color.WORLD_GROUND, Color.WORLD_FIELD, Color.WORLD_YARD, Color.WORLD_PAVED).size)
        // The pack's reader keeps the three and drops the one it does not know.
        val ring = box(0f, 0f, 10f, 10f)
        assertTrue(listOf("field", "yard", "paved").all { HoloDetail.of(it, ring) != null })
        assertNull(HoloDetail.of("ufo", ring))
    }

    @Test
    fun `a town of land cover does not crowd out a lake`() {
        val cover = (0 until 80).map { HoloDetail("yard", box(-100f + it, 20f, 100f, 100f + it)) }
        val scene = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(mall))
            setDetails(cover + HoloDetail("water", box(40f, 40f, 80f, 80f)))
        }
        val f = frame(scene)
        assertEquals("the lake is drawn", 1, f.solids(Color.WORLD_WATER))
        assertTrue("the cover is capped", f.solids(Color.WORLD_YARD) in 1..40)
    }

    @Test
    fun `trees, a row of them, a lamp with its pool of light and a signal stand where the map has them`() {
        val scene = scene()
        val f = frame(scene)
        // One tree and a row of five at nine metres.
        assertEquals(6, (0 until f.solidCount).count { f.solidColor[it] in canopies })
        assertEquals(6, f.solids(Color.WORLD_TRUNK))
        assertEquals(1, f.solids(Color.WORLD_LAMP))
        assertEquals("the lamp's pool is its colour, mostly clear", 1, (0 until f.solidCount).count { (f.solidColor[it] ushr 24) in 1..0x60 })
        assertEquals(1, f.solids(Color.WORLD_SIGNAL))
        assertEquals("a lamp's pole and a signal's", 2, f.solids(Color.WORLD_POLE))
    }

    // --- T-1104: what signs say, signal lenses, road paint, kerbs, barriers ---------------

    private fun HoloFrame.said() = (0 until signCount).map { signText[it] }
    private val mall = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 2)
    private fun street(vararg details: HoloDetail, road: HoloRoad = mall) = HoloScene().apply {
        world.hazy = false
        setRoads(listOf(road))
        setDetails(details.toList())
    }

    @Test
    fun `a sign's words and a street's name come from the pack and from the house, and ones from before them still read`() {
        val f = MapPackFixture
        val file = f.write(
            File(context.cacheDir, "signs-${System.nanoTime()}/a.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 100L,
            listOf(
                f.road("Mall", 5.0 to 5.0, 5.0 to 5.005, kind = "primary", extra = """{"oneway":1}"""),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.001)), tag = "speed_sign", extra = """{"text":"35"}"""),
                // Yesterday's pack: no extra at all; and one a full disk cut short.
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.002)), tag = "speed_sign"),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.003)), tag = "sign", extra = """{"text":"""),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.004)), tag = "bollard"),
                MapPackFixture.Feature("d", listOf(doubleArrayOf(5.001, 5.001), doubleArrayOf(5.002, 5.002)), tag = "barrier"),
            ),
        )
        val got = MapPack.open(file)!!.near(5.0, 5.0, 5.01, 5.01)!!
        assertEquals("Mall", got.roads.single().name)
        assertEquals(1, got.roads.single().oneway)
        assertEquals(
            listOf("speed_sign" to "35", "speed_sign" to "", "sign" to "", "bollard" to "", "barrier" to "").sortedBy { it.toString() },
            got.details!!.map { it.kind to it.text }.sortedBy { it.toString() }, // nearest first (T-1198), so compared as a set
        )
        val house = MapsJson.near(JSONObject(
            """{"buildings":[],"roads":[{"line":[[5.0,5.0],[5.0,5.005]],"kind":"residential","name":"Mill Lane","oneway":-1},
               {"line":[[5.0,5.0],[5.005,5.0]],"kind":"residential"}],
               "details":[{"kind":"speed_sign","geom":[[5.001,5.001]],"text":"35"},{"kind":"stop","geom":[[5.001,5.002]]},
               {"kind":"sign","geom":[[5.001,5.003]],"text":"${"x".repeat(40)}"},{"kind":"gate","geom":[[5.001,5.004]]}]}""",
        ))
        assertEquals(listOf("Mill Lane", ""), house.roads.map { it.name })
        assertEquals(listOf("35", "", "x".repeat(HoloDetail.TEXT_MAX), ""), house.details!!.map { it.text })
    }

    @Test
    fun `a stop sign says STOP, a limit its number and a sign its words, and a blank or a far one says nothing`() {
        val f = frame(street(
            HoloDetail("stop", listOf(at(7f, 4f))),
            HoloDetail("speed_sign", listOf(at(7f, 10f)), "35"),
            HoloDetail("sign", listOf(at(7f, 14f)), "NO OUTLET"),
            HoloDetail("speed_sign", listOf(at(7f, 18f))),
            HoloDetail("speed_sign", listOf(at(7f, 120f)), "50"),
        ))
        assertEquals(setOf("STOP", "35", "NO OUTLET"), f.said().toSet())
        assertEquals("three limits' plates and the words' plate", 4, f.solids(Color.WORLD_SIGN_PLATE))
        // A street of signs is not a wall of words.
        val many = frame(street(*Array(40) { HoloDetail("speed_sign", listOf(at(7f, 2f + it * 0.5f)), "$it") }))
        assertEquals(HoloFrame.MAX_SIGNS, many.signCount)
        // Each is written on the solid that is its plate, once (T-1120): the painter writes
        // it as it paints that solid, so what hides the plate hides the words.
        val plates = setOf(Color.WORLD_SIGN_PLATE, Color.WORLD_SIGN_STOP)
        val on = (0 until f.solidCount).flatMap { p -> generateSequence(f.solidSign[p]) { f.signNext[it] }.takeWhile { it >= 0 }.map { it to f.solidColor[p] } }
        assertEquals((0 until f.signCount).toList(), on.map { it.first }.sorted())
        assertTrue(on.all { it.second in plates })
        assertEquals("words with no solid to be on are left out", 0, HoloFrame().apply { sign("35", 1f, 1f, 9f, 0) }.signCount)
    }

    @Test
    fun `two named streets meeting have a blade each on one post, and a crossing with one name has none`() {
        val named = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(0f, -50f), at(0f, 14f), at(0f, 200f)), "primary", lanes = 2, name = "Mall"),
                HoloRoad(listOf(at(-60f, 14f), at(0f, 14f), at(60f, 14f)), "residential", name = "Mill Lane"),
                // A street that changes ways but not its name, and one nobody named.
                HoloRoad(listOf(at(0f, -50f), at(-40f, -50f)), "primary", name = "Mall"),
                HoloRoad(listOf(at(0f, 14f), at(30f, 44f)), "residential"),
            ))
        }
        val f = frame(named)
        assertEquals(1, named.world.counts[12])
        assertEquals(listOf("Mall", "Mill Lane"), f.said())
        assertEquals(2, f.solids(Color.WORLD_SIGN_BLADE))
    }

    // --- T-1167: the owner, 2026-11-12: "hardly any street signs, no interstate signs ... all
    // roads are flat, no bridges, I want bridges" ----------------------------------------

    @Test
    fun `a named street meeting one nobody named has a blade with its one name`() {
        val s = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(0f, -50f), at(0f, 14f), at(0f, 200f)), "primary", lanes = 2, name = "Mall"),
                HoloRoad(listOf(at(0f, 14f), at(60f, 14f)), "residential"),
            ))
        }
        val f = frame(s)
        assertEquals(1, s.world.counts[12])
        assertEquals(listOf("Mall"), f.said())
        assertEquals(1, f.solids(Color.WORLD_SIGN_BLADE))
    }

    @Test
    fun `a numbered road has its number on a shield beside it, the first of several, and an unnumbered one none`() {
        val numbered = HoloRoad(listOf(at(0f, 10f), at(0f, 400f)), "motorway", lanes = 2, oneway = 1, ref = "I 95;US 1")
        // T-1183: a marker stands after a junction, so a street crosses where the road begins.
        val s = street(road = numbered).apply { setRoads(listOf(numbered, HoloRoad(listOf(at(-60f, 10f), at(0f, 10f), at(60f, 10f)), "residential"))) }
        val f = frame(s)
        assertTrue("a shield is kept", s.world.counts[16] >= 1)
        // The interstate shape carries the number alone: the network is the shape (T-1183).
        assertTrue("it says the first number: ${f.said()}", "95" in f.said())
        assertFalse(f.said().any { it?.contains("US 1") == true })
        val plain = street()
        frame(plain)
        assertEquals(0, plain.world.counts[16])
    }

    // --- T-1183: signs come from what is mapped ---------------------------------------------

    /** The frame with the car at ([east], [north]) heading north. */
    private fun frameAt(scene: HoloScene, east: Float, north: Float) =
        HoloFrame().also { scene.build(it, cam, plane.lat(north), plane.lon(east), 0f, 1_000L) }

    private fun HoloFrame.wide(solid: Int): Float {
        val xs = (0 until solidLen[solid]).map { pts[(solidStart[solid] + it) * 2] }
        return xs.max() - xs.min()
    }

    @Test
    fun `a route number is read into its network and the number on its marker from the contract's table (T-1183)`() {
        val contract = JSONObject(File("../../tests/contracts/route-shields.json").readText())
        val rows = contract.getJSONArray("cases")
        assertTrue(rows.length() >= 8)
        for (i in 0 until rows.length()) {
            val row = rows.getJSONObject(i)
            val ref = row.getString("ref")
            assertEquals(ref, row.getString("network") to row.getString("label"), HoloWorld.routeMark(ref))
        }
        assertEquals("only the first of several", "interstate" to "99", HoloWorld.routeMark("I 99;US 9"))
    }

    private fun marked(ref: String?, vararg more: HoloRoad) = HoloScene().apply {
        world.hazy = false
        setRoads(listOf(HoloRoad(listOf(at(0f, 30f), at(0f, 400f)), "motorway", lanes = 2, oneway = 1, ref = ref), *more))
    }

    private val crossing30 = HoloRoad(listOf(at(-60f, 30f), at(0f, 30f), at(60f, 30f)), "residential")

    @Test
    fun `a route marker has its network's shape, and says the number without the network (T-1183)`() {
        // The corners of each plate: an interstate's shield, a US route's, a state route's, the rest a plain plate.
        for ((ref, corners) in mapOf("I 99" to 5, "US 9" to 6, "NT 12" to 8, "A1" to 4)) {
            val f = frame(marked(ref, crossing30))
            val plates = (0 until f.solidCount).filter { f.solidColor[it] == Color.WORLD_SIGN_PLATE }
            assertEquals("$ref: one marker", 1, plates.size)
            assertEquals("$ref: its shape", corners, f.solidLen[plates.single()])
            assertEquals(listOf(HoloWorld.routeMark(ref).second), f.said())
        }
    }

    /** Where a numbered road's markers stood as a car drove [from] to [to]: north of the way's start, the stands apart. */
    private fun stands(scene: HoloScene, from: Int, to: Int): List<Float> {
        val seen = java.util.TreeSet<Float>()
        for (n in from..to step 20) { frameAt(scene, 0f, n.toFloat()); scene.world.shieldSpots.forEach { seen += it[1] } }
        val groups = ArrayList<MutableList<Float>>()
        for (v in seen) if (groups.isEmpty() || v - groups.last().last() > 30f) groups += mutableListOf(v) else groups.last() += v
        return groups.map { it.average().toFloat() }
    }

    @Test
    fun `a route marker stands after a junction and then a kilometre on, never every hundred metres, and a way no junction touches has none (T-1183)`() {
        val way = listOf(at(0f, 30f), at(0f, 1500f), at(0f, 3000f))
        val road = HoloRoad(way, "motorway", lanes = 2, oneway = 1, ref = "I 99")
        fun scene(vararg more: HoloRoad) = HoloScene().apply { world.hazy = false; setRoads(listOf(road, *more)) }
        val a = stands(scene(crossing30), -100, 3100)
        assertEquals("after the junction, then every kilometre: $a", 3, a.size)
        assertEquals(1000f, a[1] - a[0], 5f)
        assertEquals(1000f, a[2] - a[1], 5f)
        // A street across the way's middle is a junction: the marker after it, the kilometre counted from there.
        val middle = HoloRoad(listOf(at(-60f, 1500f), at(0f, 1500f), at(60f, 1500f)), "residential")
        val b = stands(scene(crossing30, middle), -100, 3100)
        assertEquals("the second marker moved to the junction: $b", 3, b.size)
        assertEquals(1470f, b[1] - b[0], 5f)
        // Nothing touches it: a stretch of road with a number is not a place for a reminder.
        assertEquals(emptyList<Float>(), stands(scene(), -100, 3100))
        // Another numbered road touching is a junction too, one with the same number carrying on is not.
        val carried = HoloRoad(listOf(at(0f, 3000f), at(0f, 3500f)), "motorway", lanes = 2, oneway = 1, ref = "I 99")
        assertEquals("the same number carrying on is no junction", 0, stands(scene(carried), 2800, 3400).size)
    }

    private val tagged = HoloRoad(
        listOf(at(0f, 30f), at(30f, 80f), at(60f, 170f)), "motorway_link", oneway = 1,
        destination = "Northtown;Eastport", destinationRef = "I 99;US 9", junctionRef = "12B",
    )
    private val bare = HoloRoad(listOf(at(0f, 150f), at(-30f, 190f), at(-60f, 260f)), "motorway_link", oneway = 1)
    private val spine = HoloRoad(listOf(at(0f, -200f), at(0f, 30f), at(0f, 150f), at(0f, 400f)), "motorway", lanes = 2, oneway = 1, ref = "I 99")

    @Test
    fun `a link with a destination has a guide board at the split with its exit, routes and names, and a link with no tags none (T-1183)`() {
        val s = HoloScene().apply { world.hazy = false; setRoads(listOf(spine, tagged, bare)) }
        val f = frame(s)
        assertEquals("one board, for the tagged link", 1, s.world.counts[17])
        assertTrue("its words: ${f.said()}", f.said().containsAll(listOf("EXIT 12B", "I 99, US 9", "Northtown / Eastport")))
        assertEquals("a green board", 1, f.solids(Color.WORLD_SIGN_BLADE))
        val plain = HoloScene().apply { world.hazy = false; setRoads(listOf(spine, bare)) }
        val g = frame(plain)
        assertEquals(0, plain.world.counts[17])
        assertFalse(g.said().any { it?.startsWith("EXIT") == true || it?.contains("Northtown") == true })
        assertEquals(0, g.solids(Color.WORLD_SIGN_BLADE))
        // A link that only has its exit number still has a board: the number is on it, alone.
        val number = HoloScene().apply { world.hazy = false; setRoads(listOf(spine, HoloRoad(tagged.line, "motorway_link", oneway = 1, junctionRef = "12B"))) }
        assertTrue("EXIT 12B" in frame(number).said())
        assertEquals(1, number.world.counts[17])
    }

    @Test
    fun `a street name's blades face the traffic on the street they name (T-1183)`() {
        val corner = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(0f, -50f), at(0f, 14f), at(0f, 200f)), "residential", name = "Mall"),
                HoloRoad(listOf(at(-60f, 14f), at(0f, 14f), at(60f, 14f)), "residential", name = "Mill Lane"),
            ))
        }
        // The blade stands 6.5 m out and 6.5 m past the corner; the words show from about 25 m, and the plate
        // seen from there at an angle of less than BLADE_FACE_MIN (0.3) has none.
        val up = frameAt(corner, 0f, -2f)
        // Each name's blade is square to the way its street runs: Mall runs north, Mill Lane east.
        val faces = corner.world.bladeFaces
        assertEquals(2, faces.size)
        assertEquals("Mall's plate faces along Mall", 1f, abs(faces[0][1]), 0.01f)
        assertEquals("Mill Lane's along Mill Lane", 1f, abs(faces[1][0]), 0.01f)
        val plates = (0 until up.solidCount).filter { up.solidColor[it] == Color.WORLD_SIGN_BLADE }
        assertEquals(2, plates.size)
        // From a car on Mall, 16 m back, Mall's is broad on the screen and Mill Lane's is edge-on, with no words on it.
        assertTrue("Mall's plate is broad: ${up.wide(plates[0])} against ${up.wide(plates[1])}", up.wide(plates[0]) > 2 * up.wide(plates[1]))
        assertEquals(listOf("Mall"), up.said())
        // And the other way round from a car on Mill Lane.
        val west = HoloFrame().also { corner.build(it, cam, plane.lat(14f), plane.lon(-16f), 90f, 1_000L) }
        assertEquals(listOf("Mill Lane"), west.said())
    }

    @Test
    fun `a bridge across the way ahead is drawn raised, with rails and piers, and the ground beside it stays down`() {
        fun crossing(bridge: Boolean) = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(HoloRoad(listOf(at(-60f, 60f), at(60f, 60f)), "primary", lanes = 2, bridge = bridge)))
        }
        val over = crossing(true)
        val level = crossing(false)
        val up = frame(over)
        val flat = frame(level)
        assertTrue("the deck is higher on the screen", up.top { it == Color.WORLD_ASPHALT } < flat.top { it == Color.WORLD_ASPHALT } - 20f)
        assertTrue("rails and piers", up.solids(Color.WORLD_BARRIER) >= 4)
        assertEquals(0, flat.solids(Color.WORLD_BARRIER))
        assertTrue("the middle of the span is metres up", over.world.riseOn(0f, 60f) > 3f)
        // T-1181: the deck is level to its ends (the road that leads there climbs to it), not a hump.
        assertEquals("level to its ends", over.world.riseOn(0f, 60f), over.world.riseOn(-60f, 60f), 0.01f)
        assertEquals("the car passing under it stays on the ground", 0f, over.world.riseOn(0f, 0f), 0.01f)
        assertEquals("ground off the span is ground", 0f, over.world.riseOn(0f, 30f), 0.01f)
        assertEquals("a level road has no height", 0f, level.world.riseOn(0f, 60f), 0.01f)
        // The review: a route's line or a label under the span is on the ground, one along it on the deck.
        assertEquals("the ground under the span", 0f, over.world.rise(0f, 60f), 0.01f)
        assertEquals("something going under it", 0f, over.world.riseOn(0f, 60f, 0f, 9f), 0.01f)
        assertTrue("something going along it", over.world.riseOn(0f, 60f, 9f, 0f) > 3f)
    }

    @Test
    fun `a car on a bridge rides its deck, the ground below it, and a tunnel is not raised`() {
        val s = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(HoloRoad(listOf(at(0f, -50f), at(0f, 50f)), "primary", lanes = 2, bridge = true, layer = 1)))
        }
        frame(s)
        assertEquals("heights are counted from the deck under the car", 0f, s.world.riseOn(0f, 0f), 0.01f)
        assertTrue("the ground beside the span is below it", s.world.riseOn(30f, 0f) < -3f)
        val tunnel = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(HoloRoad(listOf(at(-60f, 60f), at(60f, 60f)), "primary", lanes = 2, tunnel = true, layer = -1)))
        }
        frame(tunnel)
        assertEquals(0f, tunnel.world.riseOn(0f, 60f), 0.01f)
    }

    @Test
    fun `two bridge ways end to end are one span, level where they join`() {
        val s = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(-80f, 60f), at(0f, 60f)), "primary", lanes = 2, bridge = true),
                HoloRoad(listOf(at(0f, 60f), at(80f, 60f)), "primary", lanes = 2, bridge = true),
            ))
        }
        frame(s)
        assertTrue("the join is up, not a dip to the ground", s.world.riseOn(0f, 60f) > 3f)
        assertEquals(s.world.riseOn(-10f, 60f), s.world.riseOn(10f, 60f), 0.01f)
    }

    @Test
    fun `a signal has a red, an amber and a green lens on its housing`() {
        val f = frame(street(HoloDetail("signal", listOf(at(8f, 20f)))))
        assertEquals(1, f.solids(Color.WORLD_SIGNAL))
        for (lens in listOf(Color.WORLD_SIGNAL_RED, Color.WORLD_SIGNAL_AMBER, Color.WORLD_SIGNAL_GREEN)) assertEquals(1, f.solids(lens))
        fun top(color: Int): Float {
            val i = (0 until f.solidCount).first { f.solidColor[it] == color }
            return (0 until f.solidLen[i]).minOf { f.pts[(f.solidStart[i] + it) * 2 + 1] }
        }
        assertTrue("red is not over amber over green", top(Color.WORLD_SIGNAL_RED) < top(Color.WORLD_SIGNAL_AMBER) && top(Color.WORLD_SIGNAL_AMBER) < top(Color.WORLD_SIGNAL_GREEN))
    }

    @Test
    fun `a signal mapped on a street's centre line stands beyond its edge, on the right of the traffic it faces`() {
        // T-1237: the map puts a signal on the road's own line, and its pole stood in the lane.
        val half = maxOf(HoloScene.halfWidth("primary"), 2 * 3.4f / 2)
        fun poleX(road: HoloRoad, detail: HoloDetail): Float {
            val f = frame(street(detail, road = road))
            // Each scene has the one detail, so the one pole is its pole.
            val i = (0 until f.solidCount).first { f.solidColor[it] == Color.WORLD_POLE }
            return (0 until f.solidLen[i]).map { f.pts[(f.solidStart[i] + it) * 2] }.average().toFloat()
        }
        // Traffic approaching the near end (north, at 40 m) keeps right: east of the line.
        val north = HoloRoad(listOf(at(0f, -50f), at(0f, 40f)), "primary", lanes = 2)
        val onLine = poleX(north, HoloDetail("signal", listOf(at(0f, 20f))))
        val edge = poleX(north, HoloDetail("lamp", listOf(at(half, 20f))))
        assertTrue("a signal on the line stands past the edge: $onLine, edge at $edge", onLine > edge)
        // Approaching the south end (at 10 m) it keeps right: west of the line.
        val south = HoloRoad(listOf(at(0f, 10f), at(0f, 200f)), "primary", lanes = 2)
        val west = poleX(south, HoloDetail("signal", listOf(at(0f, 20f))))
        val westEdge = poleX(south, HoloDetail("lamp", listOf(at(-half, 20f))))
        assertTrue("and on the other end's right: $west, edge at $westEdge", west < westEdge)
        // One already beside the road stays where the map put it.
        val verge = HoloDetail("signal", listOf(at(half + 3f, 20f)))
        val alone = frame(HoloScene().apply { world.hazy = false; setDetails(listOf(verge)) })
        val i = (0 until alone.solidCount).first { alone.solidColor[it] == Color.WORLD_POLE }
        val unmoved = (0 until alone.solidLen[i]).map { alone.pts[(alone.solidStart[i] + it) * 2] }.average().toFloat()
        assertEquals(unmoved, poleX(north, verge), 0.01f)
    }

    @Test
    fun `a ramp the map counts no lanes on is one lane, and a counted one keeps its dashes`() {
        // T-1234: beside its photo a single-lane ramp was a 14 m road with a dashed line down it.
        fun dashes(lanes: Int) = frame(street(road = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "trunk_link", lanes = lanes, oneway = 1)))
            .let { it.solids(Color.WORLD_MARKING) + it.solids(Color.WORLD_MARKING_CENTRE) }
        assertEquals("only its two edge lines (one white, one yellow, T-1246)", 2, dashes(0))
        assertTrue("two counted lanes are parted", dashes(2) > 2)
    }

    @Test
    fun `a signal on a bridge stands on its deck, not on the ground under it`() {
        // T-1232: an interchange's junction on the overpass showed no signal at all, each
        // drawn a deck's height down and hidden by the deck the car was on.
        fun housingTop(bridge: Boolean): Float {
            val road = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 2, bridge = bridge, layer = if (bridge) 1 else 0)
            val f = frame(HoloScene().apply {
                world.hazy = false
                setRoads(listOf(road))
                setDetails(listOf(HoloDetail("signal", listOf(at(3f, 20f)))))
            })
            val i = (0 until f.solidCount).first { f.solidColor[it] == Color.WORLD_SIGNAL }
            return (0 until f.solidLen[i]).minOf { f.pts[(f.solidStart[i] + it) * 2 + 1] }
        }
        assertEquals("as high above the car's road as on a plain street", housingTop(false), housingTop(true), 1f)
    }

    @Test
    fun `a one-way street has no centre line, and a two-way one keeps it`() {
        assertTrue(frame(street()).solids(Color.WORLD_MARKING_CENTRE) > 0)
        val oneWay = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 2, oneway = 1)
        val f = frame(street(road = oneWay))
        // T-1246: its one yellow line is the left edge, not a line between lanes (see the edge test below).
        assertEquals(1, f.solids(Color.WORLD_MARKING_CENTRE))
        assertTrue("its lanes are not parted", f.solids(Color.WORLD_MARKING) > 2)
    }

    /** How far a run's start lies from the centre line of its own piece. */
    private fun lateral(r: FloatArray): Float {
        val len = kotlin.math.hypot(r[7] - r[5], r[8] - r[6])
        return kotlin.math.abs((r[0] - r[5]) * (r[8] - r[6]) / len - (r[1] - r[6]) * (r[7] - r[5]) / len)
    }

    /** The side an edge line of [colour] lies on, east (+) or west (-) of a road running north on x = 0, one run each. */
    private fun edgeSides(road: HoloRoad, colour: Int): List<Float> =
        paint(road).filter { it[4].toInt() == colour && kotlin.math.abs(it[0]) > 1f }.map { kotlin.math.sign(it[0]) }.distinct()

    @Test
    fun `a one-way street paints its left edge in the centre colour and its right edge white, a two-way street both white (T-1246)`() {
        val north = listOf(at(0f, -50f), at(0f, 200f))
        // Heading north, right-hand traffic: the left of travel is the west.
        val forward = HoloRoad(north, "primary", lanes = 2, oneway = 1)
        assertEquals(listOf(-1f), edgeSides(forward, Color.WORLD_MARKING_CENTRE))
        assertEquals(listOf(1f), edgeSides(forward, Color.WORLD_MARKING))
        // oneway=-1 travels south along the same line: the left of travel is the east.
        val back = HoloRoad(north, "primary", lanes = 2, oneway = -1)
        assertEquals(listOf(1f), edgeSides(back, Color.WORLD_MARKING_CENTRE))
        assertEquals(listOf(-1f), edgeSides(back, Color.WORLD_MARKING))
        // Two-way: the yellow is only the centre line, on the line itself.
        val two = HoloRoad(north, "primary", lanes = 2)
        assertEquals(emptyList<Float>(), edgeSides(two, Color.WORLD_MARKING_CENTRE))
        assertEquals(listOf(-1f, 1f), edgeSides(two, Color.WORLD_MARKING).sorted())
    }

    // --- T-1182: where roads meet, no line crosses the join -----------------------------------

    private fun way(kind: String, vararg pts: Pair<Float, Float>, oneway: Int = 0, lanes: Int = 0) =
        HoloRoad(pts.map { at(it.first, it.second) }, kind, lanes = lanes, oneway = oneway)

    /** Every run of paint the scene lays: [e0, n0, e1, n1, colour, its piece's e0, n0, e1, n1, half]. */
    private fun paint(vararg roads: HoloRoad): List<FloatArray> {
        val scene = HoloScene().apply { world.hazy = false; setRoads(roads.toList()) }
        val runs = ArrayList<FloatArray>()
        scene.world.lineTap = { runs += it }
        frame(scene)
        return runs
    }

    private fun FloatArray.ofPiece(e0: Float, n0: Float, e1: Float, n1: Float) =
        listOf(5 to e0, 6 to n0, 7 to e1, 8 to n1).all { (i, v) -> kotlin.math.abs(this[i] - v) < 0.05f }

    /** Whether ([x], [y]) is on the surface of the road piece e0,n0 to e1,n1 of half width [half], less the edge line's own width. */
    private fun onSurface(x: Float, y: Float, e0: Float, n0: Float, e1: Float, n1: Float, half: Float): Boolean {
        val len = kotlin.math.hypot(e1 - e0, n1 - n0)
        val ue = (e1 - e0) / len
        val un = (n1 - n0) / len
        val u = (x - e0) * ue + (y - n0) * un
        val v = (x - e0) * un - (y - n0) * ue
        return u in 0f..len && kotlin.math.abs(v) < half - 0.4f
    }

    /** Where a run of [colour] and of the piece [mine] lies on the surface of any of [others] (e0, n0, e1, n1, half), sampled each metre. */
    private fun lyingOn(runs: List<FloatArray>, mine: List<FloatArray>, others: List<FloatArray>, colour: Int? = null): List<String> {
        val bad = ArrayList<String>()
        for (r in runs) {
            if (colour != null && r[4].toInt() != colour) continue
            if (mine.none { r.ofPiece(it[0], it[1], it[2], it[3]) }) continue
            val len = kotlin.math.hypot(r[2] - r[0], r[3] - r[1])
            // Not the run's own ends: the vertex two roads share is on both surfaces by being theirs.
            var s = 0.25f
            while (s <= len - 0.25f) {
                val x = r[0] + (r[2] - r[0]) * s / len
                val y = r[1] + (r[3] - r[1]) * s / len
                for (o in others) if (onSurface(x, y, o[0], o[1], o[2], o[3], o[4])) bad += "(${"%.1f".format(x)}, ${"%.1f".format(y)}) on ${o.toList()}"
                s += 1f
            }
        }
        return bad
    }

    /** Whether some run passes within 15 cm of ([x], [y]). */
    private fun painted(runs: List<FloatArray>, x: Float, y: Float) = runs.any {
        val dx = it[2] - it[0]
        val dy = it[3] - it[1]
        val t = (((x - it[0]) * dx + (y - it[1]) * dy) / (dx * dx + dy * dy)).coerceIn(0f, 1f)
        kotlin.math.hypot(x - it[0] - dx * t, y - it[1] - dy * t) < 0.15f
    }

    // A primary road split at (0, -20), and a link leaving it there at a shallow angle (7 degrees), both 5 m a side.
    // The link is given two lanes in each test: one the map counts no lanes on is a one-lane ramp (T-1234).
    private val trunk = listOf(floatArrayOf(0f, -100f, 0f, -20f, 5f), floatArrayOf(0f, -20f, 0f, 100f, 5f))
    private val spur = floatArrayOf(0f, -20f, 14f, 100f, 5f)

    @Test
    fun `a link leaving a road of its width at a shallow angle crosses none of its lines, and leaves its edge open at the mouth`() {
        val runs = paint(way("primary", 0f to -100f, 0f to -20f, 0f to 100f), way("primary_link", 0f to -20f, 14f to 100f, lanes = 2))
        // T-1191: the road's own lines run on, over the link's surface past its mouth (see the merge test below).
        assertEquals("the link's paint on the road", emptyList<String>(), lyingOn(runs, listOf(spur), trunk))
        assertTrue("a two-way link has its centre line", runs.any { it[4].toInt() == Color.WORLD_MARKING_CENTRE && it.ofPiece(0f, -20f, 14f, 100f) })
        // The road's right edge is open at the mouth and runs on either side of it.
        assertFalse("its edge crosses the mouth", painted(runs, 4.7f, -14f))
        assertTrue("its edge before the mouth", painted(runs, 4.7f, -80f))
        assertTrue("its edge beyond the link", painted(runs, 4.7f, 90f))
        // The link keeps its edge lines where it is clear of the road.
        assertTrue(painted(runs, 11.7f + 4.7f, 80f))
        assertTrue(painted(runs, 11.7f - 4.7f, 80f))
    }

    @Test
    fun `two roads crossing at a shared point paint nothing of either on the other, and keep their lines out to it`() {
        val runs = paint(way("primary", -60f to 0f, 0f to 0f, 60f to 0f), way("primary", 0f to -60f, 0f to 0f, 0f to 60f))
        val across = listOf(floatArrayOf(-60f, 0f, 0f, 0f, 5f), floatArrayOf(0f, 0f, 60f, 0f, 5f))
        val up = listOf(floatArrayOf(0f, -60f, 0f, 0f, 5f), floatArrayOf(0f, 0f, 0f, 60f, 5f))
        assertEquals(emptyList<String>(), lyingOn(runs, across, up))
        assertEquals(emptyList<String>(), lyingOn(runs, up, across))
        for ((x, y) in listOf(-30f to 0f, 30f to 0f, -30f to 4.7f, 30f to -4.7f, 0f to -30f, 0f to 30f, 4.7f to 30f, -4.7f to -30f)) {
            assertTrue("($x, $y) lost its line", painted(runs, x, y))
        }
    }

    @Test
    fun `a road carrying on through a vertex or onto its next way keeps its lines unbroken`() {
        // One road in two ways, straight and then bending 9 degrees: the centre line and both edges go through.
        for (bend in listOf(0f, 8f)) {
            val runs = paint(way("primary", 0f to -50f, 0f to 0f), way("primary", 0f to 0f, bend to 50f))
            val dx = bend / kotlin.math.hypot(bend, 50f)
            val dy = 50f / kotlin.math.hypot(bend, 50f)
            for (off in listOf(-4.7f, 0f, 4.7f)) {
                for (s in 1..48) {
                    assertTrue("bend $bend, offset $off, ${s} m before", painted(runs, off, -s.toFloat()))
                    assertTrue("bend $bend, offset $off, ${s} m after", painted(runs, s * dx + off * dy, s * dy - off * dx))
                }
            }
        }
        // The same road as one way with a vertex in it.
        val one = paint(way("primary", 0f to -50f, 0f to 0f, 0f to 50f))
        for (s in -49..49) assertTrue("one way, at $s", painted(one, 4.7f, s.toFloat()))
    }

    @Test
    fun `a one-way link keeps its own edge lines away from the mouth, and has no centre line`() {
        val runs = paint(way("primary", 0f to -100f, 0f to -20f, 0f to 100f), way("primary_link", 0f to -20f, 14f to 100f, oneway = 1, lanes = 2))
        // T-1246: the yellow it has is its left edge, never a line between lanes.
        val yellow = runs.filter { it[4].toInt() == Color.WORLD_MARKING_CENTRE && it.ofPiece(0f, -20f, 14f, 100f) }
        assertTrue("one way has no line between opposing lanes", yellow.all { lateral(it) > it[9] - 1f })
        assertEquals(emptyList<String>(), lyingOn(runs, listOf(spur), trunk))
        // Both edges of the link, the whole way from where it is clear of the road to its far end.
        for (n in listOf(70f, 85f, 99f)) {
            val e = 14f * (n + 20f) / 120f
            assertTrue("left edge at $n", painted(runs, e - 4.7f, n))
            assertTrue("right edge at $n", painted(runs, e + 4.7f, n))
        }
    }

    @Test
    fun `a dense frame of marked roads crossing at every corner is cut, and still builds in a frame's time`() {
        // 12 roads each way at 20 m, a vertex at each crossing: the pieces and the cuts of a city centre.
        val grid = buildList {
            for (i in -6..5) {
                add(HoloRoad((-12..12).map { at(i * 20f + 10f, it * 20f) }, "primary", lanes = 4))
                add(HoloRoad((-12..12).map { at(it * 20f, i * 20f + 10f) }, "primary", lanes = 4))
            }
        }
        val scene = HoloScene().apply { world.hazy = false; setRoads(grid) }
        val runs = ArrayList<FloatArray>()
        scene.world.lineTap = { runs += it }
        frame(scene)
        scene.world.lineTap = null
        // No dash or edge on a crossing's box: the box round (10, 10) is nobody's line.
        assertTrue("paint in a crossing's box", runs.none { r -> kotlin.math.abs((r[0] + r[2]) / 2 - 10f) < 3f && kotlin.math.abs((r[1] + r[3]) / 2 - 10f) < 3f })
        assertTrue("no paint at all", runs.size > 100)
        val times = (0 until 7).map { val t = System.nanoTime(); frame(scene); (System.nanoTime() - t) / 1e6 }.sorted()
        println("T-1182 dense marked frame build: median ${times[3]} ms, best ${times[0]} ms")
        assertTrue("a dense frame of crossings took ${times[3]} ms", times[3] < 250.0)
    }

    @Test
    fun `a stop and a signal have a stop line on their side of the road, and one on the junction itself has none`() {
        // Beside the road, to its right: that half. On the road's own line: the whole width.
        val beside = street(HoloDetail("stop", listOf(at(7f, 20f)))).also { frame(it) }.world.stopLines.single()
        val onIt = street(HoloDetail("signal", listOf(at(0f, 20f)))).also { frame(it) }.world.stopLines.single()
        assertTrue("not on the sign's side", beside[4] > 0f)
        assertEquals(onIt[5] / 2, beside[5], 0.01f)
        assertEquals(0f, onIt[4])
        val left = street(HoloDetail("stop", listOf(at(-7f, 20f)))).also { frame(it) }.world.stopLines.single()
        assertEquals(-beside[4], left[4], 0.01f)
        // A one-way street has one way: the whole width, whichever side the sign stands.
        val oneWay = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 2, oneway = 1)
        assertEquals(0f, street(HoloDetail("stop", listOf(at(7f, 20f))), road = oneWay).also { frame(it) }.world.stopLines.single()[4])
        val junction = HoloScene().apply {
            setRoads(listOf(
                HoloRoad(listOf(at(0f, -50f), at(0f, 20f)), "primary"), HoloRoad(listOf(at(0f, 20f), at(0f, 200f)), "primary"),
                HoloRoad(listOf(at(0f, 20f), at(60f, 20f)), "residential"),
            ))
            setDetails(listOf(
                HoloDetail("signal", listOf(at(0f, 20f))), HoloDetail("stop", listOf(at(0f, 20f))), HoloDetail("give_way", listOf(at(7f, 10f))),
            ))
        }
        frame(junction)
        assertEquals("a line for a signal or a stop on the junction itself, or for a give-way", 0, junction.world.counts[10])
        // A stop nowhere near a street has no street to stop on.
        assertEquals(0, HoloScene().apply { setDetails(listOf(HoloDetail("stop", listOf(at(7f, 20f))))) }.also { frame(it) }.world.counts[10])
    }

    @Test
    fun `a town street has a pavement and a kerb either side, and a motorway has neither`() {
        val town = frame(street(road = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "residential")))
        assertEquals(2, town.solids(Color.WORLD_PAVEMENT))
        assertEquals(2, town.solids(Color.WORLD_KERB))
        val fast = frame(street(road = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "motorway", lanes = 3)))
        assertEquals(0, fast.solids(Color.WORLD_PAVEMENT) + fast.solids(Color.WORLD_KERB))
        // The road lies over its pavement's inner edge, never the pavement over the road.
        val order = (0 until town.solidCount).map { town.solidColor[town.solidOrder[it]] }
        assertTrue(order.indexOf(Color.WORLD_PAVEMENT) < order.indexOf(Color.WORLD_ASPHALT))
    }

    @Test
    fun `a guard rail, a bollard and a gate are drawn, and roadworks and a building site still are`() {
        val s = street(
            HoloDetail("barrier", listOf(at(9f, 10f), at(9f, 40f), at(12f, 60f))),
            HoloDetail("bollard", listOf(at(-7f, 12f))),
            HoloDetail("gate", listOf(at(-9f, 24f))),
            HoloDetail("roadworks", listOf(at(-12f, 30f), at(-12f, 40f))),
            HoloDetail("construction", box(20f, 0f, 36f, 16f)),
        )
        val f = frame(s)
        assertEquals("a rail per stretch", 2, s.world.counts[11])
        assertEquals("two stretches of rail and the bollard", 3, f.solids(Color.WORLD_BARRIER))
        assertEquals("the gate's two posts", 2, f.solids(Color.WORLD_POLE))
        assertTrue("the gate's bar and the roadworks' boards", f.solids(Color.WORLD_CONE) >= 3)
        assertEquals(1, f.solids(Color.WORLD_CONSTRUCTION))
        // None of it from the far side of town.
        val far = street(HoloDetail("barrier", listOf(at(9f, 300f), at(9f, 340f))), HoloDetail("bollard", listOf(at(7f, 300f))))
        frame(far)
        assertEquals(0, far.world.counts[11] + far.world.counts[9])
    }

    // --- T-1073: signs, cameras and the far distance -------------------------------------

    private fun posts(vararg kinds: String) = HoloScene().apply {
        world.hazy = false
        setRoads(listOf(HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 2)))
        setDetails(kinds.mapIndexed { i, k -> HoloDetail(k, listOf(at(7f, 12f + i * 6f))) })
    }

    @Test
    fun `a stop sign is a red octagon, a give-way a triangle, a speed limit a white plate and any other sign a yellow diamond`() {
        val f = frame(posts("stop", "give_way", "speed_sign", "sign"))
        fun sides(color: Int) = (0 until f.solidCount).filter { f.solidColor[it] == color }.map { f.solidLen[it] }
        assertEquals(listOf(8), sides(Color.WORLD_SIGN_STOP))
        assertEquals("the give-way's triangle and the limit's plate", listOf(3, 4), sides(Color.WORLD_SIGN_PLATE).sorted())
        assertEquals(listOf(4), sides(Color.WORLD_SIGN_WARN))
        assertEquals("a post each", 4, f.solids(Color.WORLD_POLE))
    }

    @Test
    fun `a speed camera, a camera and a plate reader each stand on a pole and are told apart`() {
        val f = frame(posts("speed_camera", "camera", "alpr"))
        assertEquals(3, f.solids(Color.WORLD_POLE))
        assertEquals("the speed camera's box", 1, f.solids(Color.WORLD_CAMERA_SPEED))
        assertEquals("a housing for the camera and for the reader", 2, f.solids(Color.WORLD_CAMERA))
        assertEquals("the reader's panel", 1, f.solids(Color.WORLD_CAMERA_ALPR))
        // None of them is drawn from the far side of town.
        val far = HoloScene().apply { setDetails(listOf(HoloDetail("alpr", listOf(at(7f, 300f))))) }
        assertEquals(0, frame(far).solidCount)
    }

    @Test
    fun `the world is drawn to the fog's end, fading, and not past it`() {
        fun town(north: Float) = HoloScene().apply { setBuildings(listOf(HoloBuilding(box(-8f, north, 8f, north + 12f), 12f))) }
        val near = frame(town(30f))
        // 380 m was the end until T-1103, 800 m until T-1185: the far half of the screen was empty haze.
        val far = frame(town(600f))
        assertTrue("nothing at 600 m", far.solidCount > 0)
        // The land under the town (T-1188) is one sheet to the horizon, the same colour in both.
        assertTrue("a far wall is not hazed", (0 until far.solidCount).filter { far.solidColor[it] != Color.WORLD_GROUND }
            .none { i -> (0 until near.solidCount).any { near.solidColor[it] == far.solidColor[i] } })
        assertTrue("the view reaches 1,500 m", HoloCamera.FOG_END_M >= 1500f)
        assertTrue("nothing at 1,450 m", frame(town(1450f)).solidCount > 0)
        assertEquals("past the haze", 0, frame(town(HoloCamera.FOG_END_M + 30f)).solidCount)
    }

    @Test
    fun `a city denser than the caps is cut to them, the nearest kept, and still builds in a frame's time`() {
        // 3,600 blocks out to 900 m: more than is drawn, as a centre is.
        val city = buildList {
            for (i in -30 until 30) for (j in -30 until 30) add(HoloBuilding(box(i * 30f + 8f, j * 30f + 8f, i * 30f + 24f, j * 30f + 24f), 12f))
        }
        val scene = HoloScene().apply { setBuildings(city) }
        frame(scene)
        assertTrue("at least 2,000 buildings, got ${scene.world.counts[0]}", scene.world.counts[0] >= 2000)
        assertEquals("the cap", 2400, scene.world.counts[0])
        val times = (0 until 7).map { val t = System.nanoTime(); frame(scene); (System.nanoTime() - t) / 1e6 }.sorted()
        println("T-1103 dense frame build: median ${times[3]} ms, best ${times[0]} ms")
        // Loose on purpose (a shared CI host): it catches a build that went quadratic, not a slow day.
        assertTrue("a dense frame took ${times[3]} ms", times[3] < 250.0)
    }

    @Test
    fun `the dense frame's build fits a phone's sixteen milliseconds at the new limits`() {
        // T-1185. Cities of about 800 (the old cap), 1,600 and 6,400 blocks (cut to the new cap of
        // 2,400): a frame built with every limit at its worst, tall and short blocks mixed.
        fun city(side: Int, pitch: Float) = buildList {
            for (i in -side / 2 until side / 2) for (j in -side / 2 until side / 2) add(HoloBuilding(box(i * pitch + 8f, j * pitch + 8f, i * pitch + 24f, j * pitch + 24f), 6f + (i * 7 + j * 3).mod(40).toFloat()))
        }
        val phone = 8.0 // a phone's cores are about this many times slower than this host's: a stated guess
        val slack = 1.0 // measured 0.6-0.8 ms here at 1,600 and 2,400 (5-6.4 ms by the phone's factor): 2.5x of room for a busy host
        for ((side, pitch) in listOf(28 to 30f, 40 to 30f, 80 to 30f)) {
            val scene = HoloScene().apply { setBuildings(city(side, pitch)) }
            val t0 = System.nanoTime(); frame(scene); val first = (System.nanoTime() - t0) / 1e6
            repeat(40) { frame(scene) } // the JIT's warm-up is not a phone's steady frame
            val times = (0 until 21).map { val t = System.nanoTime(); frame(scene); (System.nanoTime() - t) / 1e6 }.sorted()
            val c = scene.world.counts
            println("T-1185 dense frame build: ${c[0]} buildings, ${c[1]} pieces, ${scene.world.rows} rows, median ${times[10]} ms, best ${times[0]} ms, first with pick $first ms, x$phone = ${times[10] * phone} ms")
            assertTrue("a ${c[0]} building frame took ${times[10]} ms here, ${times[10] * phone} ms on a phone", times[10] * phone < 16.0 * slack)
        }
    }

    @Test
    fun `a long tree row and long roadworks keep the near ones when there are more than are drawn`() {
        // T-1060's review: a row was laid out end to end, so a long one spent the cap far away.
        val scene = HoloScene().apply {
            world.hazy = false
            setDetails(listOf(
                HoloDetail("tree_row", listOf(at(-9f, -3000f), at(-9f, 3000f))),
                HoloDetail("tree", listOf(at(9f, 20f))),
                HoloDetail("roadworks", (0..60).map { at(12f, -3000f + it * 100f) }),
            ))
        }
        val f = frame(scene)
        val counts = scene.world.counts
        assertTrue("the lone tree by the car was crowded out", (0 until f.solidCount).count { f.solidColor[it] in canopies } > 20)
        assertTrue("trees kept from beyond the radius: ${counts[4]}", counts[4] <= (2 * HoloCamera.FOG_END_M / 9 + 3).toInt())
        assertTrue("roadworks kept from beyond the radius: ${counts[8]}", counts[8] in 1..9)
    }

    @Test
    fun `a wide building whose first corner is far is still drawn when the car is beside it`() {
        // A mall 600 m long: its first vertex is 590 m away, its near wall 10 m.
        val scene = HoloScene().apply { setBuildings(listOf(HoloBuilding(listOf(at(600f, 10f), at(600f, 40f), at(10f, 40f), at(10f, 10f)), 9f))) }
        assertTrue(frame(scene).solidCount > 0)
    }

    @Test
    fun `with real streets the board's grid and road are not drawn, and the wireframe still is when asked for`() {
        val solid = frame(scene())
        val wire = frame(scene().apply { real = false })
        assertEquals("the wireframe has no solids", 0, wire.solidCount)
        assertTrue("the wireframe city is lines", wire.lineCount > solid.lineCount + 50)
        // No map of the place: nothing solid, and the board's grid is back.
        val bare = frame(HoloScene())
        assertEquals(0, bare.solidCount)
        assertTrue(bare.lineCount > 20)
    }

    @Test
    fun `a second frame is the first one again, and needs no more room`() {
        val scene = scene()
        val f = HoloFrame()
        scene.build(f, cam, 5.0, 5.0, 0f, 1_000L)
        val solids = f.solidCount
        val room = f.pts.size
        assertNotNull(f.solidOrder)
        repeat(3) { scene.build(f, cam, 5.0, 5.0, 0f, 1_000L) }
        assertEquals(solids, f.solidCount)
        assertEquals(room, f.pts.size)
        assertFalse(solids == 0)
    }

    // --- T-1106: shop fronts and their names, doors, wall lines, tree crowns, specks ------

    private fun onlyRed(c: Int) = (c shr 16 and 0xFF) > 0 && (c shr 8 and 0xFF) == 0 && (c and 0xFF) == 0
    private fun shopStreet(vararg buildings: HoloBuilding) = street().apply { setBuildings(buildings.toList()) }

    @Test
    fun `a business's name and trade are read from a pack and from the house, and a name that is not a sign's is left out`() {
        val f = MapPackFixture
        val file = f.write(
            File(context.cacheDir, "shop-${System.nanoTime()}/a.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 100L,
            listOf(
                MapPackFixture.Feature("b", f.building(5.001, 5.001, 5.002, 5.002).points, 6.0, "osm", extra = """{"sign":"Corner Bakery","trade":"bakery"}"""),
                MapPackFixture.Feature("b", f.building(5.003, 5.003, 5.004, 5.004).points, 6.0, "osm", extra = """{"sign":"${"x".repeat(MapsJson.MAX_SIGN + 1)}","trade":"cafe"}"""),
            ),
        )
        val got = MapPack.open(file)!!.near(5.0, 5.0, 5.01, 5.01)!!.buildings.sortedBy { it.ring[0].lat } // nearest first (T-1198): south to north here
        assertEquals("Corner Bakery" to "bakery", got[0].look.sign to got[0].look.trade)
        assertEquals("a name too long for a board", "", got[1].look.sign)
        val house = MapsJson.look(JSONObject("""{"sign":"Made Up Fuel","trade":"fuel"}"""))
        assertEquals("Made Up Fuel" to "fuel", house.sign to house.trade)
        assertEquals("", MapsJson.look(JSONObject("""{"sign":"a\u0000b","trade":"fuel"}""")).sign)
        assertEquals("a name that reverses its reading order", "", MapsJson.look(JSONObject("""{"sign":"Cafe\u202eoof","trade":"cafe"}""")).sign)
        assertEquals(MapsJson.MAX_SIGN, MapPackFixture.contract.getJSONObject("pack").getJSONObject("sign").getInt("max"))
    }

    @Test
    fun `a shop fronts its street with glass, a board in its trade's colour and its name, and a wall too short keeps the board without the words`() {
        // East of the street, 20 m up it: the wall that faces the street is the west one, the ring's fourth.
        val shop = HoloBuilding(box(8f, 14f, 24f, 30f), 9f, HoloLook(kind = "retail", sign = "Corner Bakery", trade = "bakery"))
        val scene = shopStreet(shop)
        val f = frame(scene)
        assertEquals(listOf(3), scene.world.fronts.toList())
        assertEquals(listOf<String?>("Corner Bakery"), f.said())
        fun boards(fr: HoloFrame) = (0 until fr.solidCount).count { i ->
            val c = fr.solidColor[i]
            fr.solidLen[i] == 4 && (c shr 16 and 0xFF) > (c shr 8 and 0xFF) * 3 / 2 && (c shr 16 and 0xFF) > (c and 0xFF) * 3 / 2
        }
        assertEquals("one board in the food trade's red", 1, boards(f))
        assertEquals(Color.WORLD_FASCIA_FUEL, HoloWorld.fasciaFor("fuel"))
        // A driveway along its south wall does not turn the shop from its street.
        val drive = street(road = mall).apply {
            setRoads(listOf(mall, HoloRoad(listOf(at(0f, 13f), at(40f, 13f)), "service")))
            setBuildings(listOf(shop))
        }
        frame(drive)
        assertEquals(listOf(3), drive.world.fronts.toList())
        assertEquals(Color.WORLD_FASCIA_SHOP, HoloWorld.fasciaFor("anything else"))
        val narrow = frame(shopStreet(HoloBuilding(box(8f, 14f, 24f, 18.5f), 9f, HoloLook(kind = "retail", sign = "A Very Long Name For A Bakery", trade = "bakery"))))
        assertEquals("a board with no words", 1 to emptyList<String?>(), boards(narrow) to narrow.said())
        // The same building with no business in it has neither.
        val plain = frame(shopStreet(HoloBuilding(box(8f, 14f, 24f, 30f), 9f, HoloLook(kind = "retail"))))
        assertEquals(0 to emptyList<String?>(), boards(plain) to plain.said())
    }

    @Test
    fun `a frame writes the nearest shops' names and leaves the street's own signs their room`() {
        val shops = (0 until 14).map { HoloBuilding(box(8f, 2f + it * 4f, 20f, 6f + it * 4f), 9f, HoloLook(sign = "Shop $it", trade = "books")) }
        val f = frame(street(HoloDetail("speed_sign", listOf(at(6f, 20f)), "50")).apply { setBuildings(shops) })
        val said = f.said()
        assertTrue("the limit lost its number to the shops: $said", "50" in said)
        assertTrue("more shop names than a frame's share: $said", said.count { it!!.startsWith("Shop") } in 1..8)
    }

    @Test
    fun `picking the fronts and the specks of a centre full of shops, homes and streets takes a frame's time, not a second`() {
        // 3,600 shops and homes on a grid of 122 streets cut into 40 m pieces: more of each than is kept.
        val blocks = buildList {
            for (i in -30 until 30) for (j in -30 until 30) add(HoloBuilding(
                box(i * 30f + 8f, j * 30f + 8f, i * 30f + 24f, j * 30f + 24f), 9f,
                if ((i + j) % 2 == 0) HoloLook(kind = "retail", sign = "Shop $i $j", trade = "books") else HoloLook(kind = "house"),
            ))
        }
        val streets = buildList {
            for (i in -30..30) {
                add(HoloRoad((-22..22).map { at(i * 30f, it * 40f) }, "residential"))
                add(HoloRoad((-22..22).map { at(it * 40f, i * 30f) }, "residential"))
            }
        }
        val times = (0 until 5).map {
            val scene = HoloScene().apply { setBuildings(blocks); setRoads(streets) }
            val t = System.nanoTime(); frame(scene); val ms = (System.nanoTime() - t) / 1e6
            assertTrue("no fronts picked", scene.world.fronts.count { it >= 0 } > 20)
            ms
        }.sorted()
        println("T-1106 dense pick and first frame: median ${times[2]} ms, best ${times[0]} ms")
        // Loose on purpose (a shared CI host): the pick runs every 20 m of driving, on the drawing thread.
        assertTrue("picking a dense centre took ${times[2]} ms", times[2] < 400.0)
    }

    @Test
    fun `a home has a door on the wall that faces its street, and a building with no street near fronts with its longest wall`() {
        val green = 0xFF00FF00.toInt()
        val scene = shopStreet(HoloBuilding(box(-24f, 14f, -8f, 26f), 6f, HoloLook(roofColour = green, kind = "house")))
        val f = frame(scene)
        assertEquals("the east wall", listOf(1), scene.world.fronts.toList())
        val doors = (0 until f.solidCount).count { i ->
            val c = f.solidColor[i]
            f.solidLen[i] == 4 && (c shr 16 and 0xFF) == 0 && (c and 0xFF) == 0 && (c shr 8 and 0xFF) in 1..0x8F
        }
        assertEquals("one door, in the roof's colour, darker", 1, doors)
        val alone = HoloScene().apply {
            world.hazy = false
            setBuildings(listOf(HoloBuilding(box(-30f, 20f, -10f, 28f), 6f, HoloLook(kind = "house"))))
        }
        frame(alone)
        assertTrue("not a long wall: ${alone.world.fronts.toList()}", alone.world.fronts.single() in listOf(0, 2))
    }

    @Test
    fun `a near wall has its joints, upright on a shop and a course a floor on a block, and a far one has none`() {
        val red = 0xFFFF0000.toInt()
        fun reds(kind: String, n: Float) = frame(shopStreet(HoloBuilding(box(8f, n, 20f, n + 12f), 12f, HoloLook(colour = red, kind = kind))))
            .let { f -> (0 until f.solidCount).count { onlyRed(f.solidColor[it]) } }
        val far = reds("retail", 150f)
        assertTrue("no upright joints on the near shop", reds("retail", 14f) > far)
        assertTrue("a block has more lines than a shop: a course a floor", reds("apartments", 14f) > reds("retail", 14f))
    }

    @Test
    fun `trees are not one shape or one green`() {
        val f = frame(street(*(0 until 16).map { HoloDetail("tree", listOf(at(-9f - it % 4 * 7f, 12f + it * 5f))) }.toTypedArray()))
        val light = canopies
        val dark = (0..7).map { HoloWorld.vary(Color.WORLD_CANOPY_SHADE, it) }.toSet()
        val crowns = (0 until f.solidCount).filter { f.solidColor[it] in light }
        assertEquals(16, crowns.size)
        assertTrue("one green", crowns.map { f.solidColor[it] }.toSet().size >= 3)
        // A broad tree has a low bough under its crown: a third shape in the shade's green.
        assertTrue("no broad tree among sixteen", (0 until f.solidCount).count { f.solidColor[it] in dark } > 16)
    }

    @Test
    fun `near asphalt and grass are speckled, the paint is not, and bare ground has none`() {
        val scene = street(HoloDetail("green", box(20f, 0f, 50f, 30f)))
        val f = frame(scene)
        fun tones(c: Int) = f.solids(HoloWorld.shade(c, 1.22f)) + f.solids(HoloWorld.shade(c, 0.8f))
        assertTrue("no specks on the street", tones(Color.WORLD_ASPHALT) > 10)
        assertTrue("no specks on the grass", tones(Color.WORLD_GREEN) > 10)
        // Those behind the camera are kept and not drawn.
        assertTrue(tones(Color.WORLD_ASPHALT) + tones(Color.WORLD_GREEN) <= scene.world.counts[14])
        val bare = HoloScene().apply { world.hazy = false }
        frame(bare)
        assertEquals(0, bare.world.counts[14])
    }

    // --- the ground's height (T-1105) ------------------------------------------------

    private val grid = MapPackFixture.contract.getJSONObject("pack").getJSONObject("ground").getInt("grid")

    /** The cells round the car (it stands on the corner of cell 500, 500), [height] metres at sample (x, y) counted from that corner. */
    private fun ground(height: (Int, Int) -> Int): List<HoloGroundCell> =
        (498..501).flatMap { cy ->
            (498..501).map { cx ->
                HoloGroundCell(cx, cy, grid, ShortArray(grid * grid) { k ->
                    height((cx - 500) * (grid - 1) + k % grid, (cy - 500) * (grid - 1) + k / grid).toShort()
                })
            }
        }

    /** A street north from the car with a red building and a tree beside it, 200 m on. */
    private fun hillside(cells: List<HoloGroundCell>?) = HoloScene().apply {
        world.hazy = false
        setRoads(listOf(HoloRoad(listOf(at(0f, -50f), at(0f, 400f)), "primary", lanes = 2)))
        setBuildings(listOf(HoloBuilding(box(14f, 195f, 30f, 211f), 12f, HoloLook(colour = 0xFFFF0000.toInt()))))
        setDetails(listOf(HoloDetail("tree", listOf(at(-12f, 200f)))))
        cells?.let { setGround(it) }
    }

    /** The highest point on the screen (the smallest y) of the solids [pick] chooses. */
    private fun HoloFrame.top(pick: (Int) -> Boolean): Float =
        (0 until solidCount).filter { pick(solidColor[it]) }
            .minOf { s -> (0 until solidLen[s]).minOf { pts[(solidStart[s] + it) * 2 + 1] } }

    private val red: (Int) -> Boolean = { it and 0x00FFFF == 0 && it and 0xFF0000 != 0 }

    @Test
    fun `with ground heights a road climbs a hill and a building and a tree stand on it`() {
        // 5 m a sample to the north: about 1 in 14.
        val hill = hillside(ground { _, y -> 100 + 5 * y })
        val level = hillside(null)
        val up = frame(hill)
        val flat = frame(level)
        val sample = (GeoPlane.M_PER_DEG / 100 / (grid - 1)).toFloat()
        assertEquals("200 m up the slope", 200f / sample * 5f, hill.world.rise(0f, 200f), 0.5f)
        assertEquals("the car's own ground is where heights are counted from", 0f, hill.world.rise(0f, 0f), 0.01f)
        assertEquals("east of the car the slope is level", 0f, hill.world.rise(150f, 0f), 0.01f)
        assertTrue("the road ahead rises on the screen", up.top { it == Color.WORLD_ASPHALT } < flat.top { it == Color.WORLD_ASPHALT } - 20f)
        assertTrue("the building stands on the hill", up.top(red) < flat.top(red) - 20f)
        assertTrue("the tree stands on the hill", up.top { it == Color.WORLD_TRUNK } < flat.top { it == Color.WORLD_TRUNK } - 20f)
        // A long straight is cut so it lies on the slope between its ends.
        assertTrue("the street is in pieces", hill.world.counts[1] > level.world.counts[1])
        assertTrue("open ground is drawn", hill.world.counts[13] > 0)
        assertTrue(hill.world.hilly)
    }

    @Test
    fun `a hilly city with long streets builds in a frame's time, its streets cut within their cap`() {
        // The review's ask: heights add a lookup to every point, a sheet of land and cut roads.
        val scene = HoloScene().apply {
            setBuildings(buildList {
                for (i in -30 until 30) for (j in -30 until 30) add(HoloBuilding(box(i * 30f + 8f, j * 30f + 8f, i * 30f + 24f, j * 30f + 24f), 12f))
            })
            setRoads(buildList {
                for (i in -30..30) {
                    add(HoloRoad(listOf(at(i * 30f, -900f), at(i * 30f, 900f)), "residential"))
                    add(HoloRoad(listOf(at(-900f, i * 30f), at(900f, i * 30f)), "residential"))
                }
            })
            setGround(ground { x, y -> 300 + 4 * y + (x * x) % 7 })
        }
        frame(scene)
        assertTrue(scene.world.hilly)
        assertEquals("the pieces' cap", 2400, scene.world.counts[1])
        val times = (0 until 7).map { val t = System.nanoTime(); frame(scene); (System.nanoTime() - t) / 1e6 }.sorted()
        println("T-1105 hilly dense frame build: median ${times[3]} ms, best ${times[0]} ms")
        assertTrue("a hilly dense frame took ${times[3]} ms", times[3] < 250.0)
    }

    @Test
    fun `land is the same patches after the car moves on`() {
        val scene = hillside(ground { _, y -> 100 + 5 * y })
        frame(scene)
        val before = scene.world.rise(120f, 260f) - scene.world.rise(0f, 0f)
        HoloFrame().also { scene.build(it, cam, plane.lat(180f), plane.lon(0f), 0f, 2_000L) }
        assertEquals("the ground itself does not move with the square", before, scene.world.rise(120f, 260f) - scene.world.rise(0f, 0f), 0.05f)
    }

    @Test
    fun `a building stands level on a slope, on the ground under its middle`() {
        // Steep to the east, across a building that faces the car: unpinned, its wall's foot
        // would be 9 m higher at one end than the other.
        val scene = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(HoloRoad(listOf(at(0f, -50f), at(0f, 400f)), "primary")))
            setBuildings(listOf(HoloBuilding(box(-8f, 100f, 8f, 116f), 12f, HoloLook(colour = 0xFFFF0000.toInt()))))
            setGround(ground { x, _ -> 500 + 40 * x })
        }
        val f = frame(scene)
        assertTrue(scene.world.rise(8f, 100f) - scene.world.rise(-8f, 100f) > 8f)
        fun xs(s: Int) = (0 until f.solidLen[s]).map { f.pts[(f.solidStart[s] + it) * 2] }
        val front = (0 until f.solidCount).filter { red(f.solidColor[it]) && f.solidLen[it] == 4 }.maxByOrNull { xs(it).max() - xs(it).min() }!!
        val ys = (0 until 4).map { f.pts[(f.solidStart[front] + it) * 2 + 1] }.sorted()
        assertEquals("its roof line is level", ys[0], ys[1], 1.5f)
        assertEquals("and so is its foot", ys[2], ys[3], 1.5f)
    }

    @Test
    fun `with no heights, or level ones, the scene is as flat as before`() {
        val before = frame(hillside(null))
        for (cells in listOf(emptyList(), ground { _, _ -> 250 })) {
            val scene = hillside(cells)
            val f = frame(scene)
            assertFalse(scene.world.hilly)
            assertEquals(0f, scene.world.rise(0f, 300f), 0f)
            assertEquals(0, scene.world.counts[13])
            assertEquals(before.solidCount, f.solidCount)
            assertEquals(before.top { it == Color.WORLD_ASPHALT }, f.top { it == Color.WORLD_ASPHALT }, 0f)
        }
    }

    @Test
    fun `where the map has no height the ground is level with the car, not a cliff`() {
        // Only the car's own cell is held: everything north-east of the corner it stands on.
        val scene = hillside(ground { _, y -> 100 + 5 * y }.filter { it.cx == 500 && it.cy == 500 })
        frame(scene)
        assertTrue(scene.world.rise(100f, 200f) > 5f)
        assertEquals("west of the corner nothing is known", 0f, scene.world.rise(-200f, 200f), 0.01f)
    }

    @Test
    fun `the ground's heights are read from a pack and from the house, and a broken cell is left out`() {
        val f = MapPackFixture
        val h = List(grid * grid) { it }
        val file = f.write(
            File(context.cacheDir, "ground-${System.nanoTime()}/a.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 100L,
            listOf(
                f.ground(500, 500, grid, h),
                f.ground(501, 500, grid, h.take(5)),
                f.ground(502, 500, grid, h),
                f.road("Mall", 5.0 to 5.0, 5.0 to 5.005),
            ),
        )
        val got = MapPack.open(file)!!.near(5.001, 5.001, 5.015, 5.009)!!.ground
        assertEquals(listOf(500 to 500), got.map { it.cx to it.cy })
        assertEquals(grid, got[0].n)
        assertEquals(h.map { it.toShort() }, got[0].h.toList())

        val answer = JSONObject(
            """{"buildings":[],"roads":[],"ground":[
               {"cell":[500,500],"n":2,"h":[1,2,3,99999]},
               {"cell":[501,500],"n":2,"h":[1,2,3]},
               {"cell":[502,500],"n":2,"h":[1,2,"x",4]},
               {"cell":[500.5,500],"n":2,"h":[1,2,3,4]},
               {"cell":[99999,500],"n":2,"h":[1,2,3,4]},
               {"cell":[503,500],"n":900,"h":[1,2,3,4]},
               "x"]}""",
        )
        val near = MapsJson.near(answer)
        assertEquals(listOf(500 to 500), near.ground.map { it.cx to it.cy })
        assertEquals("a height off the Earth is its nearest real one", MapsJson.MAX_GROUND_M.toShort(), near.ground[0].h[3])
        assertTrue("an answer from before the ground has none", MapsJson.near(JSONObject("""{"buildings":[],"roads":[]}""")).ground.isEmpty())
        // Two tiles that hold the same cell are one cell.
        assertEquals(1, NearMap.merge(listOf(near, near), 10).ground.size)
    }

    // --- the light (T-1107) ------------------------------------------------------------
    // The owner, 2026-11-12: "accurate lighting depending on the time of day, and from street
    // lamps". The scene's place is 5 N 5 E, where on the March equinox the sun is overhead at
    // 11:40 UTC, due east at 08:00, due west at 15:30, on the horizon at 17:50 and far under at 23:40.

    private fun utc(h: Int, m: Int) = java.time.LocalDateTime.of(2024, 3, 20, h, m).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()

    // A pool is the lamp's own colour: a shadow and the car's beam are translucent too (T-1164).
    private fun HoloFrame.pools() = (0 until solidCount).map { solidColor[it] }
        .filter { it and 0xFFFFFF == Color.WORLD_LAMP and 0xFFFFFF }.map { it ushr 24 }.filter { it in 1..0x60 }

    @Test
    fun `at noon every surface is brighter, the lamp is off with no pool under it and no window is lit`() {
        val scene = scene().apply { utcMs = utc(11, 40) }
        val f = frame(scene)
        assertEquals(1f, scene.daylight)
        assertEquals("the streets in daylight", 2, f.solids(HoloWorld.daylit(Color.WORLD_ASPHALT, 1f)))
        assertEquals("a street still in its night tone", 0, f.solids(Color.WORLD_ASPHALT))
        assertTrue("daylight did not brighten the asphalt", (HoloWorld.daylit(Color.WORLD_ASPHALT, 1f) and 0xFF) > (Color.WORLD_ASPHALT and 0xFF) + 40)
        assertEquals("a pool of lamp light at noon", emptyList<Int>(), f.pools())
        assertEquals("the lamp's head is lit at noon", 0, f.solids(Color.WORLD_LAMP))
        assertEquals(0, scene.world.litWindows)
    }

    @Test
    fun `after dark the world is its night tones, the lamp lights the road under it and some windows are lit, the same ones each frame`() {
        val scene = scene().apply { utcMs = utc(23, 40) }
        val f = frame(scene)
        assertEquals(0f, scene.daylight)
        assertEquals(2, f.solids(Color.WORLD_ASPHALT))
        assertEquals("the lamp's pool, at its full strength", 1, f.pools().size)
        assertEquals("the lamp's head", 1, f.solids(Color.WORLD_LAMP))
        val lit = scene.world.litWindows
        assertTrue("no window is lit after dark", lit > 0)
        assertEquals("a lit window is not its colour", lit, f.solids(HoloWorld.mix(0, Color.WORLD_WINDOW_LIT, 1f)))
        assertTrue("every window is lit: nobody is out", lit < f.solidCount / 2)
        frame(scene)
        assertEquals("the lit windows moved between frames", lit, scene.world.litWindows)
        // Driving on re-picks what is near, nearest first: a building keeps its lit windows whatever its rank.
        fun litOf(extra: List<HoloBuilding>): Int {
            val s = HoloScene().apply {
                world.hazy = false
                setBuildings(extra + HoloBuilding(box(14f, 20f, 30f, 36f), 12f))
                utcMs = utc(23, 40)
            }
            frame(s)
            return s.world.litWindows
        }
        // A windowless shed nearer the car takes rank 0 and adds no window of its own.
        assertEquals("a building's lit windows changed with its rank", litOf(emptyList()), litOf(listOf(HoloBuilding(box(4f, 2f, 8f, 6f), 3f, HoloLook(kind = "shed")))))
        // With no clock the scene is what it always was: night, lamps on, windows dark.
        val plain = frame(scene.apply { utcMs = 0L })
        assertEquals(0, scene.world.litWindows)
        assertEquals(1, plain.pools().size)
        // The clock adds only the car's beam and the dark at the foot of the walls (T-1164).
        assertEquals(f.solidCount - f.beams().size - f.inked(HoloWorld.FOOT_ALPHA).size, plain.solidCount)
    }

    @Test
    fun `at dusk the light is between the two and the lamp's pool is coming up`() {
        val scene = scene().apply { utcMs = utc(17, 50) }
        val f = frame(scene)
        assertTrue("${scene.daylight}", scene.daylight > 0.2f && scene.daylight < 0.8f)
        val night = frame(scene().apply { utcMs = utc(23, 40) }).pools().single()
        assertTrue("the pool at dusk is as strong as at night", f.pools().single() in 1 until night)
    }

    @Test
    fun `the walls facing the sun are the lit ones, the west wall in the afternoon and not in the morning`() {
        // The red building stands east of the street, so the wall the camera sees faces west.
        // A wall of pure red has no green or blue but what daylight lifts black to.
        val lift = HoloWorld.daylit(0xFF000000.toInt(), 1f) and 0xFF
        fun brightestRed(h: Int, m: Int): Int {
            val f = frame(scene().apply { utcMs = utc(h, m) })
            return (0 until f.solidCount).map { f.solidColor[it] }
                .filter { c -> (c shr 8 and 0xFF) == lift && (c and 0xFF) == lift && (c shr 16 and 0xFF) > lift }
                .maxOf { it shr 16 and 0xFF }
        }
        assertTrue("the afternoon sun does not light the west wall", brightestRed(15, 30) > brightestRed(8, 0) + 20)
    }

    // --- T-1164: shadows, the car's lights and the foot of a wall ----------------------------
    // The owner, 2026-11-12: "way better lighting". Each is drawn only when the map has a clock.

    private fun HoloFrame.inked(alpha: Float) = (0 until solidCount).filter { solidColor[it] == HoloWorld.ink(alpha) }
    private fun HoloFrame.beams() = (0 until solidCount).filter { solidColor[it] and 0xFFFFFF == HoloWorld.BEAM_INK && solidColor[it] ushr 24 < 0xFF }
    private fun HoloFrame.midX(solids: List<Int>) = solids.map { s -> (0 until solidLen[s]).map { pts[(solidStart[s] + it) * 2] }.average() }.average()
    private fun block(h: Int, m: Int, vararg details: HoloDetail) = HoloScene().apply {
        world.hazy = false
        setBuildings(listOf(HoloBuilding(box(-8f, 40f, 8f, 56f), 10f)))
        setDetails(details.toList())
        utcMs = utc(h, m)
    }

    @Test
    fun `a building and a tree throw a shadow away from the sun, west in the morning and east in the afternoon`() {
        val full = HoloWorld.SHADOW_ALPHA
        val morning = frame(block(8, 0))
        val afternoon = frame(block(15, 30))
        assertTrue("no shadow in the morning", morning.inked(full).isNotEmpty())
        assertTrue("no shadow in the afternoon", afternoon.inked(full).isNotEmpty())
        // The car heads north, so west is the left of the screen.
        assertTrue("the shadow did not move from west to east", morning.midX(morning.inked(full)) + 20 < afternoon.midX(afternoon.inked(full)))
        val tree = HoloDetail("tree", listOf(at(-6f, 25f)))
        assertEquals("a tree's shadow", morning.inked(full).size + 1, frame(block(8, 0, tree)).inked(full).size)
    }

    @Test
    fun `a building far from the car throws no shadow, so a city's worth of them costs nothing`() {
        fun shadows(north: Float): Int {
            val s = HoloScene().apply {
                world.hazy = false
                setBuildings(listOf(HoloBuilding(box(-8f, north, 8f, north + 16f), 10f)))
                utcMs = utc(8, 0)
            }
            return frame(s).inked(HoloWorld.SHADOW_ALPHA * s.daylight).size
        }
        assertTrue("no shadow from a near building", shadows(60f) > 0)
        assertEquals(0, shadows(200f))
    }

    @Test
    fun `a shadow is long under a low sun and no longer than its cap, and there is none overhead, at night or with no clock`() {
        assertEquals(0f, HoloWorld.shadowReach(90.0), 0.001f)
        assertEquals(1f, HoloWorld.shadowReach(45.0), 0.001f)
        assertTrue(HoloWorld.shadowReach(20.0) > HoloWorld.shadowReach(60.0))
        assertEquals(HoloWorld.SHADOW_MAX, HoloWorld.shadowReach(3.0))
        assertEquals("the sun on the horizon", 0f, HoloWorld.shadowReach(0.0))
        assertEquals(0f, HoloWorld.shadowReach(-30.0))
        assertEquals("a shadow at night", 0, frame(block(23, 40)).inked(HoloWorld.SHADOW_ALPHA).size)
        val plain = frame(block(8, 0).apply { utcMs = 0L })
        assertEquals("with no clock something translucent but the lamps' pools is drawn", 0,
            (0 until plain.solidCount).count { plain.solidColor[it] ushr 24 < 0xFF })
    }

    @Test
    fun `after dark the car's lights are on the road ahead of it, fainter with distance, and by day they are not`() {
        val night = frame(block(23, 40))
        val alphas = night.beams().map { night.solidColor[it] ushr 24 }
        assertEquals("$alphas", 3, alphas.size)
        assertEquals("the beam does not fade", alphas.sortedDescending(), alphas)
        assertEquals(3, alphas.toSet().size)
        // Ahead is up the screen: the far band is above the near one.
        assertTrue(night.top { it == night.solidColor[night.beams().last()] } < night.top { it == night.solidColor[night.beams().first()] })
        assertEquals("the car's lights at noon", 0, frame(block(11, 40)).beams().size)
    }

    @Test
    fun `the car heads north with east on its right, which is what west and east mean in these tests`() {
        fun x(e0: Float, e1: Float): Double {
            val f = frame(HoloScene().apply { world.hazy = false; setBuildings(listOf(HoloBuilding(box(e0, 40f, e1, 56f), 10f, HoloLook(colour = 0xFFFF0000.toInt())))) })
            return f.midX((0 until f.solidCount).filter { red(f.solidColor[it]) })
        }
        assertTrue(x(-30f, -14f) < x(14f, 30f))
    }

    @Test
    fun `a dense city under a morning sun still builds in a frame's time`() {
        val city = buildList {
            for (i in -30 until 30) for (j in -30 until 30) add(HoloBuilding(box(i * 30f + 8f, j * 30f + 8f, i * 30f + 24f, j * 30f + 24f), 12f))
        }
        val scene = HoloScene().apply { setBuildings(city); utcMs = utc(8, 0) }
        val f = frame(scene)
        assertTrue("no shadow in the city", f.inked(HoloWorld.SHADOW_ALPHA).isNotEmpty())
        val times = (0 until 7).map { val t = System.nanoTime(); frame(scene); (System.nanoTime() - t) / 1e6 }.sorted()
        println("T-1164 dense lit frame build: median ${times[3]} ms, best ${times[0]} ms")
        assertTrue("a dense lit frame took ${times[3]} ms", times[3] < 250.0)
    }

    @Test
    fun `a near wall is dark where it meets the ground, and a far one is not`() {
        val near = HoloScene().apply {
            world.hazy = false
            setBuildings(listOf(HoloBuilding(box(-8f, 20f, 8f, 36f), 10f)))
            utcMs = utc(8, 0)
        }
        assertTrue("no dark at the wall's foot", frame(near).inked(HoloWorld.FOOT_ALPHA).isNotEmpty())
        val far = HoloScene().apply {
            world.hazy = false
            setBuildings(listOf(HoloBuilding(box(-8f, 140f, 8f, 156f), 10f)))
            utcMs = utc(8, 0)
        }
        assertEquals(0, frame(far).inked(HoloWorld.FOOT_ALPHA).size)
    }

    // --- T-1108: what is live today, from the house's work-zone feed -------------------------

    @Test
    fun `live roadworks stand as boards along their line, and with none live the street is as it was`() {
        val s = street()
        val bare = frame(s).solidCount
        assertEquals(0, frame(s).solids(Color.WORLD_CONE))
        s.setLive(listOf(HoloDetail("roadworks", listOf(at(0f, 10f), at(0f, 40f)))))
        val f = frame(s)
        assertTrue("boards along thirty metres", f.solids(Color.WORLD_CONE) >= 4)
        assertEquals("works are not a closed road", 0, s.world.counts[15])
        s.setLive(emptyList())
        assertEquals(bare, frame(s).solidCount)
    }

    @Test
    fun `a closed road is barred across at each end of the closure, beside what the map already has`() {
        val s = street(HoloDetail("barrier", listOf(at(9f, 10f), at(9f, 40f))))
        val mapped = frame(s).solidCount
        s.setLive(listOf(HoloDetail("closure", listOf(at(0f, 20f), at(0f, 60f)))))
        val f = frame(s)
        assertEquals("a barricade at each end", 2, s.world.counts[15])
        assertEquals("the mapped rail stays", 1, s.world.counts[11])
        // Three rails a barricade, striped, on two legs.
        assertTrue(f.solidCount >= mapped + 2 * (3 * 2 + 2))
        // The end 400 m off is not drawn; the near one still is.
        s.setLive(listOf(HoloDetail("closure", listOf(at(0f, 30f), at(0f, 400f)))))
        frame(s)
        assertEquals(1, s.world.counts[15])
        // A town with every street shut is six barricades a frame, the nearest first.
        s.setLive((1..5).map { HoloDetail("closure", listOf(at(0f, 10f * it), at(0f, 10f * it + 5f))) })
        frame(s)
        assertEquals(6, s.world.counts[15])
    }

    @Test
    fun `the house's live answer reads as roadworks and closures, and what does not read is left out`() {
        val got = MapsJson.live(org.json.JSONObject("""{"source":"on","as_of":1,"zones":[
            {"kind":"roadworks","line":[[5.001,5.001],[5.002,5.002]]},
            {"kind":"closure","line":[[5.001,5.001],[5.002,5.002],[5.003,5.002]]},
            {"kind":"parade","line":[[5.001,5.001],[5.002,5.002]]},
            {"kind":"closure","line":[[5.001,5.001]]},
            {"kind":"closure","line":[[5.001,5.001],["x",2]]},
            {"kind":"roadworks"}, 7]}"""))
        assertEquals(listOf("roadworks" to 2, "closure" to 3), got.map { it.kind to it.pts.size })
        assertEquals(5.001, got[0].pts[0].lon, 1e-9)
        assertEquals(emptyList<HoloDetail>(), MapsJson.live(org.json.JSONObject("""{"source":"off","zones":[]}""")))
        assertEquals(emptyList<HoloDetail>(), MapsJson.live(org.json.JSONObject("{}")))
    }

    // --- T-1181: a bridge is a level deck, the roads to it climb, its piers stand at a spacing ---

    private fun overpass(vararg extra: HoloRoad) = HoloScene().apply {
        world.hazy = false
        setRoads(listOf(HoloRoad(listOf(at(0f, 150f), at(0f, 250f)), "primary", lanes = 2, bridge = true)) + extra)
    }

    private val approaches = arrayOf(
        HoloRoad(listOf(at(0f, 30f), at(0f, 150f)), "primary", lanes = 2),
        HoloRoad(listOf(at(0f, 250f), at(0f, 400f)), "primary", lanes = 2),
    )

    @Test
    fun `a bridge's deck is level end to end and the roads to its ends climb to it at no more than a road's grade (T-1181)`() {
        val s = overpass(*approaches)
        frame(s)
        fun y(n: Float) = s.world.riseOn(0f, n, 0f, 1f)
        val deck = y(200f)
        assertTrue("the deck is metres up: $deck", deck > 5f)
        for (n in 150..250 step 10) assertEquals("level at $n", deck, y(n.toFloat()), 0.01f)
        assertEquals("the road meets the deck", deck, y(149f), 0.1f)
        for (range in listOf(150 downTo 40 step 10, 250..390 step 10)) range.zipWithNext().forEach { (a, b) ->
            assertTrue("${y(a.toFloat())} to ${y(b.toFloat())} over 10 m", kotlin.math.abs(y(a.toFloat()) - y(b.toFloat())) <= 0.55f)
        }
        assertTrue("it climbs", y(100f) > 1f && y(100f) < deck)
        assertEquals(0f, y(390f), 0.01f)
        // A road that ends on no bridge is as flat as it was.
        val plain = HoloScene().apply { world.hazy = false; setRoads(listOf(approaches[0])) }
        frame(plain)
        assertEquals(0f, plain.world.riseOn(0f, 100f, 0f, 1f), 0.01f)
    }

    @Test
    fun `piers stand every 30 m of a high deck, never on a road beneath, and an approach has a bank with no pier (T-1181)`() {
        // The deck is 100 m: piers at 15, 45 and 75 m along it, north 165, 195 and 225.
        val across = HoloRoad(listOf(at(-30f, 195f), at(30f, 195f)), "residential")
        val s = overpass(*approaches, across)
        val f = frame(s)
        val piers = s.world.piersOf()
        val along = piers.map { it[2] }
        assertEquals("a pier every 30 m but the one the road is under: $along", listOf(15f, 75f), along)
        assertTrue("none on the approaches", piers.all { it[1] in 150f..250f })
        val clear = overpass(*approaches)
        frame(clear)
        assertEquals(listOf(15f, 45f, 75f), clear.world.piersOf().map { it[2] })
        // Nor in water.
        val wet = overpass(*approaches)
        wet.setDetails(listOf(HoloDetail("water", box(-20f, 155f, 20f, 175f))))
        frame(wet)
        assertEquals(listOf(45f, 75f), wet.world.piersOf().map { it[2] })
        // The climb to the deck is a bank with filled sides, and the road with no bridge to climb to has none.
        val bank = HoloWorld.shade(Color.WORLD_LAND, HoloWorld.BANK_DARK)
        assertTrue("an approach has sides: ${f.solids(bank)}", f.solids(bank) >= 2)
        assertEquals(0, frame(HoloScene().apply { world.hazy = false; setRoads(listOf(approaches[0])) }).solids(bank))
    }

    @Test
    fun `a deck overhead between the camera and the car is not drawn, so it never hides the road (T-1181)`() {
        val fascia = HoloWorld.shade(Color.WORLD_BARRIER, HoloWorld.FASCIA_DARK)
        val own = HoloRoad(listOf(at(0f, -200f), at(0f, 400f)), "motorway", lanes = 2)
        fun footbridge(north: Float) = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(own, HoloRoad(listOf(at(-50f, north), at(50f, north)), "path", bridge = true)))
        }
        // The camera is 11.5 m behind the car: a footbridge 6 m behind it would be painted over everything.
        assertEquals(0, frame(footbridge(-6f)).solids(fascia))
        assertTrue("one ahead is drawn", frame(footbridge(60f)).solids(fascia) > 0)
    }

    @Test
    fun `two carriageways on one deck have a side only at the deck's outer edges (T-1181)`() {
        val fascia = HoloWorld.shade(Color.WORLD_BARRIER, HoloWorld.FASCIA_DARK)
        fun deck(vararg easts: Float) = HoloScene().apply {
            world.hazy = false
            setRoads(easts.map { HoloRoad(listOf(at(it, 150f), at(it, 250f)), "primary", lanes = 2, bridge = true) })
        }
        val one = frame(deck(0f)).solids(fascia)
        assertTrue(one >= 2)
        // 6 m apart and 5 m to each edge: the inner sides lie on the other's surface.
        assertEquals("the pair has the outer two sides only", one, frame(deck(0f, 6f)).solids(fascia))
        // Far enough apart to be two bridges: each keeps both sides.
        assertEquals(one * 2, frame(deck(0f, 40f)).solids(fascia))
    }

    // --- T-1191: bridges and cut lines at shallow merges, stacked decks, long decks and bends ---

    @Test
    fun `a road keeps its lines along a link that merges into it at a shallow angle, and gives them up only at the mouth (T-1191)`() {
        val runs = paint(way("primary", 0f to -100f, 0f to -20f, 0f to 100f), way("primary_link", 0f to -20f, 14f to 100f, lanes = 2))
        // The link's surface lies over the road's edge to 63 m on and over its centre line to 23 m: that is the link's, not a mouth.
        for (n in listOf(30f, 45f, 60f)) assertTrue("the road's edge at $n", painted(runs, 4.7f, n))
        for (n in listOf(5f, 15f, 22f)) assertTrue("the road's centre line at $n", painted(runs, 0f, n))
        // The mouth itself is still open.
        assertFalse("the edge at the mouth", painted(runs, 4.7f, -14f))
    }

    @Test
    fun `a road bending 45 degrees, or with a metre between its ways, keeps its own lines (T-1191)`() {
        val bent = paint(way("primary", 0f to -50f, 0f to 0f), way("primary", 0f to 0f, 35.36f to 35.36f))
        val d = 0.7071f
        for (off in listOf(-4.7f, 0f, 4.7f)) for (s in 1..48) {
            assertTrue("45 degrees, offset $off, $s m before", painted(bent, off, -s.toFloat()))
            assertTrue("45 degrees, offset $off, $s m after", painted(bent, s * d + off * d, s * d - off * d))
        }
        // The next way starts a metre on: the lines of both run to their own ends.
        val gap = paint(way("primary", 0f to -50f, 0f to 0f), way("primary", 0f to 1f, 0f to 50f))
        for (off in listOf(-4.7f, 4.7f)) for (n in listOf(-10f, -3f, -0.7f, 1.7f, 4f, 10f)) {
            assertTrue("a metre gap, offset $off at $n", painted(gap, off, n))
        }
    }

    @Test
    fun `two bridges of different layers that meet at one vertex are two decks, each at its own height (T-1191)`() {
        fun deck(layer: Int, from: Float, to: Float) = HoloRoad(listOf(at(0f, from), at(0f, to)), "primary", lanes = 2, bridge = true, layer = layer)
        val s = HoloScene().apply { world.hazy = false; setRoads(listOf(deck(1, 150f, 250f), deck(2, 250f, 350f))) }
        frame(s)
        assertEquals("the lower deck", 5.5f, s.world.riseOn(0f, 200f, 0f, 1f), 0.1f)
        assertEquals("the upper deck", 11f, s.world.riseOn(0f, 300f, 0f, 1f), 0.1f)
        // The same layer is one deck still: one level over both ways.
        val one = HoloScene().apply { world.hazy = false; setRoads(listOf(deck(1, 150f, 250f), deck(1, 250f, 350f))) }
        frame(one)
        assertEquals(one.world.riseOn(0f, 200f, 0f, 1f), one.world.riseOn(0f, 300f, 0f, 1f), 0.01f)
    }

    @Test
    fun `piers keep one spacing along a deck of two ways, not a new start at each (T-1191)`() {
        val s = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(0f, 150f), at(0f, 250f)), "primary", lanes = 2, bridge = true),
                HoloRoad(listOf(at(0f, 250f), at(0f, 350f)), "primary", lanes = 2, bridge = true),
            ))
        }
        frame(s)
        val north = s.world.piersOf().map { it[1] }.sorted()
        assertEquals("a pier every 30 m of 200: $north", 7, north.size)
        north.zipWithNext().forEach { (a, b) -> assertEquals("spacing $a to $b", 30f, b - a, 0.05f) }
    }

    @Test
    fun `a dense frame of 2400 pieces looks at a few neighbours for each cut, not at every piece, and builds no slower (T-1191)`() {
        val grid = buildList {
            for (i in -15..14) {
                add(HoloRoad((-20..20).map { at(i * 20f + 10f, it * 20f) }, "primary", lanes = 4))
                add(HoloRoad((-20..20).map { at(it * 20f, i * 20f + 10f) }, "primary", lanes = 4))
            }
        }
        val scene = HoloScene().apply { world.hazy = false; setRoads(grid) }
        frame(scene)
        val pieces = scene.world.counts[1]
        assertTrue("a city's worth of pieces: $pieces", pieces >= 2000)
        scene.world.scans = 0
        frame(scene)
        val scans = scene.world.scans
        println("T-1191 scan counts: $scans pieces looked at for $pieces")
        assertTrue("$scans pieces looked at for $pieces", scans < pieces * 25L)
        val times = (0 until 7).map { val t = System.nanoTime(); frame(scene); (System.nanoTime() - t) / 1e6 }.sorted()
        println("T-1191 dense 2400-piece frame build: median ${times[3]} ms, best ${times[0]} ms")
        assertTrue("a frame of $pieces pieces took ${times[3]} ms", times[3] < 250.0)
    }

    @Test
    fun `a deck the car drives under is clipped at the car, not dropped a piece at a time (T-1191)`() {
        val fascia = HoloWorld.shade(Color.WORLD_BARRIER, HoloWorld.FASCIA_DARK)
        fun deck(from: Float, to: Float) = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(HoloRoad(listOf(at(12f, from), at(12f, to)), "primary", lanes = 2, bridge = true)))
        }
        // 100 m of deck from 20 m behind the car: pieces of 16.7 m, the 3rd starts at 13.3 m ahead; the 2nd crosses the car's line.
        // Whole pieces: 4 x 2 sides. Clipped where it is 3 m ahead of the car: one more piece, 5 x 2.
        assertEquals(10, frame(deck(-20f, 80f)).solids(fascia))
        // A deck wholly behind the car is not drawn.
        assertEquals(0, frame(deck(-120f, -20f)).solids(fascia))
    }

    // --- T-1184: a tunnel is under what it passes beneath: no road on the surface, a portal at each end ---

    /** A street north through the car's place, with the road [middle] between two that join it; null leaves a gap. */
    private fun through(from: Float, to: Float, middle: Boolean?, vararg more: HoloBuilding) = HoloScene().apply {
        world.hazy = false
        setRoads(listOfNotNull(
            HoloRoad(listOf(at(0f, -300f), at(0f, from)), "primary", lanes = 2),
            middle?.let { HoloRoad(listOf(at(0f, from), at(0f, to)), "primary", lanes = 2, tunnel = it) },
            HoloRoad(listOf(at(0f, to), at(0f, 400f)), "primary", lanes = 2),
        ))
        setBuildings(more.toList())
    }

    private val portalFace = HoloWorld.shade(Color.WORLD_BARRIER, HoloWorld.PORTAL_DARK)
    private val tubeWall = HoloWorld.shade(Color.WORLD_BARRIER, HoloWorld.TUBE_WALL_DARK)
    private val tubeRoof = HoloWorld.shade(Color.WORLD_BARRIER, HoloWorld.TUBE_ROOF_DARK)

    @Test
    fun `a tunnel's road and paint are not drawn from outside, it has a portal at each end, and a building passage stays a road (T-1184)`() {
        // The car is on the street at 0 m; the tunnel is 40 to 120 m ahead of it.
        val gap = frame(through(40f, 120f, null))
        val tunnel = frame(through(40f, 120f, true))
        for (color in listOf(Color.WORLD_ASPHALT, Color.WORLD_MARKING, Color.WORLD_MARKING_CENTRE, Color.WORLD_PAVEMENT, Color.WORLD_KERB)) {
            assertEquals("a tunnel is not on the surface: $color", gap.solids(color), tunnel.solids(color))
        }
        assertEquals("a portal at each end", 2, tunnel.solids(portalFace))
        assertEquals("none without a tunnel", 0, gap.solids(portalFace))
        // A passage through a building is a road: tagged for a tunnel by the map, it is not one here.
        val passage = frame(through(40f, 120f, false))
        assertTrue("drawn: ${passage.solids(Color.WORLD_ASPHALT)} over ${gap.solids(Color.WORLD_ASPHALT)}", passage.solids(Color.WORLD_ASPHALT) > gap.solids(Color.WORLD_ASPHALT))
        assertEquals(0, passage.solids(portalFace))
        // Nor is it seen from outside as walls and a roof.
        assertEquals(0, tunnel.solids(tubeWall) + tunnel.solids(tubeRoof))
    }

    @Test
    fun `two tunnel ways that meet are one tunnel with a portal at its two ends only (T-1184)`() {
        val s = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(0f, -300f), at(0f, 40f)), "primary", lanes = 2),
                HoloRoad(listOf(at(0f, 40f), at(0f, 80f)), "primary", lanes = 2, tunnel = true),
                HoloRoad(listOf(at(0f, 80f), at(0f, 120f)), "primary", lanes = 2, tunnel = true),
                HoloRoad(listOf(at(0f, 120f), at(0f, 400f)), "primary", lanes = 2),
            ))
        }
        assertEquals(2, frame(s).solids(portalFace))
    }

    @Test
    fun `with the car on a tunnel its road, walls and roof are drawn, and the buildings above it are not (T-1184)`() {
        val over = HoloBuilding(box(-10f, 40f, 10f, 60f), 20f, HoloLook(roofColour = 0xFF112233.toInt()))
        val beside = HoloBuilding(box(30f, 40f, 50f, 60f), 20f, HoloLook(roofColour = 0xFF445566.toInt()))
        // The tunnel runs from 100 m behind the car to 200 m ahead of it.
        val inside = frame(through(-100f, 200f, true, over, beside))
        val gap = frame(through(-100f, 200f, null, over, beside))
        assertTrue("its road: ${inside.solids(Color.WORLD_ASPHALT)}", inside.solids(Color.WORLD_ASPHALT) > gap.solids(Color.WORLD_ASPHALT))
        assertTrue("its walls: ${inside.solids(tubeWall)}", inside.solids(tubeWall) >= 2)
        assertTrue("its roof: ${inside.solids(tubeRoof)}", inside.solids(tubeRoof) >= 1)
        assertEquals("no portal from inside", 0, inside.solids(portalFace))
        assertEquals("the building over it is not", 0, inside.solids(0xFF112233.toInt()))
        assertTrue("the one beside is", inside.solids(0xFF445566.toInt()) > 0)
        // From outside both stand, and the car one piece short of the tunnel has no tube.
        val outside = frame(through(40f, 120f, true, over, beside))
        assertTrue(outside.solids(0xFF112233.toInt()) > 0 && outside.solids(0xFF445566.toInt()) > 0)
    }

    @Test
    fun `a back street has no painted lines, whatever lanes it is tagged with, and a tertiary keeps its paint (T-1189)`() {
        fun paint(kind: String) = frame(street(road = HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), kind, lanes = 2)))
            .let { it.solids(Color.WORLD_MARKING) + it.solids(Color.WORLD_MARKING_CENTRE) }
        for (kind in listOf("residential", "unclassified", "living_street", "service")) assertEquals(kind, 0, paint(kind))
        assertTrue("a tertiary road is painted", paint("tertiary") > 2)
    }

    @Test
    fun `a street-name sign beside or behind the car is not drawn across it, and one ahead is (T-1197)`() {
        fun blades(north: Float) = frame(HoloScene().apply {
            world.hazy = false
            setRoads(listOf(
                HoloRoad(listOf(at(0f, -50f), at(0f, north), at(0f, 200f)), "primary", lanes = 2, name = "Mall"),
                HoloRoad(listOf(at(-60f, north), at(0f, north), at(60f, north)), "residential", name = "Mill Lane"),
            ))
        })
        assertEquals("ahead", 2, blades(14f).solids(Color.WORLD_SIGN_BLADE))
        // The lens is behind the car: a post there fills the picture and hides the car.
        assertEquals("beside the car", 0, blades(-8f).solids(Color.WORLD_SIGN_BLADE))
        assertEquals("behind it", 0, blades(-6f).solids(Color.WORLD_SIGN_BLADE))
        assertEquals(emptyList<String>(), blades(-6f).said())
    }

    @Test
    fun `a slope of open ground is a shade of the bare ground beside it, not blue-grey (T-1199)`() {
        // Steep enough either way that the sun fully lights or fully shades one of the two.
        val slopes = listOf(frame(hillside(ground { _, y -> 100 + 40 * y })), frame(hillside(ground { x, _ -> 500 + 40 * x })))
        assertTrue("a slope is drawn in the ground's own colour, lit or shaded",
            slopes.sumOf { it.solids(HoloWorld.landTone(0f)) + it.solids(HoloWorld.landTone(1f)) } > 0)
        assertEquals("level ground and a slope side on to the sun match", Color.WORLD_GROUND, HoloWorld.landTone(0.5f))
        assertEquals("no blue-grey land", 0, slopes.sumOf { it.solids(Color.WORLD_LAND) + it.solids(Color.WORLD_LAND_LIT) })
    }

    // --- T-1238: a path over a street, a cross street's edge across the mouth ----------------------------------

    /** The colour on top at the ground point ([e], [n]) in [f]: the last solid, in the order drawn, that holds its screen spot. */
    private fun topAt(f: HoloFrame, e: Float, n: Float): Int {
        val out = FloatArray(3)
        cam.view(e, 0f, n, out)
        val sx = cam.screenX(out[0], out[2])
        val sy = cam.screenY(out[1], out[2])
        var top = 0
        for (k in 0 until f.solidCount) {
            val i = f.solidOrder[k]
            var inside = false
            var a = f.solidStart[i] + f.solidLen[i] - 1
            for (b in f.solidStart[i] until f.solidStart[i] + f.solidLen[i]) {
                val xa = f.pts[a * 2]; val ya = f.pts[a * 2 + 1]
                val xb = f.pts[b * 2]; val yb = f.pts[b * 2 + 1]
                if ((ya > sy) != (yb > sy) && sx < (xb - xa) * (sy - ya) / (yb - ya) + xa) inside = !inside
                a = b
            }
            if (inside) top = f.solidColor[i]
        }
        return top
    }

    private fun scene(vararg roads: HoloRoad) = HoloScene().apply { world.hazy = false; setRoads(roads.toList()) }

    @Test
    fun `a footway crossing a street is not seen on its carriageway`() {
        val road = way("primary", 0f to -20f, 0f to 30f, 0f to 120f, lanes = 2)
        for (shared in listOf(true, false)) {
            val walk = if (shared) way("footway", -40f to 30f, 0f to 30f, 40f to 30f) else way("footway", -40f to 30f, 40f to 30f)
            val f = frame(scene(road, walk))
            for (x in listOf(-3.5f, -1.2f, 1.2f, 3.5f)) {
                assertEquals("shared $shared, at $x", Color.WORLD_ASPHALT, topAt(f, x, 30.3f))
            }
            // Out beside the street it is a path.
            assertEquals(Color.WORLD_PATH, topAt(f, 12f, 30.3f))
        }
    }

    @Test
    fun `a street meeting or crossing another of another width paints none of its lines on it, whatever lies beside them`() {
        // [step] cuts each way at a vertex every so many metres, as a mapped street has.
        fun cut(step: Float, vararg pts: Pair<Float, Float>): List<Pair<Float, Float>> = pts.toList().zipWithNext().flatMap { (a, b) ->
            val n = kotlin.math.ceil(kotlin.math.hypot(b.first - a.first, b.second - a.second) / step).toInt().coerceAtLeast(1)
            (0 until n).map { a.first + (b.first - a.first) * it / n to a.second + (b.second - a.second) * it / n }
        } + pts.last()
        fun pieces(line: List<Pair<Float, Float>>, half: Float) = line.zipWithNext().map { (a, b) -> floatArrayOf(a.first, a.second, b.first, b.second, half) }
        for (angle in listOf(90f, 60f, 40f)) for (step in listOf(1000f, 9f, 3f)) for (shared in listOf(true, false)) for (through in listOf(false, true)) for (beside in listOf(false, true)) {
            val tag = "angle $angle, step $step, shared $shared, through $through, footways $beside"
            val main = if (shared) cut(step, 0f to -20f, 0f to 30f, 0f to 120f) else cut(step, 0f to -20f, 0f to 120f)
            val sin = kotlin.math.sin(Math.toRadians(angle.toDouble())).toFloat()
            val cos = kotlin.math.cos(Math.toRadians(angle.toDouble())).toFloat()
            val side = when {
                shared && through -> cut(step, -70f to 30f, 0f to 30f, 70f to 30f)
                shared -> cut(step, -70f to 30f, 0f to 30f)
                through -> cut(step, -70f to 30f, 70f to 30f)
                else -> cut(step, -70f to 30f, 0f to 30f)
            }.map { (x, _) -> x * sin to 30f + x * cos }
            val roads = ArrayList<HoloRoad>()
            roads += way("primary", *main.toTypedArray(), lanes = 2)
            roads += way("tertiary", *side.toTypedArray())
            if (beside) {
                for (x in listOf(-7.2f, 7.2f)) roads += way("footway", *cut(step, x to -20f, x to 120f).toTypedArray())
                for (y in listOf(23f, 37f)) roads += way("footway", *cut(step, -70f to y, 70f to y).toTypedArray())
                roads += way("footway", *cut(step, -20f to 30f, 20f to 30f).toTypedArray())
            }
            val runs = paint(*roads.toTypedArray())
            assertTrue("$tag: the cross street is painted", pieces(side, 4f).any { s -> runs.any { it.ofPiece(s[0], s[1], s[2], s[3]) } })
            assertEquals(tag, emptyList<String>(), lyingOn(runs, pieces(side, 4f), pieces(main, 5f)))
        }
    }

    // --- T-1235: a road's turn lanes are arrows on them before the junction -------------------

    /** Every arrow the scene lays: [tail east, tail north, east, north the arrow points, its turns (1 left, 2 through, 4 right)]. */
    private fun arrows(vararg roads: HoloRoad): List<FloatArray> {
        val scene = HoloScene().apply { world.hazy = false; setRoads(roads.toList()) }
        val laid = ArrayList<FloatArray>()
        scene.world.arrowTap = { laid += it }
        frame(scene)
        return laid
    }

    private fun lanesWay(vararg pts: Pair<Float, Float>, lanes: Int, oneway: Int = 0, kind: String = "primary", forward: String? = null,
        backward: String? = null, both: String? = null, bridge: Boolean = false) =
        HoloRoad(pts.map { at(it.first, it.second) }, kind, lanes = lanes, oneway = oneway, turnLanes = both,
            turnLanesForward = forward, turnLanesBackward = backward, bridge = bridge, layer = if (bridge) 1 else 0)

    @Test
    fun `a one-way road with turn lanes has an arrow on each lane, a fixed distance before the junction end (T-1235)`() {
        val laid = arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 3, oneway = 1, both = "left|through|through;right")).sortedBy { it[0] }
        assertEquals(listOf(1f, 2f, 6f), laid.map { it[4] })
        // North along the road, 9 m before its end at 40: the tail of a 4 m arrow 5 m short of it.
        for (a in laid) { assertEquals(31f, a[1], 0.01f); assertEquals(0f, a[2], 0.001f); assertEquals(1f, a[3], 0.001f) }
        // The lanes are as far apart as each other, the left one to the west of the car's road line, the right one east of it.
        val gap = laid[1][0] - laid[0][0]
        assertTrue("lanes are ${laid.map { it[0] }}", gap > 2f && abs(laid[2][0] - laid[1][0] - gap) < 0.01f)
        assertTrue(laid[0][0] < 0f && laid[2][0] > 0f)
        // The road as it is has paint of its own and the arrows add to it.
        fun paint(road: HoloRoad) = frame(HoloScene().apply { world.hazy = false; setRoads(listOf(road)) }).solids(Color.WORLD_MARKING)
        assertTrue(paint(lanesWay(0f to -50f, 0f to 40f, lanes = 3, oneway = 1, both = "left|through|through;right")) >
            paint(lanesWay(0f to -50f, 0f to 40f, lanes = 3, oneway = 1)))
    }

    @Test
    fun `a two-way road has its forward lanes at the line's end on the right and its backward lanes at its start (T-1235)`() {
        val laid = arrows(lanesWay(0f to -30f, 0f to 30f, lanes = 4, forward = "left|right", backward = "through|slight_right")).sortedBy { it[3] }
        // Backward: heading south from the start, on the west side, a through arrow and a right one; forward: east side.
        assertEquals(listOf(2f, 4f, 1f, 4f), laid.map { it[4] })
        assertTrue(laid.take(2).all { it[3] == -1f && it[0] < 0f && abs(it[1] - (-21f)) < 0.01f })
        assertTrue(laid.drop(2).all { it[3] == 1f && it[0] > 0f && abs(it[1] - 21f) < 0.01f })
        // Each direction's first lane is the one by the centre line, on the left of its own travel.
        val east = laid.drop(2).sortedBy { it[0] }
        assertEquals(listOf(1f, 4f), east.map { it[4] })
    }

    @Test
    fun `a road without the tag, a list that does not fit, or the wrong key draws no arrow, and a back street none (T-1235)`() {
        assertEquals(0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 3, oneway = 1)).size)
        // Three lanes of arrows on a road the map counts two lanes on would put them in the wrong lanes.
        assertEquals(0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 2, oneway = 1, both = "left|through|right")).size)
        assertEquals("a two-way road half its lanes", 0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 4, forward = "left|through|right")).size)
        assertEquals("the unsuffixed tag is the one-way road's", 0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 4, both = "left|right")).size)
        assertEquals("a one-way road ignores :forward", 0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 2, oneway = 1, forward = "left|right")).size)
        assertEquals("nothing to draw", 0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 2, oneway = 1, both = "none|reverse")).size)
        assertEquals("a back street", 0, arrows(lanesWay(0f to -50f, 0f to 40f, lanes = 2, oneway = 1, both = "left|right", kind = "residential")).size)
        // A way against its line's direction has its lanes at the line's start.
        val against = arrows(lanesWay(0f to -40f, 0f to 50f, lanes = 2, oneway = -1, both = "left|right"))
        assertEquals(2, against.size)
        assertTrue(against.all { it[3] == -1f && abs(it[1] - (-31f)) < 0.01f })
    }

    @Test
    fun `a bridge deck carries its arrows too (T-1235)`() {
        val road = lanesWay(0f to -50f, 0f to 50f, lanes = 2, oneway = 1, both = "left|right", bridge = true)
        assertEquals(listOf(1f, 4f), arrows(road).sortedBy { it[0] }.map { it[4] })
        fun paint(r: HoloRoad) = frame(HoloScene().apply { world.hazy = false; setRoads(listOf(r)) }).solids(Color.WORLD_MARKING)
        assertTrue(paint(road) > paint(lanesWay(0f to -50f, 0f to 50f, lanes = 2, oneway = 1, bridge = true)))
    }

    @Test
    fun `a sign's words end at a whole word, never mid-word`() {
        assertEquals("fits: kept", "Maple Street", HoloWorld.whole("Maple Street", 18))
        assertEquals("the word that does not fit is left off", "North Example", HoloWorld.whole("North Example Boulevard", 18))
        assertEquals("one long word has no better cut", "Abcdefghijklmnopqr", HoloWorld.whole("Abcdefghijklmnopqrstu", 18))
        assertEquals("a route whose name runs on keeps its number", "generic" to "XY 6", HoloWorld.routeMark("XY 6 Historic"))
    }
}
