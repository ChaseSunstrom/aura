package ai.jarvis.app

import android.app.ActivityManager
import android.app.Application
import android.app.ApplicationExitInfo
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/** T-0919: a process Android kills leaves no crash line; the next start says why it ended. */
@RunWith(RobolectricTestRunner::class)
class JarvisAppExitTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()

    @Test fun `the last run's end is named by its reason`() {
        val am = app.getSystemService(ActivityManager::class.java)
        shadowOf(am).addApplicationExitInfo(app.packageName, 4321, ApplicationExitInfo.REASON_PACKAGE_STATE_CHANGE, 0)
        val line = JarvisApp.lastExit(app)
        assertTrue("$line", line?.contains("PACKAGE_STATE_CHANGE") == true)
    }

    @Test fun `the app's own process is named, not the web view's helper that ended after it`() {
        // The owner's log, 2026-11-10 03:11: "OTHER (... isolated not needed)" was the console's
        // renderer, and the app's own end went unsaid.
        val am = app.getSystemService(ActivityManager::class.java)
        shadowOf(am).addApplicationExitInfo(app.packageName, 4321, ApplicationExitInfo.REASON_PACKAGE_STATE_CHANGE, 0)
        shadowOf(am).addApplicationExitInfo("${app.packageName}:sandboxed_process0", 4400, ApplicationExitInfo.REASON_OTHER, 0)
        val line = JarvisApp.lastExit(app)
        assertTrue("$line", line?.contains("PACKAGE_STATE_CHANGE") == true)
    }

    @Test fun `a first run has nothing to say`() {
        assertNull(JarvisApp.lastExit(app))
    }
}
