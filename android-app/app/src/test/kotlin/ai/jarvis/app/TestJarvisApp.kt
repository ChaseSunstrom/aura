package ai.jarvis.app

import androidx.compose.runtime.snapshots.Snapshot
import java.lang.reflect.Method
import org.robolectric.TestLifecycleApplication

/**
 * The application every Robolectric test runs (Robolectric takes `Test` + the manifest's
 * application name over the real class), so each test starts with Compose's global snapshot
 * applied.
 *
 * Why: the coroutine that applies writes made outside a composition lives on the main looper,
 * and Robolectric resets that looper between tests. A class that ends with such a write
 * pending leaves it pending for good, and the next test with a Compose rule waits at its very
 * start for it to clear: the unfiltered suite spun there until it was killed (T-1031; the
 * island, emergency and approval classes in that order, each pair of them passing).
 *
 * Does not cover a write made after a test has started: a test that changes state from outside
 * a composition still applies it itself (`Snapshot.sendApplyNotifications()`).
 */
class TestJarvisApp : JarvisApp(), TestLifecycleApplication {
    override fun onCreate() {
        super.onCreate()
        ai.jarvis.app.car.holo.HoloLight.clock = null
    }

    override fun beforeTest(method: Method?) {
        // The car's map is lit by the hour (T-1107): no test's picture may depend on when it ran.
        ai.jarvis.app.car.holo.HoloLight.clock = null
        Snapshot.sendApplyNotifications()
    }

    override fun prepareTest(test: Any?) = Unit

    /**
     * Compose queues one task on the main looper to apply writes made outside a composition and
     * queues no other until it has run (`GlobalSnapshotManager.sent`). Robolectric resets the
     * looper after each test; a test that ends with that task queued loses it, the coroutine that
     * applies writes never wakes again in this JVM, and every later screen test that waits on a
     * write from outside its composition spins 60 s to AppNotIdleException: all of
     * CarSetupScreenshotTest and ApprovalScreenshotTest in the full suite (T-1303, T-1291).
     * So the looper is run here, and only when that task is queued: running it after every test
     * ran every class's leftover work and moved two goldens (T-1291).
     *
     * Not covered: a Compose version that renames the field fails here by name, on purpose.
     */
    override fun afterTest(method: Method?) {
        if (!applyQueued.get()) return
        System.err.println("TestJarvisApp: ran the Compose apply left queued by ${method?.declaringClass?.simpleName}.${method?.name}")
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
    }

    private val applyQueued: java.util.concurrent.atomic.AtomicBoolean by lazy {
        Class.forName("androidx.compose.ui.platform.GlobalSnapshotManager")
            .getDeclaredField("sent").apply { isAccessible = true }
            .get(null) as java.util.concurrent.atomic.AtomicBoolean
    }
}
