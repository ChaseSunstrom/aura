package ai.aura.app.car.holo

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The route the drive follows (T-0613): what the house's `/api/maps` answers are read as,
 * when a new route is asked for (a destination, off the route for long enough, past the
 * turn, by voice), and that the answer replaces the ribbon whole.
 */
class RerouteTest {

    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private val wide = HoloCamera().apply { resize(1280f, 720f, 1.5f) }

    /** North 60 m up Larch, then 200 m right along Quarry: 260 m, 26 s. */
    private fun routeA() = HouseRoute(
        listOf(at(0f, 0f), at(0f, 60f), at(200f, 60f)), 260f, 26f, "Larch Way",
        HouseRoute.Turn("Turn right onto Quarry Road", "Quarry Road", at(0f, 60f)),
    )

    /** From 100 m east: north 60 m up Locust, then left along University. */
    private fun routeB() = HouseRoute(
        listOf(at(100f, 0f), at(100f, 60f), at(-100f, 60f)), 260f, 30f, "Larch St",
        HouseRoute.Turn("Turn left onto Quarry Ave", "Quarry Ave", at(100f, 60f)),
    )

    private val home = at(200f, 60f)
    private val shown = mutableListOf<HoloRoute?>()
    private val guide = RouteGuide { shown += it }

    private fun routed(): RouteGuide {
        val req = guide.go(home, "Home", from = at(0f, 0f), now = 0L)!!
        assertTrue(guide.onRoute(req.id, routeA()))
        return guide
    }

    private fun labels(scene: HoloScene, where: LatLon): List<String> {
        val f = HoloFrame().also { scene.build(it, wide, where.lat, where.lon, 0f, 0L) }
        return (0 until f.labelCount).map { f.labelText[it]!! }
    }

    // --- the house's answers ----------------------------------------------------------

    @Test
    fun `the house's route is read, turn and all`() {
        val r = MapsJson.route(JSONObject(
            """{"distance_m": 4200, "duration_s": 720, "current_street": "Larch Way",
               "next_turn": {"instruction": "Turn right onto Quarry Road.", "street": "Quarry Road",
                             "distance_m": 90, "at": [59.3308, 18.06]},
               "shape": [[59.33, 18.06], [59.3308, 18.06], [59.3308, 18.07]]}""",
        ))!!
        assertEquals(3, r.shape.size)
        assertEquals(LatLon(59.33, 18.06), r.shape[0])
        assertEquals(4200f, r.distanceM, 0f)
        assertEquals(720f, r.durationS, 0f)
        assertEquals("Larch Way", r.street)
        assertEquals("the sentence's full stop is not drawn", "Turn right onto Quarry Road", r.turn!!.instruction)
        assertEquals("Quarry Road", r.turn!!.street)
        assertEquals(LatLon(59.3308, 18.06), r.turn!!.at)
        val holo = r.holo()
        assertEquals("Quarry Road", holo.turnStreet)
        assertEquals(LatLon(59.3308, 18.06), holo.turnAt)
    }

    @Test
    fun `a route with no turn, or a turn with nowhere, is still a route`() {
        val bare = MapsJson.route(JSONObject("""{"distance_m": 10, "duration_s": 2, "next_turn": null, "shape": [[59.33, 18.06], [59.3301, 18.06]]}"""))!!
        assertNull(bare.turn)
        assertEquals("", bare.street)
        val nowhere = MapsJson.route(JSONObject(
            """{"distance_m": 10, "duration_s": 2, "next_turn": {"instruction": "Arrive", "street": "", "at": null},
               "shape": [[59.33, 18.06], [59.3301, 18.06]]}""",
        ))!!
        assertNull(nowhere.turn!!.at)
    }

    @Test
    fun `a route the drive cannot draw is refused`() {
        for (bad in listOf(
            """{"distance_m": 10, "duration_s": 2}""",
            """{"distance_m": 10, "duration_s": 2, "shape": [[59.33, 18.06]]}""",
            """{"distance_m": 10, "duration_s": 2, "shape": [[59.33, 18.06], ["north", 18.06]]}""",
            """{"distance_m": 10, "duration_s": 2, "shape": [[59.33, 18.06], [91.0, 18.06]]}""",
            """{"distance_m": 10, "duration_s": 2, "shape": [[59.33, 18.06], [59.34]]}""",
            """{"distance_m": -5, "duration_s": 2, "shape": [[59.33, 18.06], [59.3301, 18.06]]}""",
        )) {
            assertNull(bad, MapsJson.route(JSONObject(bad)))
        }
    }

