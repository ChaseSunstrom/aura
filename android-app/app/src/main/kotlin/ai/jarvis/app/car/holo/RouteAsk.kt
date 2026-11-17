package ai.jarvis.app.car.holo

/**
 * Where a route question goes (T-1026): the house first, and the roads of the map packs the phone
 * holds when the house gave nothing and was not refusing. Kept out of [CarNavScreen] so each
 * branch is a test.
 *
 * - The house refused (looked and found no route): a failure that says so; the packs are not tried.
 * - Valhalla refused the route (the house is well and has none to give, [HouseMaps.nothing]):
 *   the packs, and no route there either is a failure that says "no route". The house is asked
 *   again next time.
 * - The house did not answer, or none is paired: the packs; no route there is a failure that does
 *   not say "no route", so the guide goes on asking.
 * - A house that did not answer is not asked again for [AWAY_MS]: each ask would wait out the
 *   connection's timeout before the packs got a turn. It is asked again after, and one answer
 *   (a route or a refusal) ends the wait.
 *
 * Main thread only. Does not cover a house that answers slowly rather than not at all.
 */
internal class RouteAsk(
    private val clock: () -> Long,
    /** Asks the house and returns true; false when none is paired, with nothing asked. The last is "Valhalla refused". */
    private val house: (LatLon, LatLon, (HouseRoute?, Boolean) -> Unit, () -> Unit) -> Boolean,
    private val packs: (LatLon, LatLon, (HouseRoute?) -> Unit) -> Unit,
) {
    private var awayAt: Long? = null

    fun ask(from: LatLon, to: LatLon, onRoute: (HouseRoute) -> Unit, onFailed: (noRoute: Boolean) -> Unit) {
        fun fromPacks(noRoute: Boolean = false) = packs(from, to) { route -> if (route != null) onRoute(route) else onFailed(noRoute) }
        val away = awayAt
        if (away != null && clock() - away < AWAY_MS) return fromPacks()
        val asked = house(from, to, { route, refused ->
            when {
                route != null -> { awayAt = null; onRoute(route) }
                refused -> { awayAt = null; onFailed(true) }
                else -> { awayAt = clock(); fromPacks() }
            }
        }, { awayAt = null; fromPacks(noRoute = true) })
        if (!asked) fromPacks()
    }

    companion object {
        const val AWAY_MS = 60_000L
    }
}
