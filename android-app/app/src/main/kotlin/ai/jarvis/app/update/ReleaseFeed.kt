package ai.jarvis.app.update

import ai.jarvis.app.BuildConfig
import org.json.JSONArray
import org.json.JSONObject

/**
 * Deciding whether a GitHub release is an update worth offering.
 *
 * The app is sideloaded — there is no store to push it — so it checks the
 * repository's releases itself. That means taking a JSON document from the
 * internet and turning it into "download this file and ask the user to install
 * it", which is the most dangerous sentence in the app. Everything that decides
 * it lives here, with no Android and no network, so it can be tested.
 *
 * Three rules, and each exists because of a specific way this goes wrong.
 *
 * **The version must actually be newer.** Android refuses to install a package
 * whose `versionCode` is not greater than the installed one, so offering an
 * equal-or-older release produces a prompt that can only fail. The comparison
 * is on the integer, never on the tag text: `v1.10.0` sorts before `v1.9.0` as
 * a string.
 *
 * **The asset must come from the release's own API payload**, over https, on
 * GitHub's own host. A release body is attacker-controllable text if the
 * repository is ever public or a token leaks, and "download whatever URL the
 * JSON said" is how an updater becomes a malware delivery service.
 *
 * **A draft is not a release.** Drafts are visible to anyone with push access
 * and are, by definition, not finished.
 */
object ReleaseFeed {

    /** Hosts a release asset may be downloaded from: the house's GitLab (the build's, T-0841) and GitHub's. */
    private val ALLOWED_HOSTS = setOf(java.net.URI(BuildConfig.GITLAB_BASE).host, "github.com", "api.github.com", "objects.githubusercontent.com")

    /** The mime the adapter's artifact store lists an APK under. */
    const val APK_MIME = "application/vnd.android.package-archive"

    /** A stored APK's title: `Jarvis <versionName>`, the name the build gives itself (`1.0.<code>`). */
    private val STORED_TITLE = Regex("""^Jarvis v?(\d+\.\d+\.(\d+))\b""")

    /** An artifact's id in the adapter's store: a content hash, nothing a path can hide in. */
    private val ARTIFACT_ID = Regex("^[0-9a-f]{64}$")

    /** The APK the release ships, once it has been judged installable. */
    data class Update(
        val versionCode: Long,
        val versionName: String,
        val tag: String,
        val downloadUrl: String,
        val sizeBytes: Long,
        val notes: String,
        val prerelease: Boolean,
    )

    /**
     * The newest installable update in [json], or null.
     *
     * [installedVersionCode] is what is running now. [allowPrerelease] lets the
     * user opt into the per-build prereleases CI publishes; with it off, only
     * full releases are offered.
     */
    fun pick(
        json: String,
        installedVersionCode: Long,
        allowPrerelease: Boolean,
    ): Update? {
        val releases = try {
            JSONArray(json)
        } catch (e: Exception) {
            return null
        }
        var best: Update? = null
        for (i in 0 until releases.length()) {
            val release = releases.optJSONObject(i) ?: continue
            val candidate = parseRelease(release, allowPrerelease) ?: continue
            if (candidate.versionCode <= installedVersionCode) continue
            // Highest code wins, not first-in-list: GitHub orders by creation
            // date, and a patch to an older line can be created most recently.
            if (best == null || candidate.versionCode > best!!.versionCode) best = candidate
        }
        return best
    }

    private fun parseRelease(release: JSONObject, allowPrerelease: Boolean): Update? {
        // GitHub says draft/prerelease outright. GitLab has `upcoming_release` for
        // a draft and no prerelease flag: the pipeline's per-push test builds are
        // the releases whose notes open with "Automatic build" (see
        // .gitlab/ci/android-apk.yml); a hand-made vX.Y.Z tag is a full release.
        if (release.optBoolean("draft", false) || release.optBoolean("upcoming_release", false)) return null
        val notes = if (release.has("body")) release.optString("body") else release.optString("description")
        val prerelease = release.optBoolean("prerelease", false) || notes.trimStart().startsWith("Automatic build")
        if (prerelease && !allowPrerelease) return null

        val tag = release.optString("tag_name").trim()
        if (tag.isEmpty()) return null

        // GitHub: `assets: [{name, browser_download_url, size, label}]`.
        // GitLab: `assets: {links: [{name, url, direct_asset_url}]}` — the pipeline
        // links the APK it uploaded to the generic package registry.
        val assets: JSONArray = when (val raw = release.opt("assets")) {
            is JSONArray -> raw
            is JSONObject -> raw.optJSONArray("links") ?: return null
            else -> return null
        }
        for (i in 0 until assets.length()) {
            val asset = assets.optJSONObject(i) ?: continue
            val name = asset.optString("name")
            if (!name.endsWith(".apk", ignoreCase = true)) continue
            val url = asset.optString("browser_download_url").ifEmpty { asset.optString("direct_asset_url").ifEmpty { asset.optString("url") } }
            if (!isAllowedDownload(url)) continue
            val code = versionCodeOf(tag, asset.optString("label")) ?: continue
            return Update(
                versionCode = code,
                versionName = versionNameOf(tag),
                tag = tag,
                downloadUrl = url,
                sizeBytes = asset.optLong("size", 0L),
                notes = notes.take(MAX_NOTES),
                prerelease = prerelease,
            )
        }
        return null
    }

