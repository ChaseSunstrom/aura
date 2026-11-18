package ai.aura.app.automation.actions.builtin

import ai.aura.app.automation.actions.ActionResult
import ai.aura.app.automation.actions.JarvisAction
import ai.aura.app.automation.policy.ActionTier
import ai.aura.app.car.input.HeadwayAccessibilityService
import ai.aura.app.config.JarvisConfig
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * `tests/contracts/island-settings.json`, as constants: the phone cannot read the file at run
 * time, so IslandSettingsActionTest reads it and fails when any of these drifts.
 */
internal object IslandSettingsContract {
    const val KEEP_AT_CAMERA = "keep_at_camera"
    const val STATUS = "status"
    const val COVER = "cover"
    const val BOTTOM = "conversations_at_bottom"
    val NAMES = listOf(KEEP_AT_CAMERA, STATUS, COVER, BOTTOM)

    /** A switch stored on that the island only shows while these are on too (Settings greys it out the same way). */
    val NEEDS = mapOf(STATUS to listOf(KEEP_AT_CAMERA), COVER to listOf(KEEP_AT_CAMERA, STATUS))

    const val NOTE_NEEDS = "needs"
    const val NOTE_ACCESSIBILITY = "cover_needs_accessibility"

    fun read(config: JarvisConfig): Map<String, Boolean> = mapOf(
        KEEP_AT_CAMERA to config.islandRest,
        STATUS to config.islandStatus,
        COVER to config.islandCover,
        BOTTOM to config.islandBottom,
    )

    fun write(config: JarvisConfig, name: String, on: Boolean) {
        when (name) {
            KEEP_AT_CAMERA -> config.islandRest = on
            STATUS -> config.islandStatus = on
            COVER -> config.islandCover = on
            BOTTOM -> config.islandBottom = on
        }
    }

    /**
     * What the island does not show yet, though the value is stored: the switches it follows that
     * are off, and a cover with no accessibility grant. Stored values are never rewritten to match
     * (the Settings toggles keep their dependants the same way), so turning the camera switch back
     * on brings the status back as it was.
     */
    fun notes(ctx: Context, now: Map<String, Boolean>): JSONArray {
        val notes = JSONArray()
        for ((name, needs) in NEEDS) {
            val unmet = needs.filter { now[it] != true }
            if (now[name] == true && unmet.isNotEmpty()) {
                notes.put(JSONObject().put("setting", name).put("note", NOTE_NEEDS).put("needs", JSONArray(unmet)))
            }
        }
        if (now[COVER] == true && !HeadwayAccessibilityService.isEnabled(ctx)) {
            // Settings opens the accessibility screen for this; a command from the house must not
            // throw one over whatever the owner is doing, so it says so and the owner grants it.
            notes.put(JSONObject().put("setting", COVER).put("note", NOTE_ACCESSIBILITY))
        }
        return notes
    }

    fun answer(ctx: Context, config: JarvisConfig, changed: List<String>? = null): ActionResult {
        val now = read(config)
        val settings = JSONObject().also { o -> NAMES.forEach { o.put(it, now.getValue(it)) } }
        val data = JSONObject().put("settings", settings).put("notes", notes(ctx, now))
        if (changed != null) data.put("changed", JSONArray(changed))
        return ActionResult.ok(data)
    }
}

/** Tier 1: the four island switches as they stand. */
object GetIslandSettings : JarvisAction {
    override val id = "get_island_settings"
    override val tier = ActionTier.AUTO
    override val description = "Read the island's settings: keep at the camera, status, cover, conversations at the bottom."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "device"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        IslandSettingsContract.answer(ctx, JarvisConfig(ctx))
}

/**
 * Tier 1 on the phone: the house reserves this action to its own approved tool (RESERVED_ACTIONS),
 * so the approval is the house's, and the toggles are the owner's own and one tap to undo.
 */
object SetIslandSettings : JarvisAction {
    override val id = "set_island_settings"
    override val tier = ActionTier.AUTO
    override val description = "Change the island's settings; name only the ones to change, each true or false."
    override val paramsSchema = mapOf(
        "keep_at_camera" to "bool: the resting island sits on the camera",
        "status" to "bool: it shows the time, network and battery",
        "cover" to "bool: it covers the status bar's own icons (needs the accessibility grant)",
        "conversations_at_bottom" to "bool: a conversation's island sits at the bottom",
    )
    override val capability = "device"

    /** The named switches and their values, or why the command is refused: nothing is applied then. */
    private fun parse(params: JSONObject): Pair<Map<String, Boolean>?, String?> {
        val wanted = LinkedHashMap<String, Boolean>()
        for (key in params.keys()) {
            if (key !in IslandSettingsContract.NAMES) {
                return null to "unknown island setting: $key (one of ${IslandSettingsContract.NAMES.joinToString(", ")})"
            }
            val v = params.opt(key)
            if (v !is Boolean) return null to "$key must be true or false"
            wanted[key] = v
        }
        if (wanted.isEmpty()) return null to "name at least one of ${IslandSettingsContract.NAMES.joinToString(", ")}"
        return wanted to null
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val (wanted, why) = parse(params)
        if (wanted == null) return ActionResult.error(why ?: "bad island settings")
        val config = JarvisConfig(ctx)
        val before = IslandSettingsContract.read(config)
        val changed = wanted.filter { (k, v) -> before[k] != v }.keys.toList()
        changed.forEach { IslandSettingsContract.write(config, it, wanted.getValue(it)) }
        return IslandSettingsContract.answer(ctx, config, changed)
    }
}
