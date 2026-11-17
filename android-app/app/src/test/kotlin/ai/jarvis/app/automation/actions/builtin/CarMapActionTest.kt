package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.car.holo.HoloGround
import ai.jarvis.app.config.JarvisConfig
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * `car_map` (T-1256): "switch the map to 3D / back to flat" by voice. Does not cover how either
 * map draws (the car goldens); it covers that the setting and the live flag both move, at once.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarMapActionTest {
    private val app get() = RuntimeEnvironment.getApplication()

    private fun run(params: JSONObject) = runBlocking { CarMapAction.execute(app, params) }

    @Test
    fun threeDSetsTheSettingAndTheLiveMapAndAnswersTheModeNow() {
        HoloGround.threeD = false
        val r = run(JSONObject().put("mode", "3d"))
        assertTrue(r.ok)
        assertTrue(JarvisConfig(app).carMap3d)
        assertTrue(HoloGround.threeD)
        assertEquals("3d", r.data!!.getString("mode"))
    }

    @Test
    fun flatTurnsBothBackOff() {
        JarvisConfig(app).carMap3d = true
        val r = run(JSONObject().put("mode", "flat"))
        assertTrue(r.ok)
        assertFalse(JarvisConfig(app).carMap3d)
        assertFalse(HoloGround.threeD)
        assertEquals("flat", r.data!!.getString("mode"))
    }

    @Test
    fun anyOtherValueIsRefusedAndNothingMoves() {
        JarvisConfig(app).carMap3d = true
        for (bad in listOf<Any>("2d", "3D ", "", true, 3)) {
            assertFalse(bad.toString(), run(JSONObject().put("mode", bad)).ok)
        }
        assertFalse(run(JSONObject()).ok)
        assertTrue(JarvisConfig(app).carMap3d)
        assertTrue(HoloGround.threeD)
    }

    @Test
    fun itIsALowTierDisplayPreference() {
        assertEquals("car_map", CarMapAction.id)
        assertEquals(ActionTier.AUTO, CarMapAction.tier)
    }
}
