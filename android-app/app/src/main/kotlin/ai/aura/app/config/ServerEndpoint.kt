package ai.aura.app.config

import org.json.JSONObject

/**
 * Which of the two Jarvis servers is at the configured URL, and how to talk to it.
 *
 * There are two, they listen on different ports, and the natural thing for a
 * person to type into "Jarvis URL" is the one with the web page on it:
 *
 *  * **jarvis-core** (:8080) is the API. Its socket is `/api/websocket` and the
 *    client authenticates by sending `{"type":"auth","access_token":...}` in
 *    response to `auth_required`.
 *  * **jarvis-web** (:8199) is the console. Its socket is `/ws`, and it is a
 *    *relay* to jarvis-core. For the browser — which cannot set headers on a
 *    WebSocket — it injects the server-held admin token and swallows the
 *    handshake, which is why that token never reaches the page. For a client
 *    that presents its own bearer token, it passes the token through and
 *    jarvis-core validates it, so the handshake looks exactly like talking to
 *    jarvis-core directly.
 *
 * That second mode is what makes one URL enough. The app presents its token on
 * the upgrade, so the two kinds now differ ONLY in the path, and pointing the
 * app at the console works for voice as well as for the management page.
 *
 * Before this the app assumed jarvis-core unconditionally, which broke in two
 * independent ways against the console URL: it dialled a path that is not
 * there, and — even had it connected — it only started a turn inside its
 * `auth_ok` branch, which the relay was eating. The symptom was that voice
 * worked in the management WebView (a web page talking to its own relay) and
 * nowhere else, with an inert orb and no error, because a pipeline that never
 * reaches LISTENING gates the microphone off.
 *
 * The kind is still *discovered* rather than assumed, because the path differs.
 * This file is the pure half — no Android, no network — so the rule can be
 * tested on the JVM and mirrored in `android-app/tools/server_endpoint_test.py`.
 */
enum class ServerKind(
    /** Where the WebSocket lives, appended to the base URL. */
    val wsPath: String,
) {
    /** jarvis-core, spoken to directly. */
    CORE("/api/websocket"),

    /**
     * jarvis-web's relay.
     *
     * It used to authenticate on the client's behalf unconditionally, which is
     * why this end once had to skip the handshake here. It no longer does: a
     * client that presents a bearer token is passed straight through to
     * jarvis-core, which validates it. So the handshake is now the same on both
     * kinds and only the path differs — which is the whole point, because it
     * means one URL works whichever server is behind it.
     */
    RELAY("/ws"),

    /**
     * The jarvis-adapter in front of Hermes (`docs/hermes-hud-protocol.md`).
     *
     * A third server with a third protocol: JSON-RPC 2.0 over `/ws`, with the
     * bearer key on the upgrade, and no Home-Assistant handshake at all. It
     * shares the relay's PATH and nothing else, which is why it is not
     * discovered by dialling — a client that speaks `assist_pipeline/run` at
     * it gets JSON-RPC errors back, not a refusal — but by an HTTP probe that
     * only the adapter answers (`GET /api/pair` → `kind: "hermes-adapter"`).
     * See [ServerEndpoint.kindFromPairProbe].
     *
     * Only the assistant path goes here: the voice turn, the HUD events, the
     * goals and the approvals. The device command channel and the companion
     * voice client speak the jarvis-core protocol and are never offered this
     * kind as a candidate — [ServerEndpoint.candidates] leaves it out.
     */
    HERMES("/ws"),
}

object ServerEndpoint {

    /**
     * The unauthenticated endpoint whose answer tells the two servers apart.
     *
     * jarvis-web serves this as public client configuration. jarvis-core has a
     * route at the same path but requires a bearer token and answers something
     * structurally different, which is what [kindFromProbe] keys on.
     */
    fun probeUrl(base: String): String? {
        val normalized = ServerUrl.normalize(base)
        if (ServerUrl.originOf(normalized) == null) return null
        return "$normalized/api/config"
    }

    /** `GET <base>/api/pair`, with the bearer: only the adapter answers it with a `kind`. */
    fun pairProbeUrl(base: String): String? {
        val normalized = ServerUrl.normalize(base)
        if (ServerUrl.originOf(normalized) == null) return null
        return "$normalized/api/pair"
    }

    /** `GET <base>/health`, unauthenticated: the adapter names itself in `service`. */
    fun healthUrl(base: String): String? {
        val normalized = ServerUrl.normalize(base)
        if (ServerUrl.originOf(normalized) == null) return null
        return "$normalized/health"
    }

    /**
     * Read `/api/pair`'s answer. [ServerKind.HERMES] when the body says
     * `"kind": "hermes-adapter"`; null otherwise — including on a 401, which
     * is what jarvis-core's `/api/pair/…` family and any unrelated server
     * answer, and says nothing about which of them it was.
     *
     * The body decides, never the status: a reverse proxy can rewrite a
     * status, and the pairing payload is the one thing only the adapter emits.
     */
    fun kindFromPairProbe(status: Int, body: String?): ServerKind? {
        val json = jsonOf(body) ?: return null
        return if (json.optString("kind") == HERMES_KIND) ServerKind.HERMES else null
    }

