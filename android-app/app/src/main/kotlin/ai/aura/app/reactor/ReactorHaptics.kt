package ai.aura.app.reactor

import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator

/**
 * The two moments the reactor is felt as well as seen: entering attention (a
 * question is waiting on the person) and entering alert (something went
 * wrong). Every other transition is silent, because a phone that buzzes on
 * "thinking" is a phone that gets muted, and then the approval buzz is lost
 * with the rest.
 */
object ReactorHaptics {
    /** What the phone can do; a seam so the JVM test records buzzes instead of needing a Vibrator. */
    fun interface Buzzer {
        /** One of `VibrationEffect.EFFECT_*`. */
        fun buzz(effect: Int)
    }

    /**
     * Buzz for [next], given [previous]: nothing unless the state changed into
     * one of the two felt states. A null [buzzer] (no vibrator, or a car) is a
     * no-op rather than a crash — haptics are never load-bearing.
     */
    fun onTransition(previous: ReactorState?, next: ReactorState, buzzer: Buzzer?) {
        if (buzzer == null || previous == next) return
        when (next) {
            ReactorState.ATTENTION -> buzzer.buzz(VibrationEffect.EFFECT_DOUBLE_CLICK)
            ReactorState.ALERT -> buzzer.buzz(VibrationEffect.EFFECT_HEAVY_CLICK)
            // The kill switch: twice, so it cannot be mistaken for an alert.
            ReactorState.KILL -> {
                buzzer.buzz(VibrationEffect.EFFECT_HEAVY_CLICK)
                buzzer.buzz(VibrationEffect.EFFECT_HEAVY_CLICK)
            }
            else -> Unit
        }
    }

    /** A [Buzzer] over the system vibrator, or null when there is none to buzz. */
    fun of(vibrator: Vibrator?): Buzzer? {
        if (vibrator == null || !vibrator.hasVibrator()) return null
        return Buzzer { effect ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                vibrator.vibrate(VibrationEffect.createPredefined(effect))
            }
        }
    }
}