    @Test
    fun `the house's match and buildings are read, buildings from GeoJSON order`() {
        val m = MapsJson.match(JSONObject("""{"lat": 59.3301, "lon": 18.0601, "street": "Quarry Road"}"""))!!
        assertEquals(LatLon(59.3301, 18.0601), m.at)
        assertEquals("Quarry Road", m.street)
        assertNull(MapsJson.match(JSONObject("""{"lon": 18.0601, "street": "Quarry Road"}""")))
        val b = MapsJson.buildings(JSONObject(
            """{"buildings": [
                 {"ring": [[18.06, 59.33], [18.061, 59.33], [18.061, 59.331]], "height_m": 12, "source": "osm"},
                 {"ring": [[18.06, 59.33], [18.061, 59.33], [18.061, 59.331]], "height_m": null, "source": "osm"},
                 {"ring": [[18.06, 59.33]], "height_m": 4, "source": "osm"}],
               "truncated": false}""",
        ))
        assertEquals("a ring of one point is not a building", 2, b.size)
        assertEquals("[lon, lat] on the wire", LatLon(59.33, 18.06), b[0].ring[0])
        assertEquals(12f, b[0].heightM!!, 0f)
        assertNull("the map does not know", b[1].heightM)
    }

    @Test
    fun `the house's roads ride with its buildings, GeoJSON order, with their class`() {
        val near = MapsJson.near(JSONObject(
            """{"buildings": [{"ring": [[18.06, 59.33], [18.061, 59.33], [18.061, 59.331]], "height_m": 12}],
               "roads": [
                 {"line": [[18.06, 59.33], [18.061, 59.33]], "kind": "residential", "name": "Quarry Road"},
                 {"line": [[18.06, 59.33]], "kind": "footway", "name": ""},
                 {"line": [[18.06, 59.33], [18.06, 59.331]], "kind": "footway", "name": null}],
               "truncated": false}""",
        ))
        assertEquals(1, near.buildings.size)
        assertEquals("a line of one point is not a road", 2, near.roads.size)
        assertEquals("[lon, lat] on the wire", LatLon(59.33, 18.06), near.roads[0].line[0])
        assertEquals("residential", near.roads[0].kind)
        // A house from before T-0980 answers no roads: its buildings still draw.
        val old = MapsJson.near(JSONObject("""{"buildings": [], "truncated": false}"""))
        assertEquals(0, old.roads.size)
    }

    // --- when a route is asked for ----------------------------------------------------

    @Test
    fun `a destination asks from where the car is and the answer is drawn`() {
        val req = guide.go(home, "Home", from = at(0f, 0f), now = 0L)!!
        assertEquals(at(0f, 0f), req.from)
        assertEquals(home, req.to)
        assertTrue(guide.routing)
        assertTrue(guide.onRoute(req.id, routeA()))
        assertFalse(guide.routing)
        assertEquals("Larch Way", shown.last()!!.street)
        assertEquals("Home", guide.label)
    }

    @Test
    fun `a destination before the first fix is asked for at the first fix`() {
        assertNull(guide.go(home, "Home", from = null, now = 0L))
        val req = guide.onPosition(at(0f, 0f), now = 100L)
        assertNotNull(req)
        assertEquals(at(0f, 0f), req!!.from)
    }

    @Test
    fun `a reroute replaces the ribbon`() {
        val scene = HoloScene()
        val g = RouteGuide { scene.setRoute(it) }
        val first = g.go(home, "Home", from = at(0f, 0f), now = 0L)!!
        g.onRoute(first.id, routeA())
        assertEquals(listOf("Larch Way", "Quarry Road"), labels(scene, at(0f, 0f)))
        // The driver turned off onto Locust, 100 m east: 60 m from the nearest of A.
        assertNull(g.onPosition(at(100f, 0f), now = 20_000L))
        val again = g.onPosition(at(100f, 0f), now = 20_000L + RouteGuide.OFF_ROUTE_MS)!!
        assertEquals(at(100f, 0f), again.from)
        assertEquals(home, again.to)
        assertTrue(g.onRoute(again.id, routeB()))
        assertEquals(listOf("Larch St", "Quarry Ave"), labels(scene, at(100f, 0f)))
        assertEquals(-1, scene.turnSign)
    }

    @Test
    fun `a moment off the route is the GPS, not a wrong turn`() {
        routed()
        var t = 20_000L
        repeat(3) {
            assertNull(guide.onPosition(at(100f, 0f), t))
            t += 1_000L
        }
        assertNull(guide.onPosition(at(1f, 30f), t))
        t += 1_000L
        repeat(3) {
            assertNull(guide.onPosition(at(100f, 0f), t))
            t += 1_000L
        }
    }

