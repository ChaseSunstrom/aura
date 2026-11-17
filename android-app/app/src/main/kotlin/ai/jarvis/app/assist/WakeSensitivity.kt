package ai.jarvis.app.assist

import ai.jarvis.app.ui.theme.JarvisThemes

/**
 * How readily this phone's own detector fires: one per-phone choice instead of
 * two raw numbers nobody can reason about (a threshold and a frame count).
 *
 * Each level is a [WakeScore] threshold and run length; the refractory period
 * is not part of it, because that guards "one phrase, one detection", which is
 * no less true on a strict phone than an eager one.
 *
 *  * [STRICT] (0.65, 3 frames, 240 ms of evidence) prevents FALSE wakes: the
 *    television, a podcast, a cough read as "hey Jarvis" in a noisy room. The
 *    cost is a phrase said softly or far away being missed.
 *  * [NORMAL] is exactly [WakeScore]'s defaults, so a phone that never touched
 *    the setting behaves as it did before the setting existed.
 *  * [EAGER] (0.4, 2 frames) prevents MISSED wakes: a quiet voice, a phone
 *    across the room, an accent the model scores low. It keeps two frames, so
 *    a single-frame transient (a door, a consonant) still cannot fire it alone;
 *    the cost is more false wakes.
 *
 * Each level also says how faint a "hey jarvis" may be and still be lifted by
 * the owner's [WakeVerifier] ([verifierGate], T-0714): the gate is the trade.
 * Through the real models on synthetic voices, 0.3 added no false fire to the
 * head's own, 0.2 lifted about twice as many missed takes for a few rhymes
 * ("hey darling", "hey Travis"), and 0.1 the most for more of them
 * (`tests/contracts/wake_verifier.json`, `scripts/verify/wake_verifier_live_check.py`).
 *
 * Applies to on-device detection only: on the server path the house scores the
 * audio and this phone never sees a score.
 */
enum class WakeSensitivity(val threshold: Float, val frames: Int, val verifierGate: Float) {
    STRICT(0.65f, 3, 0.3f),
    NORMAL(WakeScore.DEFAULT_THRESHOLD, WakeScore.DEFAULT_FRAMES, 0.2f),
    EAGER(0.4f, 2, WakeVerifier.GATE),
    ;

    /**
     * A fresh scorer at this level, with the default refractory period. While [musicPlaying]
     * (the car's music reaching the phone's open microphone) it asks for [MUSIC_EXTRA_FRAMES]
     * more frames and at least STRICT's line: music scores in short weak runs a quiet room never
     * produces, and one false wake there opened the car's microphone (T-1179).
     */
    fun scorer(musicPlaying: Boolean = false): WakeScore =
        if (musicPlaying) WakeScore(threshold = maxOf(threshold, STRICT.threshold), framesToFire = frames + MUSIC_EXTRA_FRAMES)
        else WakeScore(threshold = threshold, framesToFire = frames)

    /**
     * The scorer for one head: a head trained for a theme pack ("aura") fires at the pack's
     * own measured threshold (its scores sit near 1 for the word), at this level's run length
     * (longer while [musicPlaying]); every other head at this level's.
     */
    fun scorerFor(word: String, musicPlaying: Boolean = false): WakeScore =
        JarvisThemes.ALL.firstOrNull { it.id == word }?.let {
            WakeScore(threshold = it.wakeThreshold, framesToFire = frames + if (musicPlaying) MUSIC_EXTRA_FRAMES else 0)
        } ?: scorer(musicPlaying)

    companion object {
        /** 80 ms each: a wake over music needs this much more evidence than in silence. */
        const val MUSIC_EXTRA_FRAMES = 2

        /**
         * The stored name back to a level. Anything unrecognised (a name from a
         * later build, a hand-edited pref, nothing at all) is [NORMAL]: a typo
         * must not silently make the phone deaf or jumpy.
         */
        fun fromStored(name: String?): WakeSensitivity =
            entries.firstOrNull { it.name == name } ?: NORMAL
    }
}
