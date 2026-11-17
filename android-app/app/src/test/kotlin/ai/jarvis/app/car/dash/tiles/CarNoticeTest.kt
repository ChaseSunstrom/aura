package ai.jarvis.app.car.dash.tiles

import android.app.Notification
import android.content.Context
import android.media.session.MediaSession
import android.os.Process
import android.service.notification.StatusBarNotification
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Which of the phone's notifications are the car's notices (T-0976). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarNoticeTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()

    private fun posted(build: Notification.Builder.() -> Unit): StatusBarNotification {
        val n = Notification.Builder(ctx, "c").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Song").setContentText("Artist").apply(build).build()
        return StatusBarNotification("com.example.music", "com.example.music", 1, null, 0, 0, 0, n, Process.myUserHandle(), 1L)
    }

    @Test fun `a plain notification is a notice`() {
        assertEquals("Song", HeadwayNotificationListener.noticeOf(ctx, posted {})?.title)
    }

    @Test fun `a music app's own player is not a notice, the music card already shows it`() {
        val session = MediaSession(ctx, "t")
        assertNull(HeadwayNotificationListener.noticeOf(ctx, posted { setStyle(Notification.MediaStyle().setMediaSession(session.sessionToken)) }))
        session.release()
    }

    @Test fun `a player that names no session is still a player by its category`() {
        assertNull(HeadwayNotificationListener.noticeOf(ctx, posted { setCategory(Notification.CATEGORY_TRANSPORT) }))
    }
}
