package ai.jarvis.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone keeps the route it is guiding (T-1016): written when guidance starts or the
 * route changes, read back by a new guide (an app or car-session restart), gone on arrival
 * or cancel, ignored when old or unreadable, and never lost to a house that does not answer.
 */
class RouteGuideStoreTest {

    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private val home = at(200f, 60f)

    private fun routeA() = HouseRoute(
        listOf(at(0f, 0f), at(0f, 60f), at(200f, 60f)), 260f, 26f, "Larch Way",
        HouseRoute.Turn("Turn right onto Quarry Road", "Quarry Road", at(0f, 60f)),
    )

    private class Fake(var value: String? = null) : RouteGuide.Store {
        var writes = 0
        override fun read(): String? = value
        override fun write(json: String?) { writes++; value = json }
    }

    private val store = Fake()
    private var clock = 1_000_000_000L
    private val shown = mutableListOf<HoloRoute?>()
    private fun guide() = RouteGuide(store, { clock }) { shown += it }

    private fun started(g: RouteGuide = guide(), route: HouseRoute = routeA()): RouteGuide {
        val req = g.go(home, "Home", from = at(0f, 0f), now = 0L)!!
        assertTrue(g.onRoute(req.id, route))
        return g
    }

    /** North 1 km up Larch, then 2 km east: every point of it is more than 300 m from its end but the last stretch. */
    private fun routeLong() = HouseRoute(
        listOf(at(0f, 0f), at(0f, 1000f), at(2000f, 1000f)), 3000f, 300f, "Larch Way", null,
    )

    @Test
    fun `a new guide over the same store carries on with the route, turn and all`() {
        started()
        val again = guide()
        shown.clear()
        assertTrue(again.restore())
        assertEquals(home, again.destination)
        assertEquals("Home", again.label)
        val r = again.route!!
        assertEquals(routeA().shape, r.shape)
        assertEquals(260f, r.distanceM, 0f)
        assertEquals(26f, r.durationS, 0f)
        assertEquals("Larch Way", r.street)
        assertEquals("Quarry Road", r.turn!!.street)
        assertEquals(at(0f, 60f), r.turn!!.at)
        assertEquals("the ribbon is drawn again", 1, shown.size)
        assertNotNull(shown[0])
        // Progress is worked out from the next fix, not read from the store.
        val p = again.progress(at(0f, 30f))!!
        assertEquals(30f, p.turnM!!, 1f)
        assertEquals(230f, p.remainingM, 2f)
    }

    @Test
    fun `a route with no turn comes back with none`() {
        val g = guide()
        val req = g.go(home, "Home", at(0f, 0f), 0L)!!
        g.onRoute(req.id, HouseRoute(routeA().shape, 260f, 26f, "", null))
        val again = guide()
        assertTrue(again.restore())
        assertNull(again.route!!.turn)
    }

    @Test
    fun `it is written when guidance starts and when the route changes, never on a fix`() {
        val g = started()
        val afterStart = store.writes
        assertTrue(afterStart >= 1)
        repeat(20) { g.onPosition(at(0f, it * 2f), 1_000L + it) }
        assertEquals("fixes write nothing", afterStart, store.writes)
        val req = g.reroute(at(0f, 10f), 20_000L)!!
        g.onRoute(req.id, HouseRoute(listOf(at(0f, 10f), at(200f, 60f)), 210f, 21f, "New St", null))
        val again = guide()
        assertTrue(again.restore())
        assertEquals("New St", again.route!!.street)
    }

    @Test
    fun `arrival clears it`() {
        val g = started()
        assertNull(g.onPosition(at(195f, 60f), 5_000L))
        assertNull(g.route)
        assertNull(store.value)
        assertFalse(guide().restore())
    }

    @Test
    fun `cancel clears it`() {
        val g = started()
        g.cancel()
        assertNull(store.value)
        assertFalse(guide().restore())
    }

    @Test
    fun `a new destination drops the old route from the store until its own comes`() {
        val g = started()
        g.go(at(500f, 500f), "Work", from = null, now = 10L)
        assertNull(store.value)
    }

    @Test
    fun `a route older than twelve hours is not followed and is removed`() {
        started()
        clock += RouteGuide.STORED_MAX_MS
        assertTrue("exactly the limit still holds", guide().restore())
        clock += 1
        val late = guide()
        assertFalse(late.restore())
        assertNull(late.route)
        assertNull(late.destination)
        assertNull(store.value)
    }

    @Test
    fun `an entry that does not parse is not followed and is removed`() {
        for (bad in listOf(
            "not json at all",
            "{}",
            """{"saved_ms": 1000000000, "destination": [59.3, 18.0], "label": "x"}""",
            """{"saved_ms": 1000000000, "destination": [59.3, 18.0], "label": "x",
               "route": {"shape": [[59.3, 18.0]], "distance_m": 1, "duration_s": 1}}""",
            """{"saved_ms": 1000000000, "destination": [95.0, 18.0], "label": "x",
               "route": {"shape": [[59.3, 18.0], [59.4, 18.0]], "distance_m": 1, "duration_s": 1}}""",
        )) {
            store.value = bad
            val g = guide()
            assertFalse(bad, g.restore())
            assertNull(bad, g.route)
            assertNull("removed: $bad", store.value)
        }
    }

