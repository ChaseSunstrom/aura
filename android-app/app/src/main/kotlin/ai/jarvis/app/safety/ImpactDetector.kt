package ai.jarvis.app.safety

import kotlin.math.abs

/**
 * Every number the impact watch depends on, in one place (T-1209). UNTUNED: these are a first
 * guess from how phones behave, not measured on a real fall or a real crash, and a real phone
 * will need them moved. The watch is off by default for that reason.
 */
data class ImpactThresholds(
    /** Acceleration magnitude (m/s², gravity included) that counts as a hard impact: about 3.5 g. A pocketed walk peaks near 2 g. */
    val impactMs2: Float = 35f,
    /** After an impact the phone bounces and rattles; samples this soon after are not stillness. */
    val settleMs: Long = 1_000L,
    /** Stillness is magnitude within this much of gravity. Wide enough for breathing against a pocket. */
    val stillBandMs2: Float = 1.5f,
    /** How long the phone must stay still after the settle for it to be a person who did not get up. */
    val stillMs: Long = 10_000L,
    /** How long after an impact stillness is waited for; past it the owner moved and nothing is sent. */
    val watchMs: Long = 30_000L,
    /** A silence between samples longer than this breaks stillness: a stalled sensor is not a still owner. */
    val maxGapMs: Long = 2_000L,
    val gravityMs2: Float = 9.81f,
)

/**
 * A hard impact followed by stillness, from timestamped acceleration magnitudes.
 *
 * Pure and single-threaded: feed it samples in time order. It answers true once per impact, on
 * the sample that completes the stillness; it then needs a new impact before it can say so again.
 *
 * Does not cover: telling a fall from a crash (the caller knows whether the car link is up), a
 * fall onto something soft that never reaches [ImpactThresholds.impactMs2], or an owner who is
 * hurt but moves the phone within the stillness window.
 */
class ImpactDetector(private val t: ImpactThresholds = ImpactThresholds()) {
    private var impactAt = NONE
    private var stillSince = NONE
    private var lastAt = NONE

    /** One sample: [atMs] on any monotonic clock, [magnitude] in m/s². True when this sample completes a signal. */
    fun feed(atMs: Long, magnitude: Float): Boolean {
        val gap = lastAt != NONE && atMs - lastAt > t.maxGapMs
        lastAt = atMs
        if (impactAt == NONE) {
            if (magnitude >= t.impactMs2) impactAt = atMs
            return false
        }
        if (atMs - impactAt > t.watchMs) {
            // The window closed with no stillness; this sample may itself be the next impact.
            reset()
            if (magnitude >= t.impactMs2) impactAt = atMs
            return false
        }
        if (atMs - impactAt < t.settleMs) return false
        if (gap || abs(magnitude - t.gravityMs2) > t.stillBandMs2) {
            stillSince = NONE
            // A fresh hard impact while waiting is a new fall: its own settle and stillness.
            if (magnitude >= t.impactMs2) impactAt = atMs
            return false
        }
        if (stillSince == NONE) stillSince = atMs
        if (atMs - stillSince < t.stillMs) return false
        reset()
        return true
    }

    private fun reset() {
        impactAt = NONE
        stillSince = NONE
    }

    private companion object {
        const val NONE = Long.MIN_VALUE
    }
}
