package ai.aura.app

import ai.aura.app.glass.Haptics
import ai.aura.app.ui.ApprovalBridge
import ai.aura.app.ui.ApprovalCard
import ai.aura.app.ui.ApprovalProof
import ai.aura.app.ui.ConsentGate
import ai.aura.app.ui.ConsentText
import ai.aura.app.ui.DoorActivity
import ai.aura.app.ui.JarvisUi
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.ui.v2.ActionButton
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Hairline
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.HoldButton
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.IslandCard
import ai.aura.app.ui.v2.RuleChip
import ai.aura.app.ui.v2.Stage
import ai.aura.app.ui.v2.Tone
import android.graphics.Color
import android.os.Bundle
import android.os.CountDownTimer
import android.view.View
import android.view.WindowManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/**
 * The consent screen, in the v2 language: one clear ask (Phone.dc.html "Approval").
 *
 * Everything dangerous the device can do, and every Hermes approval the phone hears, funnels
 * through this activity. It is native, raised from the notification by [ApprovalBridge], and
 * needs nothing from the console, so it works with the console down. It is written to be
 * boring and unhelpful to an attacker:
 *
 *  * It shows the action id and the VERBATIM parameters. No summarising, no truncation: long
 *    payloads scroll, and RAW flips to the exact text that was handed to [ApprovalBridge].
 *  * The server's words (the reason, and the T-0606 card's What/Why/Impact read out of it by
 *    [ApprovalCard], on Hermes prompts only: a device prompt's reason is never read as a
 *    card) are labelled as the server's, apart from the action id. A card hides the
 *    reason and the parameters behind "Show me first"; a prompt without one shows them at once.
 *  * Anything that cannot be undone is held to confirm ([HoldButton], Motion.Dur.HOLD): a card
 *    whose Undoable is no, and every prompt without a card. A reversible card is one tap.
 *  * A device Tier-3 prompt offers no rule: it asks every single time. A Hermes prompt offers
 *    the rule chips its choices allow ([ApprovalCard.rules]).
 *  * Doing nothing denies: the countdown auto-denies, Back denies, being destroyed for any
 *    reason denies. Only the allow control allows.
 *  * A Hermes prompt raised with a way to ask also offers "Ask why" (T-1054): a deny, then one
 *    line to the model asking it to say why it needs this ([ApprovalBridge.deliverAskWhy]).
 *    It is live exactly when Deny is, and a device Tier-3 prompt never shows it.
 *  * **The keyguard is part of the gate.** The prompt shows over a locked screen so the phone
 *    lights up, but while the keyguard is up the parameters stay hidden and allow is inert;
 *    the activity asks the system to dismiss the keyguard and only opens up once the real user
 *    is through it. DENY stays live throughout: refusing is safe from anywhere.
 *  * Allow is inert for [ConsentGate.ARM_MS] after the prompt becomes readable, and the root
 *    view refuses touches that pass through another window, so an overlay or a stray tap
 *    cannot allow anything.
 *  * FLAG_SECURE keeps the parameters out of screenshots and off the screen recorder.
 *
 * The window flags, the keyguard read and the unlock request are [DoorActivity]'s; the gate
 * (what locked hides, when allow arms) is this screen's own, through [ConsentGate].
 *
 * Does not cover a touch an accessibility service injects: that path is the system's, as it
 * was for the View screen this replaced.
 */
class ApprovalActivity : DoorActivity() {

    private var requestId: String? = null
    private var countdown: CountDownTimer? = null
    private var host: View? = null

    /** The offered choices, or null for a yes/no prompt. */
    private var choices: List<String>? = null

    /** Whether this Hermes prompt can ask the model why (T-1054); false for a device prompt. */
    private var askWhyOffered = false
    private var rawParams: String = ""
    private var prettyParams: String = ""
    private var timeoutMs: Long = ApprovalBridge.TIMEOUT_MS

    // What the screen draws from. refreshGate() is the one writer of locked and armed.
    private var answered by mutableStateOf(false)

    /** Keyguard state, re-read on every resume rather than cached at create. */
    private var locked by mutableStateOf(false)

    /** True once the prompt has been readable (i.e. unlocked) for ARM_MS. */
    private var armed by mutableStateOf(false)
    private var showingRaw by mutableStateOf(false)
    private var countdownText by mutableStateOf("")

    /** The sensitive ask's challenge, when the allow must be signed behind the fingerprint (T-0945). */
    private var challenge: String? = null

    /** True while the fingerprint sheet is up, so a second tap does not open a second one. */
    private var signing = false

    /** A line above the buttons: the fingerprint was not accepted. */
    private var notice by mutableStateOf("")

