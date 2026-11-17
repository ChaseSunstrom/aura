package ai.jarvis.app.assist

import ai.jarvis.app.assist.BundledVoice.Model
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import java.io.File

/**
 * AURA's voice on the phone speaks only once its fetch measured it keeping up (T-0872): whole,
 * and a pace below 1. Does not load the model (no native runtime under Robolectric).
 */
@RunWith(RobolectricTestRunner::class)
class BundledVoiceTest {
    private val context = RuntimeEnvironment.getApplication()

    private fun unpacked(model: Model, pace: String?): File {
        val dir = File(ModelStore.directory(context), model.folder).apply { mkdirs() }
        File(dir, model.onnx).writeBytes(ByteArray(8))
        model.needs.forEach { File(dir, it).writeText("x") }
        File(dir, "espeak-ng-data").mkdirs()
        File(dir, "ready").writeText("8")
        pace?.let { File(dir, "pace").writeText(it) }
        return dir
    }

    @Test fun `her voice is real time only when whole and measured faster than it speaks`() {
        assertFalse("not fetched", BundledVoice.realtime(context, Model.KOKORO))
        val dir = unpacked(Model.KOKORO, pace = null)
        assertTrue(BundledVoice.ready(context, Model.KOKORO))
        assertFalse("never measured", BundledVoice.realtime(context, Model.KOKORO))
        File(dir, "pace").writeText("1.4")
        assertFalse("too slow", BundledVoice.realtime(context, Model.KOKORO))
        File(dir, "pace").writeText("0.6")
        assertTrue(BundledVoice.realtime(context, Model.KOKORO))
        File(dir, "voices.bin").delete()
        assertFalse("a voice table missing is not whole", BundledVoice.realtime(context, Model.KOKORO))
    }

    @Test fun `the two voices keep apart`() {
        unpacked(Model.KOKORO, pace = "0.5")
        assertFalse(BundledVoice.ready(context, Model.PIPER))
        unpacked(Model.PIPER, pace = null)
        assertTrue(BundledVoice.ready(context, Model.PIPER) && BundledVoice.ready(context, Model.KOKORO))
    }
}