    /**
     * The newest installable APK in the paired adapter's artifact store, or null.
     *
     * [json] is the adapter's `/api/artifacts` listing. A hand-built release is put in that
     * store (the owner, 2026-11-08: "how can we have the download behaviour work with either
     * one", T-0735): a row is one when its mime is an APK and its title is `Jarvis
     * <versionName>`, and it downloads from [adapterBase] with the phone's key.
     *
     * Does not prove the bytes are a Jarvis build: anything holding a key can store a file.
     * Android installs an update only when it is signed with the installed app's key, and
     * that is what refuses anything else.
     */
    fun pickStored(json: String, adapterBase: String, installedVersionCode: Long): Update? {
        val base = adapterBase.trim().trimEnd('/')
        if (base.isEmpty()) return null
        val rows = try {
            JSONObject(json).optJSONArray("artifacts")
        } catch (e: Exception) {
            null
        } ?: return null
        var best: Update? = null
        for (i in 0 until rows.length()) {
            val row = rows.optJSONObject(i) ?: continue
            if (row.optString("mime") != APK_MIME) continue
            val id = row.optString("id")
            if (!ARTIFACT_ID.matches(id)) continue
            val title = row.optString("title")
            val match = STORED_TITLE.find(title) ?: continue
            val code = match.groupValues[2].toLongOrNull()?.takeIf { it > 0 } ?: continue
            if (code <= installedVersionCode || (best != null && code <= best.versionCode)) continue
            val name = match.groupValues[1]
            best = Update(code, name, "v$name+$code", "$base/api/artifacts/$id", row.optLong("size", 0L), title.take(MAX_NOTES), prerelease = false)
        }
        return best
    }

    /** The newer of two offers, either of which may be missing. */
    fun newest(a: Update?, b: Update?): Update? =
        if (a == null || (b != null && b.versionCode > a.versionCode)) b else a

    /**
     * True for a URL this app will fetch an APK from.
     *
     * Scheme and host are both checked, and the host is matched exactly rather
     * than by suffix: `github.com.evil.test` ends with nothing useful but
     * `endsWith("github.com")` would have said yes to `evilgithub.com`.
     *
     * With [adapterBase], also an artifact under the paired adapter's own
     * `/api/artifacts/<id>`, by exact prefix, in whatever scheme the phone was paired
     * with: over the tunnel or the LAN that is http, the link its key already travels.
     */
    fun isAllowedDownload(url: String, adapterBase: String? = null): Boolean {
        val text = url.trim()
        if (text.any { it.isISOControl() }) return false
        val store = adapterBase?.trim()?.trimEnd('/')?.takeIf { it.isNotEmpty() }?.let { "$it/api/artifacts/" }
        if (store != null && text.startsWith(store)) return ARTIFACT_ID.matches(text.removePrefix(store))
        if (!text.startsWith("https://")) return false
        val authority = text.removePrefix("https://").substringBefore('/')
        // Credentials in the authority would move the real host after an '@'.
        if (authority.contains('@')) return false
        val host = authority.substringBefore(':').lowercase()
        return host in ALLOWED_HOSTS
    }

    /**
     * The versionCode a release advertises.
     *
     * CI puts it in the tag as `v<name>+<code>` — the code is what Android
     * compares and the tag is the only place a client can read it without
     * downloading the whole APK first. An asset label is accepted as a
     * fallback so a hand-made release can still be offered.
     */
    fun versionCodeOf(tag: String, label: String?): Long? {
        codeAfterPlus(tag)?.let { return it }
        return codeAfterPlus(label ?: "")
    }

    private fun codeAfterPlus(text: String): Long? {
        val plus = text.lastIndexOf('+')
        if (plus < 0 || plus == text.lastIndex) return null
        val digits = text.substring(plus + 1).trim()
        if (digits.isEmpty() || !digits.all { it.isDigit() }) return null
        return digits.toLongOrNull()?.takeIf { it > 0 }
    }

    /** The human-facing version: the tag without its `v` prefix or `+code`. */
    fun versionNameOf(tag: String): String =
        tag.removePrefix("v").substringBefore('+').ifEmpty { tag }

    private const val MAX_NOTES = 2000
}
