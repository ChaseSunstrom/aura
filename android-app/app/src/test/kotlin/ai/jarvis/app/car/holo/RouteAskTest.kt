package ai.jarvis.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

/**
 * Where a route question goes with the house away (T-1026): each branch, and the minute the
 * house is left alone after not answering. Made-up places near 5, 5.
 */
class RouteAskTest {
    private val from = LatLon(5.0, 5.0)
    private val to = LatLon(5.0, 5.01)
    private val route = HouseRoute(listOf(from, to), 700f, 60f, "Rose Street", null)
    private var now = 1_000L

    private var houseAsked = 0
    private var packsAsked = 0
    private val log = mutableListOf<String>()
    private var got: Any? = null

    /** What the house says next: (route, refused); null is "none paired". */
    private var house: Pair<HouseRoute?, Boolean>? = null
    private var valhallaRefused = false
    private var pack: HouseRoute? = null

    private val ask = RouteAsk(
        clock = { now },
        house = { _, _, cb, nothing ->
            house?.let { houseAsked++; if (valhallaRefused) nothing() else cb(it.first, it.second); true } ?: false
        },
        packs = { _, _, cb -> packsAsked++; cb(pack) },
    )

    private fun go() = ask.ask(from, to, { got = it; log += "route" }, { log += "failed noRoute=$it" })

    @Test
    fun `a house that refused is a failure that says no route, and the packs are not asked`() {
        house = null to true
        pack = route
        go()
        assertEquals(listOf("failed noRoute=true"), log)
        assertEquals(0, packsAsked)
    }

    @Test
    fun `a house that did not answer sends the question to the packs`() {
        house = null to false
        pack = route
        go()
        assertEquals(listOf("route"), log)
        assertSame(route, got)
        assertEquals(1, packsAsked)
    }

    @Test
    fun `no house paired sends the question to the packs, with nothing asked of a house`() {
        pack = route
        go()
        assertEquals(listOf("route"), log)
        assertEquals(0, houseAsked)
        assertEquals(1, packsAsked)
    }

    @Test
    fun `the packs having no route is a failure that does not say so`() {
        house = null to false
        pack = null
        go()
        assertEquals(listOf("failed noRoute=false"), log)
    }

    @Test
    fun `the house's own route is the answer, and the packs are not asked`() {
        house = route to false
        pack = null
        go()
        assertEquals(listOf("route"), log)
        assertEquals(0, packsAsked)
    }

    @Test
    fun `a house that did not answer is left alone for a minute, then asked again`() {
        house = null to false
        pack = route
        go()
        assertEquals(1, houseAsked)
        now += RouteAsk.AWAY_MS - 1
        go()
        go()
        assertEquals("the minute is not up: straight to the packs", 1, houseAsked)
        assertEquals(3, packsAsked)
        now += 1
        go()
        assertEquals("up: the house is asked again", 2, houseAsked)
        // Back: its answer ends the wait.
        house = route to false
        now += RouteAsk.AWAY_MS
        go()
        house = null to false
        go()
        assertEquals("asked again at once, as the last answer was a route", 4, houseAsked)
    }

    @Test
    fun `a refusal is an answer, so the house is not parked after it`() {
        house = null to true
        go()
        go()
        assertEquals(2, houseAsked)
    }

    @Test
    fun `Valhalla refusing sends the question to the packs and does not park the house`() {
        house = null to false
        valhallaRefused = true
        pack = route
        go()
        go()
        assertEquals(listOf("route", "route"), log)
        assertEquals(2, houseAsked)
        assertEquals(2, packsAsked)
    }

    @Test
    fun `Valhalla refusing and no way over the packs is no route, said so`() {
        house = null to false
        valhallaRefused = true
        pack = null
        go()
        assertEquals(listOf("failed noRoute=true"), log)
    }

    // T-1029: Valhalla refuses a route in a region the house keeps no routing tiles for (over
    // MAPS_ROUTE_MAX_GB). That is not "there is no route": the phone's packs may hold the roads.
    @Test
    fun `Valhalla refusing a route is the house having nothing, not the house saying none`() {
        assertEquals(true, HouseMaps.refused(404, "no_route", "/api/maps/route"))
        assertEquals(false, HouseMaps.refused(404, "valhalla_refused", "/api/maps/route"))
        assertEquals(true, HouseMaps.nothing(404, "valhalla_refused", "/api/maps/route"))
        assertEquals(false, HouseMaps.nothing(404, "valhalla_refused", "/api/maps/match"))
        assertEquals(false, HouseMaps.nothing(404, "no_route", "/api/maps/route"))
        // Off the road is still an answer for a match, and the buildings call has no such case.
        assertEquals(true, HouseMaps.refused(404, "valhalla_refused", "/api/maps/match"))
        assertEquals(true, HouseMaps.refused(404, "no_match", "/api/maps/match"))
        assertEquals(false, HouseMaps.refused(502, "valhalla_down", "/api/maps/route"))
        assertEquals(false, HouseMaps.refused(404, "", "/api/maps/route"))
    }
}
