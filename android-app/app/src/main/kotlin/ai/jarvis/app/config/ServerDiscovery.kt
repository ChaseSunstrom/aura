package ai.jarvis.app.config

import ai.jarvis.app.tailnet.JarvisHttp
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Which server is at a URL, asked over HTTP rather than by dialling a socket.
 *
 * The two jarvis-core-protocol kinds are still discovered the old way — each
 * client dials the candidates in [ServerEndpoint.candidates] and keeps the
 * one that authenticates — because that costs one failed connect at most and
 * needs no extra round trip. The adapter cannot be found that way: its `/ws`
 * accepts the same bearer on the upgrade and then speaks JSON-RPC, so a
 * pipeline client pointed at it "connects" and hears nothing it understands.
 * So the adapter is asked for by name first, and only when it does not answer
 * do the dialling clients take over.
 *
 * Blocking. Call from a worker thread; the answer is remembered by the caller
 * in `JarvisConfig.serverKind`.
 */
object ServerDiscovery {

    private const val TAG = "JarvisDiscovery"

    /**
     * The kind at [base], or null when nothing answered in a way that names a
     * server. Null is "keep believing what you believed", never "not Jarvis".
     *
     * Order: `/api/pair` with the bearer (the adapter's own payload), then
     * `/health` without one (so a wrong key still finds the adapter), then
     * `/api/config` (the console's public config or jarvis-core's refusal).
     */
    fun detect(
        base: String,
        token: String,
        clientFactory: () -> OkHttpClient = ::defaultClient,
    ): ServerKind? {
        val client = clientFactory()
        val pair = ServerEndpoint.pairProbeUrl(base) ?: return null
        fetch(client, pair, token)?.let { (status, body) ->
            ServerEndpoint.kindFromPairProbe(status, body)?.let { return it }
        }
        ServerEndpoint.healthUrl(base)?.let { url ->
            fetch(client, url, null)?.let { (status, body) ->
                ServerEndpoint.kindFromHealth(status, body)?.let { return it }
            }
        }
        ServerEndpoint.probeUrl(base)?.let { url ->
            fetch(client, url, null)?.let { (status, body) ->
                ServerEndpoint.kindFromProbe(status, body)?.let { return it }
            }
        }
        return null
    }

    private fun fetch(client: OkHttpClient, url: String, token: String?): Pair<Int, String>? {
        val request = Request.Builder().url(url).apply {
            if (!token.isNullOrEmpty()) header("Authorization", "Bearer $token")
        }.build()
        return try {
            client.newCall(request).execute().use { response ->
                response.code to response.body?.string().orEmpty()
            }
        } catch (t: Throwable) {
            Log.i(TAG, "probe failed: ${t.javaClass.simpleName}")
            null
        }
    }

    private fun defaultClient(): OkHttpClient = JarvisHttp.builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        // A probe that carries the bearer must not be moved to another host.
        .followRedirects(false)
        .followSslRedirects(false)
        .build()
}
