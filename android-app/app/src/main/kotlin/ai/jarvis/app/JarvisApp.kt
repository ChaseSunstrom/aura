package ai.jarvis.app

import ai.jarvis.app.crash.JarvisCrashHandler
import android.app.Activity
import android.app.ActivityManager
import android.app.Application
import android.app.ApplicationExitInfo
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Bundle
import android.util.Log

/**
 * Application entry point. Two jobs.
 *
 * First, install the crash handler — before anything else, including the
 * notification channels, because the crashes most worth catching are the ones
 * during startup and a handler installed after them catches nothing.
 *
 * Second, make sure the notification channels exist before anything tries to
 * post to them, in particular the approval channel: a Tier-3 request that
 * cannot be shown is a request that gets denied.
 *
 * Channel ids are public constants: the automation module posts its foreground
 * service notification and its alerts through the same three channels, so the
 * user gets one coherent set of switches in system settings instead of a pile
 * of near-duplicates.
 *
 * Open only for the unit tests' `TestJarvisApp`, which Robolectric runs in its place.
 */
open class JarvisApp : Application() {

    /**
     * The app's own activities started (T-0967). The home hides the status bar, and the resting
     * island would hide with it; counted here, before any activity exists, because the island's
     * service starts from the home after the home has already started. Started, not resumed, so
     * moving between two of them never passes through none.
     */
    var ownScreens = 0
        private set

    /** Told when [ownScreens] changes; the one resting island sets it while it runs. */
    var onOwnScreens: (() -> Unit)? = null

