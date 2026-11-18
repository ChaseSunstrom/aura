package ai.aura.app.car.log

import ai.aura.app.config.JarvisConfig
import ai.aura.app.tailnet.JarvisHttp
import android.content.Context
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Each drive's session log, sent to the owner's own Jarvis as an artifact (the
 * adapter's `/api/artifacts`, kept [TTL_S]) when the session ends. The car's
 * failures -- silence on the car's speakers, a panel that cannot reach Jarvis
 * (the owner, 2026-09-25, build 1441) -- were each diagnosed from a log the
 * owner had to find and send by hand, and most drives left none.
 *
 * Only to the paired Hermes adapter, with its key; nothing leaves when the phone
 * has no Hermes link. A failed upload is tried again by [sendPending]; the file
 * stays on the phone under Diagnostics either way.
 */
object CarLogUpload {
    private const val TAG = "JarvisCarLogUpload"
    private const val TTL_S = 7 * 24 * 3600
    private const val PREFS = "car_log_upload"
    private const val KEY_SENT = "sent"
    private val sending = AtomicBoolean(false)

    fun send(context: Context, file: File) {
        val app = context.applicationContext
        Thread({ upload(app, file) }, "car-log-upload").start()
    }

    /**
     * The drive logs that have not reached Jarvis yet, sent now; MainActivity calls it each
     * time the app comes to the front (T-0742). The upload at the end of a drive runs from the
     * car, where a VPN app can keep Jarvis off every network (T-0713), and a failed one was
     * dropped: the adapter's store held no car log from a week of drives while the owner
     * reported the car dropping. Does not cover a log older than the store keeps ([TTL_S]).
     */
    fun sendPending(context: Context) {
        val app = context.applicationContext
        val files = pending(SessionLog.shared.exports(app), sent(app), System.currentTimeMillis())
        // One pass at a time: the app coming to the front twice must not send a log twice.
        if (files.isEmpty() || !sending.compareAndSet(false, true)) return
        Thread({
            try {
                files.forEach { upload(app, it) }
            } finally {
                sending.set(false)
            }
        }, "car-log-upload").start()
    }

    /** Of [exports], the ones not in [sent] and young enough for the store to keep, oldest first. */
    internal fun pending(exports: List<File>, sent: Set<String>, nowMs: Long): List<File> =
        exports.filter { it.name !in sent && nowMs - it.lastModified() < TTL_S * 1000L }.sortedBy { it.lastModified() }

    internal fun sent(context: Context): Set<String> =
        prefs(context).getStringSet(KEY_SENT, emptySet()).orEmpty()

    /** Remembers [file] as sent; names whose file is gone (pruned) are let go. */
    internal fun markSent(context: Context, file: File) {
        val kept = sent(context).filterTo(mutableSetOf()) { File(file.parentFile, it).exists() }
        prefs(context).edit().putStringSet(KEY_SENT, kept + file.name).apply()
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /**
     * The upload of [file] to the adapter at [base]. The body is octet-stream, not text/plain:
     * through the console's relay SvelteKit's CSRF check answers 403 to a form-type POST
     * (text/plain is one) with no matching Origin, and refused every drive log for four days
     * (T-0916). The adapter files it by `x-artifact-mime`.
     */
    internal fun request(base: String, key: String, file: File): Request =
        Request.Builder().url("$base/api/artifacts")
            .header("Authorization", "Bearer $key")
            .header("x-artifact-mime", "text/plain")
            .header("x-artifact-title", "car log ${file.name}")
            .header("x-artifact-tool", "car-log")
            .header("x-artifact-ttl", TTL_S.toString())
            .post(file.asRequestBody("application/octet-stream".toMediaType()))
            .build()

    private fun upload(app: Context, file: File) {
        val config = JarvisConfig(app)
        val base = config.hermesUrl.trimEnd('/')
        if (base.isEmpty() || !file.isFile) return
        val request = request(base, config.hermesKey, file)
        runCatching {
            JarvisHttp.builder().callTimeout(60, TimeUnit.SECONDS).build().newCall(request).execute().use { r ->
                Log.i(TAG, "car log ${file.name}: HTTP ${r.code}")
                if (r.isSuccessful) markSent(app, file)
            }
        }.onFailure { Log.w(TAG, "car log ${file.name} not sent", it) }
    }
}
