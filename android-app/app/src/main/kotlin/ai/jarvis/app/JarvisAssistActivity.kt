package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.companion.CompanionMessageHandler
import ai.jarvis.app.companion.ConversationAskHost
import ai.jarvis.app.assist.CappedScroll
import ai.jarvis.app.assist.ReplyReveal
import ai.jarvis.app.assist.ReadabilityScrim
import ai.jarvis.app.assist.V2Words
import ai.jarvis.app.assist.ToolActivityView
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.ApprovalBridge
import ai.jarvis.app.assist.Phase
import ai.jarvis.app.glass.ReactorView
import ai.jarvis.app.reactor.ReactorInputs
import ai.jarvis.app.surface.SurfaceView
import ai.jarvis.app.surface.SurfaceWatch
import ai.jarvis.app.surface.TaskDockView
import ai.jarvis.app.tasks.TaskWatch
import ai.jarvis.app.channel.DeviceChannelHost
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.PermissionBridge
import android.Manifest
import androidx.activity.ComponentActivity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.text.InputType
import android.text.TextUtils
import android.util.Log
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.ViewGroup
import android.view.ViewTreeObserver
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * Siri-like activation surface for ACTION_ASSIST / ACTION_VOICE_COMMAND — the
 * transparent, lock-screen-capable popup shown by the assist gesture, the
 * assistant role, or a wake word (via the VoiceInteractionService). It hosts
 * the cinematic Jarvis orb and runs the whole conversation itself through the
 * shared [JarvisConversation] engine. Auto-closes on inactivity, a tap, or Back.
 */
class JarvisAssistActivity : ComponentActivity(), JarvisConversation.Ui {

    /** The glass reactor (`glass.Reactor`), fed by [onReactor]. */
    private lateinit var orbView: ReactorView
    private lateinit var captionView: TextView
    private lateinit var transcriptView: TextView
    private lateinit var responseView: TextView
    private lateinit var responseScroll: CappedScroll
    /** The turn labels over the user's line and the reply (T-0728). */
    private lateinit var youLabel: TextView
    private lateinit var jarvisLabel: TextView
    /** The reply's per-word fade, its streaming Markdown and its typing dots (T-0728). */
    private lateinit var reveal: ReplyReveal
    private lateinit var typedView: EditText
    // What the house has put up, and what it is working on (M103): the
    // console's surface and task dock, said in lines under the strip.
    private lateinit var surfaceView: SurfaceView
    private lateinit var taskDockView: TaskDockView
    private var unlistenSurface: (() -> Unit)? = null
    private var unlistenDock: (() -> Unit)? = null
    private lateinit var toolActivityView: ToolActivityView
    private lateinit var config: JarvisConfig
    private var convo: JarvisConversation? = null

    /**
     * Lets Jarvis ask a question on THIS card instead of starting another
     * activity over the top of it. See [ConversationAskHost].
     */
    private var askHost: ConversationAskHost? = null

    private val main = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        JarvisUi.immersive(this)

        config = JarvisConfig(this)
        setContentView(buildUi())
        sizeAsCard()

        if (!config.isConfigured) {
            startActivity(Intent(this, SettingsActivity::class.java))
            finish(); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            // NOT requestPermissions() from here, even though this activity is
            // no longer android:noHistory and could now survive the dialog.
            //
            // A first run with no microphone grant is not a conversation that
            // was interrupted — it is a conversation that has not started, and
            // there is nothing on this card worth returning to. The home screen
            // is where the rest of first-run setup lives, it can hold the round
            // trip, and it can say what else is missing. Same shape as the
            // not-configured hand-off above.
            startActivity(
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            )
            finish(); return
        }
        beginFor(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (config.isConfigured &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) beginFor(intent)
    }

