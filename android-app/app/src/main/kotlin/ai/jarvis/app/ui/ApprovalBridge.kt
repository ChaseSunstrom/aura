package ai.jarvis.app.ui

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ApprovalActivity
import ai.jarvis.app.JarvisApp
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.config.JarvisConfig
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * The Tier-3 consent gate. This is the only supported way for any module to ask
 * a human "may I actually do this?", and the answer is a plain Boolean:
 *
 * ```
 * val ok = ApprovalBridge.request(
 *     context,
 *     actionId = "sms.send",
 *     params = """{"to":"+447700900123","body":"On my way"}""",
 *     reason = "You asked me to tell Sam you are running late.",
 * )
 * if (!ok) return DeviceResult.denied(commandId)
 * ```
 *
 * Contract — every one of these is load-bearing, do not soften any of them:
 *
 *  * **Fail closed.** Every failure path denies: the activity cannot start,
 *    notifications are blocked, the process is killed, the deferred is dropped,
 *    the countdown expires. There is no path that approves without a human
 *    tapping APPROVE on [ApprovalActivity] — and that button is inert until the
 *    keyguard is gone, so it means the phone's owner, not whoever picked it up.
 *  * **No memory.** Nothing here writes to the policy store, and no overload
 *    can ever answer "always". A Tier-3 answer applies to exactly one request;
 *    the next one asks again.
 *  * **Verbatim parameters.** [params] is shown to the user as given. Pass the
 *    exact payload you are about to execute — not a summary, not the model's
 *    paraphrase. If what you execute can differ from what you passed here, the
 *    prompt is a lie and the gate is worthless.
 *  * **Untrusted input stops here.** Text from a web page, a notification or
 *    the screen may end up inside [params] or `reason` as *data*. It is
 *    rendered as text, never parsed for instructions, and it can never cause
 *    this function to approve on its own.
 *  * **Suspending, cancellable.** Cancelling the calling coroutine abandons the
 *    request (the prompt's own countdown then denies it). Cancellation
 *    propagates rather than being swallowed as a denial.
 *
 * Callers must be prepared to wait up to [TIMEOUT_MS] plus a small delivery
 * grace before this returns.
 */
object ApprovalBridge {

    const val EXTRA_REQUEST_ID = "ai.jarvis.app.approval.REQUEST_ID"
    const val EXTRA_ACTION_ID = "ai.jarvis.app.approval.ACTION_ID"
    const val EXTRA_PARAMS = "ai.jarvis.app.approval.PARAMS"
    const val EXTRA_REASON = "ai.jarvis.app.approval.REASON"

    /** Optional: the action's own description from the device-local table. */
    const val EXTRA_DESCRIPTION = "ai.jarvis.app.approval.DESCRIPTION"

    /** Optional: display label for the tier, e.g. "Tier 3, asks every time". */
    const val EXTRA_TIER_LABEL = "ai.jarvis.app.approval.TIER_LABEL"

    /** Optional: the server's command id, shown as provenance. */
    const val EXTRA_COMMAND_ID = "ai.jarvis.app.approval.COMMAND_ID"

    /** How long the prompt's countdown runs, already clamped by [clampTimeout]. */
    const val EXTRA_TIMEOUT_MS = "ai.jarvis.app.approval.TIMEOUT_MS"

    /**
     * Optional: the answers a Hermes `approval.request` offers, as a string
     * array in the order the buttons are shown — a subset of
     * `once`, `session`, `always`, `deny`. Absent for the phone's own Tier-3
     * prompts and for jarvis-core's held tools, which know only yes and no.
     */
    const val EXTRA_CHOICES = "ai.jarvis.app.approval.CHOICES"

    /** True when the prompt offers Ask why (T-1054): a Hermes request raised with a way to ask. */
    const val EXTRA_ASK_WHY = "ai.jarvis.app.approval.ASK_WHY"

    /** The challenge a phone-confirmed ask is signed over (T-0945); present means "fingerprint first". */
    const val EXTRA_CHALLENGE = "ai.jarvis.app.approval.CHALLENGE"

    /** A line the screen shows above the buttons, e.g. that the fingerprint was not accepted. */
    const val EXTRA_NOTE = "ai.jarvis.app.approval.NOTE"

    const val CHOICE_ONCE = "once"
    const val CHOICE_SESSION = "session"
    const val CHOICE_ALWAYS = "always"
    const val CHOICE_DENY = "deny"

    /** The prompt auto-denies after this long, and no caller may extend it. */
    const val TIMEOUT_MS = 60_000L

    /** Floor for a caller-supplied timeout; below this nobody could read it. */
    const val MIN_TIMEOUT_MS = 10_000L

    /** Slack so the activity's own countdown always fires first. */
    private const val DELIVERY_GRACE_MS = 5_000L

    /**
     * How long to wait for [ApprovalActivity] to say it reached the screen
     * before concluding it never will.
     *
     * Only ever used to decide whether to EXPLAIN something — the request goes
     * on waiting either way — so it is short. A prompt that is going to raise
     * itself has done so long before this.
     */
    private const val RAISE_GRACE_MS = 3_000L

    /** Fixed id: the hint replaces itself rather than stacking. */
    private const val OVERLAY_HINT_NOTIFICATION_ID = 91_301

    private const val TAG = "JarvisApproval"

    /** The device events and the frame of the console route (jarvis-core `consents.py`). */
    const val CONSENT_PENDING = "consent_pending"
    const val CONSENT_RESOLVED = "consent_resolved"
    const val TYPE_CONSENT_ANSWER = "consent_answer"

    /** What actually happened, so callers can log a denial apart from a lapse. */
    enum class Outcome {
        APPROVED,
        DENIED,
        TIMED_OUT,
        /** The prompt could not be put in front of a human at all. */
        UNDELIVERABLE;

        val approved: Boolean get() = this == APPROVED
    }

    private val pending = ConcurrentHashMap<String, CompletableDeferred<Outcome>>()

    /**
     * Server-held requests (M98): a Tier-3 tool jarvis-core holds for a yes —
     * `lock_control`, `remove_entities`, `change_setting` — raised on this
     * phone's consent screen and answered with `jarvis/approve` on the assist
     * socket. Keyed by the server's request id; the value sends the answer.
     * Until now the phone showed such a request as a strip row with nothing
     * to tap (the Android audit, 27 Aug 2026).
     */
    private val serverSenders = ConcurrentHashMap<String, (String, Boolean) -> Unit>()

    /**
     * Hermes-held requests: an `approval.request` from the HUD socket, raised
     * on the same consent screen and answered with `approval.respond` and one
     * of the choices the request offered. Keyed by Hermes's request id; the
     * value sends the choice. The offered set is kept beside it so an answer
     * the server never offered cannot be sent — it is turned into a deny.
     */
    private val hermesSenders = ConcurrentHashMap<String, (String) -> Unit>()

    /**
     * The house's consent relay (jarvis-core `consents.py`), set by the device link while it
     * runs: each prompt [requestOutcome] raises is reported as `consent_pending` so the console
     * can answer it while this phone is locked, and `consent_resolved` takes it off again.
     */
    @Volatile
    var consoleRoute: ((event: String, data: JSONObject) -> Unit)? = null

    /** Prompts reported to the console; only these take [deliverRemote]. */
    private val offeredToConsole = ConcurrentHashMap.newKeySet<String>()

    /**
     * The car's way to answer, while a car is linked (set by `CarLinkService`, cleared
     * with its session): a small card on the car screen and the question said aloud,
     * answered yes or no on the car's microphone. Asked beside the phone's own prompt,
     * never instead of it; the first answer settles the request.
     *
     * Why a second door at all: the phone's prompt is inert until the keyguard is gone,
     * and a phone locked in a cradle cannot be unlocked by a driver -- every text Jarvis
     * was asked to send from the car timed out as a no (the owner, 2026-09-26: "the
     * approvals ... won't show up on the car HUD, and won't let me approve while the
     * phone is locked"). The car is the owner's own paired head unit, and its spoken
     * answer passes the house's speaker gate like every car turn, so a passenger's
     * "yes" is refused in enforce mode. Returns null when the car could not ask.
     */
    @Volatile
    var carRoute: (suspend (title: String, detail: String, spoken: String) -> Boolean?)? = null
    private val carScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val carAsks = ConcurrentHashMap<String, Job>()

    private fun stopCarAsk(id: String) {
        carAsks.remove(id)?.cancel()
    }
    private val hermesChoices = ConcurrentHashMap<String, List<String>>()

    /**
     * Per Hermes request: what [deliverAskWhy] does after the deny (T-1054). Only a Hermes path
     * registers one; the countdown, Back and a plain Deny never call it, and it is removed with
     * the answer, so one request asks at most once.
     */
    private val hermesWhy = ConcurrentHashMap<String, () -> Unit>()

    /**
     * The challenge of a phone-confirmed Hermes ask (T-0945), and how to send its signed allow.
     * An ask with a challenge is allowed only through [deliverChoice] with a signature; the
     * bare sender in [hermesSenders] is then only ever used to deny.
     */
    private val hermesChallenges = ConcurrentHashMap<String, String>()
    private val hermesSigned = ConcurrentHashMap<String, (String, String) -> Unit>()

    /** The context the last prompt was raised with, so an answer can take its notification down. */
    @Volatile
    private var appContext: Context? = null

    /** Completed by [raised] when the prompt's activity actually runs. */
    private val onScreen = ConcurrentHashMap<String, CompletableDeferred<Unit>>()

    /** So a run of gated commands is not a run of identical complaints. */
    private val explainedOverlayGrant = AtomicBoolean(false)

    private val notificationIds = ConcurrentHashMap<String, Int>()
    private val codes = AtomicInteger(1000)

    /**
     * Show the full-screen consent prompt and suspend until the human answers.
     *
     * @param actionId local action id, e.g. `sms.send` — shown verbatim.
     * @param params the exact parameters about to be executed, ideally JSON.
     * @param reason human-readable why, shown verbatim.
     * @return true only if the user tapped APPROVE. Anything else is false.
     */
    suspend fun request(
        context: Context,
        actionId: String,
        params: String,
        reason: String,
    ): Boolean = requestOutcome(context, actionId, params, reason).approved

    /**
     * As [request], but reports how the request ended. Same guarantees; the
     * extra detail exists so the audit log can say "timed out" instead of
     * flattening everything into "denied".
     *
     * @param description the action's own description from the device-local
     *   action table. Trusted, unlike [reason], and labelled as such on screen.
     * @param tierLabel display-only tier text; defaults to the Tier-3 wording.
     * @param commandId the server's command id, shown as provenance.
     * @param timeoutMs clamped to [MIN_TIMEOUT_MS]..[TIMEOUT_MS]. A caller may
     *   ask for a shorter prompt, never a longer one — how long a consent
     *   prompt lives is not something a remote server gets to decide.
     */
    suspend fun requestOutcome(
        context: Context,
        actionId: String,
        params: String,
        reason: String,
        description: String = "",
        tierLabel: String = DEFAULT_TIER_LABEL,
        commandId: String? = null,
        timeoutMs: Long = TIMEOUT_MS,
    ): Outcome {
        val app = context.applicationContext
        val id = UUID.randomUUID().toString()
        val effectiveTimeout = clampTimeout(timeoutMs)
        val answer = CompletableDeferred<Outcome>()
        pending[id] = answer
        // Registered BEFORE anything can start the activity. `startActivity` is
        // asynchronous but not slow, and a prompt that reported itself before
        // this map had an entry for it would look exactly like one that never
        // appeared.
        onScreen[id] = CompletableDeferred()
        // The surfaces need to know before anything is on screen: the orb is a
        // TYPE_APPLICATION_OVERLAY window drawn ABOVE every Activity, so a
        // prompt raised under it lands on buttons the user cannot press.
        PromptPresence.raised()
        // Before the prompt is raised, so a console answer can never race an unreported id.
        val toConsole = consoleRoute?.takeIf { JarvisConfig(app).remoteApprovals }
        if (toConsole != null) {
            offeredToConsole.add(id)
            runCatching {
                toConsole(CONSENT_PENDING, JSONObject().put("id", id).put("action", actionId)
                    .put("params", params).put("reason", reason).put("description", description)
                    .put("timeout_s", effectiveTimeout / 1000L))
            }.onFailure { Log.w(TAG, "could not offer $actionId to the console", it) }
        }
        return try {
            val shown = raisePrompt(
                app = app,
                id = id,
                actionId = actionId,
                params = params,
                reason = reason,
                description = description,
                tierLabel = tierLabel,
                commandId = commandId,
                timeoutMs = effectiveTimeout,
            )
            if (!shown) {
                Log.w(TAG, "no way to show the prompt for $actionId; denying")
                Outcome.UNDELIVERABLE
            } else {
                // Did it actually appear, or is it sitting in the shade waiting
                // to be tapped? The answer changes nothing about the outcome —
                // a notification IS a route, and the user may well tap it —
                // but it is the one moment the app can tell the user that this
                // is a settings problem with a one-tap fix, rather than leaving
                // them to conclude Jarvis is just like this.
                if (withTimeoutOrNull(RAISE_GRACE_MS) { onScreen[id]?.await() } == null) {
                    Log.i(TAG, "the prompt for $actionId did not raise itself")
                    explainWhyNothingAppeared(app)
                }
                withTimeoutOrNull(effectiveTimeout + DELIVERY_GRACE_MS) { answer.await() }
                    ?: Outcome.TIMED_OUT
            }
        } catch (ce: CancellationException) {
            // The caller went away. Propagate — do not report a decision that
            // nobody made.
            throw ce
        } catch (t: Throwable) {
            Log.e(TAG, "approval request for $actionId failed; denying", t)
            Outcome.DENIED
        } finally {
            // In the `finally`, so every path pairs — including the throw that
            // `error` and cancellation take. A leaked `raised` leaves the orb
            // hidden and the conversation held for the life of the process.
            PromptPresence.settled()
            if (offeredToConsole.remove(id)) {
                runCatching { toConsole?.invoke(CONSENT_RESOLVED, JSONObject().put("id", id)) }
            }
            pending.remove(id)
            onScreen.remove(id)
            clearNotification(app, id)
        }
    }

    /**
     * Source-compatible entry point for the automation module's
     * `UiApprovalGateway`, which was written against a wider signature.
     *
     * Two things are deliberately not honoured:
     *
     *  * `rememberable` is ignored. This prompt has no "always allow" control
     *    and this function never returns `approved_always`, so a Tier-2 caller
     *    that wanted a remembered answer simply gets asked again. Erring toward
     *    more prompting is the only safe direction to err in.
     *  * `timeoutMs` is clamped to at most [TIMEOUT_MS].
     *
     * @param tier accepted as [Any] so this module never imports the automation
     *   module's types; only its string form is displayed.
     * @return `"approved"`, `"denied"` or `"timeout"`.
     */
    @Suppress("LongParameterList", "UNUSED_PARAMETER")
    suspend fun request(
        context: Context,
        actionId: String,
        description: String,
        params: Any?,
        tier: Any?,
        reason: String,
        commandId: String?,
        rememberable: Boolean,
        timeoutMs: Long,
    ): String {
        val outcome = requestOutcome(
            context = context,
            actionId = actionId,
            // toString() on a JSONObject is its exact serialisation, which is
            // what "verbatim" means for a structured payload.
            params = params?.toString().orEmpty(),
            reason = reason,
            description = description,
            tierLabel = tierLabelOf(tier),
            commandId = commandId,
            timeoutMs = timeoutMs,
        )
        return when (outcome) {
            Outcome.APPROVED -> "approved"
            Outcome.TIMED_OUT -> "timeout"
            Outcome.DENIED, Outcome.UNDELIVERABLE -> "denied"
        }
    }

    /**
     * Called by [ApprovalActivity] with the human's answer. Unknown or
     * already-settled ids are ignored, so a stale activity cannot approve a
     * request that has since timed out.
     */
    fun deliver(requestId: String, approved: Boolean) {
        stopCarAsk(requestId)
        if (hermesSenders.containsKey(requestId)) {
            // Yes on a Hermes request is "once": the narrowest allow. Wider
            // answers arrive through [deliverChoice], never through a Boolean.
            deliverChoice(requestId, if (approved) CHOICE_ONCE else CHOICE_DENY)
            return
        }
        val send = serverSenders.remove(requestId)
        if (send != null) {
            // A server-held request: the answer goes back on the wire, once.
            try {
                send(requestId, approved)
            } catch (t: Throwable) {
                Log.w(TAG, "could not send the answer for $requestId", t)
            }
            return
        }
        settle(requestId, if (approved) Outcome.APPROVED else Outcome.DENIED)
    }

    /**
     * Called by [ApprovalActivity] with one of the offered choices for a
     * Hermes request. A choice the request did not offer — or any choice for
     * an id nobody is waiting on — is a deny or a no-op, never a wider allow.
     */
    fun deliverChoice(requestId: String, choice: String, signature: String? = null) {
        stopCarAsk(requestId)
        val send = hermesSenders.remove(requestId)
        hermesWhy.remove(requestId)
        val offered = hermesChoices.remove(requestId).orEmpty()
        val challenge = hermesChallenges.remove(requestId)
        val signedSend = hermesSigned.remove(requestId)
        clearHermesNotification(requestId)
        if (send == null) {
            // Not a Hermes request. A choice is not a yes for any other kind
            // of prompt, so whatever it was, it is a no.
            settle(requestId, Outcome.DENIED)
            return
        }
        var answer = if (choice in offered) choice else CHOICE_DENY
        if (answer != choice) Log.w(TAG, "$requestId answered with an unoffered choice; denying")
        try {
            if (challenge != null && answer != CHOICE_DENY) {
                // A phone-confirmed ask: an allow with no signature (or nowhere to send one) is a
                // deny. The adapter would refuse it anyway; failing closed here keeps the ask from
                // being left open by an answer that can never be accepted.
                if (signature.isNullOrEmpty() || signedSend == null) {
                    Log.w(TAG, "$requestId needs the phone's signature to allow; denying")
                    answer = CHOICE_DENY
                } else {
                    signedSend(answer, signature)
                    return
                }
            }
            send(answer)
        } catch (t: Throwable) {
            Log.w(TAG, "could not send the choice for $requestId", t)
        }
    }

    /**
     * "Ask why" (T-1054): deny the Hermes request, then run the question its raiser gave. Hermes's
     * answer carries no text, so the deny goes first and the question is the next thing said. An
     * id nobody is waiting on does nothing (answered once), and a request raised with no way to
     * ask is only denied.
     */
    fun deliverAskWhy(requestId: String) {
        val why = hermesWhy.remove(requestId)
        val open = hermesSenders.containsKey(requestId)
        deliverChoice(requestId, CHOICE_DENY)
        if (!open) return
        try {
            why?.invoke()
        } catch (t: Throwable) {
            Log.w(TAG, "could not ask why for $requestId", t)
        }
    }

    /** True while [requestId] is open and was raised with a way to ask why. */
    fun canAskWhy(requestId: String): Boolean = hermesWhy.containsKey(requestId)

    /**
     * A Hermes ask that was settled somewhere else (`approval.resolved`: the console answered, the
     * adapter's two minutes ran out): forget it and take its screen and notification down. The
     * screen on show sees [isPending] go false on its next tick and closes itself. Nothing is sent:
     * the ask is already over.
     */
    fun dismissResolved(requestId: String) {
        stopCarAsk(requestId)
        hermesWhy.remove(requestId)
        hermesChallenges.remove(requestId)
        hermesSigned.remove(requestId)
        hermesChoices.remove(requestId)
        hermesSenders.remove(requestId)
        clearHermesNotification(requestId)
    }

    private fun clearHermesNotification(requestId: String) {
        appContext?.let { clearNotification(it, requestId) }
    }

    /**
     * A yes or no from the console, relayed by the house as `consent_answer`. Settles only a
     * prompt this phone raised, reported and is still waiting on: never a server-held or Hermes
     * request (those are answered on the console already), never an id the phone did not offer.
     * The prompt on screen sees [isPending] go false and closes itself.
     */
    fun deliverRemote(requestId: String, approved: Boolean): Boolean {
        if (!offeredToConsole.contains(requestId) || !pending.containsKey(requestId)) {
            Log.w(TAG, "a console answer for $requestId matched no open prompt; ignored")
            return false
        }
        Log.i(TAG, "$requestId ${if (approved) "approved" else "denied"} from the console")
        settle(requestId, if (approved) Outcome.APPROVED else Outcome.DENIED)
        return true
    }

    /** Called by [ApprovalActivity] when its countdown runs out. */
    fun deliverTimeout(requestId: String) {
        stopCarAsk(requestId)
        // A Hermes request fails closed ON THE WIRE: the tool waits for an
        // answer, and a prompt nobody read must not leave it waiting.
        if (hermesSenders.containsKey(requestId)) {
            deliverChoice(requestId, CHOICE_DENY)
            return
        }
        // A server-held request lapses on the server's own clock; nothing to send.
        if (serverSenders.remove(requestId) != null) return
        settle(requestId, Outcome.TIMED_OUT)
    }

    /** True while a prompt for this id is still waiting for an answer. */
    fun isPending(requestId: String): Boolean =
        pending.containsKey(requestId) || serverSenders.containsKey(requestId) ||
            hermesSenders.containsKey(requestId)

    /**
     * Raise a Hermes `approval.request` on the consent screen — the same
     * keyguard-aware, tapjacking-proof door every other approval uses — with
     * a button per offered choice, and answer it with [send] when the person
     * decides. [params] is the command or the arguments verbatim; [reason] is
     * the model's own text and is labelled as remote on screen. Fails closed
     * like the rest: an undeliverable prompt is a deny sent at once.
     */
    fun raiseHermesRequest(
        app: Context,
        requestId: String,
        tool: String,
        params: String,
        reason: String,
        choices: List<String>,
        waitsSeconds: Long,
        challenge: String? = null,
        signedSend: ((String, String) -> Unit)? = null,
        note: String = "",
        why: (() -> Unit)? = null,
        send: (String) -> Unit,
    ): Boolean {
        if (requestId.isBlank()) return false
        val offered = choices.filter { it in ALL_CHOICES }.ifEmpty { listOf(CHOICE_ONCE, CHOICE_DENY) }
        hermesSenders[requestId] = send
        hermesChoices[requestId] = offered
        why?.let { hermesWhy[requestId] = it }
        if (!challenge.isNullOrEmpty()) {
            hermesChallenges[requestId] = challenge
            signedSend?.let { hermesSigned[requestId] = it }
        }
        val raised = raisePrompt(
            app = app.applicationContext,
            id = requestId,
            actionId = tool.ifBlank { "tool" },
            params = params,
            reason = reason,
            description = "Hermes asks before running this tool",
            tierLabel = HERMES_TIER_LABEL,
            commandId = requestId,
            timeoutMs = clampTimeout(waitsSeconds * 1000L),
            choices = offered,
            challenge = challenge?.takeIf { it.isNotEmpty() },
            note = note,
            askWhy = why != null,
        )
        if (!raised) {
            Log.w(TAG, "no way to show the Hermes prompt for $tool; denying")
            deliverChoice(requestId, CHOICE_DENY)
        }
        return raised
    }

    /** The answers the protocol knows. */
    val ALL_CHOICES: List<String> = listOf(CHOICE_ONCE, CHOICE_SESSION, CHOICE_ALWAYS, CHOICE_DENY)

    /**
     * Raise a request jarvis-core is holding (M98) on the consent screen —
     * keyguard-aware, tapjacking-proof, the same screen the phone's own
     * Tier-3 actions use — and answer it with [send] when the person decides.
     * `summary` is the server's own sentence for the card (M66); `waitsSeconds`
     * the clock it is on, clamped like any other prompt.
     */
    fun raiseServerRequest(
        app: Context,
        requestId: String,
        tool: String,
        summary: String,
        waitsSeconds: Long,
        send: (String, Boolean) -> Unit,
    ): Boolean {
        if (requestId.isBlank()) return false
        serverSenders[requestId] = send
        val raised = raisePrompt(
            app = app.applicationContext,
            id = requestId,
            actionId = tool,
            params = "",
            reason = "${ActiveTheme.name} asks before doing this",
            description = summary.ifBlank { "${ActiveTheme.name} wants to run $tool" },
            tierLabel = DEFAULT_TIER_LABEL,
            commandId = null,
            timeoutMs = clampTimeout(waitsSeconds * 1000L),
        )
        if (!raised) serverSenders.remove(requestId)
        return raised
    }

    /**
     * True while ANY consent prompt is waiting for an answer.
     *
     * Rule 1 of [ai.jarvis.app.audio.MediaButtonGate] needs this: a headset
     * button press must be swallowed while a Tier-3 prompt is up, and the
     * button has no request id to ask about. Reading the map rather than
     * keeping a counter, so the two cannot disagree.
     */
    val anyPending: Boolean
        get() = pending.isNotEmpty() || serverSenders.isNotEmpty() || hermesSenders.isNotEmpty()

    /** Callers may shorten the prompt but never lengthen it. */
    fun clampTimeout(requested: Long): Long = when {
        requested <= 0L -> TIMEOUT_MS
        else -> requested.coerceIn(MIN_TIMEOUT_MS, TIMEOUT_MS)
    }

    private fun settle(requestId: String, outcome: Outcome) {
        stopCarAsk(requestId)
        val deferred = pending.remove(requestId)
        if (deferred == null) {
            Log.w(TAG, "answer for unknown/expired request $requestId ignored")
            return
        }
        deferred.complete(outcome)
    }

    /** Display-only. Accepts the automation module's ActionTier by name. */
    private fun tierLabelOf(tier: Any?): String = when (tier?.toString()?.uppercase()) {
        "AUTO", "1", "TIER1" -> "Tier 1, runs on its own"
        "NOTIFY", "2", "TIER2" -> "Tier 2, asks and can remember"
        else -> DEFAULT_TIER_LABEL
    }

    // --- raising the prompt -------------------------------------------------

    /**
     * The same request on the car, when one is linked; true when it was put there. A
     * yes or no from the car is delivered as the phone's own buttons deliver it, so a
     * Hermes request answers "once" or "deny", never a wider allow.
     */
    private fun askTheCar(id: String, actionId: String, params: String, reason: String, description: String): Boolean {
        val route = carRoute ?: return false
        val what = description.ifBlank { actionId.replace('_', ' ').replace('.', ' ') }
        val title = "Allow: $what?"
        val detail = params.ifBlank { reason }.take(CAR_DETAIL_CHARS)
        // Said: what it is and the model's reason, which is data to read out, never obeyed.
        val spoken = listOf(what.trimEnd('.'), reason.trim().take(CAR_REASON_CHARS)).filter { it.isNotBlank() }
            .joinToString(". ") + ". Shall I go ahead?"
        carAsks[id] = carScope.launch {
            val ok = runCatching { route(title, detail, spoken) }.getOrNull() ?: return@launch
            carAsks.remove(id)
            if (isPending(id)) deliver(id, ok)
        }
        return true
    }

    private fun raisePrompt(
        app: Context,
        id: String,
        actionId: String,
        params: String,
        reason: String,
        description: String,
        tierLabel: String,
        commandId: String?,
        timeoutMs: Long,
        choices: List<String>? = null,
        challenge: String? = null,
        note: String = "",
        askWhy: Boolean = false,
    ): Boolean {
        appContext = app
        val intent = Intent(app, ApprovalActivity::class.java)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS or
                    Intent.FLAG_ACTIVITY_NO_USER_ACTION
            )
            .putExtra(EXTRA_REQUEST_ID, id)
            .putExtra(EXTRA_ACTION_ID, actionId)
            .putExtra(EXTRA_PARAMS, params)
            .putExtra(EXTRA_REASON, reason)
            .putExtra(EXTRA_DESCRIPTION, description)
            .putExtra(EXTRA_TIER_LABEL, tierLabel)
            .putExtra(EXTRA_COMMAND_ID, commandId)
            .putExtra(EXTRA_TIMEOUT_MS, timeoutMs)
        if (choices != null) intent.putExtra(EXTRA_CHOICES, choices.toTypedArray())
        if (challenge != null) intent.putExtra(EXTRA_CHALLENGE, challenge)
        if (note.isNotEmpty()) intent.putExtra(EXTRA_NOTE, note)
        if (askWhy) intent.putExtra(EXTRA_ASK_WHY, true)

        // A phone-confirmed ask is never put to the car: its plain yes would be an allow with no
        // signature, which the adapter refuses. The car's own voice tells the driver to use the phone.
        val carReached = challenge == null && askTheCar(id, actionId, params, reason, description)

        // Notification first. If the direct start is refused by background
        // activity-start restrictions, this is the user's only route to the
        // prompt, and posting it after a failed start would be too late.
        var reachable = postNotification(app, id, actionId, reason, intent, timeoutMs) || carReached

        try {
            app.startActivity(intent)
            reachable = true
        } catch (t: Throwable) {
            // Expected when the app is backgrounded without the
            // display-over-other-apps grant. The notification carries it.
            Log.w(TAG, "direct start of the consent prompt refused", t)
        }

        // WHETHER THE PROMPT ACTUALLY APPEARED IS A DIFFERENT QUESTION, and
        // `startActivity` will not answer it: a background activity start the
        // platform refuses does not throw, it logs and drops the intent. Nor
        // does the full-screen intent above rescue it on the case that matters
        // — while the screen is on and unlocked, the platform deliberately
        // renders a full-screen intent as an ordinary heads-up notification
        // rather than taking the screen over.
        //
        // So on a phone in use, with no "display over other apps" grant, there
        // is no route that raises this by itself, and the reported symptom is
        // exactly that: *"the popup doesn't auto come up, and I have to
        // manually click on the tool call for it to work"*. That is one grant
        // away from working and the app never said so.
        return reachable
    }

    /**
     * Called by [ApprovalActivity] as it starts. The only positive evidence
     * that the prompt is in front of a person.
     */
    fun raised(requestId: String) {
        onScreen[requestId]?.complete(Unit)
    }

    /**
     * Tell the user, once, that Jarvis cannot raise its own prompts.
     *
     * Deliberately its own notification on the alerts channel rather than more
     * words on the consent one: the consent notification is about a decision
     * waiting to be made, and this is about the phone's configuration. It is
     * posted at most once per process so a run of gated commands does not
     * become a run of identical complaints.
     */
    private fun explainWhyNothingAppeared(app: Context) {
        if (!explainedOverlayGrant.compareAndSet(false, true)) return
        if (GrapheneCompat.canDrawOverlays(app)) return
        val why = "${ActiveTheme.name} had to ask you something and Android would not let it open the " +
            "prompt on its own, so it arrived as a notification you had to tap. Allow " +
            "“display over other apps” and it will come up by itself."
        runCatching {
            val settings = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:${app.packageName}"),
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val nm = app.getSystemService(NotificationManager::class.java) ?: return
            nm.notify(
                OVERLAY_HINT_NOTIFICATION_ID,
                Notification.Builder(app, JarvisApp.CHANNEL_ALERTS)
                    .setSmallIcon(ai.jarvis.app.ui.theme.ActiveTheme.statusIcon)
                    .setColor(Notify.color(Notify.Template.APPROVAL))
                    .setContentTitle("Let ${ActiveTheme.name} show you its questions")
                    .setContentText(why)
                    .setStyle(Notification.BigTextStyle().bigText(why))
                    .setContentIntent(
                        PendingIntent.getActivity(
                            app,
                            OVERLAY_HINT_NOTIFICATION_ID,
                            settings,
                            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                        )
                    )
                    .setAutoCancel(true)
                    .build()
            )
        }.onFailure { Log.w(TAG, "could not explain the overlay grant", it) }
    }

    private fun postNotification(
        app: Context,
        id: String,
        actionId: String,
        reason: String,
        intent: Intent,
        timeoutMs: Long,
    ): Boolean {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return false
        val code = codes.incrementAndGet()
        val pi = PendingIntent.getActivity(
            app,
            code,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val summary = reason.ifEmpty { "No reason given." }
        val notification = Notification.Builder(app, JarvisApp.CHANNEL_APPROVAL)
            .setSmallIcon(ai.jarvis.app.ui.theme.ActiveTheme.statusIcon)
            .setColor(Notify.color(Notify.Template.APPROVAL))
            .setContentTitle("Approve “$actionId”?")
            .setContentText(summary)
            .setStyle(Notification.BigTextStyle().bigText(summary))
            .setCategory(Notification.CATEGORY_STATUS)
            .setContentIntent(pi)
            // On Android 14+ full-screen intents are reserved for calling and
            // alarm apps, so this usually degrades to a heads-up notification.
            // That is fine: it still puts the question in front of the user.
            .setFullScreenIntent(pi, true)
            .setOngoing(true)
            .setAutoCancel(false)
            // Never leak parameters onto a locked screen. The prompt itself
            // enforces the other half of that: while the keyguard is up it
            // hides the parameters and keeps APPROVE inert, so this is not the
            // only thing standing between a locked phone and a sent SMS.
            // See ai.jarvis.app.ui.ConsentGate.
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setTimeoutAfter(timeoutMs)
            .build()
        return try {
            nm.notify(code, notification)
            notificationIds[id] = code
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not post the approval notification", t)
            false
        }
    }

    private fun clearNotification(app: Context, id: String) {
        val code = notificationIds.remove(id) ?: return
        try {
            app.getSystemService(NotificationManager::class.java)?.cancel(code)
        } catch (t: Throwable) {
            Log.w(TAG, "could not cancel the approval notification", t)
        }
    }

    private const val DEFAULT_TIER_LABEL = "Tier 3, asks every time"
    /** How much of the parameters the car's small card shows, and of the reason it says. */
    private const val CAR_DETAIL_CHARS = 160
    private const val CAR_REASON_CHARS = 160
    private const val HERMES_TIER_LABEL = "Hermes tool approval"
}
