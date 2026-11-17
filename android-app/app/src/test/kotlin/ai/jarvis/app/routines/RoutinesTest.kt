package ai.jarvis.app.routines

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The owner, 2026-09-27: "Every time you connect to my car, start my playlist on shuffle",
 * "when I get to my girlfriends house, send her a text letting her know im here". These are
 * the rules the phone applies before it hands a routine to Jarvis.
 */
@RunWith(RobolectricTestRunner::class)
class RoutinesTest {
    private val zone = ZoneId.of("America/Winnipeg")
    private fun at(day: Int, hour: Int, minute: Int = 0) = ZonedDateTime.of(2026, 9, day, hour, minute, 0, 0, zone)

    private val snapshot = RoutineSnapshot.parse(
        JSONObject(
            """
            {"routines": [
              {"id": "car1", "name": "Rocky", "when": {"event": "car_connected"}, "do": "Play Rocky 3 on shuffle", "cooldown_min": 10},
              {"id": "gf1", "name": "Here", "when": {"event": "arrive", "place": "p1", "place_label": "Cameron's house", "after": "17:00"},
               "do": "Text Cameron I'm here", "cooldown_min": 30,
               "allow_without_asking": [{"action": "send_sms", "to": "Cameron"}]},
              {"id": "am", "name": "Morning", "when": {"event": "time", "at": "07:30", "days": ["mon", "fri"]}, "do": "Read my calendar"},
              {"id": "late", "name": "Late", "when": {"event": "car_disconnected", "after": "22:00", "before": "06:00"}, "do": "x"},
              {"name": "no id", "when": {"event": "time"}}
            ],
            "places": [{"id": "p1", "label": "Cameron's house", "lat": 42.03, "lon": -40.62, "radius_m": 150}],
            "people": [{"name": "Cameron", "phone": "+1 (515) 555-0100"}]}
            """.trimIndent()
        )
    )

    @Test fun `the snapshot is read, and a routine it cannot read is left out`() {
        assertEquals(listOf("car1", "gf1", "am", "late"), snapshot.routines.map { it.id })
        assertTrue(snapshot.watchesPlaces && snapshot.hasTimes)
        assertEquals(Unasked("send_sms", "Cameron"), snapshot.routines[1].unasked.single())
    }

    @Test fun `a place a house routine waits on is watched, and only its edges go to the house`() {
        val houseOnly = RoutineSnapshot.parse(JSONObject("""{"routines": [], "places": [], "house_places": ["home"]}"""))
        assertEquals(setOf("home"), houseOnly.housePlaces)
        assertTrue(houseOnly.watchesPlaces)
        assertFalse(RoutineSnapshot.parse(JSONObject("""{"routines": []}""")).watchesPlaces)
        assertEquals(
            listOf("home" to "arrive", "home" to "leave"),
            RoutineRules.houseEdges(setOf("home", "gym"), setOf("home", "work"), setOf("home")),
        )
        assertEquals(emptyList<Pair<String, String>>(), RoutineRules.houseEdges(setOf("gym"), setOf("work"), setOf("home")))
    }

    @Test fun `hours, days and the cooldown decide whether it runs now`() {
        val gf = snapshot.routines[1]
        // 2026-09-25 is a Friday.
        assertFalse("before its hours", RoutineRules.mayRun(gf, at(25, 16, 59), null))
        assertTrue(RoutineRules.mayRun(gf, at(25, 18), null))
        val fired = at(25, 18).toInstant().toEpochMilli()
        assertFalse("GPS in and out of the circle is not a second visit", RoutineRules.mayRun(gf, at(25, 18, 20), fired))
        assertTrue(RoutineRules.mayRun(gf, at(25, 18, 31), fired))
        // A window across midnight.
        val late = snapshot.routines[3]
        assertTrue(RoutineRules.mayRun(late, at(25, 23), null))
        assertTrue(RoutineRules.mayRun(late, at(26, 5, 59), null))
        assertFalse(RoutineRules.mayRun(late, at(26, 12), null))
        assertFalse(RoutineRules.mayRun(snapshot.routines[0].copy(enabled = false), at(25, 12), null))
        // Days.
        val am = snapshot.routines[2]
        assertFalse("a Saturday", RoutineRules.mayRun(am, at(26, 7, 30), null))
    }