    @Test
    fun `nothing stored is nothing restored and nothing written`() {
        assertFalse(guide().restore())
        assertEquals(0, store.writes)
    }

    @Test
    fun `a house that does not answer never clears the kept route`() {
        started(route = routeLong())
        val g = guide()
        assertTrue(g.restore())
        // The car is off the line: the guide asks, and the house is away.
        assertNull(g.onPosition(at(100f, 0f), 20_000L))
        val req = g.onPosition(at(100f, 0f), 20_000L + RouteGuide.OFF_ROUTE_MS)!!
        g.onFailed(req.id, noRoute = false)
        assertEquals(RouteGuide.Failure.UNAVAILABLE, g.failure)
        assertNotNull(g.route)
        assertNotNull(store.value)
        assertTrue(guide().restore())
        // ...and a "no route" from the house keeps it as well.
        val again = g.reroute(at(0f, 20f), 100_000L)!!
        g.onFailed(again.id, noRoute = true)
        assertNotNull(g.route)
        assertNotNull(store.value)
        assertNotNull("guidance goes on along the kept line", g.progress(at(0f, 20f)))
    }

    // --- review fixes (T-1016) ---------------------------------------------------------

    @Test
    fun `a restored route whose first fix is near its end was a drive already over`() {
        started()
        val g = guide()
        assertTrue(g.restore())
        shown.clear()
        // 40 m short of the pin: past ARRIVED_M, so the live guide never saw an arrival.
        assertNull(g.onPosition(at(160f, 60f), 5_000L))
        assertNull(g.route)
        assertNull(g.destination)
        assertNull("the store is emptied", store.value)
        assertEquals("the ribbon goes, once", listOf<HoloRoute?>(null), shown)
        assertFalse(guide().restore())
    }

    @Test
    fun `a restored route whose first fix is far from its end is followed, and only the first fix judges`() {
        started(route = routeLong())
        val g = guide()
        assertTrue(g.restore())
        assertNull(g.onPosition(at(0f, 500f), 5_000L))
        assertNotNull(g.route)
        assertNotNull(store.value)
        // Later, 100 m from the end of the line, it is a drive, not a restore.
        assertNull(g.onPosition(at(1900f, 1000f), 6_000L))
        assertNotNull(g.route)
        assertNotNull(store.value)
    }

    @Test
    fun `a live guide near its end is not dropped by a fix`() {
        val g = started()
        assertNull(g.onPosition(at(0f, 0f), 5_000L))
        assertNotNull(g.route)
        assertNotNull(store.value)
    }

    @Test
    fun `stopping near the end of the route drops it, stopping elsewhere or before a fix keeps it`() {
        val far = started(route = routeLong())
        far.stopped()
        assertNotNull("no fix yet", store.value)
        far.onPosition(at(0f, 500f), 5_000L)
        far.stopped()
        assertNotNull(far.route)
        assertNotNull(store.value)

        val near = started()
        near.onPosition(at(0f, 0f), 5_000L)
        near.stopped()
        assertNull(near.route)
        assertNull(near.destination)
        assertNull(store.value)
        assertFalse(guide().restore())
    }

    @Test
    fun `an entry saved in the future is not followed and is removed`() {
        started()
        clock -= 1
        val g = guide()
        assertFalse(g.restore())
        assertNull(g.route)
        assertNull(store.value)
    }

    @Test
    fun `a guide that already has a destination does not restore over it`() {
        started()
        val saved = store.value
        val g = guide()
        assertNotNull(g.go(at(500f, 500f), "Work", from = at(0f, 0f), now = 0L))
        store.value = saved
        assertFalse(g.restore())
        assertEquals(at(500f, 500f), g.destination)
        assertEquals("Work", g.label)
        assertNull(g.route)
        assertTrue("routing goes on", g.routing)
        assertEquals("the entry is left as it was", saved, store.value)
    }

    @Test
    fun `a route that cannot be written leaves the kept one in place`() {
        val g = started()
        val kept = store.value
        val req = g.reroute(at(0f, 10f), 20_000L)!!
        val odd = HouseRoute(listOf(at(0f, 10f), at(200f, 60f)), Float.POSITIVE_INFINITY, Float.NaN, "Odd St", null)
        assertTrue(g.onRoute(req.id, odd))
        assertEquals("Odd St", g.route!!.street)
        assertEquals(kept, store.value)
    }

    @Test
    fun `an answer that lands after the guide is closed is not written`() {
        val g = started()
        val kept = store.value
        val writes = store.writes
        val req = g.reroute(at(0f, 10f), 20_000L)!!
        g.close()
        g.onRoute(req.id, HouseRoute(listOf(at(0f, 10f), at(200f, 60f)), 210f, 21f, "Late St", null))
        assertEquals(kept, store.value)
        assertEquals(writes, store.writes)
    }

    @Test
    fun `a turn with no place comes back with none`() {
        val g = guide()
        val req = g.go(home, "Home", at(0f, 0f), 0L)!!
        g.onRoute(req.id, HouseRoute(routeA().shape, 260f, 26f, "Larch Way", HouseRoute.Turn("Keep left", "Quarry Road", null)))
        val again = guide()
        assertTrue(again.restore())
        assertEquals("Keep left", again.route!!.turn!!.instruction)
        assertEquals("Quarry Road", again.route!!.turn!!.street)
        assertNull(again.route!!.turn!!.at)
    }
}
