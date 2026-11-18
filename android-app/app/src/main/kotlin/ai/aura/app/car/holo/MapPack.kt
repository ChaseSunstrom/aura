package ai.aura.app.car.holo

import android.content.Context
import android.database.DatabaseErrorHandler
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteException
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.floor

/** A road as the pack holds it: [oneway] is 1 along [line], -1 against it, 0 for neither; [kph] is 0 when no limit is posted. */
class PackRoad(
    val line: List<LatLon>, val kind: String, val name: String, val oneway: Int, val kph: Int, val lanes: Int = 0,
    val ref: String? = null, val bridge: Boolean = false, val tunnel: Boolean = false, val layer: Int = 0,
    val destination: String? = null, val destinationRef: String? = null, val junctionRef: String? = null,
    val trafficSign: String? = null,
    /** A car may not pass (`access` private or no, T-1049): drawn like any road, routed over only to reach an end on it. */
    val closed: Boolean = false,
    val turnLanes: String? = null, val turnLanesForward: String? = null, val turnLanesBackward: String? = null,
)

/**
 * One map pack (T-0979, T-1017): the SQLite file of a region's footprints and roads the house
 * builds (`aura/integrations/maps/pack.py`) and the phone copies ([MapPackSync]), read with
 * the platform's own SQLite, read-only. The format and its cell numbers are bound to
 * `tests/contracts/map-pack.json` by this class's tests.
 *
 * Answers what the house's `/api/maps/buildings` would, a [NearMap], with the same caps, so the
 * ground draws the same from either. Safe to call from any thread, and meant to be called off
 * the main one: a box is a few milliseconds of reading, not none.
 *
 * Not covered: a pack's places (the phone has no use for them yet); a footprint's holes or
 * second parts (the house keeps only the first ring); a pack cut short in the middle, which is
 * found when a read fails and then answers nothing, not when it is opened.
 */
