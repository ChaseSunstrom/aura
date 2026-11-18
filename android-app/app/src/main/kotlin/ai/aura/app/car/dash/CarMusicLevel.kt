package ai.aura.app.car.dash

import ai.aura.app.config.JarvisConfig
import android.content.Context
import android.media.audiofx.Visualizer
import android.util.Log
import kotlin.math.sqrt

/**
 * How loud the music is, for the car's reactor to move with (the owner,
 * 2026-09-25: "can you have a toggleable option for music animations on the arc
 * reactor on the head unit in the car? so it can move with the music").
 *
 * Android's [Visualizer] on the output mix (session 0), read only while the
 * setting ([JarvisConfig.carMusicPulse]) is on and released when it is off. The
 * level is the waveform's RMS, 0..1, smoothed so the orb breathes rather than
 * flickers.
 *
 * Does not cover a phone whose output mix refuses a visualizer (some OEM builds,
 * or no microphone grant): the level stays 0 and the reactor stays still.
 */
object CarMusicLevel {
    private const val TAG = "JarvisCarMusic"
    /** How much of the last level each new reading keeps. */
    private const val SMOOTHING = 0.6f

    data class Snapshot(val pulse: Boolean, val level: Float)

    @Volatile
    private var level = 0f
    private var visualizer: Visualizer? = null

    fun snapshot(context: Context): Snapshot {
        if (!JarvisConfig(context).carMusicPulse) {
            stop()
            return Snapshot(false, 0f)
        }
        start()
        // Two decimals: the page is told only when this changes, and noise below that is noise.
        return Snapshot(true, (level * 100).toInt() / 100f)
    }

    @Synchronized
    private fun start() {
        if (visualizer != null) return
        visualizer = runCatching {
            Visualizer(0).apply {
                captureSize = Visualizer.getCaptureSizeRange()[0]
                setDataCaptureListener(object : Visualizer.OnDataCaptureListener {
                    override fun onWaveFormDataCapture(v: Visualizer?, wave: ByteArray?, rate: Int) {
                        val w = wave ?: return
                        var sum = 0.0
                        // 8-bit unsigned samples centred on 128.
                        for (b in w) { val x = ((b.toInt() and 0xff) - 128) / 128.0; sum += x * x }
                        val rms = sqrt(sum / w.size.coerceAtLeast(1)).toFloat()
                        level = (level * SMOOTHING + (rms * 2f).coerceAtMost(1f) * (1 - SMOOTHING))
                    }

                    override fun onFftDataCapture(v: Visualizer?, fft: ByteArray?, rate: Int) = Unit
                }, Visualizer.getMaxCaptureRate() / 2, true, false)
                enabled = true
            }
        }.onFailure { Log.w(TAG, "no visualizer on the output mix; the reactor stays still", it) }.getOrNull()
    }

    @Synchronized
    fun stop() {
        visualizer?.let { runCatching { it.enabled = false; it.release() } }
        visualizer = null
        level = 0f
    }
}
