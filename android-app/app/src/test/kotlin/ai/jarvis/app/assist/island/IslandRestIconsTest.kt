package ai.jarvis.app.assist.island

import ai.jarvis.app.car.dash.tiles.HeadwayNotificationListener
import android.app.Notification
import android.content.Context
import android.os.Process
import android.service.notification.StatusBarNotification
import android.view.View
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The resting island as the status bar (T-0969): one icon per app with a notification showing,
 * read from the listener the phone already runs for the car, newest first.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class IslandRestIconsTest {
    private val ctx: Context = ApplicationProvider.getApplicationContext()

    private fun posted(pkg: String, at: Long, build: Notification.Builder.() -> Unit = {}): StatusBarNotification {
        val n = Notification.Builder(ctx, "c").setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle("t").apply(build).build()
        return StatusBarNotification(pkg, pkg, at.toInt(), null, 0, 0, 0, n, Process.myUserHandle(), at)
    }

    private fun apps(vararg active: StatusBarNotification, silent: (String) -> Boolean = { false }) =
        HeadwayNotificationListener.islandIcons(ctx, arrayOf(*active), silent).map { it.packageName }

    @Test
    fun `one icon per app, newest first, and an app's goes when its last notification clears`() {
        val mail = posted("com.example.mail", 1L)
        val chat = posted("com.example.chat", 2L)
        val mail2 = posted("com.example.mail", 3L)
        assertEquals(listOf("com.example.mail", "com.example.chat"), apps(mail, chat, mail2))
        assertNotNull(HeadwayNotificationListener.islandIcons(ctx, arrayOf(chat)) { false }.single().icon)
        assertEquals(listOf("com.example.chat"), apps(chat))
    }

    @Test
    fun `a group summary, a silent one and the phone's own are not on the island`() {
        val summary = posted("com.example.mail", 1L) { setGroup("g"); setGroupSummary(true) }
        val quiet = posted("com.example.weather", 2L)
        val own = posted(ctx.packageName, 3L)
        assertEquals(emptyList<String>(), apps(summary, quiet, own) { it == quiet.key })
    }

    @Test
    fun `the island grows to the left for its icons, the camera stays where it was, and what does not fit is counted`() {
        val view = RestView(ctx).apply { place(null, 80) }
        fun width() = view.run { measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED); measuredWidth }
        val bare = width()
        val holeBefore = view.holeX - view.measuredWidth / 2f
        val icon = ctx.getDrawable(android.R.drawable.ic_dialog_email)!!
        view.icons = listOf(icon, icon)
        val two = width()
        assertTrue("two icons widen the island ($bare -> $two)", two > bare)
        assertEquals("the camera keeps its place on screen", holeBefore, view.holeX - two / 2f + view.shift, 1f)
        view.icons = List(60) { icon }
        val many = width()
        assertTrue("the island stays on the screen ($many px)", many <= ctx.resources.displayMetrics.widthPixels)
        assertTrue("the rest are counted, not dropped silently", view.counted > 0)
        // Right to left, Android's camera and microphone dot is at the top-left: the cover's pill grows
        // no nearer its corner than [keep] (the bare pill stays on the camera wherever it is).
        val half = ctx.resources.displayMetrics.widthPixels / 2f
        val keep = ((half - RestView(ctx).apply { place(null, 80) }.holeX) / 2).toInt()
        val clear = RestView(ctx).apply { place(null, 80, clear = keep); icons = List(60) { icon } }
        val cleared = clear.run { measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED); measuredWidth }
        assertTrue("a pill kept clear of the corner is narrower ($cleared < $many)", cleared < many)
        // Centred on the hole, the window's left edge on screen is the hole less its offset in the view.
        assertTrue("the pill's left edge is clear of the corner", half - clear.holeX >= keep)
    }
}
