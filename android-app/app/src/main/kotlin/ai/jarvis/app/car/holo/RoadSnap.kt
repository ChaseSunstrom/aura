package ai.jarvis.app.car.holo

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot

/**
 * Where on the drawn roads a GPS fix is (T-1101): the nearest point of a road a car drives on,
 * within [MAX_M], that runs the way the car is heading (within [MAX_ANGLE_DEG], either way
 * along it). The road last snapped to is preferred by [STICK_M], so a fix midway between two
 * lanes of traffic does not hop from one to the other and back.
 *
 * The phone holds these roads already, to draw them; the house's match took a round trip over
 * mobile data and came every fifth fix, so the car sat a GPS-width off its road and was pulled
 * back in jumps (the owner, 2026-11-12: "keeps jumping around the road").
 *
 * A one-way road is taken only the way it runs, so the oncoming side of a divided road is
 * not taken however near it is.
 *
 * Does not cover two roads the same way closer than GPS can tell apart (the nearer wins, then
 * sticks), a two-way road drawn as one line (the car is on its centre line), or a road the map
 * does not have: there is no hit and the caller keeps what it had.
 */
internal object RoadSnap {
    class Hit(val at: LatLon, val bearingDeg: Float, val road: HoloRoad, val distM: Float)

    // ponytail: every point of every near road, once a fix (a second); a grid if packs grow past a few thousand points.
    fun snap(at: LatLon, headingDeg: Float, roads: List<HoloRoad>, last: HoloRoad? = null): Hit? {
        val p = GeoPlane(at.lat, at.lon)
        var best: Hit? = null
        var bestScore = Float.MAX_VALUE
        for (r in roads) {
            if (r.kind in NOT_DRIVEN || r.line.size < 2) continue
            var e0 = p.east(r.line[0].lon)
            var n0 = p.north(r.line[0].lat)
            for (i in 1 until r.line.size) {
                val e1 = p.east(r.line[i].lon)
                val n1 = p.north(r.line[i].lat)
                val de = e1 - e0
                val dn = n1 - n0
                val len2 = de * de + dn * dn
                if (len2 > 0f) {
                    val t = ((-e0 * de - n0 * dn) / len2).coerceIn(0f, 1f)
                    val qe = e0 + t * de
                    val qn = n0 + t * dn
                    val d = hypot(qe, qn)
                    var b = norm((atan2(de, dn) * 180.0 / PI).toFloat())
                    var off = abs(shortest(b - headingDeg))
                    // A one-way is taken only the way it runs: the other carriageway of a divided
                    // road is as near as GPS can tell, and it is going the other way.
                    if (r.oneway == -1) {
                        b = norm(b + 180f)
                        off = 180f - off
                    } else if (r.oneway == 0 && off > 90f) {
                        b = norm(b + 180f)
                        off = 180f - off
                    }
                    val score = d - if (r === last) STICK_M else 0f
                    if (d <= MAX_M && off <= MAX_ANGLE_DEG && score < bestScore) {
                        bestScore = score
                        best = Hit(LatLon(p.lat(qn), p.lon(qe)), b, r, d)
                    }
                }
                e0 = e1
                n0 = n1
            }
        }
        return best
    }

    /** A phone's GPS in a street is good to about this; further is another road or no road. */
    const val MAX_M = 20f

    /** A lane change or a bend between map points is inside this; a cross street is not. */
    const val MAX_ANGLE_DEG = 35f

    /** How much nearer another road must be before the car is moved to it. */
    const val STICK_M = 6f

    /** OSM `highway` classes no car is on. */
    private val NOT_DRIVEN = setOf("footway", "path", "cycleway", "steps", "pedestrian", "bridleway", "corridor", "platform")

    private fun norm(deg: Float): Float = ((deg % 360f) + 360f) % 360f

    private fun shortest(deg: Float): Float = ((deg % 360f) + 540f) % 360f - 180f
}

/**
 * How far the car turned in [dtS] seconds by the phone's gyroscope, degrees clockwise from
 * above (the way a compass heading grows): the turn rate ([wx], [wy], [wz], rad/s, phone axes)
 * taken about the up direction the gravity sensor gives ([gx], [gy], [gz]), so it reads the
 * same however the phone lies in its mount.
 *
 * GPS says where the car points about once a second and a moment late (the owner: "doesn't
 * know that it's turning before it's been a while"); the gyroscope says it fifty times a second.
 *
 * `0` for a gap longer than [MAX_GAP_S] (the sensor paused: the turn in between is not known)
 * and with no usable gravity. Does not cover the phone being turned in the hand: that reads as
 * the car turning until GPS pulls the heading back.
 */
internal fun gyroTurnDeg(wx: Float, wy: Float, wz: Float, gx: Float, gy: Float, gz: Float, dtS: Float): Float {
    val g = hypot(hypot(gx, gy), gz)
    if (g < MIN_GRAVITY || dtS <= 0f || dtS > MAX_GAP_S) return 0f
    // About up, counter-clockwise is positive (the right-hand rule); a heading grows clockwise.
    val aboutUp = (wx * gx + wy * gy + wz * gz) / g
    return (-aboutUp * dtS * 180.0 / PI).toFloat()
}

private const val MIN_GRAVITY = 5f
private const val MAX_GAP_S = 0.25f
