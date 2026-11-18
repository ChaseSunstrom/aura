package ai.aura.app.assist

import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.io.IOException
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.sqrt

/**
 * The owner's own ways of saying the name (T-0714).
 *
 * The owner, 2026-11-08: "I have to ask it in a specific way, I can't use different
 * intonations". The stock hey_jarvis head was trained on synthetic voices, and the
 * owner's other ways of saying it score under the 0.5 that a stranger's voice must
 * also clear. Lowering that bar lowers it for the television too. Instead the phone
 * learns the owner's takes: openWakeWord's custom verifier, a logistic regression over
 * the same 16x96 embedding window the head reads, fitted on the phone from a few takes
 * (positives) and some room sound and reading (negatives).
 *
 * [lift] is the whole of how it changes detection: a frame the head scores at or over
 * the level's gate ([WakeSensitivity.verifierGate], never under [GATE]) is raised to the
 * verifier's probability when that is higher. [WakeScore]'s
 * threshold, run length and refractory period then apply unchanged, so a frame the
 * head is sure of fires as before, and the verifier only ever adds the owner's
 * half-heard ones.
 *
 * Mirrored in `android-app/tools/wake_verifier_test.py`; the numbers are
 * `tests/contracts/wake_verifier.json`. Does not cover choosing the positive windows
 * (the enrolment screen takes each take's top-scoring windows), and it cannot make the
 * head hear a way of saying it that the head scores under [GATE]: those still miss.
 */
class WakeVerifier(
    private val mean: FloatArray,
    private val std: FloatArray,
    private val weights: FloatArray,
    private val bias: Float,
) {
    /** The window length it was fitted on: 16 x 96 on the phone. */
    val size: Int get() = weights.size

    /** P(this window is the owner saying the name). */
    fun probability(window: FloatArray): Float {
        var z = bias.toDouble()
        for (j in weights.indices) z += weights[j].toDouble() * (window[j] - mean[j]) / std[j]
        return sigmoid(z).toFloat()
    }

    /**
     * The head's [score], raised to the verifier's word when the head half-heard it (at or
     * over [gate]). A non-finite probability is no word at all: max(score, NaN) is NaN,
     * which no threshold meets.
     */
    fun lift(score: Float, window: FloatArray, gate: Float = GATE): Float {
        if (score < gate || window.size != size) return score
        val p = probability(window)
        return if (p.isFinite()) max(score, p) else score
    }

    /** Written whole to a sibling file first, so a crash mid-write leaves the old one. */
    fun save(file: File) {
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        DataOutputStream(tmp.outputStream().buffered()).use { out ->
            out.writeInt(MAGIC)
            out.writeInt(size)
            for (array in listOf(mean, std, weights)) array.forEach { out.writeFloat(it) }
            out.writeFloat(bias)
        }
        if (!tmp.renameTo(file)) throw IOException("could not replace ${file.name}")
    }

    companion object {
        /**
         * openWakeWord's default verifier threshold and the lowest gate a level uses ([WakeSensitivity.EAGER]):
         * under it the head did not hear the name at all, and an enrolment take is refused.
         */
        const val GATE = 0.1f

        /** Where the owner's verifier lives, under the app's files directory. */
        const val FILE = "wake/hey_jarvis.verifier"
        const val L2 = 0.01
        const val EPOCHS = 300
        const val RATE = 0.5
        const val STD_FLOOR = 0.001

        /**
         * "JWV1" as a big-endian int (0x4A 0x57 0x56 0x31): a file from another build or
         * another format is not read as weights. Decimal, because the token lint reads an
         * eight-digit hex literal as a colour.
         */
        private const val MAGIC = 1247237681

        private fun sigmoid(x: Double): Double = if (x >= 0) 1 / (1 + exp(-x)) else exp(x).let { it / (1 + it) }

        /**
         * Batch gradient descent from zero on the class-balanced log loss with an L2 penalty,
         * over features standardised by the training set. Class-balanced because a handful of
         * takes face hundreds of room windows, where "never" would be the cheapest answer.
         * About a second for 1536 features and a few hundred windows: run it off the main thread.
         */
        fun fit(positives: List<FloatArray>, negatives: List<FloatArray>): WakeVerifier {
            require(positives.isNotEmpty() && negatives.isNotEmpty()) { "a verifier needs takes and room sound" }
            val rows = positives + negatives
            val d = rows.first().size
            require(rows.all { it.size == d }) { "every window must be the same length" }
            val n = rows.size
            val mean = DoubleArray(d) { j -> rows.sumOf { it[j].toDouble() } / n }
            val std = DoubleArray(d) { j -> max(sqrt(rows.sumOf { (it[j] - mean[j]).let { v -> v * v } } / n), STD_FLOOR) }
            val z = rows.map { r -> DoubleArray(d) { j -> (r[j] - mean[j]) / std[j] } }
            val label = DoubleArray(n) { if (it < positives.size) 1.0 else 0.0 }
            val share = DoubleArray(n) { if (it < positives.size) 0.5 / positives.size else 0.5 / negatives.size }
            val w = DoubleArray(d)
            var b = 0.0
            val gw = DoubleArray(d)
            repeat(EPOCHS) {
                for (j in 0 until d) gw[j] = L2 * w[j]
                var gb = 0.0
                for (i in 0 until n) {
                    val row = z[i]
                    var s = b
                    for (j in 0 until d) s += w[j] * row[j]
                    val e = (sigmoid(s) - label[i]) * share[i]
                    for (j in 0 until d) gw[j] += e * row[j]
                    gb += e
                }
                for (j in 0 until d) w[j] -= RATE * gw[j]
                b -= RATE * gb
            }
            return WakeVerifier(
                FloatArray(d) { mean[it].toFloat() },
                FloatArray(d) { std[it].toFloat() },
                FloatArray(d) { w[it].toFloat() },
                b.toFloat(),
            )
        }

        /**
         * The stored verifier, or null when there is none or it is not one this build wrote. A
         * NaN or infinite value, or a zero spread, is a damaged file: it would turn every
         * half-heard frame into NaN (deaf) or into 1 (a wake on anything the head half-hears).
         */
        fun load(file: File): WakeVerifier? = try {
            DataInputStream(file.inputStream().buffered()).use { input ->
                if (input.readInt() != MAGIC) return null
                val d = input.readInt()
                if (d <= 0 || d > MAX_FEATURES) return null
                val read = { FloatArray(d) { input.readFloat() } }
                val mean = read()
                val std = read()
                val weights = read()
                val bias = input.readFloat()
                val finite = listOf(mean, std, weights).all { a -> a.all { it.isFinite() } } && bias.isFinite()
                if (!finite || std.any { it <= 0f }) return null
                WakeVerifier(mean, std, weights, bias)
            }
        } catch (e: IOException) {
            null
        }

        /** Far above the phone's 1536: a corrupt length is refused, not allocated. */
        private const val MAX_FEATURES = 1 shl 14
    }
}
