package ai.aura.app.car.holo

import ai.aura.app.ui.theme.JarvisTokens.Color
import android.graphics.Bitmap
import android.graphics.Canvas
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The flat map (T-1252) by what a driver reads off it: the car, the route ahead of it, each
 * road in its class's colour, water where the map has water, and the way the car is heading
 * at the top. Colours are read at single pixels in the middle of wide shapes, where
 * anti-aliasing cannot reach. Does not judge how the map looks: the goldens are for that.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class FlatMapTest {
    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private fun box(e0: Float, n0: Float, e1: Float, n1: Float) = listOf(at(e0, n0), at(e1, n0), at(e1, n1), at(e0, n1), at(e0, n0))

    private val roads = listOf(
        HoloRoad(listOf(at(0f, -200f), at(0f, 90f), at(0f, 760f)), "secondary", name = "Larch Way"),
        HoloRoad(listOf(at(-200f, 90f), at(0f, 90f), at(300f, 90f)), "primary", name = "Quarry Road"),
        HoloRoad(listOf(at(-200f, -26f), at(200f, -26f)), "residential"),
        HoloRoad(listOf(at(-60f, 20f), at(-60f, 60f)), "footway", name = "Not Drawn"),
    )
    private val route = HoloRoute(listOf(at(0f, -40f), at(0f, 90f), at(220f, 90f)), "Larch Way", at(0f, 90f), "Quarry Road")

    private val w = 800
    private val h = 480
    private val cx = w / 2
    private val cy = (h * 0.72f).toInt()
    private val map = FlatMap().apply { resize(w.toFloat(), h.toFloat(), 1f, Box(0f, 0f, w.toFloat(), h.toFloat())) }
    private val scene = HoloScene().apply {
        setRoads(roads)
        setDetails(listOf(HoloDetail("water", box(40f, 100f, 120f, 160f)), HoloDetail("green", box(-120f, 100f, -40f, 160f))))
    }

    private fun frame(east: Float, north: Float, heading: Float, timeMs: Long = 0L): Bitmap {
        val bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val p = at(east, north)
        map.draw(Canvas(bitmap), scene, DriveView.Pose(p.lat, p.lon, heading), timeMs)
        return bitmap
    }

    /** The pixel [ahead] metres in front of the car and [right] metres to its right. */
    private fun Bitmap.seen(ahead: Float, right: Float = 0f) =
        getPixel((cx + right * map.scale).toInt(), (cy - ahead * map.scale).toInt())

    /** Long enough after a route was first shown that its overview is over. */
    private val after = FlatMap.OVERVIEW_MS + FlatMap.BACK_MS

    /** The fixture's route in the map's own plane (metres east and south): 130 m north, then 220 m east. */
    private val lx = floatArrayOf(0f, 0f, 220f)
    private val ly = floatArrayOf(40f, -90f, -90f)

    @Test
    fun `the car, its route and the land beside it`() {
        scene.setRoute(route)
        frame(0f, 30f, 0f)
        val b = frame(0f, 30f, 0f, after)
        assertEquals("the car's arrow", Color.ACCENT, b.getPixel(cx, cy))
        assertEquals("the route ahead", Color.ACCENT, b.seen(40f))
        assertEquals("open land", Color.MAP_LAND, b.seen(20f, 60f))
        assertEquals("water", Color.MAP_WATER, b.seen(100f, 80f))
        assertEquals("green", Color.MAP_PARK, b.seen(100f, -80f))
    }

    @Test
    fun `each road in its class's colour, and a footpath not at all`() {
        val b = frame(0f, 30f, 0f)
        assertEquals("a street", Color.MAP_ROAD, b.seen(40f))
        assertEquals("a main road", Color.MAP_ROAD_MAJOR, b.seen(60f, 60f))
        assertEquals("a footpath", Color.MAP_LAND, b.seen(10f, -60f))
        assertTrue("the two named roads that are drawn carry names, the footpath none: ${map.nameCount}", map.nameCount in 2..8)
    }

    @Test
    fun `the way the car is heading is up`() {
        // On the main road, driving east: it runs up the screen, and the street it crossed is behind.
        val b = frame(50f, 90f, 90f)
        assertEquals(Color.MAP_ROAD_MAJOR, b.seen(40f))
        assertEquals(Color.MAP_LAND, b.seen(40f, 40f))
    }

    @Test
    fun `the map draws back as the car goes faster`() {
        frame(0f, 0f, 0f, 0L)
        val still = map.scale
        for (i in 1..80) frame(0f, i * 3f, 0f, i * 100L) // 30 m/s for eight seconds
        assertTrue("${map.scale} px/m against $still standing", map.scale < still * 0.6f)
    }

    @Test
    fun `a map that changed is redrawn without the car moving`() {
        frame(0f, 30f, 0f)
        scene.setRoads(emptyList())
        assertEquals(Color.MAP_LAND, frame(0f, 30f, 0f).seen(40f))
    }

    @Test
    fun `road classes`() {
        assertEquals(FlatMap.MAJOR, FlatMap.cls("motorway"))
        assertEquals(FlatMap.STREET, FlatMap.cls("motorway_link"))
        assertEquals(FlatMap.STREET, FlatMap.cls("residential"))
        assertEquals(FlatMap.MINOR, FlatMap.cls("service"))
        assertEquals(-1, FlatMap.cls("cycleway"))
    }

    // The second pass (T-1255): each layer's decision first, then what it puts on the picture.

    @Test
    fun `the driven part ends beside the car, never goes back, and a car off the route has driven none of it`() {
        assertEquals(70f, FlatMap.along(lx, ly, 3, 2f, -30f, 0f, 60f), 0.01f)
        assertEquals("round the corner", 180f, FlatMap.along(lx, ly, 3, 50f, -91f, 0f, 60f), 0.01f)
        assertEquals("what was driven stays driven", 100f, FlatMap.along(lx, ly, 3, 0f, -10f, 100f, 60f), 0.01f)
        assertEquals("off the route", -1f, FlatMap.along(lx, ly, 3, -300f, 0f, 0f, 60f), 0f)
        assertEquals("past its end", -1f, FlatMap.along(lx, ly, 3, 0f, 0f, 999f, 60f), 0f)
        // Out and back along one street: on the way out it is the way out the car is on, and
        // on the way back, the way back.
        val ox = floatArrayOf(0f, 0f, 0f)
        val oy = floatArrayOf(0f, -100f, 0f)
        assertEquals(40f, FlatMap.along(ox, oy, 3, 0f, -40f, 0f, 60f), 0.01f)
        assertEquals(160f, FlatMap.along(ox, oy, 3, 0f, -40f, 120f, 60f), 0.01f)
    }

    @Test
    fun `the next turn's arrow lies round the corner and points down the street turned into`() {
        val at = FlatMap.along(lx, ly, 3, 1f, -91f, 0f, 30f)
        assertEquals("the turn is the corner, 130 m in", 130f, at, 1.5f)
        val out = FloatArray(6)
        assertEquals(3, FlatMap.piece(lx, ly, 3, 116f, 152f, out))
        assertArrayEquals(floatArrayOf(0f, -76f, 0f, -90f, 22f, -90f), out, 0.01f)
        assertEquals("east, the way the route leaves the corner", 0f, FlatMap.bearing(out, 3), 0.01f)
        assertEquals("cut at the route's end", 2, FlatMap.piece(lx, ly, 3, 300f, 999f, out))
        assertEquals(220f, out[2], 0.01f)
        assertEquals("nothing past the end", 0, FlatMap.piece(lx, ly, 3, 400f, 500f, out))
        assertEquals("a turn that is not on the route has no arrow", -1f, FlatMap.along(lx, ly, 3, 120f, 40f, 0f, 30f), 0f)
    }

    @Test
    fun `the overview is held, eases out and is over, and only a route to somewhere new starts it`() {
        assertEquals(1f, FlatMap.overview(0L), 0f)
        assertEquals(1f, FlatMap.overview(FlatMap.OVERVIEW_MS), 0f)
        assertTrue(FlatMap.overview(FlatMap.OVERVIEW_MS + FlatMap.BACK_MS / 2) in 0.2f..0.8f)
        assertEquals(0f, FlatMap.overview(after), 0f)
        assertEquals(0f, FlatMap.overview(after * 100), 0f)
        assertEquals("a clock that went back", 0f, FlatMap.overview(-1L), 0f)
        assertTrue("the first route", FlatMap.newTrip(plane, null, at(220f, 90f)))
        assertFalse("the same route asked again at a turn", FlatMap.newTrip(plane, at(220f, 90f), at(226f, 84f)))
        assertTrue("somewhere else", FlatMap.newTrip(plane, at(220f, 90f), at(220f, 900f)))
    }

    @Test
    fun `which roads carry one-way arrows`() {
        assertTrue(FlatMap.oneWay("residential", 1, 80f))
        assertTrue("against the way it was drawn", FlatMap.oneWay("tertiary", -1, 80f))
        assertFalse("two-way", FlatMap.oneWay("residential", 0, 80f))
        assertFalse("too short for an arrow", FlatMap.oneWay("residential", 1, 12f))
        assertFalse("a driveway", FlatMap.oneWay("service", 1, 80f))
        assertFalse("a carriageway only goes one way", FlatMap.oneWay("motorway", 1, 800f))
        assertFalse(FlatMap.oneWay("motorway_link", 1, 80f))
        assertFalse(FlatMap.oneWay("trunk", 1, 800f))
        assertFalse("not drawn at all", FlatMap.oneWay("cycleway", 1, 80f))
    }

    @Test
    fun `which roads carry a shield, which places a name, and which label gives way`() {
        assertEquals("M99", FlatMap.shield("motorway", "M99"))
        assertEquals("the first of several", "M99", FlatMap.shield("trunk", "M99;A7"))
        assertNull("a side street's number", FlatMap.shield("residential", "B12"))
        assertNull(FlatMap.shield("motorway", null))
        assertNull(FlatMap.shield("primary", " "))
        assertEquals("ALDER FUEL", FlatMap.placeName("Alder Fuel", "fuel"))
        assertEquals("FUEL", FlatMap.placeName("", "fuel"))
        assertEquals("P", FlatMap.placeName(" ", "parking"))
        assertNull("a shop is not what a driver looks for", FlatMap.placeName("Corner Bakery", "bakery"))
        assertNull(FlatMap.placeName("", ""))
        // The trades a driver looks for (T-1277): the sign, or a plain word when it has none.
        assertEquals("CORNER MARKET", FlatMap.placeName("Corner Market", "supermarket"))
        assertEquals("PHARMACY", FlatMap.placeName("", "pharmacy"))
        assertEquals("HOSPITAL", FlatMap.placeName(" ", "hospital"))
        for (trade in listOf("supermarket", "restaurant", "cafe", "fast_food", "pharmacy", "hospital", "hotel", "bank", "school")) {
            assertNotNull(trade, FlatMap.placeName("", trade))
        }
        assertNull("a hairdresser is not", FlatMap.placeName("Quarry Cuts", "hairdresser"))
        assertTrue(FlatMap.isLandmark("fuel") && FlatMap.isLandmark("parking") && !FlatMap.isLandmark("cafe"))
        assertTrue("on top of one another", FlatMap.givesWay(60f, false))
        assertFalse(FlatMap.givesWay(80f, false))
        assertTrue("the same name again so soon", FlatMap.givesWay(150f, true))
        assertFalse(FlatMap.givesWay(200f, true))
    }

    @Test
    fun `the driven part is dimmed and the turn ahead carries its arrow, until it is behind`() {
        scene.setRoute(route)
        frame(0f, 30f, 0f)
        val b = frame(0f, 30f, 0f, after)
        assertEquals("behind the car", Color.ACCENT_DEEP, b.seen(-30f))
        assertEquals("ahead of it", Color.ACCENT, b.seen(40f))
        assertEquals("the arrow through the corner", Color.TEXT_BRIGHT, b.seen(60f))
        // Round the corner and 50 m on: the corner is driven, and no longer the next turn.
        assertEquals(Color.ACCENT_DEEP, frame(50f, 90f, 90f, after * 2).seen(-50f))
    }

    @Test
    fun `a new route is shown whole with north up, then the drive comes back`() {
        scene.setRoute(route)
        val b = frame(0f, 30f, 90f)
        assertEquals(1f, map.overview, 0f)
        // Heading east, yet north is up and the route is in the middle: its first street runs
        // up the picture, half the route's width left of the centre.
        assertEquals(Color.ACCENT, b.getPixel((w / 2 - 110 * map.scale).toInt(), (h / 2 - 35 * map.scale).toInt()))
        assertEquals(Color.ACCENT, frame(0f, 30f, 90f, after).getPixel(cx, cy))
        assertEquals(0f, map.overview, 0f)

        // Asked again at a turn, the same place: no second overview.
        scene.setRoute(HoloRoute(listOf(at(0f, 30f), at(0f, 90f), at(225f, 88f)), "Larch Way"))
        frame(0f, 30f, 90f, after + 100)
        assertEquals(0f, map.overview, 0f)

        // Somewhere new, and the car drives off: the overview ends long before its time.
        scene.setRoute(HoloRoute(listOf(at(0f, 30f), at(0f, 700f)), "Larch Way"))
        frame(0f, 30f, 0f, after + 200)
        assertEquals(1f, map.overview, 0f)
        for (i in 1..30) frame(0f, 30f + i, 0f, after + 200 + i * 100L) // 10 m/s for three seconds
        assertEquals(0f, map.overview, 0f)
    }

    @Test
    fun `a closure and roadworks from the live list are amber on their road, and gone when the list is`() {
        frame(0f, 30f, 0f)
        scene.setLive(listOf(
            HoloDetail(HoloWorld.CLOSURE, listOf(at(0f, 60f), at(0f, 80f))),
            HoloDetail("roadworks", listOf(at(60f, 90f), at(120f, 90f))),
        ))
        val b = frame(0f, 30f, 0f)
        assertEquals("the closed stretch", Color.WARN, b.seen(40f))
        assertEquals("the first dash of the works", Color.WARN, b.seen(60f, 62f))
        scene.setLive(emptyList())
        assertEquals(Color.MAP_ROAD, frame(0f, 30f, 0f).seen(40f))
    }

    @Test
    fun `one-way streets carry arrows the way they run`() {
        scene.setRoads(listOf(
            HoloRoad(listOf(at(-100f, 60f), at(100f, 60f)), "residential", oneway = 1, name = "Alder Street"),
            HoloRoad(listOf(at(-100f, 0f), at(100f, 0f)), "residential", oneway = -1),
            HoloRoad(listOf(at(-100f, -40f), at(100f, -40f)), "residential"),
        ))
        frame(0f, 30f, 0f)
        assertEquals("two on each one-way street, none on the other", 4, map.arrowCount)
        assertEquals("east", 0f, map.arrowDeg[0], 0.01f)
        assertEquals("west: against the way the street was drawn", 180f, map.arrowDeg[2], 0.01f)
    }

    @Test
    fun `a motorway's number, a fuel station and a car park are on the map, and a place gives way to a street's name`() {
        scene.setRoads(listOf(HoloRoad(listOf(at(-200f, 90f), at(300f, 90f)), "motorway", name = "Quarry Road", ref = "M99")))
        fun fuel(e: Float, n: Float, sign: String) = HoloBuilding(box(e, n, e + 20f, n + 16f), 6f, HoloLook(sign = sign, trade = "fuel"))
        scene.setBuildings(listOf(
            fuel(150f, 95f, "Near Fuel"), // on top of the road's name, 160 m along it
            fuel(140f, -100f, "Alder Fuel"),
            HoloBuilding(box(-150f, -100f, -130f, -84f), 6f, HoloLook(sign = "Corner Bakery", trade = "bakery")),
        ))
        scene.setDetails(listOf(HoloDetail("parking", box(-160f, -60f, -110f, -20f)), HoloDetail("parking", box(60f, -40f, 70f, -30f))))
        frame(0f, 30f, 0f)
        val seen = map.labels()
        assertTrue("$seen", seen.containsAll(listOf("QUARRY ROAD", "M99", "ALDER FUEL")))
        assertFalse("$seen", "NEAR FUEL" in seen || "CORNER BAKERY" in seen)
        assertEquals("the car park big enough to drive to, not the three spaces: $seen", 1, seen.count { it == "P" })
    }

    @Test
    fun `shops and services are named in a close view only, and give way to the street's name and to a fuel station`() {
        scene.setRoads(listOf(HoloRoad(listOf(at(-200f, 90f), at(300f, 90f)), "motorway", name = "Quarry Road", ref = "M99")))
        fun shop(e: Float, n: Float, sign: String, trade: String) = HoloBuilding(box(e, n, e + 20f, n + 16f), 6f, HoloLook(sign = sign, trade = trade))
        scene.setBuildings(listOf(
            shop(150f, 95f, "Near Cafe", "cafe"), // on top of the road's name
            shop(-60f, -100f, "Larch Books", "books"), // not a trade in the table
            shop(100f, -100f, "Alder Fuel", "fuel"),
            shop(110f, -95f, "Beside Pharmacy", "pharmacy"), // beside the fuel station, which was placed first
            shop(-130f, -100f, "Quarry Cafe", "cafe"),
            shop(-20f, -60f, "North Pharmacy", "pharmacy"),
        ))
        frame(0f, 30f, 0f)
        val close = map.labels()
        assertTrue("$close", close.containsAll(listOf("QUARRY ROAD", "ALDER FUEL", "QUARRY CAFE", "NORTH PHARMACY")))
        assertFalse("$close", "NEAR CAFE" in close || "BESIDE PHARMACY" in close || "LARCH BOOKS" in close)
        // Drawn back at speed the view is wider than a street: no shop is named.
        for (i in 1..80) frame(0f, 30f + i * 3f, 0f, after + i * 100L)
        assertTrue("${map.labels()}", map.labels().none { "CAFE" in it || "PHARMACY" in it || "FUEL" in it })
    }

    // The third pass (T-1272): labels keep their distance on the screen, and a route before the first fix.

    /** A frame before the first fix: the scene, and no car. */
    private fun bare(timeMs: Long): Bitmap {
        val bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        map.draw(Canvas(bitmap), scene, null, timeMs)
        return bitmap
    }

    @Test
    fun `labels keep their distance on the screen, so a long route's overview names its main road and the drive is as it was`() {
        // Six kilometres of one main road and a route up three of them: an overview far wider
        // than the drive ever is.
        scene.setRoads(listOf(HoloRoad(listOf(at(0f, -3000f), at(0f, 3000f)), "motorway", name = "Quarry Road", ref = "M99")))
        scene.setRoute(HoloRoute(listOf(at(0f, 0f), at(0f, 2800f)), "Quarry Road"))
        frame(0f, 0f, 0f)
        assertEquals(1f, map.overview, 0f)
        val wide = map.labels()
        assertTrue("a few names, not one every 120 m of the road: $wide", wide.size in 2..6 && "M99" in wide && "QUARRY ROAD" in wide)
        assertTrue("and they are on the picture: ${map.shown} of $wide", map.shown in 1..wide.size)

        // Back in the drive, standing: the name every 240 m and the number between, as before.
        frame(0f, 0f, 0f, after)
        assertEquals("${map.labels()}", 7, map.nameCount)
        // Drawn back at speed the picture holds four times the road, and no more names than it did.
        for (i in 1..80) frame(0f, i * 3f, 0f, after + i * 100L) // 30 m/s for eight seconds
        assertTrue("${map.labels()}", map.nameCount in 3..8)
    }

    @Test
    fun `overview opens before the first fix, stays while there is none, and the fix does not open it again`() {
        assertEquals("no route and no fix: the bare ground", Color.MAP_LAND, bare(0L).getPixel(w / 2, h / 2))
        scene.setRoute(route)
        val b = bare(100L)
        assertEquals(1f, map.overview, 0f)
        // The route whole with north up, as with a car: its first street runs up the picture,
        // half the route's width left of the centre, and starts 65 m below it.
        val x = (w / 2 - 110 * map.scale).toInt()
        assertEquals(Color.ACCENT, b.getPixel(x, (h / 2 - 35 * map.scale).toInt()))
        assertEquals("no car where none is known to be", Color.MAP_LAND, b.getPixel(x + 8, (h / 2 + 65 * map.scale).toInt() + 10))
        // Nothing else to show until the car is on the map, however long that takes.
        bare(after * 2)
        assertEquals(1f, map.overview, 0f)

        // The first fix: the look at the route ends in its own time, and is not started again.
        frame(0f, 30f, 90f, after * 2 + 100)
        assertTrue("${map.overview}", map.overview > 0.5f)
        assertEquals(Color.ACCENT, frame(0f, 30f, 90f, after * 2 + 100 + FlatMap.BACK_MS).getPixel(cx, cy))
        assertEquals(0f, map.overview, 0f)
        frame(0f, 30f, 90f, after * 4)
        assertEquals("once", 0f, map.overview, 0f)
    }

    // The fourth pass (T-1290): a centred name at the edge of the picture is moved in, whole.

    /** The text pixels in column [col], rows [from] to [to]: a name cut by the edge shows here. */
    private fun Bitmap.inkAt(col: Int, from: Int, to: Int) = (from..to).count { getPixel(col, it) == Color.TEXT_DIM }

    @Test
    fun `a centred clamp keeps a label wholly inside the picture, and leaves one in the middle where it is`() {
        assertEquals("in the middle: left where it is", 400f, FlatMap.clampInside(400f, 40f, 800f), 0f)
        assertEquals("at the left edge: moved in by its half width", 40f, FlatMap.clampInside(5f, 40f, 800f), 0f)
        assertEquals("at the right edge: moved in", 760f, FlatMap.clampInside(799f, 40f, 800f), 0f)
        assertEquals("wider than the picture: centred", 400f, FlatMap.clampInside(10f, 500f, 800f), 0f)
    }

    @Test
    fun `a place name whose point is at the left edge is drawn whole, and gives way to one it would then sit on`() {
        fun fuel(e: Float, sign: String) = HoloBuilding(box(e, 22f, e + 20f, 38f), 6f, HoloLook(sign = sign, trade = "fuel"))
        scene.setRoads(emptyList())
        scene.setDetails(emptyList())
        // A point 214 m west of the car: at the standing zoom, 10 px from the left edge of the picture.
        scene.setBuildings(listOf(fuel(-224f, "Alder Fuel")))
        frame(0f, 30f, 0f)
        assertEquals("drawn", 1, map.shown)
        assertEquals("no letter cut by the left edge", 0, frame(0f, 30f, 0f).inkAt(0, cy - 30, cy + 30))

        // Another 75 m east, placed first: the name at the edge moves in onto it, so it gives way.
        scene.setBuildings(listOf(fuel(-149f, "Near Fuel"), fuel(-224f, "Alder Fuel")))
        frame(0f, 30f, 0f)
        assertEquals("both placed: ${map.labels()}", 2, map.labels().size)
        assertEquals("the one that moved in gave way to the one already drawn", 1, map.shown)
    }
}
