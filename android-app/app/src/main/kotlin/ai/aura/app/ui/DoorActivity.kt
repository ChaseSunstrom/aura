package ai.aura.app.ui

import androidx.activity.addCallback
import androidx.fragment.app.FragmentActivity
import android.app.KeyguardManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager

/**
 * The shared frame behind the two screens that wake a locked phone with a
 * question — the Tier-3 consent prompt (`ai.aura.app.ApprovalActivity`) and
 * the companion question card (`ai.aura.app.companion.CompanionAskActivity`).
 *
 * Both are doors: they show over the keyguard so the phone lights up and the
 * question is not missed, but a locked phone is a phone in anyone's hand, so
 * each hides what it can and answers nothing until the real user is through
 * the keyguard. The mechanism that was byte-for-byte identical between them
 * (jscpd flagged two 19-line clones) lives here once:
 *
 *  * **The window.** `showWhenLocked` + `turnScreenOn`; `FLAG_KEEP_SCREEN_ON`
 *    so the prompt does not go dark mid-read; `FLAG_SECURE` so the parameters
 *    or the question stay out of screenshots and off the screen recorder,
 *    including this app's own accessibility path; no finish-on-touch-outside.
 *    Applied in [onCreate] rather than by a call the subclass has to make, so
 *    a new door cannot forget `FLAG_SECURE` — the one flag whose absence is
 *    invisible on the screen and only shows up in somebody's screen recording.
 *  * **The keyguard question.** [isLocked] is re-read on every resume and
 *    never cached at create, because the user may unlock by any route — this
 *    screen's own request, the power button, a fingerprint.
 *  * **The unlock request.** [requestUnlock] asks the system to take the user
 *    through the keyguard at most once at a time and re-runs [refreshGate] on
 *    success. A cancelled or failed dismiss leaves the screen exactly as it
 *    was — hidden and inert — and allows a later attempt; a keyguard quirk
 *    that throws must never take the prompt down with it.
 *
 * What is deliberately NOT here: the gate itself. What "locked" hides, how long
 * the arming delay is and which buttons stay live over the keyguard differ
 * between the two doors ([ConsentGate] versus `CompanionAskGate`) and are the
 * point of each screen, so each subclass owns its [refreshGate], its
 * `answered` state and its own arming timer.
 */
abstract class DoorActivity : FragmentActivity() {  // a FragmentActivity: BiometricPrompt (ApprovalProof) needs one

    /** Set while a dismiss request is outstanding, so we ask exactly once. */
    private var dismissRequested = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SECURE
        )
        setFinishOnTouchOutside(false)
        // Back must answer, never fall through to a default that leaves the
        // question open. A registered callback rather than an `onBackPressed`
        // override: `ComponentActivity` routes Back through its dispatcher,
        // and an override that skipped `super` would be the one path the
        // dispatcher never sees (and what lint's MissingSuperCall is about).
        onBackPressedDispatcher.addCallback(this) { onBack() }
    }

    /** Back on this screen: the door's own implicit answer. */
    protected abstract fun onBack()

    override fun onResume() {
        super.onResume()
        // The user may have unlocked by any route — this activity's dismiss
        // request, the power button, a fingerprint. Re-read, do not assume.
        refreshGate()
    }

    /**
     * Recompute the lock state and apply the screen's gate to its UI.
     *
     * Called here on resume and when the keyguard reports itself dismissed; the
     * subclass also calls it at create and when its arming timer fires, so every
     * path lands in the one place where "may this button do anything" is
     * decided. It runs before the views exist when `onCreate` bails out early
     * (no request to answer), so it must tolerate that.
     */
    protected abstract fun refreshGate()

    /** Keyguard state, re-read rather than cached. */
    protected fun isLocked(): Boolean =
        getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true

    /** Ask the system to take the user through the keyguard. Once at a time. */
    protected fun requestUnlock() {
        if (dismissRequested) return
        val km = getSystemService(KeyguardManager::class.java) ?: return
        dismissRequested = true
        try {
            km.requestDismissKeyguard(
                this,
                object : KeyguardManager.KeyguardDismissCallback() {
                    override fun onDismissSucceeded() {
                        dismissRequested = false
                        runOnUiThread { refreshGate() }
                    }

                    override fun onDismissError() {
                        dismissRequested = false
                    }

                    override fun onDismissCancelled() {
                        // The user chose not to unlock. Nothing opens up; the
                        // screen's countdown keeps running. Allow another attempt.
                        dismissRequested = false
                    }
                }
            )
        } catch (t: Throwable) {
            // Never let a keyguard quirk take the prompt down with it: what was
            // hidden stays hidden and what was inert stays inert either way.
            dismissRequested = false
        }
    }
}
