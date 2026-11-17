package ai.jarvis.app.audio

import ai.jarvis.app.car.link.CarPresenceReceiver
import android.content.Context
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * Watches what is plugged in or paired and reports the current [AudioRoute].
 *
 * This is the Android half of the earpiece feature; every decision it feeds is
 * made by the pure logic in [AudioRoute] and [CaptureProfile]. Its own job is
 * only discovery and applying the result, which is exactly the part that cannot
 * be unit-tested, so it is kept as small as the platform allows.
 *
 * Requires no new permissions: `AudioManager.getDevices` reports device *types*
 * without `BLUETOOTH_CONNECT`, which is only needed for names and addresses.
 * Jarvis never needs to know which headset it is, only what kind.
 */
class HeadsetMonitor(
    context: Context,
    private val onRouteChanged: (AudioRoute) -> Unit
) {
    private val appContext = context.applicationContext
    private val audio = appContext.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())

    /** The user's opt-in. Set by the caller from settings; re-emits on change. */
    @Volatile
    var headsetModeEnabled: Boolean = false
        set(value) {
            if (field == value) return
            field = value
            emit()
        }

    @Volatile
    var route: AudioRoute = AudioRoute()
        private set

    private var registered = false

    private val callback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = emit()
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = emit()
    }

    fun start() {
        if (registered || audio == null) return
        audio.registerAudioDeviceCallback(callback, main)
        registered = true
        emit()
    }

    fun stop() {
        if (!registered || audio == null) return
        try {
            audio.unregisterAudioDeviceCallback(callback)
        } catch (t: Throwable) {
            Log.d(TAG, "unregister failed", t)
        }
        registered = false
    }

    /** Recompute and notify if anything actually changed. */
    fun emit() {
        val next = detect()
        if (next == route) return
        route = next
        main.post { onRouteChanged(next) }
    }

    private fun detect(): AudioRoute {
        val am = audio ?: return AudioRoute(headsetModeEnabled = headsetModeEnabled)
        val outputs = try {
            am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        } catch (t: Throwable) {
            Log.w(TAG, "getDevices failed", t); return AudioRoute(headsetModeEnabled = headsetModeEnabled)
        }
        val inputTypes = try {
            am.getDevices(AudioManager.GET_DEVICES_INPUTS).map { it.type }.toSet()
        } catch (t: Throwable) {
            Log.w(TAG, "input getDevices failed", t); emptySet()
        }

        val kind = kindOf(outputs.map { it.type }.toSet(), CarPresenceReceiver.carHere())

        // A headset that claims a mic but exposes no input endpoint would give
        // us silence. Trust the input list over the device class.
        val scoAvailable = when (kind) {
            HeadsetKind.BLUETOOTH_SCO ->
                inputTypes.contains(AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
            HeadsetKind.BLE_HEADSET ->
                inputTypes.contains(TYPE_BLE_HEADSET)
            else -> true
        }

        return AudioRoute(
            kind = kind,
            headsetModeEnabled = headsetModeEnabled,
            scoAvailable = scoAvailable
        )
    }

    /**
     * Pin capture and playback to the headset for the duration of a turn.
     *
     * On API 31+ this is one call. Below that the only lever is the legacy SCO
     * pair, which is why [clearCommunicationRoute] exists and why every caller
     * must use them symmetrically: leaving SCO up on an API 29 device holds the
     * headset in call mode, which silences music system-wide until something
     * tears it down.
     *
     * @return true if a route was applied and [clearCommunicationRoute] is owed.
     */
    fun applyCommunicationRoute(profile: CaptureProfile): Boolean {
        val am = audio ?: return false
        if (!profile.requestCommunicationDevice) return false
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                // Never the car's hands-free, even beside a wired headset: that is a call to the car.
                val carHere = CarPresenceReceiver.carHere()
                val target = am.availableCommunicationDevices.firstOrNull {
                    it.isHeadsetLike() && !(carHere && it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
                }
                if (target == null) {
                    Log.w(TAG, "no communication device matched the detected headset")
                    false
                } else {
                    am.setCommunicationDevice(target).also {
                        if (!it) Log.w(TAG, "setCommunicationDevice refused")
                    }
                }
            } else {
                startScoLegacy(am)
                true
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not apply communication route", t)
            false
        }
    }

    @Suppress("DEPRECATION")
    private fun startScoLegacy(am: AudioManager) {
        am.mode = AudioManager.MODE_IN_COMMUNICATION
        am.startBluetoothSco()
        am.isBluetoothScoOn = true
    }

    @Suppress("DEPRECATION")
    private fun stopScoLegacy(am: AudioManager) {
        am.isBluetoothScoOn = false
        am.stopBluetoothSco()
        am.mode = AudioManager.MODE_NORMAL
    }

    /** Undo [applyCommunicationRoute]. Safe to call when nothing was applied. */
    fun clearCommunicationRoute() {
        val am = audio ?: return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                am.clearCommunicationDevice()
            } else {
                stopScoLegacy(am)
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not clear communication route", t)
        }
    }

    private fun AudioDeviceInfo.isHeadsetLike(): Boolean = when (type) {
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
        AudioDeviceInfo.TYPE_WIRED_HEADSET,
        AudioDeviceInfo.TYPE_USB_HEADSET,
        TYPE_BLE_HEADSET -> true
        else -> false
    }

    companion object {
        private const val TAG = "JarvisAudioRoute"

        /**
         * The headset the outputs say, ordered by how strongly each implies "the user is
         * wearing this and expects Jarvis in their ear", so a phone with both a paired watch
         * and an earpiece resolves to the earpiece. While the car is here its hands-free is the
         * SCO output, and nobody wears it (T-0744): counted as a headset, every conversation
         * turn pointed communication audio at the car, a call. Does not tell an earpiece's SCO
         * from the car's: in the car, an earpiece on hands-free is not a headset either.
         */
        internal fun kindOf(outputs: Set<Int>, carHere: Boolean): HeadsetKind = when {
            TYPE_BLE_HEADSET in outputs -> HeadsetKind.BLE_HEADSET
            !carHere && AudioDeviceInfo.TYPE_BLUETOOTH_SCO in outputs -> HeadsetKind.BLUETOOTH_SCO
            AudioDeviceInfo.TYPE_USB_HEADSET in outputs -> HeadsetKind.USB_HEADSET
            AudioDeviceInfo.TYPE_WIRED_HEADSET in outputs -> HeadsetKind.WIRED_HEADSET
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES in outputs -> HeadsetKind.WIRED_HEADPHONES
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP in outputs -> HeadsetKind.BLUETOOTH_A2DP
            else -> HeadsetKind.NONE
        }

        /**
         * `AudioDeviceInfo.TYPE_BLE_HEADSET`, inlined because it is API 31 and
         * this module compiles against minSdk 29. The constant is a stable part
         * of the platform ABI; on an older device no device will ever report it,
         * so the comparison is simply never true.
         */
        private const val TYPE_BLE_HEADSET = 26
    }
}