class MapPack private constructor(
    private val db: SQLiteDatabase,
    private val bbox: DoubleArray,
    /** When the house built it, unix seconds: what the phone compares to tell a rebuild. */
    val built: Long,
    val name: String,
    /** Where the extract has roads (T-1048), or `null` for a pack with no usable row: the bbox alone then. */
    private val coverage: Coverage? = null,
) {
    /**
     * Whether [west]..[north] is a box this answers: a real one, not wider than the house's cap,
     * touching the pack's own and, when the pack says where it has roads, touching a cell with
     * some. A point is the box with both corners on it (T-1048). The grid is of cells, not an
     * outline: a hole smaller than the cell and its neighbours is not seen.
     */
    fun covers(west: Double, south: Double, east: Double, north: Double): Boolean =
        sane(west, south, east, north) && west <= bbox[2] && east >= bbox[0] && south <= bbox[3] && north >= bbox[1] &&
            (coverage?.touches(west, south, east, north) ?: true)

    /**
     * The coverage row of `tests/contracts/map-pack.json`: [bits] over [cols] by [rows] cells from
     * ([x0], [y0]) in hundredths of a degree, row by row from the south, each from the west.
     */
    private class Coverage(val x0: Long, val y0: Long, val cols: Long, val rows: Long, val bits: ByteArray) {
        fun touches(w: Double, s: Double, e: Double, n: Double): Boolean {
            val ax = maxOf(floor(w * PER_DEG).toLong(), x0)
            val bx = minOf(floor(e * PER_DEG).toLong(), x0 + cols - 1)
            val ay = maxOf(floor(s * PER_DEG).toLong(), y0)
            val by = minOf(floor(n * PER_DEG).toLong(), y0 + rows - 1)
            for (y in ay..by) for (x in ax..bx) {
                val i = (y - y0) * cols + (x - x0)
                if (bits[(i shr 3).toInt()].toInt() shr (i and 7).toInt() and 1 != 0) return true
            }
            return false
        }
    }

    /** The footprints and roads in the box, or `null` when this pack does not cover it or cannot be read. */
    fun near(west: Double, south: Double, east: Double, north: Double, limit: Int = LIMIT): NearMap? {
        if (!covers(west, south, east, north)) return null
        return try {
            val box = doubleArrayOf(west, south, east, north)
            NearMap(buildings(box, limit), roadsIn(box, limit).map { HoloRoad(it.line, it.kind, it.lanes, it.oneway, it.name, it.ref, it.bridge, it.tunnel, it.layer, it.destination, it.destinationRef, it.junctionRef, it.trafficSign, it.turnLanes, it.turnLanesForward, it.turnLanesBackward) }, details(box, limit), ground(box))
        } catch (e: SQLiteException) {
            Log.w(TAG, "a pack could not be read", e)
            null
        } catch (e: IllegalStateException) {
            null // closed under a read: the feed that asked has stopped
        }
    }

    /**
     * The roads in the box, whole, each with the kind, name, oneway and limit the house knew,
     * for routing on the phone. Empty when this pack does not cover the box or cannot be read.
     */
    fun roads(west: Double, south: Double, east: Double, north: Double, limit: Int = LIMIT): List<PackRoad> {
        if (!covers(west, south, east, north)) return emptyList()
        return try {
            roadsIn(doubleArrayOf(west, south, east, north), limit)
        } catch (e: SQLiteException) {
            Log.w(TAG, "a pack could not be read", e)
            emptyList()
        } catch (e: IllegalStateException) {
            emptyList()
        }
    }

    fun close() {
        runCatching { db.close() }
    }

    private fun buildings(box: DoubleArray, limit: Int): List<HoloBuilding> {
        val out = ArrayList<HoloBuilding>()
        for (row in rows('b', box, limit, 3)) {
            val look = row.extra?.let { e -> runCatching { MapsJson.look(JSONObject(e)) }.getOrNull() } ?: HoloLook.NONE
            out += HoloBuilding(row.pts, row.h?.takeIf { it.isFinite() && it > 0.0 }?.toFloat(), look)
        }
        return out.take(limit)
    }

    /** The pack's kind `d` rows (T-1066); a pack from before them has none, and a kind this app does not know is skipped. */
    private fun details(box: DoubleArray, limit: Int): List<HoloDetail> =
        rows('d', box, limit, 1).mapNotNull { row ->
            // A sign's words (T-1104); a pack from before them, or a row with none, says nothing.
            val says = row.extra?.let { runCatching { JSONObject(it).optString("text") }.getOrNull() }.orEmpty()
            HoloDetail.of(row.tag, row.pts, says)
        }.take(limit)

    /**
     * The pack's kind `g` rows (T-1105): the ground's heights for the cells the box touches. A
     * pack from before them has none, and a row that is not a whole grid is skipped.
     */
    private fun ground(box: DoubleArray): List<HoloGroundCell> {
        val out = ArrayList<HoloGroundCell>()
        for (y in floor(box[1] * PER_DEG).toLong()..floor(box[3] * PER_DEG).toLong()) {
            val mid = y.toDouble() / PER_DEG + 0.5 / PER_DEG
            val sql = "SELECT substr(name, 1, 24), substr(tag, 1, 4), CASE WHEN length(geom) <= $MAX_GROUND_CHARS THEN geom END " +
                "FROM features WHERE kind = 'g' AND id IN (SELECT fid FROM cells WHERE cell BETWEEN ${cell(box[0], mid)} AND ${cell(box[2], mid)}) " +
                "ORDER BY id LIMIT ${MapsJson.MAX_GROUND_CELLS}"
            db.rawQuery(sql, null).use { c ->
                while (c.moveToNext() && out.size < MapsJson.MAX_GROUND_CELLS) {
                    val at = c.getString(0).orEmpty().split(',').mapNotNull { it.toIntOrNull() }
                    val n = c.getString(1).orEmpty().toIntOrNull()
                    val h = if (c.isNull(2)) null else runCatching { JSONArray(c.getString(2)) }.getOrNull()
                    if (at.size == 2 && n != null) MapsJson.groundCell(at[0], at[1], n, h)?.let { out += it }
                }
            }
        }
        return out
    }

    private fun roadsIn(box: DoubleArray, limit: Int): List<PackRoad> {
        val out = ArrayList<PackRoad>()
        val cut = BooleanArray(1)
        for (row in rows('r', box, limit, 2, cut)) {
            val extra = runCatching { JSONObject(row.extra ?: "{}") }.getOrNull() ?: JSONObject()
            val oneway = extra.optInt("oneway", 0).let { if (it == 1 || it == -1) it else 0 }
            out += PackRoad(
                row.pts, row.tag, row.name, oneway, extra.optInt("kph", 0).coerceAtLeast(0),
                extra.optInt("lanes", 0).coerceIn(0, MapsJson.MAX_LANES),
                MapsJson.refOf(extra), extra.opt("bridge") == true, extra.opt("tunnel") == true, MapsJson.layerOf(extra),
                MapsJson.wordsOf(extra, "destination", MapsJson.MAX_DESTINATION),
                MapsJson.wordsOf(extra, "destination:ref", MapsJson.MAX_REF),
                MapsJson.wordsOf(extra, "junction:ref", MapsJson.MAX_JUNCTION_REF),
                MapsJson.wordsOf(extra, "traffic_sign", MapsJson.MAX_TRAFFIC_SIGN),
                closed = (extra.opt(ACCESS_KEY) as? String) in ACCESS_CLOSED,
                turnLanes = MapsJson.turnLanesOf(extra, "turn:lanes"),
                turnLanesForward = MapsJson.turnLanesOf(extra, "turn:lanes:forward"),
                turnLanesBackward = MapsJson.turnLanesOf(extra, "turn:lanes:backward"),
            )
        }
        return if (cut[0]) CutRoads(out.take(limit)) else out.take(limit)
    }

    private class Row(val id: Long, val d: Double, val h: Double?, val tag: String, val name: String, val extra: String?, val pts: List<LatLon>)

    /**
     * What the house's reader reads: per row of cells the box touches, the features of [kind] in
     * the cells' range whose own box meets it, once each, nearest first (T-1198): by the distance
     * from the box's centre to the feature's own box (0 when it covers the centre), ties by id.
     * So a cap keeps what is near the car, not the oldest ids; `capped` in
     * `tests/contracts/map-pack.json` is the case both readers answer alike. Every cell row's
     * nearest [limit] + 1 are gathered before the cap is applied, so no row of cells is left unread
     * because an earlier one filled it. The numbers come from doubles and longs this class
     * made, never from a file or a caller's text.
     *
     * A pack is a file the house sent, so what one ask holds in memory is bounded here, not by
     * the file: a row whose geometry is over [MAX_GEOM_CHARS] is not even read out of SQLite, one
     * with more than [MAX_POINTS] points is skipped, the text columns are cut, the points of one
     * ask stop at [MAX_POINTS_ASK], and after each row of cells only the nearest [limit] + 1 are
     * kept. A skipped row is a feature the ground goes without, not a failure; a caller that must
     * know roads were left out passes [cut], whose first element is set when any were (T-1029).
     */
    private fun rows(kind: Char, box: DoubleArray, limit: Int, min: Int, cut: BooleanArray? = null): List<Row> {
        val order = compareBy<Row>({ it.d }, { it.id })
        val cx = (box[0] + box[2]) / 2
        val cy = (box[1] + box[3]) / 2
        val seen = HashMap<Long, Row>()
        var points = 0L
        for (y in floor(box[1] * PER_DEG).toLong()..floor(box[3] * PER_DEG).toLong()) {
            val mid = y.toDouble() / PER_DEG + 0.5 / PER_DEG
            val a = cell(box[0], mid)
            val b = cell(box[2], mid)
            val sql = "SELECT id, h, substr(tag, 1, $MAX_TEXT), substr(name, 1, $MAX_TEXT), " +
                "CASE WHEN length(extra) <= $MAX_EXTRA_CHARS THEN extra END, CASE WHEN length(geom) <= $MAX_GEOM_CHARS THEN geom END, " +
                "max(w - $cx, $cx - e, 0) + max(s - $cy, $cy - n, 0) AS d FROM features WHERE kind = '$kind' " +
                "AND w <= ${box[2]} AND e >= ${box[0]} AND s <= ${box[3]} AND n >= ${box[1]} " +
                "AND id IN (SELECT fid FROM cells WHERE cell BETWEEN $a AND $b) ORDER BY d, id LIMIT ${limit + 1}"
            db.rawQuery(sql, null).use { c ->
                while (c.moveToNext()) {
                    if (seen.containsKey(c.getLong(0))) continue // in two rows of cells: one feature
                    val pts = line(c.getString(5).orEmpty(), min)
                    if (pts == null) {
                        cut?.set(0, true)
                        continue
                    }
                    seen[c.getLong(0)] = Row(
                        c.getLong(0), c.getDouble(6), if (c.isNull(1)) null else c.getDouble(1), c.getString(2).orEmpty(),
                        c.getString(3).orEmpty(), if (c.isNull(4)) null else c.getString(4), pts,
                    )
                    points += pts.size
                    if (points > MAX_POINTS_ASK) {
                        cut?.set(0, true)
                        break
                    }
                }
            }
            if (seen.size > limit + 1 || points > MAX_POINTS_ASK) {
                val keep = seen.values.sortedWith(order).take(limit + 1)
                seen.clear()
                keep.forEach { seen[it.id] = it }
                points = keep.sumOf { it.pts.size.toLong() }
            }
        }
        return seen.values.sortedWith(order)
    }

    /** A JSON line of `[lon, lat]` points, or `null` for one that is short, too long or has a bad point. */
    private fun line(geom: String, min: Int): List<LatLon>? {
        val arr = runCatching { JSONArray(geom) }.getOrNull() ?: return null
        if (arr.length() < min || arr.length() > MAX_POINTS) return null
        val pts = ArrayList<LatLon>(arr.length())
        for (i in 0 until arr.length()) pts += MapsJson.point(arr.optJSONArray(i), lonFirst = true) ?: return null
        return pts
    }

    companion object {
        /** `tests/contracts/map-pack.json`'s `pack.format`; a file with another number is refused. */
        const val FORMAT = 1

        /** The road's extra key and the values of it that shut a car out (the contract's `access`, T-1049); any other is an open road. */
        const val ACCESS_KEY = "access"
        val ACCESS_CLOSED = listOf("private", "no")

        /** The most text of one cell's heights that is read: 65 by 65 numbers of five digits. */
        private const val MAX_GROUND_CHARS = 32_768

        /** The house's caps (`MAX_BUILDINGS`, `MAX_ROADS`), of each kind. */
        const val LIMIT = 5000

        /** The house answers at most 0.1 degrees across; this reads no more in one ask. */
        const val MAX_SPAN_DEG = 0.1

        /** The most text of one feature's geometry that is read; a real footprint or road is a few KB. */
        const val MAX_GEOM_CHARS = 64 * 1024

        /** The most points of one line that is kept; a longer one is skipped whole, never cut. */
        const val MAX_POINTS = 2000

        /** The most points one ask holds, over all its lines: the pack's size does not set the heap used. */
        const val MAX_POINTS_ASK = 400_000

        private const val MAX_TEXT = 200
        private const val MAX_EXTRA_CHARS = 4096
        private const val PER_DEG = 100.0
        private const val COLUMNS = 36000L
        private const val TAG = "MapPack"

        /** The number of the cell holding a point: rows of [COLUMNS] cells of 0.01 degree, south to north. */
        fun cell(lon: Double, lat: Double): Long =
            (floor(lat * PER_DEG).toLong() + 9000) * COLUMNS + floor(lon * PER_DEG).toLong() + 18000

        /** `tests/contracts/map-pack.json`'s `pack.coverage.key` and `max_cells`; bound by this class's tests. */
        private const val COVERAGE_KEY = "coverage"
        private const val COVERAGE_MAX_CELLS = 4_000_000L

        /**
         * The grid in [row] for a pack of [box], or `null` (the bbox answer) for no row, one that
         * is not base64, one whose size is not the box's cells, or a box of more cells than the
         * contract allows: a pack is a file the house sent, and a bad row must not refuse a map.
         */
        private fun coverageOf(row: String?, box: List<Double>): Coverage? {
            if (row == null || row.length > COVERAGE_MAX_CELLS / 6) return null
            val x0 = floor(box[0] * PER_DEG).toLong()
            val y0 = floor(box[1] * PER_DEG).toLong()
            val cols = floor(box[2] * PER_DEG).toLong() - x0 + 1
            val rows = floor(box[3] * PER_DEG).toLong() - y0 + 1
            if (cols < 1 || rows < 1 || cols * rows > COVERAGE_MAX_CELLS) return null
            val bits = runCatching { Base64.decode(row.trim(), Base64.DEFAULT) }.getOrNull() ?: return null
            if (bits.size.toLong() != (cols * rows + 7) / 8) return null
            return Coverage(x0, y0, cols, rows, bits)
        }

        private fun sane(w: Double, s: Double, e: Double, n: Double) =
            w.isFinite() && s.isFinite() && e.isFinite() && n.isFinite() && w <= e && s <= n &&
                e - w <= MAX_SPAN_DEG + 1e-9 && n - s <= MAX_SPAN_DEG + 1e-9

        /**
         * The pack in [file], or `null` for anything that is not one this reads: a missing, empty,
         * garbled or other-format file. Opened read-only, and a file SQLite calls corrupt is left
         * where it is (the default handler would delete it: the sync decides what to do with it).
         */
        fun open(file: File): MapPack? {
            if (!file.isFile) return null
            var db: SQLiteDatabase? = null
            return try {
                db = SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY, DatabaseErrorHandler { })
                val meta = HashMap<String, String>()
                db.rawQuery("SELECT key, value FROM meta", null).use { c -> while (c.moveToNext()) meta[c.getString(0)] = c.getString(1) }
                if (meta["format"]?.trim()?.toInt() != FORMAT) throw IllegalArgumentException("format ${meta["format"]}")
                val box = meta.getValue("bbox").split(",").map { it.trim().toDouble() }
                require(box.size == 4 && box.all { it.isFinite() } && box[0] <= box[2] && box[1] <= box[3])
                MapPack(db, box.toDoubleArray(), meta["built"]?.trim()?.toLongOrNull() ?: 0L, meta["name"].orEmpty(), coverageOf(meta[COVERAGE_KEY], box))
            } catch (e: Exception) {
                // Not a pack: SQLite's own error, a missing key, a number that is none.
                runCatching { db?.close() }
                null
            }
        }
    }
}

