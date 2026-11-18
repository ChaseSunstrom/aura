package ai.aura.app.glass

import android.os.Build
import android.view.HapticFeedbackConstants
import android.view.View

/**
 * What the HUD is felt doing (GLASS_HUD §9.6): "a light tick on snap and on
 * tile, a medium click on Approve, a heavy double on the kill switch".
 *
 * Four moments and no more. A phone that buzzes on every scroll and every
 * focus is a phone that gets its haptics turned off, and then the approval
 * click — the one that says a decision was taken — is lost with the rest.
 * The reactor's own two felt transitions (attention, alert) live in
 * `ReactorHaptics` and are not repeated here.
 *
 * The decision is a table ([forEvent]) so a JVM test pins which moment gets
 * which feel; the delivery ([perform]) goes through the view's own haptic
 * feedback so it obeys the system's haptics setting, which a raw `Vibrator`
 * does not. The kill switch is the exception: it goes through the vibrator
 * on purpose, twice, because it must be felt even by a person who turned
 * touch feedback off — that is what "the kill switch" means.
 */
object Haptics {

    /** The moments the HUD is felt at. */
    enum class Event { SNAP, TILE, APPROVE, KILL }

    /** The three feels, in rising weight. */
    enum class Feel { TICK, CLICK, HEAVY_DOUBLE }

    fun forEvent(event: Event): Feel = when (event) {
        Event.SNAP, Event.TILE -> Feel.TICK
        Event.APPROVE -> Feel.CLICK
        Event.KILL -> Feel.HEAVY_DOUBLE
    }

    /**
     * The platform constant for a feel, by API level: `CONFIRM` arrived with
     * API 30 and this app runs from 29, so the click falls back to the
     * keyboard tap rather than crashing on the one version below it.
     */
    fun constantFor(feel: Feel, sdk: Int = Build.VERSION.SDK_INT): Int = when (feel) {
        Feel.TICK -> HapticFeedbackConstants.CLOCK_TICK
        Feel.CLICK -> if (sdk >= Build.VERSION_CODES.R) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.KEYBOARD_TAP
        // Delivered by ReactorHaptics through the vibrator; the view constant
        // is the fallback when there is no vibrator to reach.
        Feel.HEAVY_DOUBLE -> HapticFeedbackConstants.LONG_PRESS
    }

    /** Feel [event] on [view]. Never throws: haptics are never load-bearing. */
    fun perform(view: View?, event: Event) {
        if (view == null) return
        val feel = forEvent(event)
        try {
            view.performHapticFeedback(constantFor(feel))
            if (feel == Feel.HEAVY_DOUBLE) view.performHapticFeedback(constantFor(feel))
        } catch (t: Throwable) {
            // A view detached mid-gesture, or a ROM with no haptics: nothing to do.
        }
    }
}
