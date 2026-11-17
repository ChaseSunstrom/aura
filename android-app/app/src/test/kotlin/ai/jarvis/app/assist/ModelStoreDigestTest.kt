package ai.jarvis.app.assist

import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/** A model without a digest to check against is refused, not trusted (T-0618). */
class ModelStoreDigestTest {
    private val sha = "ab".repeat(32)

    @Test fun `no digest header is refused`() {
        assertNotNull(ModelStore.digestProblem("m.onnx", null, sha))
        assertNotNull(ModelStore.digestProblem("m.onnx", "", sha))
    }

    @Test fun `a different digest is refused`() {
        assertNotNull(ModelStore.digestProblem("m.onnx", "cd".repeat(32), sha))
    }

    @Test fun `a matching digest is kept, whatever its case`() {
        assertNull(ModelStore.digestProblem("m.onnx", sha.uppercase(), sha))
    }
}
