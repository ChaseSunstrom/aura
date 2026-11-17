package ai.jarvis.app.ui

import ai.jarvis.app.ui.theme.JarvisTokens
import android.app.Activity
import android.os.Looper
import android.view.View
import android.widget.FrameLayout
import android.widget.TextView
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

/**
 * The note that replaced the system toast: one per screen, the latest message wins, it is
 * spoken (a polite live region), and it leaves on its own after the toast dwell.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ScreenNoteTest {

    @Test
    fun `one note per screen, the latest message wins, and it leaves after the dwell`() {
        val activity = Robolectric.buildActivity(Activity::class.java).setup().get()
        val content = activity.findViewById<FrameLayout>(android.R.id.content)

        ScreenNote.show(activity, "Pasted 12 characters")
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(JarvisTokens.Motion.Dur.TOAST / 2L))
        ScreenNote.show(activity, "Clipboard is empty")

        val notes = (0 until content.childCount).map { content.getChildAt(it) }.filterIsInstance<TextView>()
        assertEquals(1, notes.size)
        val note = notes.single()
        assertEquals("Clipboard is empty", note.text.toString())
        assertEquals(View.ACCESSIBILITY_LIVE_REGION_POLITE, note.accessibilityLiveRegion)

        // The second message restarted the dwell: still up where the first would have gone.
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(JarvisTokens.Motion.Dur.TOAST / 2L + 1))
        assertEquals(View.VISIBLE, note.visibility)
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(JarvisTokens.Motion.Dur.TOAST.toLong()))
        assertEquals(View.GONE, note.visibility)
    }
}
