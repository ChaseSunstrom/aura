package ai.jarvis.app.car.holo

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.glass.ReactorFeed
import ai.jarvis.app.reactor.core.CoreState
import ai.jarvis.app.ui.theme.JarvisThemes
import ai.jarvis.app.ui.theme.PersonaAccent
import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.test.core.app.ApplicationProvider
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The hologram drive as the head unit shows it (T-0613, Car.dc.html): the drive with
 * nothing to say, and each of the six cards, at the three surface sizes the zones are
 * laid out for. `recordRoborazziDebug` writes them to `src/test/screenshots/car-nav-*`;
 * the copies in `docs/screens/v2/car/` are what a reviewer reads against the board.
 *
 * Everything is pinned (the route, the footprints, the car's place, the clock) so the
 * same drive is the same picture. A plain `testDebugUnitTest` run records nothing, so
 * each frame is also checked for the two things a broken painter loses first: light in
 * the scene and a lit card where the zones put it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class CarNavGoldenTest {

    // These pin the 3D drive, which the owner paused (T-1252): it is drawn only when asked for.
    @org.junit.Before fun threeD() { HoloGround.threeD = true }
    @org.junit.After fun flat() { HoloGround.threeD = false }

    private val sizes = listOf(
        Triple(800, 480, 1f),
        Triple(1280, 720, 1.5f),
        Triple(1920, 720, 1.5f),
    )

    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))

    /** North up Larch for 90 m, then right onto Quarry Road. */
    private val route = HoloRoute(
        listOf(at(0f, -40f), at(0f, 90f), at(220f, 90f)),
        "Larch Way",
        turnAt = at(0f, 90f),
        turnStreet = "Quarry Road",
    )

    private val shops = listOf("Corner Bakery" to "bakery", "Larch Books" to "books", "Quarry Cafe" to "cafe", "North Pharmacy" to "pharmacy")

    /** Two rows of blocks along the street, heights fixed so the frame is too. */
    private val buildings = buildList {
        val heights = floatArrayOf(12f, 26f, 9f, 18f, 32f, 14f, 22f, 10f, 28f, 16f, 20f, 11f)
        // Up the street to 700 m (T-1103): the far end of the picture is the town, not haze.
        for (i in 0 until 32) {
            val n = -10f + i * 22f
            for ((e0, e1) in listOf(-34f to -14f, 14f to 34f)) {
                if (e0 > 0 && n > 70f && n < 110f) continue // Quarry Road runs through here.
                val height = heights[(i + if (e0 > 0) 5 else 0) % heights.size] + if (i >= heights.size) i * 1.5f else 0f
                // What the map says of each (T-1060): homes on the left, shops and offices on the
                // right, one in a colour of its own.
                val kind = if (e0 < 0) (if (height < 15f) "house" else "apartments") else (if (height < 15f) "retail" else "office")
                val look = if (i == 3 && e0 < 0) HoloLook(colour = BRICK, roofShape = "gabled", kind = kind)
                // The businesses on the right carry their names (T-1106); made-up ones.
                else if (e0 > 0) shops[i % shops.size].let { (name, trade) -> HoloLook(kind = kind, sign = name, trade = trade) }
                else HoloLook(kind = kind)
                add(HoloBuilding(listOf(at(e0, n), at(e1, n), at(e1, n + 16f), at(e0, n + 16f)), height, look))
            }
        }
    }

    /** The streets the route runs on, a side street, a driveway and a footpath that bends (T-0980). */
    private val roads = listOf(
        HoloRoad(listOf(at(0f, -200f), at(0f, 90f), at(0f, 760f)), "secondary", lanes = 2, name = "Larch Way"),
        HoloRoad(listOf(at(-200f, 90f), at(0f, 90f), at(300f, 90f)), "primary", lanes = 4, name = "Quarry Road"),
        HoloRoad(listOf(at(-200f, -26f), at(200f, -26f)), "residential"),
        HoloRoad(listOf(at(0f, 31f), at(40f, 31f)), "service"),
        HoloRoad(listOf(at(-44f, -60f), at(-44f, 40f), at(-50f, 60f), at(-62f, 74f)), "footway"),
    )

    private fun box(e0: Float, n0: Float, e1: Float, n1: Float) = listOf(at(e0, n0), at(e1, n0), at(e1, n1), at(e0, n1))

    /** The street's furniture and the ground beside it, as a map pack has them (T-1060). */
    private val details = buildList {
        add(HoloDetail("tree_row", listOf(at(-9f, 0f), at(-9f, 72f))))
        add(HoloDetail("tree_row", listOf(at(9f, 40f), at(9f, 76f))))
        for (n in listOf(20f, 50f, 80f)) add(HoloDetail("lamp", listOf(at(7.5f, n))))
        add(HoloDetail("signal", listOf(at(8f, 80f))))
        add(HoloDetail("crossing", listOf(at(0f, 76f))))
        add(HoloDetail("parking", box(40f, 28f, 76f, 62f)))
        for (i in 0 until 6) add(HoloDetail("bay", box(42f + i * 5.5f, 30f, 46.5f + i * 5.5f, 40f)))
        add(HoloDetail("green", box(-80f, 100f, -14f, 150f)))
        add(HoloDetail("water", box(40f, 104f, 120f, 150f)))
        add(HoloDetail("construction", box(-60f, -30f, -40f, -12f)))
        add(HoloDetail("roadworks", listOf(at(12f, 60f), at(12f, 70f))))
        // Signs and cameras (T-1073): the side street stops at Larch, a limit and a warning on
        // the way up, a speed camera and a plate reader before the junction, a camera after.
        add(HoloDetail("stop", listOf(at(-7.5f, 33f))))
        add(HoloDetail("speed_sign", listOf(at(7.5f, 24f)), "50"))
        add(HoloDetail("sign", listOf(at(-7.5f, 20f)), "NO OUTLET"))
        add(HoloDetail("barrier", listOf(at(-11f, 40f), at(-11f, 72f))))
        add(HoloDetail("bollard", listOf(at(6.5f, 30f))))
        add(HoloDetail("gate", listOf(at(24f, 31f))))
        add(HoloDetail("give_way", listOf(at(-7.5f, 68f))))
        add(HoloDetail("speed_camera", listOf(at(7.5f, 62f))))
        add(HoloDetail("alpr", listOf(at(-7.5f, 46f))))
        add(HoloDetail("camera", listOf(at(9f, 100f))))
    }

    private val base = DriveView(
        reactor = ReactorFeed.inputsFrom(Phase.SPEAKING),
        turn = DriveView.Turn("90 m", "Right onto Quarry Road", sign = 1),
        trip = DriveView.Trip("Home", "18:57", "12 min · 4.2 km · light traffic"),
        speed = "42",
        speedUnit = "km/h",
        heard = "how long until I'm home",
        said = "Twelve minutes. Traffic is light on Quarry Road.",
    )

    private val cards = mapOf(
        "call" to CarCard(
            CarCard.Kind.CALL, "Call · Sam", "Sam Rivera", "Mobile",
            listOf(CarAction("answer", "Answer", primary = true), CarAction("decline", "Decline"), CarAction("driving", "I'm driving")),
        ),
        "approval" to CarCard(
            CarCard.Kind.APPROVAL, "Send this to Sam?", "“No rush — see you at 7:45.”",
            "Say “send” or tap. Nothing goes out without your OK.",
            listOf(CarAction("send", "Send", primary = true), CarAction("change", "Change"), CarAction("cancel", "Cancel")),
        ),
        "message" to CarCard(
            CarCard.Kind.MESSAGE, "Message · Sam", "Running 10 min late — still on for dinner?", "",
            listOf(CarAction("reply", "Reply"), CarAction("later", "Later")),
        ),
        "home" to CarCard(CarCard.Kind.HOME, "When you get home", "Porch light on, heating to 21°", "The garage opens as you turn in"),
        "calendar" to CarCard(CarCard.Kind.CALENDAR, "Tonight", "19:45 Dinner", "You'll be home at 18:57 · leave again by 19:20"),
        "music" to CarCard(
            CarCard.Kind.MUSIC, "Playing · Spotify", "Night Drive", "Example Artist",
            listOf(CarAction("previous", "Previous"), CarAction("pause", "Pause", primary = true), CarAction("next", "Next")),
        ),
    )

    /** The ground round the street as a map pack has it (T-1105): [height] metres at (east, north) of the plane. */
    private fun ground(height: (Float, Float) -> Double): List<HoloGroundCell> {
        val n = 17
        val x0 = Math.floor(plane.lon0 * 100).toInt()
        val y0 = Math.floor(plane.lat0 * 100).toInt()
        return (y0 - 2..y0 + 2).flatMap { cy ->
            (x0 - 2..x0 + 2).map { cx ->
                HoloGroundCell(cx, cy, n, ShortArray(n * n) { k ->
                    height(plane.east((cx + (k % n) / (n - 1.0)) / 100), plane.north((cy + (k / n) / (n - 1.0)) / 100)).toInt().toShort()
                })
            }
        }
    }

    /**
     * What the flat map alone is shown with (T-1255), so the 3D pictures stay as they were: a
     * longer way home, a numbered main road, one-way streets, a fuel station, and a closure and
     * roadworks from the live list. Made-up, as everything here.
     */
    private val flatRoute = HoloRoute(
        listOf(at(0f, -40f), at(0f, 90f), at(300f, 90f), at(300f, 360f)),
        "Larch Way",
        turnAt = at(0f, 90f),
        turnStreet = "Quarry Road",
    )
    private val flatRoads = roads.map {
        when {
            it.name == "Quarry Road" -> HoloRoad(it.line, it.kind, it.lanes, name = it.name, ref = "M99")
            it.kind == "residential" -> HoloRoad(it.line, it.kind, oneway = 1, name = "Alder Street")
            else -> it
        }
    } + listOf(
        // Between two blocks, running west: its arrows point against the way it is drawn.
        HoloRoad(listOf(at(0f, 163f), at(220f, 163f)), "residential", oneway = -1),
        HoloRoad(listOf(at(300f, 90f), at(300f, 400f)), "tertiary", name = "Cedar Lane"),
    )
    // And, in the open beside them (T-1277), a pharmacy: the shops along Larch Way's own side are inside the road names' gap.
    private val flatBuildings = buildings + listOf(
        HoloBuilding(box(-135f, 24f, -115f, 40f), 6f, HoloLook(sign = "Alder Fuel", trade = "fuel")),
        HoloBuilding(box(-180f, 118f, -160f, 134f), 6f, HoloLook(sign = "Larch Pharmacy", trade = "pharmacy")),
    )
    // On open ground: the 3D scene's own car park sits under the road's number, and a place gives way.
    private val flatDetails = details + HoloDetail("parking", box(92f, 12f, 128f, 48f))
    private val flatLive = listOf(
        HoloDetail(HoloWorld.CLOSURE, listOf(at(0f, 100f), at(0f, 150f))),
        HoloDetail("roadworks", listOf(at(65f, 90f), at(125f, 90f))),
    )

    private fun render(
        view: DriveView, w: Int, h: Int, density: Float, ground: List<HoloGroundCell>? = null, utc: Long? = null,
        live: List<HoloDetail>? = null, roads: List<HoloRoad> = this.roads,
        route: HoloRoute = this.route, buildings: List<HoloBuilding> = this.buildings,
        details: List<HoloDetail> = this.details,
    ): Pair<Bitmap, CarDrive> {
        val drive = CarDrive()
        utc?.let { ms -> drive.utc = { ms } }
        ground?.let { drive.scene.setGround(it) }
        drive.scene.setRoute(route)
        drive.scene.setBuildings(buildings)
        drive.scene.setRoads(roads)
        drive.scene.setDetails(details)
        live?.let { drive.scene.setLive(it) }
        drive.resize(w, h, density, Box(0f, 0f, w.toFloat(), h.toFloat()))
        val bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        // 30 m up the street, heading north: the turn is 60 m ahead.
        val pose = at(0f, 30f)
        drive.draw(Canvas(bitmap), view, DriveView.Pose(pose.lat, pose.lon, 0f), TIME_MS)
        return bitmap to drive
    }

    private fun lit(b: Bitmap, box: Box): Int {
        var n = 0
        val step = 3
        var y = box.top.toInt().coerceAtLeast(0)
        while (y < box.bottom.toInt().coerceAtMost(b.height)) {
            var x = box.left.toInt().coerceAtLeast(0)
            while (x < box.right.toInt().coerceAtMost(b.width)) {
                val c = b.getPixel(x, y)
                if (((c shr 16) and 0xFF) + ((c shr 8) and 0xFF) + (c and 0xFF) > LIT) n++
                x += step
            }
            y += step
        }
        return n
    }

    @Test
    fun `the flat map at every size`() {
        HoloGround.threeD = false
        for ((w, h, d) in sizes) {
            // A route to somewhere new is first shown whole with north up (T-1255) ...
            val (whole, painted) = render(base, w, h, d, live = flatLive, roads = flatRoads, route = flatRoute, buildings = flatBuildings, details = flatDetails)
            whole.captureRoboImage("src/test/screenshots/car-flat-overview-${w}x$h.png")
            // ... and the drive is what the same screen shows once that is over.
            val drive = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            val pose = at(0f, 30f)
            painted.draw(Canvas(drive), base, DriveView.Pose(pose.lat, pose.lon, 0f), TIME_MS + FlatMap.OVERVIEW_MS + FlatMap.BACK_MS)
            drive.captureRoboImage("src/test/screenshots/car-flat-drive-${w}x$h.png")
            assertFalse("${w}x$h: the overview never gave way to the drive", whole.sameAs(drive))
            assertTrue("${w}x$h: no map drawn", lit(drive, Box(0f, 0f, w.toFloat(), h.toFloat())) > 50)
            assertTrue("${w}x$h: no turn drawn", lit(drive, painted.zones.turn) > 20)
        }
    }

    @Test
    fun `the drive and each card at every size`() {
        for ((w, h, d) in sizes) {
            val (drive, painted) = render(base, w, h, d)
            drive.captureRoboImage("src/test/screenshots/car-nav-drive-${w}x$h.png")
            val z = painted.zones
            assertTrue("${w}x$h: the street is dark", lit(drive, Box(0f, h * 0.3f, w.toFloat(), h * 0.8f)) > 50)
            assertTrue("${w}x$h: no turn drawn", lit(drive, z.turn) > 20)
            for ((name, card) in cards) {
                val (bitmap, d2) = render(base.copy(card = card), w, h, d)
                bitmap.captureRoboImage("src/test/screenshots/car-nav-$name-${w}x$h.png")
                assertTrue("${w}x$h $name: the card is dark", lit(bitmap, d2.zones.card) > 20)
            }
        }
    }

    @Test
    fun `in a turn the car is seen turning, on its steered wheels, along the line of the turn`() {
        // T-1061 (the owner, 2026-11-11: "more smooth instead of a hard follow, with wheels
        // turning and a line to show the turn when making it"). Up Larch at 9 m/s and round
        // the corner into Quarry Road at 40 degrees a second; the picture is mid-corner.
        val (w, h, d) = sizes[1]
        val (bitmap, drive) = render(base, w, h, d)
        val canvas = Canvas(bitmap)
        var east = 0f
        var north = 30f
        var heading = 0f
        var t = TIME_MS
        while (heading < 55f) {
            t += 33
            if (north >= 77f) heading += 40f * 0.033f
            val r = Math.toRadians(heading.toDouble())
            east += (9f * 0.033f * Math.sin(r)).toFloat()
            north += (9f * 0.033f * Math.cos(r)).toFloat()
            val p = at(east, north)
            bitmap.eraseColor(0)
            drive.draw(canvas, base, DriveView.Pose(p.lat, p.lon, heading), t)
        }
        bitmap.captureRoboImage("src/test/screenshots/car-nav-turning-${w}x$h.png")
        assertTrue("the camera is not following", drive.scene.camHeadingDeg in 15f..54f)
        assertTrue("the car is not seen turned", drive.scene.bodyYawDeg > 3f)
        assertTrue("no line of the turn", drive.scene.turnLine > 0.5f)
    }

    @Test
    fun `a road closed today is barred across, with works along it`() {
        // T-1108 (the owner, 2026-11-12: "traffic data, construction, road barriers"): what the
        // house's live answer puts on the street. By day, so the boards read.
        val (w, h, d) = sizes[1]
        val noon = java.time.LocalDateTime.of(2024, 3, 20, 10, 55).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
        val (open, _) = render(base, w, h, d, utc = noon)
        val (shut, drive) = render(base, w, h, d, utc = noon, live = listOf(HoloDetail(HoloWorld.CLOSURE, listOf(at(0f, 52f), at(0f, 84f)))))
        shut.captureRoboImage("src/test/screenshots/car-nav-live-closure-${w}x$h.png")
        assertEquals("a barricade at each end", 2, drive.scene.world.counts[15])
        assertFalse("the closure changed nothing on screen", open.sameAs(shut))
    }

    @Test
    fun `the map is lit by the hour, day, dusk and night`() {
        // T-1107 (the owner, 2026-11-12: "accurate lighting depending on the time of day, and from
        // street lamps"). The goldens' place on the March equinox: the sun 30 degrees up in the
        // south at 10:55 UTC, setting at 16:55, far under at 23:00.
        val (w, h, d) = sizes[1]
        fun at(hh: Int, mm: Int) = java.time.LocalDateTime.of(2024, 3, 20, hh, mm).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
        val shots = listOf("day" to at(10, 55), "dusk" to at(16, 55), "night" to at(23, 0)).map { (name, ms) ->
            val (bitmap, drive) = render(base, w, h, d, utc = ms)
            bitmap.captureRoboImage("src/test/screenshots/car-nav-light-$name-${w}x$h.png")
            bitmap to drive.scene.daylight
        }
        assertEquals(1f, shots[0].second)
        assertTrue("dusk is ${shots[1].second}", shots[1].second > 0.2f && shots[1].second < 0.8f)
        assertEquals(0f, shots[2].second)
        val sky = Box(0f, 0f, w.toFloat(), h * 0.4f)
        assertTrue("the day sky is no brighter than the night's", lit(shots[0].first, sky) > lit(shots[2].first, sky))
    }

    @Test
    fun `over ground with height the street climbs and the land beside it rolls`() {
        // T-1105 (the owner, 2026-11-12: "depth and height in the terrain with the roads"). The
        // street rises 1 in 12 to the north through land that rolls 18 m either side of it.
        val (w, h, d) = sizes[1]
        val (bitmap, drive) = render(base, w, h, d, ground { e, n -> 80 + n / 12.0 + 18 * Math.sin(e / 90.0) * Math.cos(n / 120.0) })
        bitmap.captureRoboImage("src/test/screenshots/car-nav-hill-${w}x$h.png")
        assertTrue("the ground is not drawn with its height", drive.scene.world.hilly)
        assertTrue("the street ahead does not climb", drive.scene.world.rise(0f, 330f) > 15f)
    }

    @Test
    fun `a motorway bridge crosses over the street ahead and the numbered street has its shield`() {
        // T-1167 (the owner, 2026-11-12: "no interstate signs ... all roads are flat, no bridges, I want bridges").
        val (w, h, d) = sizes[1]
        val over = roads.map { if (it.name == "Larch Way") HoloRoad(listOf(at(0f, -195f)) + it.line.drop(1), it.kind, it.lanes, it.oneway, it.name, ref = "US 1") else it } +
            HoloRoad(listOf(at(-200f, 60f), at(200f, 60f)), "motorway", lanes = 4, ref = "I 95", bridge = true, layer = 1)
        val (bitmap, drive) = render(base, w, h, d, roads = over)
        bitmap.captureRoboImage("src/test/screenshots/car-nav-bridge-${w}x$h.png")
        // The scene's plane is the drive's own, so the span is looked for along the street.
        assertTrue("the deck is not up", (-300..300 step 5).any { drive.scene.world.riseOn(0f, it.toFloat()) > 3f })
        assertTrue("no shield is kept", drive.scene.world.counts[16] >= 1)
    }

    @Test
    fun `a tap on a drawn button is that button's action and anywhere else is nothing`() {
        val card = cards.getValue("approval")
        val (_, drive) = render(base.copy(card = card), 1280, 720, 1.5f)
        val buttons = drive.zones.buttons(card.actions.size)
        for ((i, b) in buttons.withIndex()) {
            assertEquals(card.actions[i], drive.actionAt((b.left + b.right) / 2, (b.top + b.bottom) / 2))
        }
        assertNull(drive.actionAt(drive.zones.turn.left + 1f, drive.zones.turn.top + 1f))
        // No card, no buttons, wherever they would have been.
        val (_, bare) = render(base, 1280, 720, 1.5f)
        assertNull(bare.actionAt((buttons[0].left + buttons[0].right) / 2, (buttons[0].top + buttons[0].bottom) / 2))
    }

    /** A limit plate [north] metres up the street, with or without its number, and a wall [tall] metres high across the street at 12 m. */
    private fun plateBehindOrBefore(north: Float, text: String, tall: Float = 6f): Bitmap {
        val cam = HoloCamera().apply { resize(1280f, 720f, 1.5f) }
        val scene = HoloScene().apply {
            world.hazy = false
            setRoads(listOf(HoloRoad(listOf(at(0f, -50f), at(0f, 200f)), "primary", lanes = 2)))
            setBuildings(listOf(HoloBuilding(listOf(at(-40f, 12f), at(40f, 12f), at(40f, 15f), at(-40f, 15f), at(-40f, 12f)), tall)))
            setDetails(listOf(HoloDetail("speed_sign", listOf(at(5f, north)), text)))
        }
        val frame = HoloFrame().also { scene.build(it, cam, plane.lat(0f), plane.lon(0f), 0f, 1_000L) }
        if (text.isNotEmpty()) {
            assertEquals("the plate's number is in the frame", 1, frame.signCount)
            assertTrue("and on the screen: ${frame.signX[0]}, ${frame.signY[0]}", frame.signX[0] in 0f..1280f && frame.signY[0] in 0f..720f)
        }
        val bitmap = Bitmap.createBitmap(1280, 720, Bitmap.Config.ARGB_8888)
        HoloPainter().apply { resize(1280f, 720f, cam.horizonY(), cam.unit) }.paint(Canvas(bitmap), frame)
        return bitmap
    }

    @Test
    fun `a sign's words behind a nearer wall are hidden by it, and words in front of the wall are read`() {
        // Behind (T-1120): the wall covers the plate, so its number must not be on the wall either.
        assertTrue("the number showed through the wall", plateBehindOrBefore(18f, "35").sameAs(plateBehindOrBefore(18f, "")))
        // In front: the number is on its plate, over the wall behind it.
        assertFalse("the number in front of the wall was lost", plateBehindOrBefore(8f, "35").sameAs(plateBehindOrBefore(8f, "")))
    }

    @Test
    fun `a tall wall is ordered by where it stands, so a plate in front of it is still read`() {
        // T-1131: the camera looks down, so a wall's top read as nearer and a 30 m wall was painted over a plate 4 m before it.
        assertFalse("the tall wall was painted over the plate in front of it", plateBehindOrBefore(8f, "35", 30f).sameAs(plateBehindOrBefore(8f, "", 30f)))
        assertTrue("the number showed through the tall wall", plateBehindOrBefore(18f, "35", 30f).sameAs(plateBehindOrBefore(18f, "", 30f)))
    }

    @Test
    fun `with no fix yet the zones still draw and the turn zone says why`() {
        val drive = CarDrive()
        drive.resize(800, 480, 1f, Box(0f, 0f, 800f, 480f))
        val bitmap = Bitmap.createBitmap(800, 480, Bitmap.Config.ARGB_8888)
        drive.draw(Canvas(bitmap), DriveView(status = "Waiting for GPS"), null, TIME_MS)
        bitmap.captureRoboImage("src/test/screenshots/car-nav-nofix-800x480.png")
        assertTrue(lit(bitmap, drive.zones.turn) > 10)
    }

    @Test
    fun `AURA's docked galaxy steps by the drive clock's milliseconds`() {
        val drive = CarDrive()
        drive.resize(800, 480, 1f, Box(0f, 0f, 800f, 480f))
        val canvas = Canvas(Bitmap.createBitmap(800, 480, Bitmap.Config.ARGB_8888))
        drive.draw(canvas, DriveView(status = "Waiting for GPS"), null, TIME_MS)
        drive.draw(canvas, DriveView(status = "Waiting for GPS"), null, TIME_MS + 16)
        // GalaxyModel.step takes ms, as V2Core passes it: 0.016 here left the car's galaxy a still.
        assertEquals(16.0, drive.galaxy!!.t, 0.5)
    }

    @Test
    fun `in the JARVIS theme the docked core is the v2 core, in the conversation's state at the drive's clock`() {
        // T-0722: the drive was the last car screen on the pre-v2 reactor. The goldens above wear
        // AURA (the default pack), so this is the one picture of JARVIS's drive.
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        PersonaAccent.set(context, "jarvis")
        try {
            val (w, h, d) = sizes[1]
            val (bitmap, drive) = render(base, w, h, d)
            bitmap.captureRoboImage("src/test/screenshots/car-nav-jarvis-${w}x$h.png")
            assertTrue("no core in its zone", lit(bitmap, drive.zones.core) > 50)
            val core = drive.core!!
            assertNull("JARVIS's drive holds AURA's stars", drive.galaxy)
            assertEquals(CoreState.SPEAKING, core.state)
            assertEquals(TIME_MS.toDouble(), core.t, 0.0)
            // A new state is told to the engine, at the drive's clock: its entry plays (the
            // kick, the shock ring). A core only ever stepped would play it once, at boot.
            val pose = at(0f, 30f)
            drive.draw(Canvas(bitmap), base.copy(reactor = ReactorFeed.inputsFrom(Phase.LISTENING)), DriveView.Pose(pose.lat, pose.lon, 0f), TIME_MS + 16)
            assertEquals(CoreState.LISTENING, core.state)
            assertEquals(1, core.changes)
            assertEquals((TIME_MS + 16).toDouble(), core.kickAt, 0.0)
            // A clock that went backwards (a new drive) is a fresh core, not one kicked in its future.
            drive.draw(Canvas(bitmap), base, DriveView.Pose(pose.lat, pose.lon, 0f), TIME_MS - 500)
            assertEquals(0, drive.core!!.changes)
        } finally {
            PersonaAccent.set(context, JarvisThemes.DEFAULT_ID)
        }
    }

    private companion object {
        /** A moment on the reactor's clock and the scan plane's, fixed so a golden is one picture. */
        const val TIME_MS = 1_250L

        /** Summed RGB above this is light, not the background's near-black. */
        const val LIT = 150

        /** A colour the map gives one building, as `building:colour` would: map data, not the app's palette. */
        val BRICK = android.graphics.Color.rgb(142, 64, 48)
    }
}
