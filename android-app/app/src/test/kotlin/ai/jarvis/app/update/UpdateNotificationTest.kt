package ai.jarvis.app.update

import android.app.NotificationManager
import android.content.Context
import android.os.Looper
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class UpdateNotificationTest {
    private val app: Context = RuntimeEnvironment.getApplication()
    private val shade = shadowOf(app.getSystemService(NotificationManager::class.java))

    private fun shown() = shade.allNotifications.single().extras.getCharSequence("android.text").toString()

    @Test
    fun `the download bar is posted a few times a second at most, however fast the bytes come`() {
        // The owner's log, 2026-11-10: id 6305 posted ~14 times a second on the LAN, every update
        // past five a second shed by the system ("rate limit (5.0) exceeded").
        val mb = 1L shl 20
        UpdateNotification.downloading(app, "1.0", 1 * mb, 100 * mb)
        UpdateNotification.downloading(app, "1.0", 2 * mb, 100 * mb)
        assertEquals("1% · 1 of 100 MB", shown())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(300))
        UpdateNotification.downloading(app, "1.0", 3 * mb, 100 * mb)
        assertEquals("3% · 3 of 100 MB", shown())
    }
}