/**
 * The packs in a folder (the copies [MapPackSync] keeps), read as one: the house's answer for
 * a box is what the packs covering it hold together. A pack copied in, replaced or deleted is
 * noticed on the next ask. Safe from any thread.
 */
open class MapPacks(private val dir: File) {
    private class Held(val size: Long, val at: Long, val pack: MapPack?)

    private val held = HashMap<String, Held>()

    /** The footprints and roads in the box from the packs that cover it, or `null` when none does. */
    open fun near(west: Double, south: Double, east: Double, north: Double): NearMap? {
        val got = open().mapNotNull { it.near(west, south, east, north) }
        if (got.isEmpty()) return null
        // Two regions that meet share the features on their border.
        return NearMap.merge(got, MapPack.LIMIT)
    }

    /** Whether a pack answers for the box at all (it may still hold nothing there). */
    open fun covers(west: Double, south: Double, east: Double, north: Double): Boolean =
        open().any { it.covers(west, south, east, north) }

    /** [MapPack.roads] over every pack that covers the box. */
    open fun roads(west: Double, south: Double, east: Double, north: Double, limit: Int = MapPack.LIMIT): List<PackRoad> {
        val out = LinkedHashMap<List<LatLon>, PackRoad>()
        var cut = false
        for (p in open()) {
            val got = p.roads(west, south, east, north, limit)
            cut = cut || got is CutRoads
            for (r in got) out.putIfAbsent(r.line, r)
        }
        return out.values.take(limit).let { if (cut) CutRoads(it) else it }
    }

