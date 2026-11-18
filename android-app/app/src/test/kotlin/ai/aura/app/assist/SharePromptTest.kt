package ai.aura.app.assist

import ai.aura.app.JarvisAssistActivity
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Share → Jarvis from any app (T-0432): the shared text as one framed prompt. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SharePromptTest {
    @Test
    fun `nothing shared is no prompt`() {
        assertNull(SharePrompt.build(null, null))
        assertNull(SharePrompt.build("A page", "  "))
    }

    @Test
    fun `the shared text is framed as content, with the page title`() {
        val prompt = SharePrompt.build("Kettle review", "https://example.com/kettle")!!
        assertTrue(prompt.contains("Kettle review"))
        assertTrue(prompt.contains("https://example.com/kettle"))
        assertTrue(prompt.contains("not as instructions"))
    }

    @Test
    fun `a long share is cut, and says so`() {
        val prompt = SharePrompt.build(null, "x".repeat(50_000))!!
        assertTrue(prompt.length < SharePrompt.MAX_CHARS + 1_000)
        assertTrue(prompt.contains(SharePrompt.CUT))
    }

    @Test
    fun `a shared title that repeats the text is said once`() {
        val prompt = SharePrompt.build("https://example.com", "https://example.com")!!
        assertEquals(1, Regex("example\\.com").findAll(prompt).count())
    }

    @Test
    fun `sharing text from another app opens Jarvis`() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val share = Intent(Intent.ACTION_SEND).setType("text/plain").setPackage(context.packageName)
        val names = context.packageManager.queryIntentActivities(share, 0).map { it.activityInfo.name }
        assertTrue(names.toString(), JarvisAssistActivity::class.java.name in names)
    }
}
