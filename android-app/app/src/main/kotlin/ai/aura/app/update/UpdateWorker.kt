package ai.aura.app.update

import ai.aura.app.config.JarvisConfig
import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * The update check when nobody opens the app (ADR-0050): hourly, on any network,
 * through the same gate and the same next step as the home screen's resume check.
 *
 * The phone is often a box in a drawer or a car mount -- the home screen's onResume
 * may not run for days -- and the owner wants it on the latest build regardless. So
 * the check is also a periodic job; [AutoUpdate.shouldCheckNow] keeps the two from
 * doubling up, and [AutoUpdate.act] decides whether the build installs itself.
 *
 * Does not retry a failed check sooner than the next period: a GitLab that is down
 * is down for everyone, and the next hour is soon enough.
 */
class UpdateWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        val app = applicationContext
        val config = JarvisConfig(app)
        if (!config.isConfigured) return Result.success()
        if (!AutoUpdate.shouldCheckNow(app, config)) return Result.success()
        val found = UpdateChecker(app).check(AutoUpdate.installedVersionCode(app), config.allowPrereleaseUpdates)
        if (found is UpdateChecker.Result.Offered) AutoUpdate.act(app, config, found.update)
        return Result.success()
    }

    companion object {
        private const val NAME = "jarvis-update-check"

        /**
         * Idempotent: UPDATE keeps the enqueue time, so every app start does not reset the hour,
         * and moves work queued under the class's old name (ai.jarvis.app, T-1531) to this
         * one: KEEP left it naming a class the app no longer has. Called from
         * JarvisApp.onCreate, so it must never throw: a WorkManager that is not initialised
         * (a Robolectric process has no startup provider; TimeTrigger guards the same way)
         * costs the background check, not the app's start.
         */
        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<UpdateWorker>(1, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            runCatching {
                WorkManager.getInstance(context)
                    .enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.UPDATE, request)
            }.onFailure { Log.w(TAG, "the hourly update check is not scheduled", it) }
        }

        private const val TAG = "UpdateWorker"
    }
}