    override fun onCreate() {
        // FIRST. Anything above this line is a crash nobody can diagnose.
        JarvisCrashHandler.install(this)
        super.onCreate()
        // A killed process left the home-screen widget on its last look (T-1292).
        runCatching { ai.jarvis.app.assist.OrbWidget.redraw(this) }
        // The car's map is lit by the hour (T-1107).
        ai.jarvis.app.car.holo.HoloLight.clock = System::currentTimeMillis
        // Flat unless the owner asked for the 3D drive back (T-1252).
        ai.jarvis.app.car.holo.HoloGround.threeD = ai.jarvis.app.config.JarvisConfig(this).carMap3d
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityStarted(a: Activity) { ownScreens++; onOwnScreens?.invoke() }
            override fun onActivityStopped(a: Activity) { ownScreens = maxOf(0, ownScreens - 1); onOwnScreens?.invoke() }
            override fun onActivityCreated(a: Activity, saved: Bundle?) = Unit
            override fun onActivityResumed(a: Activity) = Unit
            override fun onActivityPaused(a: Activity) = Unit
            override fun onActivitySaveInstanceState(a: Activity, out: Bundle) = Unit
            override fun onActivityDestroyed(a: Activity) = Unit
        })
        // A run Android killed left no crash line; this says why it ended (T-0919).
        lastExit(this)?.let { line ->
            Log.i(TAG, line)
            ai.jarvis.app.car.log.SessionLog.shared.info("JarvisApp", line)
        }
        // The console's faces on every View screen, before the first one is built.
        ai.jarvis.app.ui.JarvisUi.loadFaces(this)
        createChannels()
        // The persona's accent from the first frame, not only once a conversation says it.
        ai.jarvis.app.ui.theme.PersonaAccent.load(this)
        // The car (ADR-0047): its theme and the one-off setting migration
        // Headway ran at start. Its crash log is not installed -- Jarvis's
        // handler above is the process's one default handler.
        ai.jarvis.app.car.theme.HeadwayTheme.load(this)
        ai.jarvis.app.car.HeadwaySettings.migrateAppSource(this) { note ->
            ai.jarvis.app.car.log.SessionLog.shared.info("JarvisApp", note)
        }
        // The hourly update check, for the phone nobody opens (ADR-0050).
        ai.jarvis.app.update.UpdateWorker.schedule(this)
        // The house's map packs, copied on an unmetered network for the car's ground (T-1017).
        ai.jarvis.app.car.holo.MapPackWorker.schedule(this)
        // Jarvis's own link home, a scanned WireGuard tunnel, when the person chose one
        // (the car needs it; see TailnetLink). Off the main thread: the start reads its
        // state, and one that fails is tried again until it comes up (T-0913). None
        // chosen, the library is never loaded.
        val tailnetConfig = ai.jarvis.app.config.JarvisConfig(this)
        val tunnelDir = ai.jarvis.app.tailnet.TailnetLink.stateDir(this)
        if (tailnetConfig.wireGuardConf.isNotBlank()) {
            Thread({ ai.jarvis.app.tailnet.TailnetLink.keepUp(tunnelDir, { tailnetConfig.wireGuardConf }) }, "jarvis-tunnel").start()
        }
        // Each new network starts it again, so its Endpoint is resolved there (T-0738); with
        // no tunnel up that does nothing, and one saved later in Settings is covered too.
        ai.jarvis.app.tailnet.TailnetLink.watchNetworks(this, tunnelDir)
    }

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm == null) {
            Log.w(TAG, "no NotificationManager; channels not created")
            return
        }

        val approval = NotificationChannel(
            CHANNEL_APPROVAL,
            getString(R.string.notification_channel_approval),
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = getString(R.string.notification_channel_approval_desc)
            enableVibration(true)
            setShowBadge(true)
            // Parameters can name people and places; keep them off the lock
            // screen until the user is past the keyguard.
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            // Only takes effect once the user grants notification-policy
            // access. Harmless without it, and correct with it: a consent
            // request that Do Not Disturb swallows is a silent denial.
            setBypassDnd(true)
        }

        val service = NotificationChannel(
            CHANNEL_SERVICE,
            getString(R.string.notification_channel_service),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notification_channel_service_desc)
            setShowBadge(false)
            enableVibration(false)
            setSound(null, null)
        }

        val alerts = NotificationChannel(
            CHANNEL_ALERTS,
            getString(R.string.notification_channel_alerts),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = getString(R.string.notification_channel_alerts_desc)
            setShowBadge(true)
        }

        nm.createNotificationChannels(listOf(approval, service, alerts))
    }

    companion object {
        private const val TAG = "JarvisApp"

        /** Enough to reach past a few renderer ends to the app's own. */
        private const val EXITS_READ = 8

        /**
         * Why Android ended this app's last run, by the reason's own name, or null on a first
         * run. Two deaths in the owner's log had no crash line and no kill line (T-0919): a kill
         * by the system reaches no uncaught-exception handler, so only this record says which
         * (PACKAGE_STATE_CHANGE is a launcher-alias switch, LOW_MEMORY or FREEZER the system).
         * The app's own process, not the newest record: the console's web view renderer ends on
         * its own ("isolated not needed") and stood in for the app's end in the owner's log
         * (2026-11-10). Says nothing about runs before the last one.
         */
        internal fun lastExit(context: android.content.Context): String? {
            val am = context.getSystemService(ActivityManager::class.java) ?: return null
            val exits = runCatching { am.getHistoricalProcessExitReasons(context.packageName, 0, EXITS_READ) }
                .getOrNull().orEmpty()
            val last = exits.firstOrNull { it.processName == context.packageName } ?: exits.firstOrNull() ?: return null
            val reason = ApplicationExitInfo::class.java.fields.firstOrNull {
                it.name.startsWith("REASON_") && it.type == Int::class.javaPrimitiveType && it.getInt(null) == last.reason
            }?.name?.removePrefix("REASON_") ?: "reason ${last.reason}"
            return "the last run ended: $reason (status ${last.status}, importance ${last.importance}" +
                "${last.description?.let { ", $it" } ?: ""}) at ${java.time.Instant.ofEpochMilli(last.timestamp)}"
        }

        /** Tier-3 consent requests. High importance, bypasses DND when allowed. */
        const val CHANNEL_APPROVAL = "jarvis_approval"

        /** The automation module's foreground-service notification. */
        const val CHANNEL_SERVICE = "jarvis_service"

        /** Anything Jarvis posts on the user's behalf. */
        const val CHANNEL_ALERTS = "jarvis_alerts"
    }
}
