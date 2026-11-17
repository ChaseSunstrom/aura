package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.config.JarvisConfig
import android.content.Context
import org.json.JSONObject

/**
 * Tier 1: the car's map as the flat map or the 3D drive (T-1256). A display preference the owner
 * flips in Settings and undoes by saying the other word, so no prompt. [JarvisConfig.carMap3d]'s
 * setter also moves `HoloGround.threeD`, so the running car screen changes without a reconnect.
 * Does not cover how either map draws.
 */
object CarMapAction : JarvisAction {
    override val id = "car_map"
    override val tier = ActionTier.AUTO
    override val description = "Switch the car's map between the flat map and the 3D drive; answers the mode now in force."
    override val paramsSchema = mapOf("mode" to "string: `flat` or `3d`")
    override val capability = "device"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val on = when (params.opt("mode")) {
            "3d" -> true
            "flat" -> false
            else -> return ActionResult.error("mode must be \"flat\" or \"3d\"")
        }
        JarvisConfig(ctx).carMap3d = on
        return ActionResult.ok(JSONObject().put("mode", if (on) "3d" else "flat"))
    }
}
