package ai.jarvis.app.update

import ai.jarvis.app.config.JarvisConfig
import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.Log
import org.json.JSONObject

/**
 * When an automatic update check is due, and the last one's result, remembered.
 *
 * The app is sideloaded, so nothing else updates it — and until now nothing
 * checked unless the user opened Settings and pressed CHECK FOR UPDATES, which
 * is a button most people press once. A box you own and do not watch is exactly
 * the one that should notice a new build on its own.
 *
 * **A production build installs itself; a test build never does** (ADR-0050, the
 * owner, 2026-09-24: "updating the APK from it automatically … it should always be
 * the latest"). [act] hands an offer to [UpdateChecker.install] unattended when
 * [shouldInstallUnattended] says so -- the "Install updates automatically" setting
 * is on and the build is not a prerelease -- and otherwise only surfaces it, as
 * before. Unattended is a request, not a bypass: the APK still comes only from an
 * allowed host ([ReleaseFeed]), Android still refuses one not signed with this app's
 * key, and where the platform still wants a tap (below API 31, or the first update
 * after a browser install) its prompt arrives as a notification.
 *
 * ## The split, and why
 *
 * [isCheckDue] is pure arithmetic over four inputs — the setting, connectivity,
 * the last-check time and the clock — so the policy is a table a JVM test and
 * `android-app/tools/auto_update_test.py` can both pin, with no Android in
 * sight. Everything that touches SharedPreferences or the network is in the
 * thin wrappers below and stays untested by design; the decision they wrap is
 * the part that can be wrong in an interesting way.
 */
object AutoUpdate {

    /**
     * At most one automatic check an hour.
     *
     * A check is a round trip to GitLab plus JSON parsing; onResume can fire
     * many times an hour (every return from Settings, every unlock), so without
     * a floor the "automatic" check would run on every one of them. An hour,
     * not the day it was: the pipeline publishes every green build of the branch
     * production runs, and the owner wants the phone on the latest one (ADR-0050).
     * [UpdateWorker] runs the same gate in the background at the same period.
     */
    const val CHECK_INTERVAL_MS = 60L * 60L * 1000L

    /**
     * Whether an automatic check should run right now. Pure.
     *
     * The four reasons not to, in order:
     *
     *  * the user turned automatic checks off — their call, and it beats
     *    everything else;
     *  * the device is offline — a check would only fail, and failing counts as
     *    a check (see [shouldCheckNow]), so running one offline would burn the
     *    daily slot on a guaranteed miss;
     *  * it was checked less than [CHECK_INTERVAL_MS] ago.
     *
     * First run ([lastCheckMs] <= 0) is due: a fresh install should learn about
     * a newer build without waiting a day first.
     *
     * A [lastCheckMs] in the FUTURE — the clock was set forward once and then
     * corrected, or a backup restored a stamp from another device — is also
     * due. Trusting a future stamp would lock checks out until real time caught
     * up to it, which could be years; treating "elapsed is negative" as due
     * self-heals on the next check, which rewrites the stamp to now.
     */
    fun isCheckDue(
        enabled: Boolean,
        online: Boolean,
        lastCheckMs: Long,
        nowMs: Long,
    ): Boolean {
        if (!enabled) return false
        if (!online) return false
        if (lastCheckMs <= 0L) return true
        val elapsed = nowMs - lastCheckMs
        if (elapsed < 0L) return true
        return elapsed >= CHECK_INTERVAL_MS
    }

    /**
     * Whether an offer installs itself rather than waiting for INSTALL. Pure.
     *
     * Only with the setting on, and never a prerelease: a test build is every
     * commit of any branch, which "include test builds" lets a person *choose*
     * to run -- it must not arrive on its own.
     */
    fun shouldInstallUnattended(autoInstall: Boolean, update: ReleaseFeed.Update): Boolean =
        autoInstall && !update.prerelease

    // --- the thin Android wrappers -----------------------------------------

    /**
     * What a check that found [update] does next. Off the main thread: an unattended
     * install downloads the APK here. Otherwise it is remembered and posted, so the
     * notice and the notification carry it until INSTALL or LATER.
     */
    fun act(context: Context, config: JarvisConfig, update: ReleaseFeed.Update) {
        rememberOffer(context, update)
        if (shouldInstallUnattended(config.autoInstallUpdates, update)) {
            val result = UpdateChecker(context.applicationContext).install(update, unattended = true)
            if (result is UpdateChecker.Result.Handed) return
            Log.w(TAG, "unattended install did not start: $result; offering it instead")
        }
        UpdateNotification.post(context.applicationContext, update)
    }

    /** This build's versionCode, or 0 if the platform will not say. */
    fun installedVersionCode(context: Context): Long =
        runCatching { context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode }
            .getOrDefault(0L)

