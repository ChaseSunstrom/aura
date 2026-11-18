package ai.aura.app.routines

import org.json.JSONArray
import org.json.JSONObject
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * The routines Jarvis saved (`hermes/plugins/aura-hud/routines.py`), as the phone keeps
 * and judges them: which trigger fired, whether the routine may run now, what Jarvis is
 * asked to do, and whether a text it then sends was allowed to go unasked.
 *
 * Pure, so the rules the owner relies on are unit tests (`RoutinesTest`), not a drive:
 * the owner's examples (2026-09-27) were "Every time you connect to my car, start my
 * playlist on shuffle" and "when I get to my girlfriends house, send her a text letting
 * her know im here".
 *
 * Does not decide what a routine does: Jarvis carries out `do` as an ordinary turn, and
 * every step of it goes through the phone's own tiers.
 */
data class RoutineWhen(
    val event: String,
    val placeId: String? = null,
    val placeLabel: String? = null,
    val at: String? = null,
    val days: List<String> = emptyList(),
    val after: String? = null,
    val before: String? = null,
    /** A `time` routine's one day ("YYYY-MM-DD"): "tomorrow at nine" fires once, then. */
    val date: String? = null,
    /** A `message` routine's person, their number, and words the message must contain. */
    val from: String? = null,
    val fromPhone: String? = null,
    val contains: String? = null,
)

data class Unasked(val action: String, val to: String)

data class Routine(
    val id: String,
    val name: String,
    val on: RoutineWhen,
    val instruction: String,
    val once: Boolean = false,
    val cooldownMin: Int = 0,
    val enabled: Boolean = true,
    val unasked: List<Unasked> = emptyList(),
)

data class RoutinePlace(val id: String, val label: String, val lat: Double, val lon: Double, val radiusM: Double)

data class RoutinePerson(val name: String, val phone: String?)

data class RoutineSnapshot(
    val routines: List<Routine> = emptyList(),
    val places: List<RoutinePlace> = emptyList(),
    val people: List<RoutinePerson> = emptyList(),
    /** Places a house-run routine waits on (the away-lock, T-0332): watched, and their edges reported. */
    val housePlaces: Set<String> = emptySet(),
) {
    val watchesPlaces: Boolean
        get() = housePlaces.isNotEmpty() || routines.any { it.enabled && it.on.event in PLACE_EVENTS }
    val hasTimes: Boolean get() = routines.any { it.enabled && it.on.event == TIME }
    val watchesMessages: Boolean get() = routines.any { it.enabled && it.on.event == MESSAGE }

    companion object {
        const val CAR_CONNECTED = "car_connected"
        const val CAR_DISCONNECTED = "car_disconnected"
        const val ARRIVE = "arrive"
        const val LEAVE = "leave"
        const val TIME = "time"
        const val MESSAGE = "message"
        val PLACE_EVENTS = setOf(ARRIVE, LEAVE)

        /** The plugin's snapshot (`routines.snapshot()`); anything unreadable is left out. */
        fun parse(json: JSONObject?): RoutineSnapshot {
            if (json == null) return RoutineSnapshot()
            val routines = json.optJSONArray("routines").objects().mapNotNull { r ->
                val w = r.optJSONObject("when") ?: return@mapNotNull null
                val id = r.optString("id").ifEmpty { return@mapNotNull null }
                Routine(
                    id = id,
                    name = r.optString("name").ifEmpty { id },
                    on = RoutineWhen(
                        event = w.optString("event"),
                        placeId = w.optString("place").ifEmpty { null },
                        placeLabel = w.optString("place_label").ifEmpty { null },
                        at = w.optString("at").ifEmpty { null },
                        days = w.optJSONArray("days").strings(),
                        after = w.optString("after").ifEmpty { null },
                        before = w.optString("before").ifEmpty { null },
                        date = w.optString("date").ifEmpty { null },
                        from = w.optString("from").ifEmpty { null },
                        fromPhone = w.optString("from_phone").ifEmpty { null },
                        contains = w.optString("contains").ifEmpty { null },
                    ),
                    instruction = r.optString("do"),
                    once = r.optBoolean("once"),
                    cooldownMin = r.optInt("cooldown_min"),
                    enabled = r.optBoolean("enabled", true),
                    unasked = r.optJSONArray("allow_without_asking").objects()
                        .map { Unasked(it.optString("action"), it.optString("to")) }
                        .filter { it.action.isNotEmpty() && it.to.isNotEmpty() },
                )
            }
            val places = json.optJSONArray("places").objects().mapNotNull { p ->
                if (!p.has("lat") || !p.has("lon")) return@mapNotNull null
                RoutinePlace(p.optString("id"), p.optString("label"), p.optDouble("lat"), p.optDouble("lon"), p.optDouble("radius_m", 150.0))
            }
            val people = json.optJSONArray("people").objects().map { RoutinePerson(it.optString("name"), it.optString("phone").ifEmpty { null }) }
            val housePlaces = json.optJSONArray("house_places").strings().toSet()
            return RoutineSnapshot(routines, places, people, housePlaces)
        }

        private fun JSONArray?.objects(): List<JSONObject> =
            if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

        private fun JSONArray?.strings(): List<String> =
            if (this == null) emptyList() else (0 until length()).map { optString(it) }.filter { it.isNotEmpty() }
    }
}