    private val armRunnable = Runnable {
        armed = true
        refreshGate()
    }

    private class Prompt(
        val actionId: String,
        val description: String,
        val reason: String,
        val commandId: String,
        val tierLabel: String,
        val card: ApprovalCard?,
        val rules: List<ApprovalCard.Rule>,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val id = intent?.getStringExtra(ApprovalBridge.EXTRA_REQUEST_ID)
        if (id.isNullOrEmpty()) {
            // No request to answer: nothing to approve, so nothing happens.
            finish()
            return
        }
        requestId = id
        // Reached the screen. `startActivity` returning tells the bridge nothing: a background
        // start the platform refuses does not throw, and a full-screen intent degrades to a
        // heads-up notification whenever the screen is on and unlocked. This is the only
        // positive evidence that the person is reading the prompt.
        ApprovalBridge.raised(id)

        val reason = intent?.getStringExtra(ApprovalBridge.EXTRA_REASON).orEmpty()
        timeoutMs = ApprovalBridge.clampTimeout(
            intent?.getLongExtra(ApprovalBridge.EXTRA_TIMEOUT_MS, ApprovalBridge.TIMEOUT_MS)
                ?: ApprovalBridge.TIMEOUT_MS
        )
        rawParams = intent?.getStringExtra(ApprovalBridge.EXTRA_PARAMS).orEmpty()
        prettyParams = ConsentText.fields(rawParams)
        choices = intent?.getStringArrayExtra(ApprovalBridge.EXTRA_CHOICES)
            ?.filter { it in ApprovalBridge.ALL_CHOICES }
            ?.takeIf { it.isNotEmpty() }
        challenge = intent?.getStringExtra(ApprovalBridge.EXTRA_CHALLENGE)?.takeIf { it.isNotEmpty() }
        askWhyOffered = choices != null && intent?.getBooleanExtra(ApprovalBridge.EXTRA_ASK_WHY, false) == true
        notice = intent?.getStringExtra(ApprovalBridge.EXTRA_NOTE).orEmpty()
        // Only a Hermes prompt carries a card the server wrote (its approval hook appends it
        // last). A device Tier-3 reason is the requester's own text, which the model may have
        // written: card lines in it would retitle the ask, fold the parameters away and make
        // Allow one tap.
        val card = if (choices != null) ApprovalCard.parse(reason) else null
        val prompt = Prompt(
            actionId = intent?.getStringExtra(ApprovalBridge.EXTRA_ACTION_ID).orEmpty(),
            description = intent?.getStringExtra(ApprovalBridge.EXTRA_DESCRIPTION).orEmpty(),
            reason = reason,
            commandId = intent?.getStringExtra(ApprovalBridge.EXTRA_COMMAND_ID).orEmpty(),
            tierLabel = intent?.getStringExtra(ApprovalBridge.EXTRA_TIER_LABEL)
                ?.takeIf { it.isNotEmpty() } ?: "Tier 3, asks every time",
            card = card,
            rules = choices?.let { ApprovalCard.rules(card, it) }.orEmpty(),
        )

        val view = ComposeView(this).apply {
            // At the root, so no control on the screen can be pressed through an overlay:
            // ViewGroup drops an obscured event before any child sees it.
            filterTouchesWhenObscured = true
            setContent { Stage { Screen(prompt) } }
        }
        host = view
        setContentView(view)
        JarvisUi.fitSystemBars(view)
        focus()
        refreshGate()
        startCountdown()
    }

    /**
     * What is behind the island dims, and blurs where the platform blurs across windows (API 31+,
     * not under battery saver). The prompt never depends on either: the island is opaque.
     */
    private fun focus() {
        window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        window.setDimAmount(Color.alpha(JarvisTokens.Material.Floating.DIM) / OPAQUE)
        if (windowManager.isCrossWindowBlurEnabled) {
            window.addFlags(WindowManager.LayoutParams.FLAG_BLUR_BEHIND)
            window.attributes = window.attributes.apply {
                blurBehindRadius = JarvisUi.dp(this@ApprovalActivity, JarvisTokens.Material.Floating.BLUR_DP)
            }
        }
    }

    // --- the keyguard half of the gate --------------------------------------

    /**
     * Recompute the lock state and restart the arming delay when the prompt becomes readable.
     * Called at create, on resume, when the arm timer fires and when the keyguard reports
     * itself dismissed; the screen derives every control's state from [locked] and [armed].
     */
    override fun refreshGate() {
        val view = host ?: return
        if (answered) return
        val wasLocked = locked
        locked = isLocked()
        if (locked) {
            // Arming restarts once the prompt is actually readable.
            armed = false
            view.removeCallbacks(armRunnable)
            requestUnlock()
        } else if (wasLocked || !armed) {
            view.removeCallbacks(armRunnable)
            view.postDelayed(armRunnable, ConsentGate.ARM_MS)
        }
    }

