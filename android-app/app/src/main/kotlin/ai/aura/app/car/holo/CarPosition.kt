package ai.aura.app.car.holo

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/**
 * Where the drive draws the car between GPS fixes (T-0613).
 *
 * A fix is the phone's GPS, about once a second. The house's map-match (`/api/maps/match`
 * on the last [TRACE_SIZE] fixes) says where on the road the newest of them was; the
 * difference is kept as an offset and added to every later fix until it is [MATCH_MAX_AGE_MS]
 * old, so the car sits on the road the ribbon is drawn on rather than a GPS-width beside it.
 * Where the phone holds the roads it draws ([roads]), a moving fix is put on them here by
 * [RoadSnap], every fix and with no round trip, and the house's match is only the fallback
 * (T-1101). Between GPS bearings the heading follows the gyroscope ([turned]), so a turn is
 * drawn as it is driven; each bearing then pulls the heading back by [BEARING_PULL].
 * Between fixes the car is carried on along its heading at its speed (dead reckoning), at
 * most [MAX_DEAD_RECKON_MS]. Neither a fix nor a match makes the car jump: each change is
 * eased in over [BLEND_MS] from wherever it was drawn at that instant.
 *
 * Fixes and matches arrive on the main thread, frames are read on the surface's thread:
 * every public method holds the instance's lock.
 *
 * Not covered: there is no wheel speed, so a tunnel longer than the cap stops the car where
 * the cap ends; with no gyroscope a bend taken between fixes is cut, as before; the match
 * offset is a translation, right on a straight road and a metre or two off on a tight bend
 * until the next match; the antimeridian and the poles.
 */
class CarPosition {
    private var has = false
    private var fixLat = 0.0
    private var fixLon = 0.0
    private var fixMs = 0L
    private var speed = 0f
    private var heading = 0f

    private var offLat = 0.0
    private var offLon = 0.0
    private var matchMs = 0L

    // What was drawn minus where the car now is, at [blendMs]; shrinks to nothing over BLEND_MS.
    private var errLat = 0.0
    private var errLon = 0.0
    private var errHeading = 0f
    private var blendMs = 0L

    // Scratch for [target]: frames read it under the lock, so one pair serves them all.
    private var tLat = 0.0
    private var tLon = 0.0

    private val fixes = ArrayDeque<LatLon>(TRACE_SIZE)

    private var roads: List<HoloRoad> = emptyList()
    private var onRoad: HoloRoad? = null
    private var snapMs = Long.MIN_VALUE
    private var gyroMs = Long.MIN_VALUE

    /** The newest GPS fix; [fixMs] also moves with the gyroscope, and the cap counts from GPS. */
    private var gpsMs = 0L

    /** The roads the scene draws round the car, for [RoadSnap]. */
    @Synchronized
    fun roads(near: List<HoloRoad>) {
        roads = near
        // Each answer builds its roads anew: the one the car is on is found again by its line,
        // or the stick would be lost at every refetch.
        onRoad = onRoad?.let { was -> near.firstOrNull { it.line == was.line } }
    }

    /**
     * The gyroscope says the car turned [deltaDeg] (clockwise) by [timeMs]. Only while moving:
     * a parked car's phone is picked up and put down.
     */
    @Synchronized
    fun turned(deltaDeg: Float, timeMs: Long) {
        if (!has || speed <= 0f || !deltaDeg.isFinite()) return
        // The way so far was driven on the old heading: carry the car to here first, or the
        // whole stretch since the fix would swing round the fix like a clock's hand.
        if (timeMs > fixMs) {
            target(timeMs)
            fixLat = tLat - offLat
            fixLon = tLon - offLon
            fixMs = timeMs
        }
        heading = norm(heading + deltaDeg)
        gyroMs = timeMs
    }

    /**
     * One GPS fix at [timeMs] (the clock frames are drawn on). A fix worse than
     * [MAX_ACCURACY_M] is dropped (`false`); `0` accuracy is "not reported" and kept.
     * Below [MIN_HEADING_MPS], or with no [bearingDeg], the car is parked: it is not carried
     * on and keeps the heading it last had while moving, because a standing phone's bearing
     * points anywhere.
     */
    @Synchronized
    fun fix(lat: Double, lon: Double, speedMps: Float, bearingDeg: Float?, accuracyM: Float = 0f, timeMs: Long): Boolean {
        if (!valid(lat, lon) || accuracyM > MAX_ACCURACY_M) return false
        val moving = speedMps >= MIN_HEADING_MPS && bearingDeg != null && bearingDeg.isFinite()
        rebase(timeMs) {
            fixLat = lat
            fixLon = lon
            fixMs = timeMs
            gpsMs = timeMs
            speed = if (moving) speedMps else 0f
            if (moving) {
                // With a live gyroscope the heading is already through the turn GPS is still
                // reporting the start of: the bearing corrects drift, it does not replace.
                val live = timeMs - gyroMs in 0L..GYRO_LIVE_MS
                heading = if (live) norm(heading + shortest(bearingDeg!! - heading) * BEARING_PULL) else norm(bearingDeg!!)
            }
            val hit = if (moving && roads.isNotEmpty()) RoadSnap.snap(LatLon(lat, lon), heading, roads, onRoad) else null
            if (hit != null) {
                offLat = hit.at.lat - lat
                offLon = hit.at.lon - lon
                matchMs = timeMs
                snapMs = timeMs
                onRoad = hit.road
            } else if (timeMs - matchMs > MATCH_MAX_AGE_MS) {
                offLat = 0.0
                offLon = 0.0
                onRoad = null
            }
        }
        if (fixes.size == TRACE_SIZE) fixes.removeFirst()
        fixes.addLast(LatLon(lat, lon))
        return true
    }

