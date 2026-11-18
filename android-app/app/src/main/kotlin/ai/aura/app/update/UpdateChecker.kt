package ai.aura.app.update

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.BuildConfig
import ai.aura.app.config.JarvisConfig
import ai.aura.app.tailnet.JarvisHttp
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Fetching the app's own updates from the project's GitLab releases (the
 * pipeline in .gitlab/ci/android-apk.yml publishes every build there).
 *
 * The app is sideloaded, so nothing else is going to update it. CI publishes
 * every build as a prerelease and every hand-published tag as a full release;
 * [ReleaseFeed] decides which of those is worth offering and this brings it
 * down and hands it to the platform installer.
 *
 * **Who confirms.** By default the system's own "do you want to install this
 * update?" prompt, through [PackageInstaller]. An [install] with `unattended` asks
 * the platform to skip it (`USER_ACTION_NOT_REQUIRED`, API 31+), which it grants
 * only to the app that installed the current version and only for a package signed
 * with the same key -- so a stolen token alone cannot replace the app, a stolen
 * signing key can. That trade was the owner's to make and was made (ADR-0050, the
 * judgement this paragraph used to hold the other way); [AutoUpdate.act] uses it for
 * production builds only, and a person can turn it off.
 *
 * `PackageInstaller` rather than an `ACTION_VIEW` on a `content://` URI: the
 * session API needs no FileProvider, never writes the APK anywhere another app
 * can read it, and reports a real result instead of leaving the app guessing
 * whether the installer ever appeared.
 */