    /**
     * Decide, and record the attempt if it is due. Call off the main thread —
     * it reads connectivity and writes SharedPreferences.
     *
     * The stamp is written when the check is *decided*, not when it *succeeds*:
     * a server that is down must not be retried on every resume for the rest of
     * the day, which is what "at most once per 24 hours" has to mean or it means
     * nothing. An offline device writes no stamp (the check was not run), so it
     * checks as soon as it is back online.
     */
    fun shouldCheckNow(
        context: Context,
        config: JarvisConfig,
        nowMs: Long = System.currentTimeMillis(),
    ): Boolean {
        val prefs = prefs(context)
        val due = isCheckDue(
            enabled = config.autoUpdateEnabled,
            online = isOnline(context),
            lastCheckMs = prefs.getLong(KEY_LAST_CHECK, 0L),
            nowMs = nowMs,
        )
        if (due) prefs.edit().putLong(KEY_LAST_CHECK, nowMs).apply()
        return due
    }

    /**
     * Keep the offer a check just found, so the home screen can re-show its
     * notice on later resumes without checking again — the daily check finds it
     * once, the notice reappears until it is installed or dismissed.
     */
    fun rememberOffer(context: Context, update: ReleaseFeed.Update) {
        prefs(context).edit().putString(KEY_OFFER, toJson(update).toString()).apply()
    }

    /**
     * The stored offer, if it is still worth showing: newer than what is
     * installed, and not one the user tapped LATER on.
     *
     * The download URL is re-validated here even though [UpdateChecker.install]
     * validates it again before fetching — a value read back from a file is an
     * untrusted value, and the cheap check keeps a tampered pref from ever
     * reaching the UI as an installable offer.
     */
    fun pendingOffer(context: Context, installedVersionCode: Long): ReleaseFeed.Update? {
        val prefs = prefs(context)
        val update = prefs.getString(KEY_OFFER, null)?.let { fromJson(it) } ?: return null
        if (update.versionCode <= installedVersionCode) return null
        if (isDismissed(context, update.versionCode)) return null
        if (!ReleaseFeed.isAllowedDownload(update.downloadUrl, JarvisConfig(context).hermesUrl)) return null
        return update
    }

    /** Remember that the user does not want to be nagged about [versionCode]. */
    fun dismiss(context: Context, versionCode: Long) {
        prefs(context).edit().putLong(KEY_DISMISSED_CODE, versionCode).apply()
    }

    /**
     * True once the user has dismissed this build (or any newer-numbered one).
     * A later, higher versionCode is above the mark and shows again.
     */
    fun isDismissed(context: Context, versionCode: Long): Boolean =
        versionCode <= prefs(context).getLong(KEY_DISMISSED_CODE, 0L)

    private fun isOnline(context: Context): Boolean = try {
        val cm = context.applicationContext.getSystemService(ConnectivityManager::class.java)
        val active = cm?.activeNetwork
        val caps = active?.let { cm.getNetworkCapabilities(it) }
        caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
    } catch (t: Throwable) {
        // A device that cannot answer "am I online?" is treated as offline: the
        // failure mode is a skipped check, never a crash on the resume path.
        Log.w(TAG, "connectivity check failed", t)
        false
    }

    private fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    private fun toJson(u: ReleaseFeed.Update): JSONObject = JSONObject()
        .put("versionCode", u.versionCode)
        .put("versionName", u.versionName)
        .put("tag", u.tag)
        .put("downloadUrl", u.downloadUrl)
        .put("sizeBytes", u.sizeBytes)
        .put("notes", u.notes)
        .put("prerelease", u.prerelease)

    private fun fromJson(text: String): ReleaseFeed.Update? = try {
        val o = JSONObject(text)
        val tag = o.optString("tag")
        val url = o.optString("downloadUrl")
        val code = o.optLong("versionCode", 0L)
        if (tag.isEmpty() || url.isEmpty() || code <= 0L) null
        else ReleaseFeed.Update(
            versionCode = code,
            versionName = o.optString("versionName").ifEmpty { tag },
            tag = tag,
            downloadUrl = url,
            sizeBytes = o.optLong("sizeBytes", 0L),
            notes = o.optString("notes"),
            prerelease = o.optBoolean("prerelease", false),
        )
    } catch (t: Throwable) {
        Log.w(TAG, "could not read the stored update offer", t)
        null
    }

    private const val TAG = "JarvisUpdate"

    /** The updater's own bookkeeping, kept out of [JarvisConfig]'s file. */
    private const val FILE = "jarvis_update"

    /** Epoch millis of the last automatic check that was decided (see [shouldCheckNow]). */
    const val KEY_LAST_CHECK = "update.last_check"

    /** The highest versionCode the user has said LATER to. */
    const val KEY_DISMISSED_CODE = "update.dismissed_code"

    /** The last offer a check found, as JSON, so the notice survives a resume. */
    const val KEY_OFFER = "update.offer"
}
