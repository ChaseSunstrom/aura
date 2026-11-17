package ai.jarvis.app

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.assist.MicStreamer
import ai.jarvis.app.assist.ModelStore
import ai.jarvis.app.assist.OnDeviceWakeWord
import ai.jarvis.app.assist.WakeEnrol
import ai.jarvis.app.assist.WakeVerifier
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.ui.v2.ActionButton
import ai.jarvis.app.ui.v2.Body
import ai.jarvis.app.ui.v2.Hairline
import ai.jarvis.app.ui.v2.Headline
import ai.jarvis.app.ui.v2.HoldButton
import ai.jarvis.app.ui.v2.Label
import ai.jarvis.app.ui.v2.Projection
import ai.jarvis.app.ui.v2.Stage
import ai.jarvis.app.ui.v2.Tone
import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import java.io.File
import java.util.Collections
import java.util.Locale
import kotlin.concurrent.thread

/**
 * "Teach it the name" (T-0714): the owner says "Hey Jarvis" eight ways, then reads a
 * line aloud, and the phone fits a [WakeVerifier] on those takes and saves it; the wake
 * service reads it on its next open. The owner, 2026-11-08: "I have to ask it in a
 * specific way, I can't use different intonations".
 *
 * Each take shows the stock head's own best score, so a way of saying it that the head
 * did not hear at all is said, not silently learnt (the lift cannot raise it). Does not
 * cover the house's listener: the verifier is used only when this phone hears the wake
 * word itself.
 */
