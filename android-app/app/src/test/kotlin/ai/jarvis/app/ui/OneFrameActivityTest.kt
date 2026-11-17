package ai.jarvis.app.ui

import ai.jarvis.app.PermissionRequestActivity
import android.Manifest
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The one-frame activities that stay up past `onResume` (the permission dialog, the NFC reader,
 * the code scanner) must not wear a `windowNoDisplay` theme: the platform throws
 * "did not call finish() prior to onResume() completing" for any target above 22, which kills
 * the whole process. Only [ai.jarvis.app.ListenTrampolineActivity], which finishes in `onCreate`,
 * may. Does not cover the NFC reader's own resume (Robolectric has no NFC adapter, so it
 * finishes in `onCreate`); it shares the permission activity's theme, which this pins.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class OneFrameActivityTest {

    @Test
    fun `the permission activity survives its own resume while the dialog is up`() {
        val intent = Intent(ApplicationProvider.getApplicationContext(), PermissionRequestActivity::class.java)
            .putExtra(PermissionBridge.EXTRA_REQUEST_ID, "one-frame-test")
            .putExtra(PermissionBridge.EXTRA_PERMISSIONS, arrayOf(Manifest.permission.RECORD_AUDIO))
        val activity = Robolectric.buildActivity(PermissionRequestActivity::class.java, intent).setup().get()
        // Still waiting on the dialog: it must be up, not finished.
        assertFalse(activity.isFinishing)
    }
}