    @Test fun `arriving counts inside the radius, leaving only well past it`() {
        val places = snapshot.places
        val (entered, _) = RoutineRules.transitions(emptySet(), 42.0301, -40.6201, 20.0, places)
        assertEquals(setOf("p1"), entered)
        // 170 m out: past the radius, not past the margin -- still there.
        val (_, stillIn) = RoutineRules.transitions(setOf("p1"), 42.0315, -40.62, 20.0, places)
        assertTrue(stillIn.isEmpty())
        val (_, left) = RoutineRules.transitions(setOf("p1"), 42.0330, -40.62, 20.0, places)
        assertEquals(setOf("p1"), left)
        // A vague fix says nothing.
        assertTrue(RoutineRules.transitions(emptySet(), 42.03, -40.62, 900.0, places).first.isEmpty())
    }

    @Test fun `a place added while inside it is a baseline, not an arrival`() {
        val places = listOf(RoutinePlace("p1", "Home", 42.03, -40.62, 150.0))
        assertEquals(setOf("p1"), RoutineRules.baseline(emptySet(), 42.0301, -40.6201, 20.0, places))
        assertTrue(RoutineRules.baseline(setOf("p1"), 42.0301, -40.6201, 20.0, places).isEmpty())
        assertTrue(RoutineRules.baseline(emptySet(), 42.0301, -40.6201, 900.0, places).isEmpty())
        assertTrue(RoutineRules.baseline(emptySet(), 42.05, -40.62, 20.0, places).isEmpty())
    }

    @Test fun `the next time of day skips days it does not run`() {
        val am = snapshot.routines[2]
        // Friday 08:00 -> Monday 07:30.
        assertEquals(at(28, 7, 30), RoutineRules.nextFire(am, at(25, 8)))
        assertEquals(at(25, 7, 30), RoutineRules.nextFire(am, at(25, 7)))
    }

    @Test fun `a text goes unasked only to the allowed person, and only once consented`() {
        val gf = snapshot.routines[1]
        val key = RoutineRules.consentKey(gf, gf.unasked.single())
        val toCameron = JSONObject().put("to", "5155550100").put("body", "I'm here")
        assertFalse("no consent yet", RoutineRules.allowsUnasked(gf, "send_sms", toCameron, snapshot.people, emptySet()))
        assertTrue(RoutineRules.allowsUnasked(gf, "send_sms", toCameron, snapshot.people, setOf(key)))
        assertFalse(RoutineRules.allowsUnasked(gf, "send_sms", JSONObject().put("to", "5155550199"), snapshot.people, setOf(key)))
        assertFalse(RoutineRules.allowsUnasked(gf, "place_call", JSONObject().put("number", "5155550100"), snapshot.people, setOf(key)))
        // A changed instruction is a new consent.
        assertFalse(RoutineRules.allowsUnasked(gf.copy(instruction = "Text everyone"), "send_sms", toCameron, snapshot.people, setOf(key)))
    }

    @Test fun `a reply from the person, and a dated time, are triggers`() {
        // The owner, 2026-10-08: "add something to my calendar when it got a response".
        val r = RoutineSnapshot.parse(
            JSONObject(
                """{"routines": [
                  {"id": "m", "name": "Lori", "when": {"event": "message", "from": "Grandma Lori", "from_phone": "+1 515 555 0142"}, "do": "x", "once": true},
                  {"id": "y", "name": "Yes", "when": {"event": "message", "from": "Cameron", "contains": "yes"}, "do": "x"},
                  {"id": "t", "name": "Bank", "when": {"event": "time", "at": "09:00", "date": "2026-09-12"}, "do": "Call the bank"}
                ]}"""
            )
        ).routines
        val (lori, yes, bank) = r
        assertTrue(RoutineRules.messageMatches(lori, "Grandma Lori", "Sure, 3pm", null, fromSmsApp = true))
        assertTrue(RoutineRules.messageMatches(lori, "Lori Smith", "Sure", contactName = "Lori Smith", fromSmsApp = true))
        assertTrue(RoutineRules.messageMatches(lori, "(515) 555-0142", "Sure", null, fromSmsApp = true))
        assertFalse(RoutineRules.messageMatches(lori, "Cameron", "Sure", null, fromSmsApp = true))
        // A chat app's name counts for a routine whose every text still asks (Ren on Snapchat)...
        assertTrue(RoutineRules.messageMatches(lori, "Grandma Lori", "Sure", null, fromSmsApp = false))
        // ...but one allowed to text unasked trusts only the SMS app, where the number is hers.
        val unasked = lori.copy(unasked = listOf(Unasked("send_sms", "Sam")))
        assertFalse(RoutineRules.messageMatches(unasked, "Grandma Lori", "Sure", null, fromSmsApp = false))
        assertTrue(RoutineRules.messageMatches(unasked, "+1 515 555 0142", "Sure", null, fromSmsApp = true))
        assertTrue(RoutineRules.messageMatches(yes, "Cameron", "yes", null, fromSmsApp = false))
        assertTrue(RoutineRules.messageMatches(yes, "cameron", "YES let's go", null, fromSmsApp = true))
        assertFalse(RoutineRules.messageMatches(yes, "Cameron", "no", null, fromSmsApp = true))
        assertFalse(RoutineRules.messageMatches(bank, "Cameron", "yes", null, fromSmsApp = true))
        // A dated time fires on that day only, and never once it has passed.
        assertEquals(ZonedDateTime.of(2026, 9, 12, 9, 0, 0, 0, zone), RoutineRules.nextFire(bank, at(10, 12)))
        assertEquals(null, RoutineRules.nextFire(bank, at(12, 9, 1)))
    }