    @Test
    fun `off the route for long enough asks exactly once`() {
        routed()
        var asked = 0
        for (s in 20..40) {
            if (guide.onPosition(at(100f, 0f), s * 1_000L) != null) asked++
        }
        assertEquals(1, asked)
    }

    @Test
    fun `an answer to an older question is dropped`() {
        val one = guide.go(home, "Home", from = at(0f, 0f), now = 0L)!!
        val two = guide.reroute(at(0f, 5f), now = 1_000L)!!
        assertFalse(guide.onRoute(one.id, routeA()))
        assertTrue(shown.none { it != null })
        assertTrue(guide.onRoute(two.id, routeB()))
        assertEquals("Larch St", shown.last()!!.street)
        // Cancelled while the house was thinking: the late answer draws nothing.
        val three = guide.reroute(at(0f, 5f), now = 2_000L)!!
        guide.cancel()
        assertFalse(guide.onRoute(three.id, routeA()))
        assertNull(shown.last())
    }

    @Test
    fun `a failed reroute keeps the old route and does not hammer the house`() {
        routed()
        guide.onPosition(at(100f, 0f), 20_000L)
        val req = guide.onPosition(at(100f, 0f), 20_000L + RouteGuide.OFF_ROUTE_MS)!!
        val count = shown.size
        guide.onFailed(req.id, noRoute = false)
        assertEquals("nothing redrawn", count, shown.size)
        assertEquals("Larch Way", guide.route!!.street)
        val failedAt = 20_000L + RouteGuide.OFF_ROUTE_MS
        assertNull(guide.onPosition(at(100f, 0f), failedAt + RouteGuide.MIN_ASK_MS - 1))
        assertNotNull(guide.onPosition(at(100f, 0f), failedAt + RouteGuide.MIN_ASK_MS))
    }

    @Test
    fun `no route there is said, and asked again only after a while`() {
        val req = guide.go(home, "Home", from = at(0f, 0f), now = 0L)!!
        guide.onFailed(req.id, noRoute = true)
        assertEquals(RouteGuide.Failure.NO_ROUTE, guide.failure)
        assertNull(guide.onPosition(at(0f, 1f), 1_000L))
        assertNotNull(guide.onPosition(at(0f, 1f), RouteGuide.MIN_ASK_MS))
        val down = guide.reroute(at(0f, 1f), RouteGuide.MIN_ASK_MS + 1)!!
        guide.onFailed(down.id, noRoute = false)
        assertEquals(RouteGuide.Failure.UNAVAILABLE, guide.failure)
    }

    @Test
    fun `progress counts down to the turn and to the end`() {
        routed()
        val p = guide.progress(at(2f, 20f))!!
        assertEquals(40f, p.turnM!!, 0.1f)
        assertEquals(240f, p.remainingM, 0.5f)
        assertEquals(24f, p.remainingS, 0.1f)
        val later = guide.progress(at(0f, 50f))!!
        assertEquals(10f, later.turnM!!, 0.1f)
        assertNull("no route, no progress", RouteGuide {}.progress(at(0f, 0f)))
    }

    @Test
    fun `past the turn the next one is asked for`() {
        routed()
        assertNull("not yet past it", guide.onPosition(at(5f, 60f), 20_000L))
        val next = guide.onPosition(at(30f, 60f), 21_000L)
        assertNotNull(next)
        assertEquals(home, next!!.to)
    }

    @Test
    fun `arriving clears the route and asks for nothing more`() {
        routed()
        assertNull(guide.onPosition(at(195f, 60f), 20_000L))
        assertNull(shown.last())
        assertNull(guide.route)
        assertNull(guide.destination)
        assertNull(guide.onPosition(at(100f, -300f), 60_000L))
    }

    @Test
    fun `cancelling clears the ribbon and the destination`() {
        routed()
        guide.cancel()
        assertNull(shown.last())
        assertNull(guide.route)
        assertNull(guide.onPosition(at(0f, 0f), 60_000L))
        assertNull("no destination to reroute to", guide.reroute(at(0f, 0f), 60_000L))
    }

    @Test
    fun `distances and durations read the way the board writes them`() {
        assertEquals("90 m", RouteGuide.metres(87f))
        assertEquals("0 m", RouteGuide.metres(4f))
        assertEquals("1.2 km", RouteGuide.metres(1234f))
        assertEquals("4.2 km", RouteGuide.metres(4200f))
        assertEquals("12 km", RouteGuide.metres(12_400f))
        assertEquals("1 min", RouteGuide.duration(30f))
        assertEquals("12 min", RouteGuide.duration(720f))
        assertEquals("1 h", RouteGuide.duration(3600f))
        assertEquals("1 h 5 min", RouteGuide.duration(3900f))
    }
}