/** What the phone knows about its own place watch (T-0170): the inputs of [RoutineRules.armed]. */
data class ArmedFacts(
    val fine: Boolean,
    val coarse: Boolean,
    /** Allowed in the background (always true before Android 10, which had no such permission). */
    val background: Boolean,
    /** At least one location provider is switched on. */
    val providerOn: Boolean,
    /** A location watch is registered now. */
    val watching: Boolean,
    /** Seconds since the last fix, or null for none yet. */
    val lastFixAgeS: Long?,
)

/** Whether place routines can fire: `armed`, `degraded` (no recent fix) or `not_armed` with a reason. */
data class Armed(val state: String, val reason: String?)

object RoutineRules {
    /** A fix older than this degrades the state: a still phone may legitimately go quiet. */
    const val MAX_FIX_AGE_S = 6 * 3600L

    /**
     * The phone-wide state of the place watch, by tests/contracts/routine-armed.json's `compute`
     * cases (the console draws it from the same table's `render` cases). Permission comes first:
     * it is the fix the owner has to make; fix age only degrades.
     */
    fun armed(f: ArmedFacts): Armed = when {
        !f.fine && !f.coarse -> Armed("not_armed", "no_location_permission")
        !f.background -> Armed("not_armed", "no_background_location")
        !f.providerOn -> Armed("not_armed", "location_off")
        !f.watching -> Armed("not_armed", "not_watching")
        f.lastFixAgeS == null || f.lastFixAgeS > MAX_FIX_AGE_S -> Armed("degraded", null)
        else -> Armed("armed", null)
    }

    /**
     * The place edges the house is told about (`routine_place`, T-0332): only for the places a
     * house-run routine waits on, so jarvis-core learns no more of where the owner goes than a
     * routine needs. Arrivals first, then departures, as [RoutineWatcher.onFix] fires them.
     */
    fun houseEdges(entered: Set<String>, left: Set<String>, housePlaces: Set<String>): List<Pair<String, String>> =
        entered.filter { it in housePlaces }.map { it to RoutineSnapshot.ARRIVE } +
            left.filter { it in housePlaces }.map { it to RoutineSnapshot.LEAVE }


    /** Beyond the radius by this much (or the fix's accuracy, if worse) before a leave counts. */
    const val EXIT_MARGIN_M = 50.0

    /** A fix this vague says nothing about a 150 m circle. */
    const val MAX_ACCURACY_M = 250.0

    private val DAY = mapOf(
        "mon" to DayOfWeek.MONDAY, "tue" to DayOfWeek.TUESDAY, "wed" to DayOfWeek.WEDNESDAY,
        "thu" to DayOfWeek.THURSDAY, "fri" to DayOfWeek.FRIDAY, "sat" to DayOfWeek.SATURDAY, "sun" to DayOfWeek.SUNDAY,
    )

    private fun time(hhmm: String?): LocalTime? = runCatching {
        val (h, m) = hhmm!!.split(":").map { it.toInt() }
        LocalTime.of(h, m)
    }.getOrNull()

    /**
     * Whether [r] may run at [now]: switched on, on one of its days, inside its hours
     * (a window may run past midnight: 22:00-06:00), and past its cooldown since
     * [lastFiredMs].
     */
    fun mayRun(r: Routine, now: ZonedDateTime, lastFiredMs: Long?): Boolean {
        if (!r.enabled) return false
        if (r.on.days.isNotEmpty() && r.on.days.mapNotNull { DAY[it] }.none { it == now.dayOfWeek }) return false
        val after = time(r.on.after)
        val before = time(r.on.before)
        if (after != null || before != null) {
            val t = now.toLocalTime()
            val from = after ?: LocalTime.MIN
            val to = before ?: LocalTime.MAX
            val inside = if (from <= to) t >= from && t <= to else t >= from || t <= to
            if (!inside) return false
        }
        if (lastFiredMs != null && r.cooldownMin > 0 &&
            now.toInstant().toEpochMilli() - lastFiredMs < r.cooldownMin * 60_000L
        ) return false
        return true
    }

    fun metres(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val r = 6_371_000.0
        val dLat = Math.toRadians(lat2 - lat1)
        val dLon = Math.toRadians(lon2 - lon1)
        val a = sin(dLat / 2).let { it * it } +
            cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(dLon / 2).let { it * it }
        return 2 * r * asin(sqrt(a))
    }

    /**
     * The places entered and left since [inside], for one fix. Inside is within the
     * radius; outside is past the radius plus [EXIT_MARGIN_M] (or the fix's accuracy),
     * so a phone parked on the edge does not arrive and leave all evening. A fix
     * vaguer than [MAX_ACCURACY_M] changes nothing.
     */
    fun transitions(
        inside: Set<String>,
        lat: Double,
        lon: Double,
        accuracyM: Double,
        places: List<RoutinePlace>,
    ): Pair<Set<String>, Set<String>> {
        if (accuracyM > MAX_ACCURACY_M) return emptySet<String>() to emptySet()
        val entered = HashSet<String>()
        val left = HashSet<String>()
        for (p in places) {
            val d = metres(lat, lon, p.lat, p.lon)
            if (p.id !in inside && d <= p.radiusM) entered += p.id
            if (p.id in inside && d > p.radiusM + maxOf(EXIT_MARGIN_M, accuracyM)) left += p.id
        }
        return entered to left
    }

