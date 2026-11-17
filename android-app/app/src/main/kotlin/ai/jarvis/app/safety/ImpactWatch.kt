package ai.jarvis.app.safety

import ai.jarvis.app.automation.actions.builtin.EmergencyAlert
import ai.jarvis.app.car.service.CarLinkService
import ai.jarvis.app.config.JarvisConfig
import android.content.Context
import android.content.SharedPreferences
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.util.Log
import dev.headway.transport.LinkState
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.sqrt

/** What the watch does on a signal: start the emergency alert. A seam so no test places a call or sends a text. */
fun interface ImpactAlert {
    /** [kind] is "fall", or "crash" while the car link is up. Must return quickly: the countdown runs elsewhere. */
    fun start(kind: String)
}

/**
 * Listens to the accelerometer while, and only while, the owner's impact setting is on
 * ([JarvisConfig.impactWatch], off by default), feeds an [ImpactDetector] and, on a signal,
 * starts [alert]. The alert's own countdown is the owner's chance to cancel; this never sends
 * anything itself.
 *
 * Does not cover: tuning (see [ImpactThresholds]), a phone with no accelerometer (it stays
 * quiet and logs), or a process the system has killed (nothing listens until the automation
 * service is back).
 */
class ImpactWatch(
    private val config: JarvisConfig,
    private val sensors: SensorManager?,
    private val alert: ImpactAlert,
    private val carUp: () -> Boolean = { CarLinkService.linkState.value is LinkState.Connected },
    private val thresholds: ImpactThresholds = ImpactThresholds(),
) {
    private val detector = ImpactDetector(thresholds)
    private var registered = false

    private val listener = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            val v = e.values
            val magnitude = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
            if (!detector.feed(e.timestamp / 1_000_000L, magnitude)) return
            // The switch may have gone off while a batch was in flight.
            if (config.impactWatch) alert.start(if (carUp()) "crash" else "fall")
        }

        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
    }

    // Held strongly: SharedPreferences keeps its listeners weakly.
    private val prefsListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == JarvisConfig.KEY_IMPACT_WATCH) refresh()
    }

    /** Begin following the setting; call [stop] to end. */
    fun start() {
        config.watch(prefsListener)
        refresh()
    }

    fun stop() {
        config.unwatch(prefsListener)
        unregister()
    }

    /** Brings the sensor listener in line with the setting. Safe to call repeatedly. */
    @Synchronized
    fun refresh() {
        if (config.impactWatch) register() else unregister()
    }

    private fun register() {
        if (registered) return
        val accel = sensors?.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        if (accel == null) {
            Log.w(TAG, "impact watch is on but there is no accelerometer")
            return
        }
        // Batched by the sensor hub so the CPU is not woken for every sample: up to two seconds
        // late costs nothing against ten seconds of stillness.
        registered = sensors.registerListener(listener, accel, SAMPLE_US, BATCH_US)
        if (!registered) Log.w(TAG, "impact watch could not register its listener")
    }

    private fun unregister() {
        if (!registered) return
        sensors?.unregisterListener(listener)
        registered = false
    }

    companion object {
        private const val TAG = "ImpactWatch"
        private const val SAMPLE_US = 20_000
        private const val BATCH_US = 2_000_000

        /** The phone's own: the real alert, from the saved contact list. */
        fun forApp(context: Context): ImpactWatch {
            val app = context.applicationContext
            val config = JarvisConfig(app)
            return ImpactWatch(config, app.getSystemService(SensorManager::class.java), deviceAlert(app, config))
        }

        private val scope = CoroutineScope(
            SupervisorJob() + Dispatchers.Default +
                CoroutineExceptionHandler { _, t -> Log.w(TAG, "impact alert failed", t) },
        )

        private fun deviceAlert(app: Context, config: JarvisConfig) = ImpactAlert { kind ->
            val contacts = runCatching { JSONArray(config.emergencyContacts) }.getOrDefault(JSONArray())
            // Plain words only: the alert refuses a link or a number in `what`.
            val what = if (kind == "crash") {
                "A hard impact was felt while driving and the phone has not moved since."
            } else {
                "A hard fall was felt and the phone has not moved since."
            }
            scope.launch {
                val r = EmergencyAlert.execute(app, JSONObject().put("contacts", contacts).put("what", what))
                Log.i(TAG, "impact alert ($kind): ${r.ok}")
            }
        }
    }
}
