package ai.jarvis.app.assist.hermes

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.time.Instant
import java.time.OffsetDateTime

/**
 * One panel on the HUD surface — a typed card JARVIS pulls up.
 *
 * A panel arrives as `hud.panel.show`, is refreshed by `hud.panel.update`, and
 * leaves by `hud.panel.dismiss` (its own id, or `*` for the lot). The shape is
 * `tests/contracts/hud-panels.json`, verbatim: [payload] is the kind's own body
 * as the event sent it — nothing here draws (the console in the home WebView does).
 *
 * Mirror of the console's `Panel` in `aura-web/src/lib/hermes/panels.ts`;
 * `android-app/tools/hud_panels_test.py` checks the two carry the same rules.
 */
data class Panel(
    val id: String,
    /** One of [HudPanels.KINDS]; a kind off the list is still carried. */
    val kind: String,
    val title: String,
    /** The kind's own body, verbatim from the event. */
    val payload: JSONObject,
    /** Who put it up: `agent` (a tool), `user` (voice/click), `system` (an event). */
    val origin: String,
    /** Epoch ms, from the event's `created_at` when it parses, else when it arrived. */
    val createdAt: Long,
    /** Monotonic per panel; a lower rev than the one held is ignored. */
    val rev: Int,
    /** Seconds, as the emitter set it; null when the panel stays until dismissed. */
    val ttlS: Double?,
    /** Epoch ms this panel times out locally, or null — the ttl fallback. */
    val expiresAt: Long?,
)

/**
 * The panel surface as a reducer, pure so every rule is a JVM test.
 *
 * The rules are small and all here, the same three the console keeps:
 *
 *   - [rev][Panel.rev] is monotonic per panel; a show or update with a lower rev
 *     than the one held is ignored (a reconnect replays the stream, and a stale
 *     frame must not undo a newer one).
 *   - [DISMISS_ALL] (`*`) dismisses everything.
 *   - [ttlS][Panel.ttlS], when set, is the emitter's job — it dismisses the
 *     panel itself — but the client keeps [expiresAt][Panel.expiresAt] so a
 *     surface can time it out locally if the dismiss never comes (a dropped
 *     socket, a crashed plugin).
 *
 * A late joiner catches up with [seedFromList] from a `hud @<sid> list` reply.
 */
object HudPanels {

    /** The id `hud.panel.dismiss` uses to clear the whole surface. */
    const val DISMISS_ALL = "*"

    /**
     * The seventeen kinds the contract names, in its order. The last three are
     * the media kinds — a published artifact by url + mime — and the list is
     * append-only: the mirror (`android-app/tools/hud_panels_test.py`) compares
     * it to the contract element-wise.
     */
    val KINDS: List<String> = listOf(
        "text", "table", "image", "chart", "camera", "code", "terminal",
        "web", "device", "telemetry", "goal", "approval", "clarify", "notice",
        "video", "audio", "document",
    )

    /** Who a panel's origin may be, per the contract. */
    val ORIGINS: List<String> = listOf("agent", "user", "system")

    /**
     * A [Panel] from a `hud.panel.show` payload (or one row of a `hud list`).
     *
     * Null when there is no id — a panel with no id cannot be updated or
     * dismissed, so it is not one we can hold. Everything else is read loosely:
     * a field the emitter left out is a sensible default, never a crash.
     *
     * The ttl clock starts when the panel arrives here, so a `hud list` seeded
     * after a reconnect gives a whole window again rather than a stale one, and
     * the emitter's own dismiss is still the authority.
     */
    fun panelFromShow(payload: JSONObject, now: Long): Panel? {
        val id = payload.optString("id")
        if (id.isEmpty()) return null
        val ttlS = ttlOf(payload.opt("ttl_s"))
        return Panel(
            id = id,
            kind = payload.optString("kind").ifEmpty { "text" },
            title = payload.optString("title"),
            payload = payload.optJSONObject("payload") ?: JSONObject(),
            origin = payload.optString("origin").ifEmpty { "agent" },
            createdAt = createdMs(payload.opt("created_at"), now),
            rev = intOrNull(payload.opt("rev")) ?: 0,
            ttlS = ttlS,
            expiresAt = if (ttlS != null) now + (ttlS * 1000).toLong() else null,
        )
    }

    /** Insert or replace a panel, honouring its rev; a lower rev than held is ignored. */
    fun applyShow(panels: List<Panel>, panel: Panel): List<Panel> {
        val index = panels.indexOfFirst { it.id == panel.id }
        if (index == -1) return panels + panel
        if (panel.rev < panels[index].rev) return panels
        val next = panels.toMutableList()
        next[index] = panel
        return next
    }

