package ai.aura.app.automation.actions.builtin

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.TreeMap

/**
 * The daily power ledger (T-0582): per day and per feature (wake word, place watch, the adapter
 * link, the car) how long it was on, what the battery dropped while it was, and how hot the phone
 * got. `power_report` answers it and the console shows today against yesterday.
 *
 * Android does not let an app measure a feature's own draw, so this is not energy attribution: a
 * feature's drop is the whole phone's unplugged drop shared equally between the features that were
 * on in that interval. The rules are pinned by `tests/contracts/power-ledger.json`.
 *
 * It takes no readings of its own: [tick] rides the heartbeats the app already has (the adapter
 * channel's and the wake word's), so it adds no alarm and no wakelock. A day therefore says how many
 * minutes it covers, and a phone whose heartbeats stopped covers fewer; it does not guess the rest.
 */
object PowerLedger {
    val FEATURES = listOf("wake", "location", "link", "car")
    const val KEEP_DAYS = 7

    /** An interval longer than this is a gap (the app was asleep or dead), not an interval of use. */
    const val MAX_GAP_S = 1200L

    /** The two heartbeats overlap; a reading closer than this to the last is dropped. */
    private const val MIN_TICK_S = 240L
    private const val FILE = "power_ledger.json"

    /** `on` is the features that were running at the instant of the reading. */
    data class Reading(
        val at: Instant,
        val on: Set<String>,
        val cpuMs: Long,
        val percent: Int,
        val charging: Boolean,
        val tempC: Double?,
    )

    private class Feat(var seconds: Long = 0, var dropPct: Double = 0.0, var peak: Double? = null)

    private class Day(val date: LocalDate) {
        var coveredS = 0L
        var cpuMs = 0L
        var dropPct = 0.0
        var peak: Double? = null
        val features = FEATURES.associateWith { Feat() }
    }

    private fun round2(v: Double) = Math.round(v * 100) / 100.0

    class Ledger(private val zone: ZoneId) {
        private val days = TreeMap<LocalDate, Day>()
        private var last: Reading? = null

        @Synchronized
        fun record(r: Reading) {
            val date = r.at.atZone(zone).toLocalDate()
            val day = days.getOrPut(date) { Day(date) }
            r.tempC?.let { t ->
                day.peak = maxOf(day.peak ?: t, t)
                r.on.filter { it in FEATURES }.forEach { f -> day.features.getValue(f).let { it.peak = maxOf(it.peak ?: t, t) } }
            }
            val prev = last
            last = r
            while (days.size > KEEP_DAYS) days.remove(days.firstKey())
            if (prev == null) return
            val dt = r.at.epochSecond - prev.at.epochSecond
            // A clock that went back, or a reading over midnight, starts again: nothing is credited.
            if (dt <= 0 || prev.at.atZone(zone).toLocalDate() != date) return
            day.coveredS += minOf(dt, MAX_GAP_S)
            val on = prev.on.filter { it in FEATURES }
            on.forEach { day.features.getValue(it).seconds += minOf(dt, MAX_GAP_S) }
            if (dt > MAX_GAP_S) return
            // Cpu time falls when the process restarted: what it has now is all it ran since.
            day.cpuMs += (r.cpuMs - prev.cpuMs).let { if (it < 0) r.cpuMs else it }
            val drop = prev.percent - r.percent
            if (!prev.charging && !r.charging && drop > 0) {
                day.dropPct += drop
                on.forEach { day.features.getValue(it).dropPct += drop.toDouble() / on.size }
            }
        }

        @Synchronized
        fun lastAt(): Instant? = last?.at

        /** Newest day first, in the shape `power_report` answers and the console maps. */
        @Synchronized
        fun daysJson(): JSONArray = JSONArray(days.descendingMap().values.map { dayJson(it, raw = false) })

        @Synchronized
        fun report(today: LocalDate): JSONObject = JSONObject()
            .put("today", today.toString())
            .put("yesterday", today.minusDays(1).toString())
            .put("days", daysJson())

        /** What is written to disk: the answer's days with the unrounded sums, and the last reading. */
        @Synchronized
        fun toJson(): JSONObject {
            val out = JSONObject().put("days", JSONArray(days.descendingMap().values.map { dayJson(it, raw = true) }))
            last?.let { r ->
                out.put(
                    "last",
                    JSONObject().put("at", r.at.toString()).put("on", JSONArray(r.on.toList())).put("cpu_ms", r.cpuMs)
                        .put("percent", r.percent).put("charging", r.charging).also { o -> r.tempC?.let { o.put("temp_c", it) } },
                )
            }
            return out
        }

        private fun dayJson(d: Day, raw: Boolean): JSONObject {
            fun num(v: Double) = if (raw) v else round2(v)
            val o = JSONObject().put("date", d.date.toString())
                .put("covered_min", Math.round(d.coveredS / 60.0))
                .put("cpu_s", Math.round(d.cpuMs / 1000.0))
                .put("drop_pct", num(d.dropPct))
            d.peak?.let { o.put("peak_temp_c", it) }
            val f = JSONObject()
            FEATURES.forEach { name ->
                val x = d.features.getValue(name)
                f.put(name, JSONObject().put("seconds", x.seconds).put("drop_pct", num(x.dropPct)).also { j -> x.peak?.let { j.put("peak_temp_c", it) } })
            }
            o.put("features", f)
            if (raw) o.put("covered_s", d.coveredS).put("cpu_ms", d.cpuMs)
            return o
        }