    /**
     * The car's voice button arrives here over Bluetooth hands-free. While JARVIS is
     * on the car's screen it is the car's own turn (BluetoothVoice.handToCar), with no
     * hands-free call; otherwise confirm it and wait for the car's microphone link
     * first, or the car says no phone can do speech recognition.
     */
    private fun beginFor(intent: Intent?) {
        if (ai.jarvis.app.audio.BluetoothVoice.handToCar(intent)) {
            finish()
        } else if (ai.jarvis.app.audio.BluetoothVoice.canAnswer(this, intent)) {
            ai.jarvis.app.audio.BluetoothVoice.start(this) { if (!isFinishing) begin() }
        } else if (intent?.action == Intent.ACTION_SEND) {
            // Shared from another app (T-0432): the text is the first prompt, typed for the owner.
            // CharSequence, not String: an app sharing styled text gets null from getStringExtra.
            begin(ai.jarvis.app.assist.SharePrompt.build(
                intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT)?.toString(),
                intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()))
        } else {
            begin()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) begin()
            else finish()
        }
    }

    /**
     * A small card near the bottom of the screen, not a full-screen surface.
     *
     * The theme carries `windowIsFloating`; without it this call is a no-op
     * because the platform forces an Activity window to MATCH_PARENT. Bottom
     * gravity because this is a thumb-reachable popup, and the point of a
     * dimmed floating window is that the user can still see what it
     * interrupted.
     */
    private fun sizeAsCard() {
        val screen = resources.displayMetrics.widthPixels
        val width = minOf(screen - JarvisUi.dp(this, JarvisUi.Size.SHEET), JarvisUi.dp(this, JarvisUi.Size.PANEL_MAX))
        window.setLayout(width, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.attributes = window.attributes.also {
            it.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            it.y = JarvisUi.dp(this, JarvisUi.Size.DROP)
        }
    }

    private fun buildUi(): ViewGroup {
        val pad = JarvisUi.dp(this, JarvisUi.Space.SCREEN)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, pad, pad, pad)
            // Not a panel — see ReadabilityScrim. The theme's dim is a flat
            // wash over the whole screen, which stops the app behind competing
            // but does nothing for the orb's own edge; this is the gradient
            // that gives the orb and the words a ground of their own, with no
            // border anywhere. The text keeps its shadow on top of both.
            background = ReadabilityScrim()
        }

        // The reactor alone: no brackets, no wordmark, no vignette (GLASS_HUD
        // §13 retires them with the old orb). The window's dim is the ground.
        orbView = ReactorView(this)
        root.addView(
            orbView,
            LinearLayout.LayoutParams(
                JarvisUi.dp(this, ORB_DP),
                JarvisUi.dp(this, ORB_DP)
            )
        )

        // The state readout the orb used to paint itself. A real view, so it is
        // in the accessibility tree and a test can read it. The console's Label
        // (V2Words): the core above it carries the state's colour.
        captionView = V2Words.label(TextView(this)).apply {
            text = "Listening"
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.ROW), 0, 0)
            // "A real view, so it is in the accessibility tree" — it was in the
            // tree and nothing ever read it aloud, because being in the tree
            // only earns a description on focus and nothing focuses a caption.
            // This is the one line that says whether Jarvis is listening,
            // thinking or speaking, and it changes several times a turn.
            JarvisUi.liveRegion(this)
        }
        root.addView(captionView, fullWidth())

        // What the turn is DOING, above what it is saying. Hidden until there
        // is something to show, so an ordinary question looks exactly as it did.
        toolActivityView = ToolActivityView(this).apply {
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.ROW), 0, 0)
        }
        root.addView(toolActivityView, fullWidth())
        // And what the house has PUT UP and is WORKING ON (M103): the surface's
        // panels one line each, the task dock with its bars — hidden until
        // there is something, like the tool activity above.
        surfaceView = SurfaceView(this).apply {
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.ROW), 0, 0)
            onDismiss = { panelId -> dismissPanel(panelId) }
        }
        root.addView(surfaceView, fullWidth())
        taskDockView = TaskDockView(this).apply {
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.ROW), 0, 0)
        }
        root.addView(taskDockView, fullWidth())

        // The console's Subtitles (V2Words), as on AssistOverlay: the user's line
        // dim, the reply bright, both glowing.
        transcriptView = V2Words.subtitle(JarvisUi.transcriptView(this), jarvis = false).apply {
            maxLines = 3
            ellipsize = TextUtils.TruncateAt.END
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.GAP), 0, 0)
        }
        // The whole reply, scrolled when long: four lines cut a Markdown list off (T-0727).
        responseView = V2Words.subtitle(JarvisUi.responseView(this), jarvis = true)
        // Each line under who said it, as the console's conversation turns (T-0728).
        youLabel = V2Words.who(TextView(this), jarvis = false).apply {
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.GAP), 0, 0)
        }
        jarvisLabel = V2Words.who(TextView(this), jarvis = true).apply {
            val dp = { v: Int -> JarvisUi.dp(this@JarvisAssistActivity, v) }
            setPadding(0, dp(JarvisUi.Space.GAP), 0, dp(JarvisUi.Space.SNUG))
        }
        // Follow the reply as it streams, its held-back last word included.
        reveal = ReplyReveal(responseView, jarvisLabel, drawn = {
            responseScroll.post { responseScroll.fullScroll(View.FOCUS_DOWN) }
        })
        responseScroll = CappedScroll(this, JarvisUi.dp(this, JarvisTokens.Size.SHEET_MIN)).apply {
            isVerticalScrollBarEnabled = false
            isVerticalFadingEdgeEnabled = true
            addView(responseView)
        }
        // What replaces the card under the caption: a hard shadow, so it survives
        // being drawn over whatever the dim is over. The subtitles glow instead.
        captionView.setShadowLayer(
            JarvisUi.dp(this, JarvisUi.Space.SNUG).toFloat(),
            0f,
            JarvisUi.dp(this, JarvisUi.Space.HAIRLINE).toFloat(),
            JarvisTokens.Color.OVERLAY_HEAVY,
        )
        root.addView(youLabel, fullWidth())
        root.addView(transcriptView, fullWidth())
        root.addView(jarvisLabel, fullWidth())
        root.addView(responseScroll, fullWidth())

        // Type to Jarvis (M98): the console's typed form, on the phone. One
        // line, sent on the keyboard's action; the same pipeline as speech.
        // The console's dock field (V2Words): the kit's field, as a pill. Not a bare
        // EditText: that drew the platform's pale underline box on the dark card.
        typedView = V2Words.dock(JarvisUi.field(this, V2Words.dockHint, "")).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            imeOptions = EditorInfo.IME_ACTION_SEND
            setOnEditorActionListener { view, actionId, _ ->
                if (actionId == EditorInfo.IME_ACTION_SEND) {
                    val typed = view.text.toString()
                    view.text = null
                    convo?.sendTyped(typed)
                    true
                } else {
                    false
                }
            }
            // A tap in the field must not be the tap that closes the screen.
            setOnClickListener { }
        }
        root.addView(typedView, fullWidth().apply { topMargin = JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.GAP) })

        root.setOnClickListener { finish() }
        return root
    }

    private fun fullWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

    /** [shared] is sent as a typed prompt once the conversation starts (a Share to Jarvis). */
    private fun begin(shared: String? = null) {
        // A second assist invocation is delivered to this same instance
        // (singleTask), and starting another conversation without ending the
        // first leaves an AudioRecord, a capture thread and a WebSocket running
        // with nothing holding them — two open mics for one question.
        convo?.stop()
        convo = null
        // The reactor needs no entrance call: it draws from its first frame,
        // which is the defect `orb_is_started_test.py` guarded against in the
        // old View. The pre-draw wait stays, so the conversation opens once
        // there is a frame for the haptic to land on.
        orbView.viewTreeObserver.addOnPreDrawListener(
            object : ViewTreeObserver.OnPreDrawListener {
                override fun onPreDraw(): Boolean {
                    orbView.viewTreeObserver.removeOnPreDrawListener(this)
                    haptic()
                    // Take the microphone off the wake listener before opening
                    // one of our own. Two AudioRecords on one device is a coin
                    // toss over which gets the audio, and losing it means the
                    // conversation the user just triggered hears nothing.
                    WakeWordService.pause(this@JarvisAssistActivity)
                    convo = JarvisConversation(
                        this@JarvisAssistActivity, config, this@JarvisAssistActivity,
                        inactivityMs = 8000L,
                        // This screen is the wake word's fallback surface — a
                        // full-screen intent on a locked phone — so the user is
                        // already talking here too.
                        speechAlreadyUnderway = true,
                    ).also { it.takesScreen = true; it.start(); shared?.let(it::sendTyped) }
                    askHost = ConversationAskHost(
                        context = this@JarvisAssistActivity,
                        config = config,
                        conversation = { convo },
                        surface = askSurface,
                    ).also { CompanionMessageHandler.speechHost = it }
                    return true
                }
            }
        )
    }

    // --- JarvisConversation.Ui (main thread) ------------------------------

    override fun onMode(mode: Phase, label: String) {
        ai.jarvis.app.car.voice.CarVoice.mirror(mode, label)
        captionView.text = label
        reveal.thinking(mode == Phase.THINKING)
    }

    override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

    override fun onReactor(inputs: ReactorInputs) = orbView.setInputs(inputs)

    override fun onPersona(name: String) {
        ai.jarvis.app.ui.theme.PersonaAccent.set(this, name)
    }

    override fun onTranscript(text: String) = said(text)

    override fun onResponse(text: String) {
        reveal.show(text)
    }

    private fun said(text: String) {
        transcriptView.text = text
        youLabel.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
    }

    override fun onError(message: String) {
        reveal.show(message)
        captionView.text = "Error"
        orbView.setPhase(Phase.ERROR)
    }

    override fun onTools(run: ToolRun) = toolActivityView.render(run)

    override fun onIdle() { if (!isFinishing) finish() }

    // --- haptics / lifecycle ---------------------------------------------

    private fun haptic() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                jarvisVibrator()?.vibrate(
                    VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK)
                )
                return
            } catch (e: Exception) { Log.w(TAG, "tick failed", e) }
        }
        @Suppress("DEPRECATION")
        orbView.performHapticFeedback(
            HapticFeedbackConstants.KEYBOARD_TAP,
            HapticFeedbackConstants.FLAG_IGNORE_GLOBAL_SETTING
        )
    }

    private fun jarvisVibrator(): Vibrator? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

    /** This card's view of a question, for [ConversationAskHost]. */
    private val askSurface = object : ConversationAskHost.Surface {
        override val isShowing: Boolean get() = !isFinishing && !isDestroyed

        override fun onMode(mode: Phase, label: String) {
            orbView.setPhase(mode)
            captionView.text = label
        }

        override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

        override fun onQuestion(text: String) {
            reveal.show(text)
            said("")
        }

        override fun onAnswerTranscript(text: String) = said(text)

        override fun onResting() {
            orbView.setPhase(Phase.IDLE)
            captionView.text = "Listening"
        }
    }

    override fun onStart() {
        // The surface and the dock follow the house while the screen is up (M103).
        unlistenSurface = SurfaceWatch.listen { panels -> runOnUiThread { if (::surfaceView.isInitialized) surfaceView.render(panels) } }
        unlistenDock = TaskWatch.listen { rows -> runOnUiThread { if (::taskDockView.isInitialized) taskDockView.render(rows) } }
        super.onStart()
        // Back on screen — either from the very first frame, or because a
        // prompt of ours has just been answered and handed the foreground back.
        main.removeCallbacks(giveUp)
        // Carry on from where the prompt interrupted — the same call the ask
        // host makes when a question is answered, declined or times out, and
        // for the same reason: the conversation is owed its microphone back in
        // all three cases. A no-op on the first frame, where nothing was held.
        if (held) {
            held = false
            convo?.resumeAfterQuestion()
        }
    }

    /**
     * What `android:noHistory` used to do, done deliberately.
     *
     * `noHistory` finished this activity whenever it stopped being visible and
     * could not tell why. Our own prompts — the consent screen, a question, the
     * permission trampoline — appear over it in tasks of their own, so every
     * one of them destroyed the conversation on the way up and answering it
     * returned to nothing. That is the reported *"it closes the Hey Jarvis
     * popup instead of persisting the conversation"*, and a turn needing one
     * approval could not be completed at all.
     *
     * So: still finish when the user has genuinely moved on — pressed home,
     * opened something else, turned the screen off — and stay for a prompt this
     * app itself raised.
     */
    override fun onStop() {
        unlistenSurface?.invoke(); unlistenSurface = null
        unlistenDock?.invoke(); unlistenDock = null
        super.onStop()
        if (isFinishing) return
        if (!ourOwnPromptIsUp()) {
            finish()
            return
        }
        // A prompt that is never answered must not leave this alive for ever.
        // Its own timeout plus slack: by then the bridge has settled the
        // request itself and there is nothing to come back to.
        Log.i(TAG, "staying alive behind a prompt of ours")
        main.postDelayed(giveUp, ApprovalBridge.TIMEOUT_MS + GIVE_UP_SLACK_MS)

        // PUT THE CONVERSATION DOWN while the prompt is up.
        //
        // `holdForQuestion` exists for exactly this and says why: "Give the
        // microphone up completely rather than muting it. Two owners of one
        // AudioRecord is the coin toss this whole area exists to avoid." It was
        // written for a question taking the mic, and a consent prompt covering
        // the screen is the same situation — the user is reading, not talking,
        // and `running` deliberately stays true so no inactivity timer pulls
        // the surface out from under them.
        //
        // This is not tidiness. Under `noHistory` the activity was destroyed
        // here and the microphone went back with it; now it can sit stopped for
        // over a minute, and a live AudioRecord behind a full-screen consent
        // prompt would be recording a room whose owner believes the
        // conversation is paused — while the inactivity timer and the VAD ran
        // against audio nobody meant to send.
        held = convo?.holdForQuestion() ?: false
    }

    /** True while [onStop] has parked the conversation behind a prompt. */
    private var held = false

    /**
     * True while something this app put on screen is waiting for the user.
     *
     * Deliberately asked of the bridges rather than tracked here: the prompt is
     * raised by the service that received the command, not by this activity, so
     * a flag set locally would be set by the wrong object at the wrong time.
     */
    private fun ourOwnPromptIsUp(): Boolean =
        ApprovalBridge.anyPending ||
            PermissionBridge.anyPending ||
            CompanionMessageHandler.ledger.inFlightCount > 0

    private val giveUp = Runnable {
        if (!isFinishing) {
            Log.i(TAG, "nothing came back from the prompt; closing the conversation")
            finish()
        }
    }

    /**
     * Take a panel down, through the same `jarvis/surface/remove` the console
     * sends, on the device channel. The event that follows redraws the list;
     * nothing is removed locally on faith.
     */
    private fun dismissPanel(panelId: String) {
        val channel = DeviceChannelHost.channel() ?: return
        CoroutineScope(Dispatchers.IO).launch {
            try {
                channel.request(SurfaceWatch.TYPE_REMOVE, SurfaceWatch.removeArgs(panelId))
            } catch (t: Throwable) {
                android.util.Log.w("JarvisAssist", "could not take panel $panelId down", t)
            }
        }
    }

    override fun onDestroy() {
        // The car wore this conversation's phases; it goes quiet with it.
        ai.jarvis.app.car.voice.CarVoice.mirror(ai.jarvis.app.assist.Phase.IDLE, "")
        main.removeCallbacks(giveUp)
        askHost?.let { CompanionMessageHandler.clearSpeechHost(it); it.stop() }
        askHost = null
        convo?.stop(); convo = null
        // The screen this call was made over is not for a later, unrelated conversation.
        ai.jarvis.app.assist.ScreenContext.offer(null)
        ai.jarvis.app.audio.BluetoothVoice.stop(this)
        // Give the microphone back. Unconditional and after the stop, so the
        // listener never re-opens it while this one is still closing — and a
        // resume with the setting off is a no-op, so the asymmetric case where
        // the user turned wake-word off mid-conversation is safe too.
        WakeWordService.resume(this)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JarvisAssist"

        private const val REQ_MIC = 4711

        /** Set when the popup was opened by the wake word rather than by a tap. */
        const val EXTRA_FROM_WAKE_WORD = "ai.jarvis.app.FROM_WAKE_WORD"

        /**
         * Set when a headset button opened it. Carried for the audit trail and
         * for the transcript's own "how did this start" — the popup behaves the
         * same either way, because the button is a tap by another name.
         */
        const val EXTRA_FROM_HEADSET_BUTTON = "ai.jarvis.app.FROM_HEADSET_BUTTON"

        /** Side of the orb's slot in the card. The reactor sizes itself to it. */
        private const val ORB_DP = 200

        /**
         * How long past a prompt's own deadline to wait before closing anyway.
         *
         * The bridge has settled the request by then, so there is nothing left
         * to be returned to — this only stops a crashed or swiped-away prompt
         * from leaving an invisible conversation alive.
         */
        private const val GIVE_UP_SLACK_MS = 15_000L

        fun newIntent(context: Context): Intent =
            Intent(context, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
