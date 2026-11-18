package ai.aura.app

import ai.aura.app.config.JarvisConfig
import ai.aura.app.safety.ImpactAlert
import ai.aura.app.safety.ImpactWatch
import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * The sensor listener's lifetime follows the setting, and a signal starts the alert seam: never
 * a real text or call, only [ImpactAlert] (T-1209). Does not cover a real accelerometer's rate
 * or batching; Robolectric delivers the events we hand it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ImpactWatchTest {
    private val app: Context get() = RuntimeEnvironment.getApplication()
    private lateinit var sm: SensorManager
    private lateinit var config: JarvisConfig
    private val kinds = mutableListOf<String>()
    private var car = false

    @Before fun setUp() {
        sm = app.getSystemService(SensorManager::class.java)
        shadowOf(sm).addSensor(Sensor.TYPE_ACCELEROMETER, org.robolectric.shadows.ShadowSensor.newInstance(Sensor.TYPE_ACCELEROMETER))
        config = JarvisConfig(app)
        config.impactWatch = false
        kinds.clear()
        car = false
    }

    private fun watch() = ImpactWatch(config, sm, ImpactAlert { kinds += it }, carUp = { car })

    private fun listening() = shadowOf(sm).listeners.isNotEmpty()

    /** One sample of [magnitude] m/s² at [ms] (the sensor clock is in nanoseconds). */
    private fun sample(ms: Long, magnitude: Float) {
        val e = org.robolectric.shadows.ShadowSensorManager.createSensorEvent(3)
        e.values[0] = 0f
        e.values[1] = 0f
        e.values[2] = magnitude
        e.timestamp = ms * 1_000_000L
        shadowOf(sm).sendSensorEventToListeners(e)
    }

    /** A drop at [from] and eleven seconds of a phone lying still. */
    private fun dropAndLieStill(from: Long = 0L) {
        sample(from, 60f)
        var t = from + 100
        while (t <= from + 12_000) { sample(t, 9.81f); t += 100 }
    }

    @Test fun `with the setting off no listener is registered, and no signal starts anything`() {
        val w = watch().also { it.start() }
        assertFalse(listening())
        dropAndLieStill()
        assertTrue(kinds.isEmpty())
        w.stop()
    }

    @Test fun `with the setting on a fall starts the alert once, as a fall`() {
        config.impactWatch = true
        val w = watch().also { it.start() }
        assertTrue(listening())
        dropAndLieStill()
        assertEquals(listOf("fall"), kinds)
        w.stop()
    }

    @Test fun `with the car link up the same trace is a crash`() {
        config.impactWatch = true
        car = true
        val w = watch().also { it.start() }
        dropAndLieStill()
        assertEquals(listOf("crash"), kinds)
        w.stop()
    }

    @Test fun `turning the setting on and off registers and unregisters while the watch runs`() {
        val w = watch().also { it.start() }
        assertFalse(listening())
        config.impactWatch = true
        assertTrue("the pref change registers the listener", listening())
        config.impactWatch = false
        assertFalse("and the next one drops it", listening())
        w.stop()
    }

    @Test fun `stop drops the listener even with the setting on`() {
        config.impactWatch = true
        val w = watch().also { it.start() }
        w.stop()
        assertFalse(listening())
    }

    @Test fun `refresh twice registers once`() {
        config.impactWatch = true
        val w = watch().also { it.start(); it.refresh() }
        assertEquals(1, shadowOf(sm).listeners.size)
        w.stop()
    }

    @Test fun `the setting is off by default`() {
        assertFalse(JarvisConfig(app).also { RuntimeEnvironment.getApplication().getSharedPreferences("jarvis_config", 0).edit().clear().commit() }.impactWatch)
    }
}
