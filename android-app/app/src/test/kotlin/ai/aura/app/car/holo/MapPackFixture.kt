package ai.aura.app.car.holo

import android.database.sqlite.SQLiteDatabase
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.floor

/**
 * A small pack written to `tests/contracts/map-pack.json`'s format, for the suites that read or
 * copy one. Its cells are counted here from the contract's own formula, not by [MapPack], so a
 * reader that numbers them differently reads nothing. Every place is made up (Ruritania,
 * Strelsau, near 5, 5); no binary pack is kept in the repo.
 */
object MapPackFixture {
    val contract = JSONObject(File("../../tests/contracts/map-pack.json").readText())
    val format = contract.getJSONObject("pack").getInt("format")

    class Feature(
        val kind: String,
        /** `[lon, lat]` points. */
        val points: List<DoubleArray>,
        val h: Double? = null,
        val tag: String = "",
        val name: String = "",
        val extra: String? = null,
        /** The geometry as written, for a row whose geom is not points (the ground's heights). */
        val geom: String? = null,
    )

    /** One cell's ground (T-1105), under the contract's own kind: [h] is [n] by [n] metres. */
    fun ground(cx: Int, cy: Int, n: Int, h: List<Int>) = Feature(
        contract.getJSONObject("pack").getJSONObject("kinds").getString("ground"),
        listOf(doubleArrayOf((cx + 0.5) / 100, (cy + 0.5) / 100)), tag = n.toString(), name = "$cx,$cy", geom = h.toString(),
    )

    fun building(w: Double, s: Double, e: Double, n: Double, h: Double? = 9.0) =
        Feature("b", listOf(doubleArrayOf(w, s), doubleArrayOf(e, s), doubleArrayOf(e, n), doubleArrayOf(w, n), doubleArrayOf(w, s)), h, "osm")

    fun road(name: String, vararg pts: Pair<Double, Double>, kind: String = "residential", extra: String? = null) =
        Feature("r", pts.map { doubleArrayOf(it.first, it.second) }, null, kind, name, extra)

    /** A pack of [features] covering [bbox] (w, s, e, n), built at [built]; [format] is the contract's unless a test says otherwise. */
    fun write(
        file: File,
        bbox: DoubleArray,
        built: Long,
        features: List<Feature>,
        format: Int = this.format,
        name: String = "Ruritania",
        /** The meta row `coverage` (T-1048), written as given (even a bad one); none when null, as a pack from before it. */
        coverage: String? = null,
    ): File {
        file.parentFile?.mkdirs()
        file.delete()
        val db = SQLiteDatabase.openOrCreateDatabase(file, null)
        try {
            db.execSQL("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execSQL(
                "CREATE TABLE features(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, w REAL NOT NULL, s REAL NOT NULL, " +
                    "e REAL NOT NULL, n REAL NOT NULL, h REAL, tag TEXT NOT NULL, name TEXT NOT NULL, extra TEXT, geom TEXT NOT NULL)",
            )
            db.execSQL("CREATE TABLE cells(cell INTEGER NOT NULL, fid INTEGER NOT NULL, PRIMARY KEY(cell, fid)) WITHOUT ROWID")
            db.beginTransaction()
            for ((key, value) in mapOf(
                "format" to format.toString(), "name" to name, "bbox" to bbox.joinToString(","), "built" to built.toString(),
                "buildings" to features.count { it.kind == "b" }.toString(), "roads" to features.count { it.kind == "r" }.toString(), "places" to "0",
            ) + (coverage?.let { mapOf(contract.getJSONObject("pack").getJSONObject("coverage").getString("key") to it) } ?: emptyMap())
            ) db.execSQL("INSERT INTO meta VALUES (?, ?)", arrayOf(key, value))
            for ((i, f) in features.withIndex()) {
                val id = i + 1L
                val w = f.points.minOf { it[0] }
                val s = f.points.minOf { it[1] }
                val e = f.points.maxOf { it[0] }
                val n = f.points.maxOf { it[1] }
                val geom = f.geom ?: JSONArray().also { a -> f.points.forEach { a.put(JSONArray().put(it[0]).put(it[1])) } }.toString()
                db.execSQL(
                    "INSERT INTO features(id, kind, w, s, e, n, h, tag, name, extra, geom) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    arrayOf<Any?>(id, f.kind, w, s, e, n, f.h, f.tag, f.name, f.extra, geom),
                )
                for (y in floor(s * 100).toLong()..floor(n * 100).toLong()) {
                    for (x in floor(w * 100).toLong()..floor(e * 100).toLong()) {
                        db.execSQL("INSERT OR IGNORE INTO cells VALUES (?, ?)", arrayOf<Any?>((y + 9000) * 36000 + x + 18000, id))
                    }
                }
            }
            db.setTransactionSuccessful()
            db.endTransaction()
        } finally {
            db.close()
        }
        return file
    }
}