class WakeEnrolActivity : ComponentActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var config: JarvisConfig
    private var prompt by mutableStateOf("")
    private var status by mutableStateOf("")
    /** One line per take heard: its style, its peak, and what the takes do about it. */
    private var takeRows by mutableStateOf<List<String>>(emptyList())
    private var recordText by mutableStateOf(RECORD)
    private var recordEnabled by mutableStateOf(true)
    private var restartEnabled by mutableStateOf(false)
    private var forgetEnabled by mutableStateOf(false)

    private var detector: OnDeviceWakeWord? = null
    private var mic: MicStreamer? = null
    /** Written on the capture thread while [collecting], read on the main thread after. */
    private val steps: MutableList<Pair<Float, FloatArray>> = Collections.synchronizedList(mutableListOf())
    @Volatile private var collecting = false
    private var pausedWake = false
    private var busy = false

    private val takes = mutableListOf<WakeEnrol.Take>()
    private val stored: File get() = File(filesDir, WakeVerifier.FILE)

    /** The last step: reading aloud, every window of it a "not the name". */
    private val reading: Boolean get() = takes.size >= WakeEnrol.STYLES.size

    private val autoStop = Runnable { stop() }
    private val warmedUp = Runnable { if (mic != null) begin() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = JarvisConfig(this)
        setContent { Page() }
        render()
    }

    /**
     * Home or another app takes the screen: the mic and the service's listening go back.
     * A fit in flight keeps them until its file is saved.
     */
    override fun onStop() {
        if (!busy) closeMic()
        super.onStop()
    }

    override fun onDestroy() {
        // Mid-fit, the fit thread gives the mic back after the save, so the service's reopen
        // reads the new file and not the old one.
        closeMic(giveBack = !busy)
        super.onDestroy()
    }

    @Composable
    private fun Page() {
        Stage {
            Column(
                Modifier
                    .fillMaxSize()
                    .safeDrawingPadding()
                    .verticalScroll(rememberScrollState())
                    .padding(JarvisTokens.Space.SCREEN.dp),
                verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
            ) {
                Headline("Teach it the name")
                Body(
                    "Say “Hey ${ActiveTheme.name}” the ways you really do, so it wakes for all of them and still not for the television.",
                    dim = true,
                )
                if (!config.wakeWordOnDevice) {
                    Body(
                        "Used when this phone hears the wake word itself: turn on “Detect “Hey ${ActiveTheme.name}” on this phone” in Settings.",
                        tone = Tone.WARN,
                    )
                }
                Label("Say this")
                Projection(Modifier.fillMaxWidth()) { Body(prompt) }
                ActionButton(recordText, onClick = { if (collecting) stop() else start() }, enabled = recordEnabled, filled = true)
                if (status.isNotEmpty()) Body(status)

                if (takeRows.isNotEmpty()) {
                    Label("Takes")
                    Projection(Modifier.fillMaxWidth()) {
                        takeRows.forEachIndexed { index, row ->
                            if (index > 0) Hairline()
                            Body(row, dim = true)
                        }
                    }
                }

                ActionButton("Start over", onClick = ::restart, enabled = restartEnabled)
                // Deletes the saved takes, so held rather than tapped.
                HoldButton("Forget my takes", onConfirm = ::forget, tone = Tone.DANGER, enabled = forgetEnabled)
            }
        }
    }

    private fun render() {
        prompt = if (reading) {
            "Last, read this aloud in your ordinary voice:\n“$READING”"
        } else {
            "Take ${takes.size + 1} of ${WakeEnrol.STYLES.size}: “Hey ${ActiveTheme.name}”, ${WakeEnrol.STYLES[takes.size]}."
        }
        recordText = if (collecting) STOP else RECORD
        recordEnabled = !busy
        restartEnabled = !busy && !collecting && takes.isNotEmpty()
        forgetEnabled = !busy && !collecting && stored.isFile
        if (status.isEmpty() && stored.isFile) {
            status = "Your takes are saved and the wake word listens for them. Teach it again to replace them."
        }
    }

    private fun start() {
        if (busy || collecting) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (mic != null) return begin()
        busy = true
        status = "Opening the wake word…"
        render()
        thread(name = "wake-enrol-open") {
            val opened = OnDeviceWakeWord.open(ModelStore.directory(this))
            main.post {
                busy = false
                // Left while the models loaded: not a reason to open the mic behind the user's back.
                if (!lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
                    opened?.close()
                    giveMicBack()
                    return@post
                }
                if (opened == null) {
                    closeMic()
                    status = "The wake word's models are not on this phone yet: Settings → Download models, then come back."
                    return@post render()
                }
                openMic(opened)
            }
        }
        // The service would hear the takes and wake on them: it is given the mic back on close.
        WakeWordService.pause(this)
        pausedWake = true
    }

    private fun openMic(opened: OnDeviceWakeWord) {
        opened.onWindow = { score, window -> if (collecting) steps.add(score to window) }
        detector = opened
        val streamer = MicStreamer(
            onPcm = { buffer, length -> detector?.score(buffer, length) },
            onLevel = { },
            onUnavailable = { reason -> main.post { closeMic(); status = reason; render() } },
        )
        mic = streamer
        try {
            streamer.start()
        } catch (t: Throwable) {
            closeMic()
            status = "Could not open the microphone."
            return render()
        }
        // The head needs two seconds of audio before its first score (OnDeviceWakeWord.score);
        // the mic stays open between takes, so only the first take waits for it.
        status = "Getting ready…"
        main.postDelayed(warmedUp, WARM_UP_MS)
    }

    private fun begin() {
        if (collecting) return
        main.removeCallbacks(warmedUp)
        steps.clear()
        collecting = true
        status = if (reading) "Listening — read the line, then tap Stop." else "Listening — say it, then tap Stop."
        main.postDelayed(autoStop, if (reading) READING_MS else TAKE_MS)
        render()
    }

    private fun stop() {
        if (!collecting) return
        main.removeCallbacks(autoStop)
        collecting = false
        val heard = synchronized(steps) { steps.toList() }
        if (reading) return fit(heard.map { it.second })
        val take = WakeEnrol.take(heard)
        if (take == null) {
            val best = heard.maxOfOrNull { it.first } ?: 0f
            status = "It did not hear the name in that one (best ${two(best)}). Say it again, a little closer."
        } else {
            takes += take
            addRow(takes.size, WakeEnrol.STYLES[takes.size - 1], take.peak)
            status = if (reading) "Heard all ${takes.size}. Last, the reading." else "Heard. Next one."
        }
        render()
    }

    private fun addRow(n: Int, style: String, peak: Float) {
        // The stock head's own word: over 0.5 it already woke for this one; over the level's gate
        // the takes teach it; under that only a more eager level lifts it.
        val verdict = when {
            peak >= 0.5f -> "already wakes"
            peak >= config.wakeSensitivity.verifierGate -> "the takes will teach it"
            else -> "faint: lifted only at a more eager sensitivity"
        }
        takeRows = takeRows + "$n. $style — heard ${two(peak)}, $verdict"
    }

    private fun fit(room: List<FloatArray>) {
        if (room.size < MIN_ROOM_WINDOWS) {
            status = "That was too short to learn the room from: read the whole line, then tap Stop."
            return render()
        }
        busy = true
        // The service gets the mic back only once the file is saved, so its reopen reads the new takes.
        closeMic(giveBack = false)
        status = "Learning your ways of saying it…"
        render()
        val kept = takes.toList()
        thread(name = "wake-enrol-fit") {
            val outcome = runCatching { WakeEnrol.fit(kept, room).also { it.verifier.save(stored) } }
            main.post {
                busy = false
                outcome.onSuccess {
                    status = "Saved. With each take left out, the rest still recognised ${it.recognised} of ${it.of}. " +
                        "The wake word now listens for your ways of saying it."
                    takes.clear()
                    takeRows = emptyList()
                }.onFailure {
                    status = "Could not save the takes: ${it.message}"
                }
                giveMicBack()
                render()
            }
        }
    }

    private fun restart() {
        takes.clear()
        takeRows = emptyList()
        status = ""
        render()
    }

    private fun forget() {
        stored.delete()
        // A pause and a resume reopen the service's detector, which drops the verifier it holds.
        if (!pausedWake) WakeWordService.pause(this)
        pausedWake = true
        closeMic()
        status = "Forgotten: the wake word listens the stock way again."
        render()
    }

    private fun closeMic(giveBack: Boolean = true) {
        main.removeCallbacks(autoStop)
        main.removeCallbacks(warmedUp)
        collecting = false
        mic?.stop()
        mic = null
        detector?.close()
        detector = null
        if (giveBack) giveMicBack()
    }

    /**
     * A resume reopens the service's detector, which reads the saved takes. Left while a
     * fit is still running, the service reopens first and reads the new file on its next
     * open (after the next conversation).
     */
    private fun giveMicBack() {
        if (pausedWake) WakeWordService.resume(applicationContext)
        pausedWake = false
    }

    private fun two(score: Float) = String.format(Locale.ROOT, "%.2f", score)

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) start()
        else status = "${ActiveTheme.name} needs the microphone to hear your takes."
    }

    private companion object {
        const val RECORD = "Record"
        const val STOP = "Stop"
        const val REQ_MIC = 41
        const val WARM_UP_MS = 2_100L
        const val TAKE_MS = 4_000L
        const val READING_MS = 14_000L
        /** About eight seconds of reading: under it the room is too thin to tell from the name. */
        const val MIN_ROOM_WINDOWS = 100
        const val READING = "The quick brown fox jumps over the lazy dog, then rests in the warm afternoon sun. " +
            "Tomorrow looks like rain, so the washing can wait until the weekend."
    }
}