    // A reader that was mid-open when close() ran must not fill `held` again: nobody would close
    // those packs. Until reopen(), an ask finds no packs.
    private var closed = false

    /** Closes every open pack; asks answer nothing until [reopen]. */
    @Synchronized
    fun close() {
        closed = true
        held.values.forEach { it.pack?.close() }
        held.clear()
    }

    /** Lets a closed set be asked again (a drive that starts after one stopped). */
    @Synchronized
    fun reopen() {
        closed = false
    }

    @Synchronized
    private fun open(): List<MapPack> {
        if (closed) return emptyList()
        val files = dir.listFiles { f -> f.isFile && f.name.endsWith(".pack") }.orEmpty()
        val names = files.map { it.name }.toSet()
        for (gone in held.keys - names) held.remove(gone)?.pack?.close()
        return files.sortedBy { it.name }.mapNotNull { f ->
            val h = held[f.name]
            if (h != null && h.size == f.length() && h.at == f.lastModified()) return@mapNotNull h.pack
            h?.pack?.close()
            // Remembered when unreadable too, so a bad file is not opened again on every ask.
            MapPack.open(f).also { held[f.name] = Held(f.length(), f.lastModified(), it) }
        }
    }

    companion object {
        /** Where the phone keeps its copies: not backed up (a state is gigabytes the house can give again). */
        fun dir(context: Context) = File(context.noBackupFilesDir, "maps")

        fun of(context: Context) = MapPacks(dir(context))
    }
}

/**
 * Roads read short (T-1029): a line was skipped, a geometry was too big to read, or the ask's
 * points budget ran out. Still a list for the ground, which draws what there is; the router
 * refuses it, since a road left out may be the way.
 */
class CutRoads(roads: List<PackRoad>) : List<PackRoad> by roads
