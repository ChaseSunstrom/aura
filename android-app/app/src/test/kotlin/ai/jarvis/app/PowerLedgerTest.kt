package ai.jarvis.app

import ai.jarvis.app.automation.actions.builtin.PowerLedger
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset

/**
 * The daily power ledger against `tests/contracts/power-ledger.json` (T-0582): a made-up day of
 * fake readings must add up to the contract's seconds, shared drop and peak temperature per
 * feature. The python mirror (`tools/power_ledger_test.py`) reads the same file.
 */
class PowerLedgerTest {
    private val contract = JSONObject(File("../../tests/contracts/power-ledger.json").readText())

    private fun reading(o: JSONObject) = PowerLedger.Reading(
        at = Instant.parse(o.getString("at")),
        on = o.getJSONArray("on").let { a -> (0 until a.length()).map { a.getString(it) }.toSet() },
        cpuMs = o.getLong("cpu_ms"),
        percent = o.getInt("percent"),
        charging = o.getBoolean("charging"),
        tempC = if (o.has("temp_c")) o.getDouble("temp_c") else null,
    )

    private fun fed(case: JSONObject): PowerLedger.Ledger {
        val ledger = PowerLedger.Ledger(ZoneOffset.UTC)
        val readings = case.getJSONArray("readings")
        for (i in 0 until readings.length()) ledger.record(reading(readings.getJSONObject(i)))
        return ledger
    }

    private fun cases() = contract.getJSONArray("cases").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }

    /** Same keys, same numbers (to a hundredth), whatever the order a JSONObject keeps them in. */
    private fun assertSame(path: String, want: Any?, got: Any?) {
        when (want) {
            is JSONObject -> {
                val g = got as JSONObject
                assertEquals("$path keys", want.keys().asSequence().toSet(), g.keys().asSequence().toSet())
                want.keys().forEach { assertSame("$path.$it", want.get(it), g.get(it)) }
            }
            is JSONArray -> {
                val g = got as JSONArray
                assertEquals("$path length", want.length(), g.length())
                for (i in 0 until want.length()) assertSame("$path[$i]", want.get(i), g.get(i))
            }
            is Number -> assertEquals(path, want.toDouble(), (got as Number).toDouble(), 0.005)
            else -> assertEquals(path, want, got)
        }
    }

    @Test fun `the contract names the features the ledger keeps`() {
        val names = contract.getJSONArray("features").let { a -> (0 until a.length()).map { a.getString(it) } }
        assertEquals(names, PowerLedger.FEATURES)
        assertEquals(contract.getInt("keep_days"), PowerLedger.KEEP_DAYS)
        assertEquals(contract.getLong("max_gap_s"), PowerLedger.MAX_GAP_S)
    }

    @Test fun `fake readings add up to the contract's seconds, shared drop and peak per feature`() {
        for (case in cases()) {
            val days = case.optJSONArray("days") ?: continue
            assertSame(case.getString("name"), days, fed(case).daysJson())
        }
    }

    @Test fun `a feature that was on is credited seconds and a share of the drop, never the plugged-in rise`() {
        val day = fed(cases()[0]).daysJson().getJSONObject(0)
        val features = day.getJSONObject("features")
        assertEquals(1800, features.getJSONObject("wake").getInt("seconds"))
        assertEquals(3.5, features.getJSONObject("wake").getDouble("drop_pct"), 0.005)
        // the car was on while the phone charged and climbed 85 -> 86: no negative drop
        assertEquals(0.0, features.getJSONObject("car").getDouble("drop_pct"), 0.0)
        // the hour-long gap is counted as max_gap_s, not as an hour
        assertEquals(60, day.getInt("covered_min"))
    }

    @Test fun `only the newest seven days are kept`() {
        for (case in cases()) {
            val want = case.optJSONArray("dates") ?: continue
            val got = fed(case).daysJson()
            assertEquals(want.length(), got.length())
            for (i in 0 until want.length()) assertEquals(want.getString(i), got.getJSONObject(i).getString("date"))
        }
    }

    @Test fun `a clock that runs backwards credits nothing and does not rewind the day`() {
        val ledger = PowerLedger.Ledger(ZoneOffset.UTC)
        ledger.record(PowerLedger.Reading(Instant.parse("2030-03-01T10:00:00Z"), setOf("wake"), 0, 80, false, 30.0))
        ledger.record(PowerLedger.Reading(Instant.parse("2030-03-01T09:00:00Z"), setOf("wake"), 5000, 70, false, 31.0))
        ledger.record(PowerLedger.Reading(Instant.parse("2030-03-01T09:10:00Z"), setOf("wake"), 6000, 69, false, 31.0))
        val day = ledger.daysJson().getJSONObject(0)
        assertEquals(1, ledger.daysJson().length())
        assertEquals(600, day.getJSONObject("features").getJSONObject("wake").getInt("seconds"))
    }

    @Test fun `the ledger survives being written down and read back`() {
        val ledger = fed(cases()[0])
        val back = PowerLedger.Ledger.fromJson(ledger.toJson(), ZoneOffset.UTC)
        assertSame("round trip", ledger.daysJson(), back.daysJson())
        // and it carries on from the last reading, not from scratch
        back.record(PowerLedger.Reading(Instant.parse("2030-01-10T09:50:00Z"), setOf("car"), 410000, 86, true, 41.0))
        assertEquals(2400, back.daysJson().getJSONObject(0).getJSONObject("features").getJSONObject("car").getInt("seconds"))
    }

    @Test fun `a damaged file reads as an empty ledger`() {
        for (bad in listOf("", "not json", "[]", "{\"days\": 4}", "{\"days\": [{\"date\": \"x\"}]}")) {
            val ledger = PowerLedger.Ledger.parse(bad, ZoneOffset.UTC)
            assertEquals(bad, 0, ledger.daysJson().length())
        }
    }

    @Test fun `power_report answers today, yesterday and the days in the contract's shape`() {
        val answer = contract.getJSONObject("answer")
        val stored = JSONObject().put("days", answer.getJSONArray("days"))
        val ledger = PowerLedger.Ledger.fromJson(stored, ZoneOffset.UTC)
        val report = ledger.report(LocalDate.parse(answer.getString("today")))
        assertSame("answer", answer, report)
        val first = report.getJSONArray("days").getJSONObject(0)
        val fields = contract.getJSONArray("day_fields").let { a -> (0 until a.length()).map { a.getString(it) } }
        assertTrue(first.keys().asSequence().all { it in fields })
    }

    @Test fun `a day with no rows is not invented`() {
        val report = PowerLedger.Ledger(ZoneOffset.UTC).report(LocalDate.parse("2030-01-10"))
        assertEquals(0, report.getJSONArray("days").length())
        assertEquals("2030-01-09", report.getString("yesterday"))
    }
}
