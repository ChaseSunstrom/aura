package ai.jarvis.app.assist

/**
 * From the owner's recorded takes to a [WakeVerifier] (T-0714): which windows of a take
 * are the name, which takes the head did not hear at all, and how well the result
 * recognises a take it was not fitted on.
 *
 * Pure, so the JVM tests drive it with made-up windows; [WakeEnrolActivity] feeds it
 * what [OnDeviceWakeWord.onWindow] reports while a take is recorded. Does not cover
 * whether the takes were really the name: a take of "hey Travis" that the head
 * half-hears is learnt as the owner's way of saying it.
 */
object WakeEnrol {
    /** How the screen asks for each take: the ways of saying it the stock head misses. */
    val STYLES = listOf(
        "the way you usually say it",
        "quickly, in passing",
        "drawn out, slowly",
        "quietly, almost under your breath",
        "from across the room",
        "as a question",
        "flat and tired",
        "calling out, a little louder",
    )

    /** Windows kept per take: its best few steps, where the head heard the name most. */
    const val TOP = 3

    /** One recorded take: the head's best score in it and the windows it was heard in. */
    class Take(val peak: Float, val windows: List<FloatArray>)

    /**
     * The take in [steps] (each step's own score and window), or null when the head never
     * reached [WakeVerifier.GATE]: the lift could not raise such a frame, so learning it
     * would only teach the verifier noise.
     */
    fun take(steps: List<Pair<Float, FloatArray>>): Take? {
        val best = steps.sortedByDescending { it.first }.take(TOP)
        val peak = best.firstOrNull()?.first ?: return null
        if (peak < WakeVerifier.GATE) return null
        return Take(peak, best.filter { it.first >= WakeVerifier.GATE }.map { it.second })
    }

    /** A fitted verifier and how many takes it recognised when each was left out of its fit. */
    class Result(val verifier: WakeVerifier, val recognised: Int, val of: Int)

    /**
     * Fit on every take against [room] (reading aloud and room sound: never the name),
     * then leave each take out in turn and ask whether the rest recognise its best
     * window. A low count on the screen is a verifier that learnt the takes, not the
     * owner. Takes x fits: a few seconds on a phone, off the main thread.
     */
    fun fit(takes: List<Take>, room: List<FloatArray>): Result {
        require(takes.size >= 2) { "at least two takes" }
        val verifier = WakeVerifier.fit(takes.flatMap { it.windows }, room)
        val recognised = takes.indices.count { i ->
            val rest = takes.filterIndexed { j, _ -> j != i }.flatMap { it.windows }
            WakeVerifier.fit(rest, room).probability(takes[i].windows.first()) > 0.5f
        }
        return Result(verifier, recognised, takes.size)
    }
}