        companion object {
            /** Reads what [toJson] wrote, or what `power_report` answered; a day it cannot read is skipped. */
            fun fromJson(o: JSONObject, zone: ZoneId): Ledger {
                val ledger = Ledger(zone)
                val arr = o.optJSONArray("days") ?: JSONArray()
                for (i in 0 until arr.length()) runCatching {
                    val j = arr.getJSONObject(i)
                    val day = Day(LocalDate.parse(j.getString("date")))
                    day.coveredS = if (j.has("covered_s")) j.getLong("covered_s") else j.getLong("covered_min") * 60
                    day.cpuMs = if (j.has("cpu_ms")) j.getLong("cpu_ms") else j.getLong("cpu_s") * 1000
                    day.dropPct = j.optDouble("drop_pct", 0.0)
                    if (j.has("peak_temp_c")) day.peak = j.getDouble("peak_temp_c")
                    val fj = j.optJSONObject("features")
                    FEATURES.forEach { name ->
                        val x = fj?.optJSONObject(name) ?: return@forEach
                        day.features.getValue(name).also {
                            it.seconds = x.optLong("seconds", 0)
                            it.dropPct = x.optDouble("drop_pct", 0.0)
                            if (x.has("peak_temp_c")) it.peak = x.getDouble("peak_temp_c")
                        }
                    }
                    ledger.days[day.date] = day
                }
                while (ledger.days.size > KEEP_DAYS) ledger.days.remove(ledger.days.firstKey())
                o.optJSONObject("last")?.let { l ->
                    ledger.last = runCatching {
                        Reading(
                            at = Instant.parse(l.getString("at")),
                            on = l.getJSONArray("on").let { a -> (0 until a.length()).map { a.getString(it) }.toSet() },
                            cpuMs = l.getLong("cpu_ms"),
                            percent = l.getInt("percent"),
                            charging = l.getBoolean("charging"),
                            tempC = if (l.has("temp_c")) l.getDouble("temp_c") else null,
                        )
                    }.getOrNull()
                }
                return ledger
            }

            /** A missing, empty or damaged file is an empty ledger: the next tick starts it again. */
            fun parse(text: String, zone: ZoneId): Ledger =
                runCatching { fromJson(JSONObject(text), zone) }.getOrElse { Ledger(zone) }
        }
    }

    /** Whether the wake word's service is up; set by `WakeWordService`. */
    @Volatile private var wakeOn = false

    fun wakeIs(on: Boolean) {
        wakeOn = on
    }

    private val lock = Any()

    /**
     * Takes a reading and adds it to the ledger on disk. Safe to call from any heartbeat and as
     * often as one likes: a reading within [MIN_TICK_S] of the last is dropped, and it never throws
     * (a ledger that cannot be written must not take a heartbeat down with it).
     */
    fun tick(ctx: Context, now: Instant = Instant.now()) {
        runCatching {
            val file = File(ctx.applicationContext.filesDir, FILE)
            val zone = ZoneId.systemDefault()
            synchronized(lock) {
                val ledger = Ledger.parse(runCatching { file.readText() }.getOrDefault(""), zone)
                val last = ledger.lastAt()
                if (last != null && now.epochSecond - last.epochSecond in 0 until MIN_TICK_S) return
                val battery = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
                val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
                val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
                if (level < 0 || scale <= 0) return
                val temp = battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)?.takeIf { it != Int.MIN_VALUE }?.let { it / 10.0 }
                val plugged = (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
                ledger.record(Reading(now, featuresOn(), android.os.Process.getElapsedCpuTime(), level * 100 / scale, plugged, temp))
                val tmp = File(file.parentFile, "$FILE.tmp")
                tmp.writeText(ledger.toJson().toString())
                if (!tmp.renameTo(file)) tmp.delete()
            }
        }
    }

    /** The ledger's days as `power_report` answers them, or an empty ledger when nothing has been written. */
    fun report(ctx: Context): JSONObject = runCatching {
        val zone = ZoneId.systemDefault()
        val text = synchronized(lock) { File(ctx.applicationContext.filesDir, FILE).readText() }
        Ledger.parse(text, zone).report(LocalDate.now(zone))
    }.getOrElse { Ledger(ZoneId.systemDefault()).report(LocalDate.now()) }

    private fun featuresOn(): Set<String> = buildSet {
        if (wakeOn) add("wake")
        if (ai.aura.app.routines.RoutineWatcher.watchingPlaces) add("location")
        if (ai.aura.app.channel.DeviceChannelHost.channel()?.isRegistered == true) add("link")
        if (ai.aura.app.car.service.CarLinkService.linkState.value is dev.headway.transport.LinkState.Connected) add("car")
    }
}
