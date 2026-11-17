package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * The personal wake verifier (T-0714).
 *
 * The example is `tests/contracts/wake_verifier.json`, carried here verbatim because a
 * JVM test's working directory is not the repository;
 * `android-app/tools/wake_verifier_test.py` fails if this file and the contract drift apart.
 */
class WakeVerifierTest {
    private val positives = listOf(
        floatArrayOf(1.0f, 0.8f, -0.2f, 0.5f),
        floatArrayOf(0.9f, 1.1f, -0.1f, 0.4f),
        floatArrayOf(1.2f, 0.7f, -0.3f, 0.7f),
        floatArrayOf(0.8f, 0.9f, 0.0f, 0.3f),
    )
    private val negatives = listOf(
        floatArrayOf(-0.5f, 0.1f, 0.3f, -0.6f),
        floatArrayOf(-0.3f, -0.2f, 0.4f, -0.4f),
        floatArrayOf(-0.6f, 0.0f, 0.2f, -0.7f),
        floatArrayOf(-0.4f, 0.2f, 0.5f, -0.5f),
        floatArrayOf(0.1f, -0.3f, 0.3f, -0.2f),
        floatArrayOf(-0.2f, 0.1f, 0.1f, -0.3f),
    )
    private val take = floatArrayOf(0.85f, 0.75f, -0.1f, 0.45f)
    private val room = floatArrayOf(-0.35f, 0.05f, 0.35f, -0.5f)

    @Test
    fun `the contract example fits to its expected probabilities`() {
        val v = WakeVerifier.fit(positives, negatives)
        assertEquals(0.9749f, v.probability(take), 1e-3f)
        assertEquals(0.0097f, v.probability(room), 1e-3f)
    }

    @Test
    fun `lift only raises a half-heard frame`() {
        val v = WakeVerifier.fit(positives, negatives)
        val gate = WakeVerifier.GATE
        assertTrue(v.lift(gate, take) > 0.5f)
        assertEquals(gate - 0.01f, v.lift(gate - 0.01f, take))
        assertEquals(0.9f, v.lift(0.9f, room))
        assertEquals(gate, v.lift(gate, room))
        // A window of another length (a head with another shape) is the head's own word.
        assertEquals(gate, v.lift(gate, floatArrayOf(1f, 1f)))
        // Each level's own gate: strict does not lift what normal does.
        assertEquals(0.25f, v.lift(0.25f, take, WakeSensitivity.STRICT.verifierGate))
        assertTrue(v.lift(0.25f, take, WakeSensitivity.NORMAL.verifierGate) > 0.5f)
        // A non-finite probability is no word: the head's score stands.
        val broken = WakeVerifier(FloatArray(4), FloatArray(4) { 1f }, FloatArray(4) { Float.NaN }, 0f)
        assertEquals(0.3f, broken.lift(0.3f, take))
    }

    @Test
    fun `with a verifier a half-heard run of the owner's fires, and a room run does not`() {
        val v = WakeVerifier.fit(positives, negatives)
        val owner = WakeSensitivity.NORMAL.scorer()
        val tv = WakeSensitivity.NORMAL.scorer()
        val plain = WakeSensitivity.NORMAL.scorer()
        var ownerFired = false
        var tvFired = false
        var plainFired = false
        for (i in 0 until 4) {
            val t = 1_000L + i * 80
            ownerFired = ownerFired or owner.onScore(t, v.lift(0.3f, take))
            tvFired = tvFired or tv.onScore(t, v.lift(0.3f, room))
            plainFired = plainFired or plain.onScore(t, 0.3f)
        }
        assertTrue(ownerFired)
        assertTrue(!tvFired && !plainFired)
    }

    @Test
    fun `it survives a save and a load, and a foreign file is not weights`() {
        val dir = Files.createTempDirectory("wake").toFile()
        val file = File(dir, "hey_jarvis.verifier")
        val v = WakeVerifier.fit(positives, negatives)
        v.save(file)
        val back = WakeVerifier.load(file)!!
        assertEquals(v.probability(take), back.probability(take), 1e-6f)
        assertEquals(4, back.size)
        file.writeText("not a verifier")
        assertNull(WakeVerifier.load(file))
        // A damaged value is refused, not read: NaN would make every half-heard frame NaN.
        WakeVerifier(floatArrayOf(0f, 0f), floatArrayOf(1f, 1f), floatArrayOf(Float.NaN, 1f), 0f).save(file)
        assertNull(WakeVerifier.load(file))
        WakeVerifier(floatArrayOf(0f, 0f), floatArrayOf(1f, 0f), floatArrayOf(1f, 1f), 0f).save(file)
        assertNull(WakeVerifier.load(file))
        assertNull(WakeVerifier.load(File(dir, "missing")))
        dir.deleteRecursively()
    }
}