    /**
     * Fold a `hud.panel.update` in: a new payload and/or title on an existing
     * panel. An update for a panel we never saw is ignored — there is nothing to
     * draw it on, and `hud list` is how a late joiner catches up. A lower rev
     * than held is ignored too.
     */
    fun applyUpdate(panels: List<Panel>, payload: JSONObject, now: Long): List<Panel> {
        val id = payload.optString("id")
        if (id.isEmpty()) return panels
        val index = panels.indexOfFirst { it.id == id }
        if (index == -1) return panels
        val held = panels[index]
        val revRaw = if (payload.has("rev")) intOrNull(payload.opt("rev")) else null
        if (revRaw != null && revRaw < held.rev) return panels
        val hasTtl = payload.has("ttl_s")
        val ttlS = if (hasTtl) ttlOf(payload.opt("ttl_s")) else held.ttlS
        val next = panels.toMutableList()
        next[index] = held.copy(
            rev = revRaw ?: held.rev,
            title = if (payload.has("title")) payload.optString("title") else held.title,
            payload = if (payload.has("payload")) (payload.optJSONObject("payload") ?: JSONObject()) else held.payload,
            ttlS = ttlS,
            // A fresh ttl on an update restarts the local clock; otherwise the
            // panel keeps the deadline it had.
            expiresAt = if (hasTtl) (if (ttlS != null) now + (ttlS * 1000).toLong() else null) else held.expiresAt,
        )
        return next
    }

    /** Remove one panel by id, or every panel when the id is [DISMISS_ALL]. */
    fun applyDismiss(panels: List<Panel>, id: String): List<Panel> {
        if (id == DISMISS_ALL) return if (panels.isNotEmpty()) emptyList() else panels
        val next = panels.filter { it.id != id }
        return if (next.size == panels.size) panels else next
    }

    /**
     * Seed the surface from a `hud list` reply: a JSON array of show-shaped rows.
     * Applied through [applyShow], so a row with a lower rev than one already
     * held (an event that beat the list back) does not clobber it.
     */
    fun seedFromList(panels: List<Panel>, list: JSONArray?, now: Long): List<Panel> {
        if (list == null) return panels
        var next = panels
        for (i in 0 until list.length()) {
            val row = list.optJSONObject(i) ?: continue
            val panel = panelFromShow(row, now) ?: continue
            next = applyShow(next, panel)
        }
        return next
    }

    /** Parse a `hud list` plugin output (a JSON array, or `{panels:[…]}`) into rows, tolerating junk. */
    fun parsePanelList(output: Any?): JSONArray = when (output) {
        is JSONArray -> output
        is JSONObject -> output.optJSONArray("panels") ?: JSONArray()
        is String -> {
            val text = output.trim()
            if (text.isEmpty()) {
                JSONArray()
            } else {
                try {
                    when (val parsed = JSONTokener(text).nextValue()) {
                        is JSONArray -> parsed
                        // Some plugins wrap the list: `{panels: [...]}`.
                        is JSONObject -> parsed.optJSONArray("panels") ?: JSONArray()
                        else -> JSONArray()
                    }
                } catch (e: Exception) {
                    JSONArray()
                }
            }
        }
        else -> JSONArray()
    }

    /** Drop panels whose local ttl has passed — the fallback when no dismiss arrived. */
    fun pruneExpired(panels: List<Panel>, now: Long): List<Panel> {
        val kept = panels.filter { it.expiresAt == null || now < it.expiresAt!! }
        return if (kept.size == panels.size) panels else kept
    }

    // --- loose reads, the same as the console's -------------------------------

    private fun intOrNull(value: Any?): Int? = when (value) {
        is Number -> value.toInt()
        is String -> value.toDoubleOrNull()?.toInt()
        else -> null
    }

    /** A positive number is a ttl; zero, negative, absent or unparseable is "no ttl". */
    private fun ttlOf(value: Any?): Double? {
        val n = when (value) {
            is Number -> value.toDouble()
            is String -> value.toDoubleOrNull()
            else -> null
        } ?: return null
        return if (n.isFinite() && n > 0) n else null
    }

    /** A `created_at` (ISO string or epoch-ms number) as epoch ms, or [now] when missing/unparseable. */
    private fun createdMs(value: Any?, now: Long): Long = when (value) {
        is Number -> value.toLong()
        is String -> {
            if (value.isBlank()) {
                now
            } else {
                parseInstant(value) ?: now
            }
        }
        else -> now
    }

    private fun parseInstant(text: String): Long? {
        try {
            return Instant.parse(text).toEpochMilli()
        } catch (_: Exception) {
        }
        try {
            return OffsetDateTime.parse(text).toInstant().toEpochMilli()
        } catch (_: Exception) {
        }
        return null
    }
}

/**
 * Which notice panels are news to a surface that shows each once (the wake
 * overlay's pull-up): the ones not in [seen]. A surface's first look seeds
 * [seen] with what is already up -- a panel there before the card came out is
 * not news. Returns the ids now seen and the fresh notices, oldest first.
 */
fun freshNotices(seen: Set<String>?, panels: List<Panel>): Pair<Set<String>, List<Panel>> {
    val ids = panels.map { it.id }.toSet()
    if (seen == null) return ids to emptyList()
    return (seen + ids) to panels.filter { it.kind == "notice" && it.id !in seen }.sortedBy { it.createdAt }
}
