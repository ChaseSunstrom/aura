package ai.jarvis.app.audio

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothHeadset
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * The car's own voice button, over Bluetooth hands-free (the owner, 2026-09-25:
 * "I get a no compatible phones for speech recognition on my car when I try to
 * use the voice button").
 *
 * The button sends the phone AT+BVRA; Android's hands-free service starts
 * `ACTION_VOICE_COMMAND` (JarvisAssistActivity) and then waits a few seconds for
 * the app to confirm with [BluetoothHeadset.startVoiceRecognition]. Nothing did,
 * so the car timed out and said no phone could do it. Confirming also opens the
 * SCO link: the car's microphone in, the car's speakers out. [start] confirms,
 * waits for that link (up to [SCO_WAIT_MS]) and points communication audio at it,
 * so the conversation's capture and reply (AudioRoute's communication profile)
 * are the car's; [stop] gives it back.
 *
 * Does not cover a head unit reached only through Android Auto: its voice key
 * arrives on the AA input channel instead (CarLinkService `onVoiceKey`). While JARVIS is on
 * the car's screen the button is the car's turn ([handToCar]), never hands-free.
 */
object BluetoothVoice {
    private const val TAG = "JarvisBtVoice"
    private const val SCO_WAIT_MS = 3000L

    private var headset: BluetoothHeadset? = null
    private var device: BluetoothDevice? = null
    private var receiver: BroadcastReceiver? = null

    /** How the last [start] ended, in words, for the log. */
    @Volatile
    private var outcome = "not tried"

    /**
     * The car's voice button while JARVIS is on the car's screen: the car's own turn over
     * Android Auto (its microphone in, its guidance voice out) instead of hands-free voice
     * recognition, which puts the car in a voice call that sounds wrong and does not let go
     * (T-0744). True when it was handed over. Does not cover a car reached only over plain
     * Bluetooth: [start] still answers that one, as the car asks.
     */
    fun handToCar(intent: Intent?): Boolean {
        if (intent?.action != Intent.ACTION_VOICE_COMMAND) return false
        val turn = ai.jarvis.app.car.dash.CarShell.onVoiceRequested ?: return false
        Log.i(TAG, "voice button with the car up: the car's own turn, no hands-free")
        turn()
        return true
    }

    /** True when a request to talk came from a hands-free device and this can answer it. */
    fun canAnswer(context: Context, intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_VOICE_COMMAND &&
            context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED &&
            context.getSystemService(BluetoothManager::class.java)?.adapter?.isEnabled == true

    /**
     * Confirm the car's request and open its microphone link; [ready] runs once
     * the link is up, or after [SCO_WAIT_MS] without it (the phone's own mic
     * then), and exactly once either way.
     */
    @SuppressLint("MissingPermission")
    fun start(context: Context, ready: () -> Unit) {
        val app = context.applicationContext
        val main = Handler(Looper.getMainLooper())
        var done = false
        val once = { if (!done) { done = true; ready() } }
        main.postDelayed(once, SCO_WAIT_MS)
        outcome = "Android's hands-free service did not answer in $SCO_WAIT_MS ms"
        val adapter: BluetoothAdapter = app.getSystemService(BluetoothManager::class.java)?.adapter
            ?: run { outcome = "this phone has no Bluetooth adapter"; return once() }
        adapter.getProfileProxy(app, object : BluetoothProfile.ServiceListener {
            override fun onServiceConnected(profile: Int, proxy: BluetoothProfile) {
                val hs = proxy as? BluetoothHeadset ?: run { outcome = "no hands-free profile"; return once() }
                headset = hs
                val car = runCatching { hs.connectedDevices.firstOrNull() }.getOrNull()
                    ?: run { outcome = "no car is connected to this phone for calls"; return once() }
                device = car
                receiver = object : BroadcastReceiver() {
                    override fun onReceive(c: Context, i: Intent) {
                        if (i.getIntExtra(BluetoothHeadset.EXTRA_STATE, -1) == BluetoothHeadset.STATE_AUDIO_CONNECTED) {
                            outcome = "the car's audio link connected, but Android would not route capture to it"
                            route(app)
                            main.post(once)
                        }
                    }
                }.also { app.registerReceiver(it, IntentFilter(BluetoothHeadset.ACTION_AUDIO_STATE_CHANGED)) }
                val ok = runCatching { hs.startVoiceRecognition(car) }.getOrDefault(false)
                Log.i(TAG, "voice recognition for ${car.address.takeLast(5)}: ${if (ok) "started" else "refused"}")
                outcome = if (ok) "voice recognition started, but the car's audio link did not connect in $SCO_WAIT_MS ms"
                else "the car refused hands-free voice recognition"
                if (!ok) once()
            }

            override fun onServiceDisconnected(profile: Int) {
                headset = null
            }
        }, BluetoothProfile.HEADSET)
    }

    /** Communication audio to the car's SCO link, so capture and replies are the car's. */
    private fun route(context: Context) {
        val am = context.getSystemService(AudioManager::class.java) ?: return
        val sco = am.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO } ?: return
        runCatching { am.setCommunicationDevice(sco) }
    }

    /** End the car's voice session: the link closes and communication audio comes back to the phone. */
    @SuppressLint("MissingPermission")
    fun stop(context: Context) {
        val app = context.applicationContext
        receiver?.let { runCatching { app.unregisterReceiver(it) } }
        receiver = null
        val hs = headset
        val car = device
        if (hs != null && car != null) runCatching { hs.stopVoiceRecognition(car) }
        runCatching { app.getSystemService(AudioManager::class.java)?.clearCommunicationDevice() }
        hs?.let { runCatching { app.getSystemService(BluetoothManager::class.java)?.adapter?.closeProfileProxy(BluetoothProfile.HEADSET, it) } }
        headset = null
        device = null
    }
}