    /**
     * The places in [places] not yet in [known] that this fix is inside: the first fix
     * after a place is added is where the phone already was, not an arrival. Without it
     * "when I get home, text Isa", said at home, fired at once and its own confirmation
     * was cut off (the 2026-10-08 drive). What it does not cover: a vague first fix
     * leaves the place unknown, so the next good fix is the baseline instead.
     */
    fun baseline(known: Set<String>, lat: Double, lon: Double, accuracyM: Double, places: List<RoutinePlace>): Set<String> {
        if (accuracyM > MAX_ACCURACY_M) return emptySet()
        return places.filter { it.id !in known && metres(lat, lon, it.lat, it.lon) <= it.radiusM }.map { it.id }.toSet()
    }

    /** The next moment a `time` routine fires after [now], on one of its days. */
    fun nextFire(r: Routine, now: ZonedDateTime): ZonedDateTime? {
        val at = time(r.on.at) ?: return null
        if (r.on.date != null) {
            val once = runCatching { LocalDate.parse(r.on.date) }.getOrNull()?.atTime(at)?.atZone(now.zone) ?: return null
            return once.takeIf { it.isAfter(now) }
        }
        val days = r.on.days.mapNotNull { DAY[it] }.toSet()
        var day = now.toLocalDate()
        repeat(8) {
            val candidate = day.atTime(at).atZone(now.zone)
            if (candidate.isAfter(now) && (days.isEmpty() || candidate.dayOfWeek in days)) return candidate
            day = day.plusDays(1)
        }
        return null
    }

    /** What Jarvis is sent when [r] fires: the name, what happened, and its own instruction. */
    fun prompt(r: Routine, what: String): String =
        "[routine: ${r.name}] $what. Do this now: ${r.instruction} " +
            "(A routine Sir set up; carry it out without asking a question, and answer in one short spoken sentence.)"

    private fun digits(s: String): String = s.filter { it.isDigit() }.takeLast(10)

    private fun name(s: String?): String = s.orEmpty().lowercase().filter { it.isLetterOrDigit() || it == ' ' }.trim()

    /**
     * Whether a message from [sender] saying [text] is the one [r] waits for. The sender
     * is what the messaging app shows: the contact's name ([contactName], looked up from
     * the person's number, since "Grandma Lori" to Jarvis is "Lori Smith" in Contacts),
     * Jarvis's own name for them, or a bare number from someone not in Contacts.
     *
     * Any chat app lets a stranger take the display name "Grandma Lori", so a routine
     * allowed to text or call unasked ([Routine.unasked]) matches a person with a number
     * only on [fromSmsApp], where the carrier's number is the one thing they cannot set.
     * Every other routine matches the name from any app (Isa answers on Snapchat; the
     * 2026-10-08 "tell my grandma what she says" never fired): a spoofed name there can
     * only start a turn whose texts and calls still ask the owner.
     */
    fun messageMatches(r: Routine, sender: String, text: String, contactName: String?, fromSmsApp: Boolean): Boolean {
        if (r.on.event != RoutineSnapshot.MESSAGE) return false
        if (r.on.fromPhone != null && r.unasked.isNotEmpty() && !fromSmsApp) return false
        val who = name(sender)
        val number = digits(sender)
        val same = (who.isNotEmpty() && (who == name(r.on.from) || who == name(contactName))) ||
            (number.length >= 7 && number == digits(r.on.fromPhone.orEmpty()))
        return same && (r.on.contains == null || text.contains(r.on.contains, ignoreCase = true))
    }

    /**
     * Whether [action] with [params] is one [r] was allowed to run unasked, per the owner's
     * consent [consented] (keys from [consentKey]). The recipient must be exactly the one
     * allowed: the same number (last ten digits), or the named person whose number it is.
     */
    fun allowsUnasked(
        r: Routine,
        action: String,
        params: JSONObject,
        people: List<RoutinePerson>,
        consented: Set<String>,
    ): Boolean {
        val target = listOf("to", "number", "phone").firstNotNullOfOrNull { k -> params.optString(k).ifEmpty { null } } ?: return false
        return r.unasked.any { u ->
            if (u.action != action || consentKey(r, u) !in consented) return@any false
            val allowed = people.firstOrNull { it.name.equals(u.to, ignoreCase = true) }?.phone ?: u.to
            val a = digits(allowed)
            val t = digits(target)
            (a.isNotEmpty() && a == t) || u.to.equals(target, ignoreCase = true)
        }
    }

    /** One consent per routine, action, recipient and instruction: changing any asks again. */
    fun consentKey(r: Routine, u: Unasked): String = "${r.id}|${u.action}|${u.to}|${r.instruction.hashCode()}"
}
