package ai.aura.app.car.dash.tiles

import ai.aura.app.ui.theme.ActiveTheme
import kotlinx.coroutines.launch
import ai.aura.app.assist.JarvisConversation
import ai.aura.app.assist.Phase
import ai.aura.app.assist.ToolRun
import ai.aura.app.car.dash.DashTile
import ai.aura.app.config.JarvisConfig
import ai.aura.app.glass.ReactorView
import ai.aura.app.reactor.ReactorInputs
import ai.aura.app.ui.MarkdownText
import ai.aura.app.ui.theme.JarvisTokens
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.TextView
import dev.headway.dash.PaneKind

/**
 * The Jarvis tab (ADR-0047): the reactor centre stage, what was last said, and one
 * touch to talk. The same conversation engine as the phone, so a turn begun in the
 * car is a turn in the same session the console shows.
 *
 * Does not open the microphone on arriving -- a tab switch is not consent to listen
 * (the template screen's rule, android-app/tools/car_app_test.py). The reactor swells
 * with the voice (the owner's Build Brief, §1 car), the car mic's level on a steering-wheel
 * turn and the conversation's on a touch one, through the same gain and smoothing as the phone.
 */
class JarvisTile(context: Context) : DashTile, JarvisConversation.Ui {

    private val appContext = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val config = JarvisConfig(appContext)

    override val kind: String = PaneKind.JARVIS

    private var reactor: ReactorView? = null
    private var caption: TextView? = null
    private var heard: TextView? = null
    private var answer: TextView? = null
    private var convo: JarvisConversation? = null

    override fun createView(context: Context): View {
        val panel = CarStyle.panel(context).apply { gravity = Gravity.CENTER_HORIZONTAL }
        val size = CarStyle.dp(context, JarvisTokens.Size.PANEL_MAX.toFloat() / 2f)
        val orb = ReactorView(context).apply {
            layoutParams = LinearLayout.LayoutParams(size, size)
            contentDescription = "${ActiveTheme.name}. Tap to talk"
            isClickable = true
            setOnClickListener { toggle() }
            setPhase(Phase.IDLE, linkUp = config.isConfigured)
        }
        val state = CarStyle.label(context, JarvisTokens.Type.HINT, CarStyle.DIM).apply {
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        }
        val said = CarStyle.label(context, JarvisTokens.Type.BODY, CarStyle.DIM).apply {
            gravity = Gravity.CENTER
            maxLines = 2
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        }
        val reply = CarStyle.label(context, JarvisTokens.Type.RESPONSE, CarStyle.TEXT).apply {
            gravity = Gravity.CENTER
            maxLines = 4
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        }
        panel.addView(orb)
        panel.addView(state)
        panel.addView(said)
        panel.addView(reply)
        reactor = orb
        caption = state
        heard = said
        answer = reply
        idleText()
        return panel
    }

    private fun idleText() {
        caption?.text = if (config.isConfigured) IDLE_HINT else "Pair ${ActiveTheme.name} on the phone first"
    }

    /**
     * A touch starts a turn; a touch during one ends it. With a car session up it is
     * the car's microphone, exactly as the steering-wheel button (CarShell's voice
     * request); the phone's only when no car is listening.
     */
    fun toggle() {
        ai.aura.app.car.dash.CarShell.onVoiceRequested?.let { carMic ->
            carMic()
            return
        }
        val running = convo
        if (running != null) {
            running.stop()
            convo = null
            return
        }
        if (!config.isConfigured) return idleText()
        if (appContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            caption?.text = "${ActiveTheme.name} needs the microphone; allow it on the phone"
            return
        }
        heard?.text = ""
        answer?.text = ""
        convo = JarvisConversation(appContext, config, this).also { it.start() }
    }

    private var following: kotlinx.coroutines.Job? = null

    /**
     * Follows the car's own voice turns (CarVoice), so the reactor listens, thinks and
     * speaks with the steering-wheel button as it does with a touch.
     */
    override fun start() {
        if (following != null) return
        following = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.Main).launchFollowing()
    }

    private fun kotlinx.coroutines.CoroutineScope.launchFollowing(): kotlinx.coroutines.Job = launch {
        launch { ai.aura.app.car.voice.CarVoice.level.collect { reactor?.setAmplitude(it) } }
        ai.aura.app.car.voice.CarVoice.view.collect { view ->
            if (view == ai.aura.app.car.voice.CarVoiceView()) return@collect
            reactor?.setPhase(view.phase, linkUp = config.isConfigured)
            caption?.text = view.caption.ifEmpty { if (view.phase == Phase.IDLE) IDLE_HINT else "" }
            heard?.text = view.heard
            answer?.text = MarkdownText.spanned(view.answer)
        }
    }

    override fun stop() {
        following?.cancel()
        following = null
        convo?.stop()
        convo = null
    }

    override fun describe(): String = "jarvis: ${caption?.text ?: ""}"

    // --- the conversation's UI, marshalled to the car's main thread -------------

    private fun ui(block: () -> Unit) {
        main.post(block)
    }

    override fun onMode(mode: Phase, label: String) = ui { caption?.text = label.lowercase().replaceFirstChar { it.uppercase() } }

    override fun onAmplitude(level: Float) = ui { reactor?.setAmplitude(level) }

    override fun onReactor(inputs: ReactorInputs) = ui { reactor?.setInputs(inputs) }

    override fun onTranscript(text: String) = ui { heard?.text = text }

    override fun onResponse(text: String) = ui { answer?.text = MarkdownText.spanned(text) }

    override fun onError(message: String) = ui { caption?.text = message }

    override fun onIdle() = ui {
        convo = null
        reactor?.setPhase(Phase.IDLE, linkUp = config.isConfigured)
        idleText()
    }

    override fun onTools(run: ToolRun) = Unit

    private companion object {
        const val IDLE_HINT = "Tap the reactor or press the voice button to talk"
    }
}