    /** The fixes the house is asked to match, oldest first. */
    @Synchronized
    fun trace(): List<LatLon> = fixes.toList()

    /**
     * The house put [raw] (the newest fix it was sent) on the road at [snapped]. A snap
     * further than [MAX_SNAP_M] is the wrong road (a parallel street, a bridge over) and is
     * refused (`false`).
     */
    @Synchronized
    fun matched(raw: LatLon, snapped: LatLon, timeMs: Long): Boolean {
        if (!has || !valid(raw.lat, raw.lon) || !valid(snapped.lat, snapped.lon)) return false
        // The phone's own snap is newer than any answer to a trace sent before it.
        if (snapMs != Long.MIN_VALUE && timeMs - snapMs <= LOCAL_SNAP_FRESH_MS) return false
        val dn = (snapped.lat - raw.lat) * GeoPlane.M_PER_DEG
        val de = (snapped.lon - raw.lon) * GeoPlane.M_PER_DEG * cos(raw.lat * PI / 180.0)
        if (hypot(de, dn) > MAX_SNAP_M) return false
        rebase(timeMs) {
            offLat = snapped.lat - raw.lat
            offLon = snapped.lon - raw.lon
            matchMs = timeMs
        }
        return true
    }

    /** Where to draw the car at [timeMs]; `null` before the first fix. */
    @Synchronized
    fun at(timeMs: Long): DriveView.Pose? {
        if (!has) return null
        target(timeMs)
        val k = left(timeMs)
        return DriveView.Pose(tLat + errLat * k, tLon + errLon * k, norm(heading + errHeading * k.toFloat()))
    }

    /** Where the car is at [t] by the newest fix and match alone, into [tLat], [tLon]. */
    private fun target(t: Long) {
        val d = speed * (minOf(t, gpsMs + MAX_DEAD_RECKON_MS) - fixMs).coerceAtLeast(0L) / 1000.0
        val h = heading * PI / 180.0
        val lat = fixLat + offLat
        tLat = lat + d * cos(h) / GeoPlane.M_PER_DEG
        tLon = fixLon + offLon + d * sin(h) / (GeoPlane.M_PER_DEG * cos(lat * PI / 180.0))
    }

    /** How much of the last change's error is still drawn at [t]: 1 at the change, 0 once eased. */
    private fun left(t: Long): Double = 1.0 - ((t - blendMs).toDouble() / BLEND_MS).coerceIn(0.0, 1.0)

    /** Applies [change] at [t] so that what is drawn at [t] does not move. */
    private inline fun rebase(t: Long, change: () -> Unit) {
        if (!has) {
            change()
            has = true
            blendMs = t
            return
        }
        target(t)
        val k = left(t)
        val shownLat = tLat + errLat * k
        val shownLon = tLon + errLon * k
        val shownHeading = heading + errHeading * k.toFloat()
        change()
        target(t)
        errLat = shownLat - tLat
        errLon = shownLon - tLon
        errHeading = shortest(shownHeading - heading)
        blendMs = t
    }

    companion object {
        /** GPS comes about once a second; this carries the car over a dropped fix or a short tunnel. */
        const val MAX_DEAD_RECKON_MS = 5_000L

        /** One fix interval: the next fix's correction is eased in by the time the one after lands. */
        const val BLEND_MS = 1_000L

        /** Walking pace: below it GPS speed is noise and its bearing is meaningless. */
        const val MIN_HEADING_MPS = 1.5f

        /** A fix vaguer than this is a cold start or a wall of glass, not where the car is. */
        const val MAX_ACCURACY_M = 50f

        /** Further than this the match picked another road. */
        const val MAX_SNAP_M = 50f

        /** The match is asked for every few fixes; an offset older than this is stale. */
        const val MATCH_MAX_AGE_MS = 15_000L

        /** A snap made here this recently outranks the house's match of older fixes. */
        const val LOCAL_SNAP_FRESH_MS = 3_000L

        /** The gyroscope reports many times a second; silent this long, GPS bearing is all there is. */
        const val GYRO_LIVE_MS = 500L

        /** How much of the gap to GPS's bearing each fix closes while the gyroscope steers. */
        const val BEARING_PULL = 0.3f

        /** How many fixes the house matches: enough for it to see which way the car goes. */
        const val TRACE_SIZE = 10

        private fun valid(lat: Double, lon: Double) =
            lat.isFinite() && lon.isFinite() && lat in -90.0..90.0 && lon in -180.0..180.0

        private fun norm(deg: Float): Float = ((deg % 360f) + 360f) % 360f

        private fun shortest(deg: Float): Float = ((deg % 360f) + 540f) % 360f - 180f
    }
}