    // --- UI -----------------------------------------------------------------

    @Composable
    private fun Screen(p: Prompt) {
        val open = ConsentGate.approveEnabled(locked, armed, answered)
        val note = ConsentGate.blockedReason(locked, armed)
        var rule by remember { mutableStateOf(p.rules.firstOrNull()) }
        var details by remember { mutableStateOf(p.card == null) }
        val space = JarvisTokens.Space
        // The island at the top (T-0695), under where the overlay's pill sits while a turn waits
        // on this answer, so the two read as one Jarvis; it was a sheet over the whole screen.
        val pill = JarvisTokens.Material.Floating.PILL_HEIGHT_DP + space.GAP + space.SNUG
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                .padding(start = space.GAP.dp, end = space.GAP.dp, top = pill.dp, bottom = space.SECTION.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            IslandCard(Modifier.fillMaxWidth()) {
                // The tier, in the held colour: nothing has been refused yet, so not red.
                Label(p.tierLabel, tone = Tone.WARN)
                val headline = p.card?.what ?: p.description.ifEmpty { p.actionId.ifEmpty { "(no action id)" } }
                Headline(headline, Modifier.semantics { heading() })
                if (p.card == null && p.description.isNotEmpty() && headline != p.description) Body(p.description, dim = true)

                Label("Action")
                // AN ACTION ID IS NOT A SENTENCE. TalkBack reads `media.play_on_speaker` as one
                // run-on word; the dots and underscores become spaces for a listener, because
                // mishearing WHICH thing is the failure that matters on this screen.
                val spoken = getString(
                    R.string.a11y_approval,
                    p.actionId.ifEmpty { "no action id" }.replace(Regex("[._]"), " "),
                )
                Body(p.actionId.ifEmpty { "(no action id)" }, Modifier.semantics { contentDescription = spoken })

                p.card?.let { card ->
                    Label("Why")
                    Body(card.why)
                    Label("Impact")
                    Body(card.impact)
                }

                if (details) {
                    Label("Why the server says so")
                    val said = if (p.card != null) ApprovalCard.withoutCard(p.reason) else p.reason
                    // Remote text, rendered as text and nothing else. No reason is suspicious.
                    if (said.isEmpty()) Body("(no reason given — treat that as suspicious)", tone = Tone.DANGER)
                    else Body(said)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Label("Exactly what will run", Modifier.weight(1f))
                        // Through the gate: RAW cannot reveal the parameters while locked.
                        ActionButton("Raw", { showingRaw = !showingRaw })
                    }
                    Body(ConsentGate.paramsText(locked, if (showingRaw) rawParams else prettyParams), dim = locked)
                }

                if (p.rules.isNotEmpty()) {
                    Hairline()
                    for (r in p.rules) RuleChip(r.label, selected = rule == r, onClick = { rule = r }, enabled = open)
                }

                // A Hermes prompt whose choices allow no rule has no allow at all.
                if (choices == null || p.rules.isNotEmpty()) {
                    val onAllow = { allow(rule) }
                    if (p.card?.undoable == true) {
                        ActionButton("Allow", onAllow, Modifier.fillMaxWidth(), tone = Tone.WARN, enabled = open, filled = true)
                    } else {
                        HoldButton("Allow", onAllow, Modifier.fillMaxWidth(), enabled = open)
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(space.STEP.dp)) {
                    ActionButton("Deny", { answer(false) }, Modifier.weight(1f), tone = Tone.DANGER, enabled = ConsentGate.denyEnabled(answered))
                    if (askWhyOffered) {
                        ActionButton("Ask why", { askWhy() }, Modifier.weight(1f), enabled = ConsentGate.askWhyEnabled(askWhyOffered, answered))
                    } else if (!details) {
                        ActionButton("Show me first", { details = true }, Modifier.weight(1f))
                    }
                }
                if (askWhyOffered && !details) ActionButton("Show me first", { details = true }, Modifier.fillMaxWidth())

                // A prompt that auto-denies has a limited time, and this line is the only thing
                // saying so: it is a live region so a listener hears it too.
                Label(countdownText, Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                // Says WHY allow is dead (locked phone, arming delay); a disabled control with an
                // unspoken reason is a screen that appears to have stopped working.
                if (note != null) Body(note, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, dim = true)
                if (notice.isNotEmpty()) Body(notice, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, tone = Tone.DANGER)

                val footer = if (choices != null) {
                    "Ask every time runs it this one time. A rule is remembered by Hermes, not by " +
                        "this phone. If you did not just ask for this, deny it."
                } else {
                    "Tier 3 is asked every time; this answer is not remembered. " +
                        "If you did not just ask for this, deny it."
                }
                Body(if (p.commandId.isEmpty()) footer else "$footer\nCommand ${p.commandId}", dim = true)
            }
        }
    }

    // --- countdown ----------------------------------------------------------

    private fun startCountdown() {
        countdown?.cancel()
        // Said from the first frame, not from the first tick: a prompt that auto-denies must
        // never be on screen without saying so.
        countdownText = "Auto-deny in ${timeoutMs / 1000L}s"
        countdown = object : CountDownTimer(timeoutMs, 1000L) {
            override fun onTick(millisUntilFinished: Long) {
                // Answered somewhere else (the console, the car): nothing is left to decide here.
                val id = requestId
                if (!answered && id != null && !ApprovalBridge.isPending(id)) {
                    answered = true
                    host?.removeCallbacks(armRunnable)
                    countdown?.cancel()
                    countdown = null
                    finish()
                    return
                }
                countdownText = "Auto-deny in ${(millisUntilFinished / 1000L).coerceAtLeast(0L)}s"
            }

            override fun onFinish() {
                countdownText = "Auto-denied"
                answer(approved = false, timedOut = true)
            }
        }.also { it.start() }
    }

    // --- answering ----------------------------------------------------------

    private fun allow(rule: ApprovalCard.Rule?) {
        val offered = choices
        if (offered == null || rule == null) answer(true) else answerChoice(rule.choice)
    }

    private fun answer(approved: Boolean, timedOut: Boolean = false) {
        if (answered) return
        // The last line of defence: an allow that reaches here without the gate open is
        // dropped rather than trusted.
        if (approved && !ConsentGate.approveEnabled(isLocked(), armed, false)) return
        answered = true
        // The one decision on this screen that cannot be taken back is the one that is felt;
        // a buzz for the automatic denial would wake somebody to tell them nothing happened.
        if (approved && !timedOut) host?.let { Haptics.perform(it, Haptics.Event.APPROVE) }
        host?.removeCallbacks(armRunnable)
        countdown?.cancel()
        countdown = null
        requestId?.let { id ->
            if (timedOut) ApprovalBridge.deliverTimeout(id)
            else ApprovalBridge.deliver(id, approved)
        }
        finish()
    }

    /** Deny, then ask the model why it wants this. Gated exactly like Deny: live whenever it is. */
    private fun askWhy() {
        if (!ConsentGate.askWhyEnabled(askWhyOffered, answered)) return
        answered = true
        host?.removeCallbacks(armRunnable)
        countdown?.cancel()
        countdown = null
        requestId?.let { ApprovalBridge.deliverAskWhy(it) }
        finish()
    }

    /** A rule on a Hermes request. Gated exactly like [answer]'s yes. */
    private fun answerChoice(choice: String) {
        if (answered || signing) return
        if (!ConsentGate.approveEnabled(isLocked(), armed, false)) return
        if (choices?.contains(choice) != true) return
        val challenge = challenge
        if (challenge != null) {
            // A sensitive ask (T-0945): the fingerprint comes first, and what it signs is what the
            // adapter checks. A refused or cancelled prompt leaves the ask open for another try
            // or a deny; the countdown keeps running, so it still lapses into a deny.
            signing = true
            ApprovalProof.signWithNote(this, challenge) { signature, why ->
                signing = false
                if (isDestroyed || answered) return@signWithNote
                if (signature == null) {
                    notice = why
                    return@signWithNote
                }
                notice = ""
                finishChoice(choice, signature)
            }
            return
        }
        finishChoice(choice, null)
    }

    private fun finishChoice(choice: String, signature: String?) {
        answered = true
        host?.let { Haptics.perform(it, Haptics.Event.APPROVE) }
        host?.removeCallbacks(armRunnable)
        countdown?.cancel()
        countdown = null
        requestId?.let { ApprovalBridge.deliverChoice(it, choice, signature) }
        finish()
    }

    private companion object {
        const val OPAQUE = 255f
    }

    /** Back must deny: the one thing Back must never do here is leave the request unanswered. */
    override fun onBack() = answer(false)

    override fun onDestroy() {
        // Swiped away, killed, config-changed out of existence: all of it is a denial. This is
        // the last line of the fail-closed guarantee.
        countdown?.cancel()
        countdown = null
        host?.removeCallbacks(armRunnable)
        if (!answered) {
            answered = true
            requestId?.let { ApprovalBridge.deliver(it, false) }
        }
        super.onDestroy()
    }
}