class UpdateChecker(
    private val context: Context,
    private val repo: String = DEFAULT_REPO,
) {

    sealed interface Result {
        /** [check] found an installable update. Nothing has been downloaded yet. */
        data class Offered(val update: ReleaseFeed.Update) : Result

        /**
         * [install] downloaded it and committed the session. The system's
         * install prompt comes next, raised by [InstallResultReceiver] when the
         * platform asks for it.
         *
         * Split from [Offered] on purpose. The two meant different things and
         * shared a name, which is how "Ready to install — confirm the system
         * prompt" came to be printed by a build where no prompt could appear.
         */
        data class Handed(val update: ReleaseFeed.Update) : Result

        /** Checked successfully; this is already the newest build. */
        data object UpToDate : Result

        /** Could not check or could not install. [message] is for the user. */
        data class Failed(val message: String) : Result
    }

    private val http = JarvisHttp.builder()
        .callTimeout(60, TimeUnit.SECONDS)
        .build()

    /**
     * The APK's own client: no deadline on the whole call, only on silence. The
     * shared one's 60 s `callTimeout` ended every download that took longer -- a
     * 127 MB APK over the car's hotspot or a slow tunnel never finished (the owner,
     * 2026-09-25: "it times out and doesnt complete, instead of just continuing").
     * A stall past [STALL_S] is a dropped read, and [install] resumes it.
     */
    private val download = JarvisHttp.builder()
        .connectTimeout(STALL_S, TimeUnit.SECONDS)
        .readTimeout(STALL_S, TimeUnit.SECONDS)
        .build()

    /** The paired adapter: its base URL (empty when the phone has no Hermes link) and key. */
    private val config by lazy { JarvisConfig(context) }
    private val adapterBase get() = config.hermesUrl.trim().trimEnd('/')

    /**
     * Ask GitLab and Jarvis what is available, and offer the newer. Blocking —
     * call it off the main thread.
     *
     * A hand-built APK is put in the paired adapter's artifact store (the owner,
     * 2026-11-08: "how can we have the download behaviour work with either one",
     * T-0735), so either source can carry the next build. One that does not answer
     * offers nothing, and the other still can; GitLab's failure is reported only
     * when Jarvis could not say either.
     *
     * [installedVersionCode] is what is running now; see
     * `PackageInfo.longVersionCode`.
     */
    fun check(installedVersionCode: Long, allowPrerelease: Boolean): Result {
        val gitlab = checkGitlab(installedVersionCode, allowPrerelease)
        val stored = checkStored(installedVersionCode)
        val update = ReleaseFeed.newest((gitlab as? Result.Offered)?.update, (stored as? Result.Offered)?.update)
        return when {
            update != null -> Result.Offered(update)
            stored == Result.UpToDate -> Result.UpToDate
            else -> gitlab
        }
    }

    /** The newest APK in the paired adapter's artifact store, or null when there is no adapter. */
    private fun checkStored(installedVersionCode: Long): Result? {
        val base = adapterBase
        if (base.isEmpty()) return null
        val request = Request.Builder()
            .url("$base/api/artifacts?kind=${ReleaseFeed.APK_MIME}")
            .header("Authorization", "Bearer ${config.hermesKey}")
            .build()
        val body = try {
            http.newCall(request).execute().use { response -> if (response.isSuccessful) response.body?.string() else null }
        } catch (t: Throwable) {
            Log.w(TAG, "jarvis update check failed", t)
            null
        } ?: return Result.Failed("${ActiveTheme.name} did not answer.")
        return ReleaseFeed.pickStored(body, base, installedVersionCode)?.let { Result.Offered(it) } ?: Result.UpToDate
    }

    private fun checkGitlab(installedVersionCode: Long, allowPrerelease: Boolean): Result {
        val url = releasesUrl(repo)
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .build()
        val body = try {
            http.newCall(request).execute().use { response ->
                if (response.code == 404) {
                    // A private repo answers 404 rather than 403 to an
                    // unauthenticated client, so say the useful thing instead
                    // of "not found", which sounds like a typo in the name.
                    return Result.Failed(
                        "GitLab returned nothing for $repo. If the project is " +
                            "private, releases cannot be checked without a token."
                    )
                }
                if (!response.isSuccessful) {
                    return Result.Failed("GitLab returned HTTP ${response.code}.")
                }
                response.body?.string()
            }
        } catch (t: Throwable) {
            Log.w(TAG, "update check failed", t)
            return Result.Failed("Could not reach GitLab: ${t.message ?: "no connection"}")
        } ?: return Result.Failed("GitLab returned an empty response.")

        val update = ReleaseFeed.pick(body, installedVersionCode, allowPrerelease)
            ?: return Result.UpToDate
        return Result.Offered(update)
    }

    /**
     * Download [update] and hand it to the platform installer. Blocking.
     *
     * Streams straight into the install session rather than to a file: the APK
     * never lands anywhere on disk that another app could swap underneath us
     * between the download finishing and the install starting.
     */
    fun install(
        update: ReleaseFeed.Update,
        unattended: Boolean = false,
        /** Bytes written and the total (0 when the server does not say), at most once per percent. */
        onProgress: (done: Long, total: Long) -> Unit = { _, _ -> },
    ): Result {
        // Re-checked here and not only when the feed was parsed. This is the
        // call that actually reaches out, and it is the one place where being
        // wrong means fetching an attacker's bytes.
        val base = adapterBase
        if (!ReleaseFeed.isAllowedDownload(update.downloadUrl, base)) {
            return Result.Failed("Refusing to download from ${update.downloadUrl}")
        }

        // Checked BEFORE the download, not after. Without "install unknown
        // apps" the commit below succeeds and the prompt is refused — sixty
        // megabytes spent to arrive at a silence. `REQUEST_INSTALL_PACKAGES` is
        // in the manifest but has been a per-app user grant since Android 8,
        // which is the same declared-but-not-held shape as the rest of them.
        if (!canInstallPackages()) {
            return Result.Failed(
                "Android will not let ${ActiveTheme.name} install an app until you allow it: " +
                    "Settings → Apps → ${ActiveTheme.appLabel} → Install unknown apps. Tap " +
                    "INSTALL PERMISSION below."
            )
        }

        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(
            PackageInstaller.SessionParams.MODE_FULL_INSTALL
        )
        if (update.sizeBytes > 0) params.setSize(update.sizeBytes)
        // A request the platform may still refuse: then the session asks for the prompt
        // (STATUS_PENDING_USER_ACTION), and InstallResultReceiver shows it or notifies.
        if (unattended && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }

        var sessionId = -1
        // Set immediately after commit() returns, and read only by the
        // `finally`. A session that was committed belongs to the platform now;
        // abandoning it would cancel the very install this method just asked
        // for. Everything else must be handed back.
        var committed = false
        try {
            sessionId = installer.createSession(params)
            installer.openSession(sessionId).use { session ->
                // Written so far, and failures in a row with nothing gained: a read
                // that drops resumes from here with a Range request, and gives up only
                // after MAX_STALLS attempts that each moved no bytes.
                var written = 0L
                var total = update.sizeBytes.coerceAtLeast(0)
                var stalls = 0
                var shown = -1L
                val report = { done: Long ->
                    val step = if (total > 0) done * 100 / total else done / PROGRESS_STEP_BYTES
                    if (step != shown) {
                        shown = step
                        UpdateNotification.downloading(context, update.versionName, done, total)
                        onProgress(done, total)
                    }
                }
                while (true) {
                    val before = written
                    try {
                        val request = Request.Builder().url(update.downloadUrl)
                            .apply { if (written > 0) header("Range", "bytes=$written-") }
                            // A build from Jarvis's store: the adapter wants the phone's key.
                            .apply { if (base.isNotEmpty() && update.downloadUrl.startsWith("$base/")) header("Authorization", "Bearer ${config.hermesKey}") }
                            .build()
                        download.newCall(request).execute().use { response ->
                            val at = resumeOffset(response.code, written)
                                ?: return Result.Failed("Download failed: HTTP ${response.code}")
                            val source = response.body?.byteStream()
                                ?: return Result.Failed("Download returned no data.")
                            if (at == 0L) total = response.body?.contentLength()?.takeIf { it > 0 } ?: total
                            written = at
                            session.openWrite(WRITE_NAME, at, -1).use { sink ->
                                val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                                while (true) {
                                    val n = source.read(buffer)
                                    if (n < 0) break
                                    sink.write(buffer, 0, n)
                                    written += n
                                    report(written)
                                }
                                session.fsync(sink)
                            }
                        }
                        break
                    } catch (e: java.io.IOException) {
                        stalls = if (written > before) 1 else stalls + 1
                        if (stalls > MAX_STALLS) throw e
                        Log.i(TAG, "download dropped at $written of $total bytes; resuming ($stalls)", e)
                        Thread.sleep(RESUME_PAUSE_MS * stalls)
                    }
                }
                session.commit(confirmationIntent().intentSender)
                committed = true
            }
            // Committed, not installed. commit() shows nothing by itself: it
            // sends STATUS_PENDING_USER_ACTION to the IntentSender above, and
            // InstallResultReceiver starts the system prompt that carries.
            return Result.Handed(update)
        } catch (t: Throwable) {
            Log.w(TAG, "install failed", t)
            return Result.Failed(installFailureMessage(t))
        } finally {
            // A `finally`, NOT the catch — which is where this used to live, and
            // which the two `return Result.Failed(...)` above walk straight past.
            //
            // `use` is inline, so those returns are non-local returns from
            // `install` itself: they unwind the sessions and close the streams,
            // and they never enter the catch. So the two most ordinary failures
            // there are — the server answering with anything but 200, and a
            // response with no body — each leaked a created, half-written
            // session, silently.
            //
            // That is not a slow leak. PackageInstaller caps ACTIVE sessions per
            // app (50 on current Android), and an abandoned-but-unclosed session
            // stays active for MAX_ACTIVE_SESSIONS' own lifetime — days. Around
            // fifty taps of CHECK FOR UPDATES against a flaky network and
            // `createSession` starts throwing IllegalStateException("Too many
            // active sessions"), so the updater is wedged for days by a failure
            // that has nothing to do with the real problem — which is precisely
            // the outcome the comment that used to sit in the catch promised to
            // prevent.
            if (sessionId >= 0 && !committed) {
                runCatching { installer.abandonSession(sessionId) }
            }
            UpdateNotification.downloaded(context)
        }
    }

    /**
     * Where the installer reports its verdict — **and raises the prompt**.
     *
     * This is not a logging convenience, which is what the comment here used to
     * claim and what made the missing receiver look harmless. `commit()` shows
     * nothing. It sends [PackageInstaller.STATUS_PENDING_USER_ACTION] here,
     * with the system's install activity in `Intent.EXTRA_INTENT`, and if
     * nobody starts that activity there is no prompt and no install — which is
     * exactly what this app did until [InstallResultReceiver] existed.
     *
     * Addressed by component rather than by action + package: an explicit
     * broadcast is never subject to the Android 8 implicit-broadcast
     * restrictions, and it cannot be intercepted or forged.
     */
    private fun confirmationIntent(): PendingIntent = PendingIntent.getBroadcast(
        context,
        0,
        Intent(context, InstallResultReceiver::class.java).setAction(ACTION_INSTALL_RESULT),
        // MUTABLE because the installer fills in its own extras. Explicit, so
        // the Android 14 ban on mutable implicit PendingIntents does not apply.
        PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /**
     * Whether the user has allowed Jarvis to install apps.
     *
     * `REQUEST_INSTALL_PACKAGES` is declared in the manifest and, since Android
     * 8, granted per-app by the user in "Install unknown apps". Declaring it is
     * not holding it.
     */
    fun canInstallPackages(): Boolean = try {
        context.packageManager.canRequestPackageInstalls()
    } catch (t: Throwable) {
        Log.w(TAG, "install-permission check failed", t)
        false
    }

    private fun installFailureMessage(t: Throwable): String {
        val detail = t.message ?: t::class.java.simpleName
        return if (detail.contains("INSTALL_FAILED_UPDATE_INCOMPATIBLE", ignoreCase = true) ||
            detail.contains("signatures do not match", ignoreCase = true)
        ) {
            // The one failure with a specific, non-obvious remedy.
            "This build was signed with a different key than the installed app, " +
                "so Android will not update in place. Uninstall ${ActiveTheme.name} first, " +
                "then install this build."
        } else {
            "Install failed: $detail"
        }
    }

    companion object {
        private const val TAG = "JarvisUpdate"
        private const val WRITE_NAME = "jarvis.apk"
        const val PAGE_SIZE = 20

        const val ACTION_INSTALL_RESULT = "ai.aura.app.INSTALL_RESULT"

        /** The GitLab project path; the pipeline that builds the APK lives here. The repo names no
         *  account: CI builds with its own project's path. */
        const val DEFAULT_REPO = BuildConfig.UPDATE_REPO
        /** The house's GitLab: CI builds with its own server's URL (T-0841). */
        const val GITLAB_BASE = BuildConfig.GITLAB_BASE

        /** Seconds of silence (connect or read) that count as a dropped download. */
        private const val STALL_S = 45L
        /** Resumes in a row that moved no bytes before the download gives up. */
        private const val MAX_STALLS = 6
        private const val RESUME_PAUSE_MS = 2_000L
        /** Progress granularity when the server does not say how big the APK is. */
        private const val PROGRESS_STEP_BYTES = 1L shl 20

        /**
         * Where a response to a request for bytes from [written] onwards is written
         * in the session: [written] for a 206 (the rest of it), 0 for a 200 (the
         * server ignored the range and sent it whole), null for anything else.
         */
        fun resumeOffset(code: Int, written: Long): Long? = when {
            code == 206 && written > 0 -> written
            code == 200 -> 0L
            else -> null
        }

        /** `GET /api/v4/projects/<url-encoded path>/releases` on the project's GitLab. */
        fun releasesUrl(repo: String): String =
            "$GITLAB_BASE/api/v4/projects/${repo.replace("/", "%2F")}/releases?per_page=$PAGE_SIZE"
    }
}