    @Test fun `what Jarvis is sent names the routine, the trigger and the instruction`() {
        val p = RoutineRules.prompt(snapshot.routines[0], "The phone just connected to the car")
        assertTrue(p.startsWith("[routine: Rocky] The phone just connected to the car. Do this now: Play Rocky 3 on shuffle"))
    }

    // --- tests/contracts/routine-armed.json: the console renders what this computes (T-0170) ---

    @Test fun `armed answers every compute case of the shared table`() {
        val table = JSONObject(File("../../tests/contracts/routine-armed.json").readText())
        assertEquals(table.getInt("max_fix_age_h") * 3600L, RoutineRules.MAX_FIX_AGE_S)
        val cases = table.getJSONArray("compute")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val f = c.getJSONObject("facts")
            val got = RoutineRules.armed(
                ArmedFacts(
                    fine = f.getBoolean("fine"), coarse = f.getBoolean("coarse"),
                    background = f.getBoolean("background"), providerOn = f.getBoolean("provider_on"),
                    watching = f.getBoolean("watching"),
                    lastFixAgeS = if (f.isNull("last_fix_age_s")) null else f.getLong("last_fix_age_s"),
                ),
            )
            val out = c.getJSONObject("out")
            val reason = if (out.isNull("reason")) null else out.getString("reason")
            assertEquals(c.getString("why"), Armed(out.getString("state"), reason), got)
        }
    }

    // --- tests/contracts/routine-rules.json: the PC's aura_desktop/routines.py reads the same table (T-0142) ---

    private val rules = JSONObject(File("../../tests/contracts/routine-rules.json").readText())
    private val ruleZone = ZoneId.of(rules.getString("zone"))
    private fun local(text: String) = LocalDateTime.parse(text).atZone(ruleZone)
    private fun one(id: String, name: String, `when`: JSONObject, extra: JSONObject.() -> Unit = {}): Routine {
        val r = JSONObject().put("id", id).put("name", name).put("when", `when`).put("do", "x")
        r.extra()
        return RoutineSnapshot.parse(JSONObject().put("routines", JSONArray().put(r))).routines.single()
    }

    @Test fun `mayRun answers every case of the shared table`() {
        val cases = rules.getJSONArray("may_run")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val r = one("r$i", "r$i", c.getJSONObject("when").put("event", "time").put("at", "00:00")) {
                put("enabled", c.getBoolean("enabled")).put("cooldown_min", c.getInt("cooldown_min"))
            }
            val now = local(c.getString("now"))
            val last = if (c.isNull("last_fired_min_ago")) null else now.toInstant().toEpochMilli() - c.getLong("last_fired_min_ago") * 60_000L
            assertEquals(c.getString("why"), c.getBoolean("runs"), RoutineRules.mayRun(r, now, last))
        }
    }

    @Test fun `nextFire answers every case of the shared table`() {
        val cases = rules.getJSONArray("next_fire")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val r = one("t$i", "t$i", c.getJSONObject("when").put("event", "time"))
            val got = RoutineRules.nextFire(r, local(c.getString("now")))?.toLocalDateTime()?.toString()
            assertEquals(c.getString("why"), if (c.isNull("next")) null else c.getString("next"), got)
        }
    }

    @Test fun `the prompt is the shared table's text`() {
        val cases = rules.getJSONArray("prompt")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val r = one("p$i", c.getString("name"), JSONObject().put("event", "time").put("at", "09:00")) { put("do", c.getString("instruction")) }
            assertEquals(c.getString("text"), RoutineRules.prompt(r, c.getString("what")))
        }
    }
}