    /**
     * The fallback for an adapter whose `/api/pair` is refusing us (a wrong
     * key still deserves the right kind, so the settings screen can say
     * "the adapter is there; the key is wrong" rather than "no server"):
     * `/health` is unauthenticated and carries `service: "jarvis-adapter"`.
     */
    fun kindFromHealth(status: Int, body: String?): ServerKind? {
        val json = jsonOf(body) ?: return null
        return if (json.optString("service") == HERMES_SERVICE) ServerKind.HERMES else null
    }

    /** The adapter's own name for itself, in both places it says it. */
    const val HERMES_KIND = "hermes-adapter"
    const val HERMES_SERVICE = "jarvis-adapter"

    private fun jsonOf(body: String?): JSONObject? {
        if (body == null || body.isBlank()) return null
        return try {
            JSONObject(body)
        } catch (e: Exception) {
            null
        }
    }

    /**
     * Read the probe's answer. Null means "cannot tell" — the caller should keep
     * whatever it already believed rather than guess.
     *
     * Keys, not status codes, decide it. A 401 from jarvis-core and a 200 from
     * jarvis-web is the common case, but a reverse proxy that rewrites status
     * codes, or a future jarvis-core that opens `/api/config` up, would make a
     * status-based rule quietly wrong. `backendUrlVar` is jarvis-web's alone:
     * it is the *name of the environment variable* the relay read its backend
     * URL from, which is a thing only the relay has.
     */
    fun kindFromProbe(status: Int, body: String?): ServerKind? {
        if (body != null && body.isNotBlank()) {
            val json = try {
                JSONObject(body)
            } catch (e: Exception) {
                null
            }
            if (json != null) {
                if (json.has("backendUrlVar") || json.has("tokenConfigured")) return ServerKind.RELAY
                // jarvis-core answers the Home Assistant config shape. `version`
                // alone is too common to key on; `components` is the giveaway.
                if (json.has("components") || json.has("ha_version")) return ServerKind.CORE
            }
        }
        // An authenticated-only route that refused us is jarvis-core: the relay's
        // copy is public and would have answered.
        if (status == 401 || status == 403) return ServerKind.CORE
        return null
    }

    /** The WebSocket URL for [base], given which server is there. */
    fun websocketUrl(base: String, kind: ServerKind): String? =
        ServerUrl.websocketUrl(base, kind.wsPath)

    /**
     * What to try, in order, when the kind is not known yet.
     *
     * RELAY first. Both endpoints now authenticate identically, so the order is
     * about which is more likely: the console is the URL a person types,
     * because it is the one with a web page on it.
     */
    fun candidates(known: ServerKind?): List<ServerKind> = when (known) {
        // HERMES is not a candidate for a jarvis-core-protocol socket, and a
        // remembered HERMES says nothing about which of the two others might
        // ALSO be at that address — so the order is the unknown one's.
        null, ServerKind.HERMES -> HOUSE_KINDS
        else -> listOf(known) + HOUSE_KINDS.filter { it != known }
    }

    /**
     * The kinds that speak jarvis-core's protocol, in the order to try them.
     *
     * The device command channel, the companion voice client and the assist
     * pipeline dial these and only these. The adapter's `/ws` accepts the
     * same bearer on the upgrade and then speaks JSON-RPC, so offering it as a
     * candidate would produce a socket that "connected" and never registered.
     */
    val HOUSE_KINDS: List<ServerKind> = listOf(ServerKind.RELAY, ServerKind.CORE)

    /** True when the assistant at [kind] is Hermes behind the adapter. */
    fun isHermes(kind: ServerKind?): Boolean = kind == ServerKind.HERMES

    /**
     * Where to fetch a media URL the pipeline handed back.
     *
     * jarvis-core answers with one of its own paths (`/api/tts_proxy/…`), which
     * only jarvis-core serves. Pointed at the console that path is a 404, and
     * the reply would be silent — for a voice assistant, the same as broken. The
     * console serves the same bytes through its media proxy at `/api/tts?path=`,
     * which attaches the server-held token and refuses anything that is not a
     * media path, so the relayed case goes through that instead.
     *
     * Returns null when [pathOrUrl] is not on the configured server, which is
     * the existing refusal: the pipeline is the component this project assumes
     * can be prompt-injected, and "it told us to fetch this" is not a reason to
     * send the bearer token somewhere else.
     */
    fun mediaUrl(base: String, kind: ServerKind?, pathOrUrl: String): String? {
        val resolved = ServerUrl.resolveOnServer(base, pathOrUrl) ?: return null
        // Only the console proxies media; jarvis-core and the adapter serve
        // their own bytes on their own origin.
        if (kind != ServerKind.RELAY) return resolved

        // The relay wants the PATH, not the whole URL. Take it back off the
        // resolved form rather than off the raw input, so an absolute URL and a
        // bare path end up at the same place and both have been origin-checked.
        val prefix = ServerUrl.normalize(base)
        val path = resolved.removePrefix(prefix)
        if (!path.startsWith("/")) return null
        return "$prefix/api/tts?path=" + java.net.URLEncoder.encode(path, "UTF-8")
    }

    /**
     * Whether a management WebView can show a console at this URL.
     *
     * Only the relay serves one. Pointed at jarvis-core the WebView would load
     * the API's JSON index, which looks like a broken page rather than like a
     * URL pointing at the wrong one of two servers.
     */
    fun servesConsole(kind: ServerKind?): Boolean = kind == ServerKind.RELAY
}
