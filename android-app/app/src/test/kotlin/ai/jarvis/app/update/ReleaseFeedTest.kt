package ai.jarvis.app.update

import ai.jarvis.app.BuildConfig
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File
import java.net.URI

/**
 * Which GitHub release, if any, is worth installing.
 *
 * This turns a document fetched over the internet into "download this file and
 * ask the user to install it", so the refusals matter more than the successes.
 * The cases are `tests/contracts/release-feed.json`, which
 * `android-app/tools/release_feed_test.py` runs too; only what Kotlin alone can
 * express (the resume rule, the releases URL, the stored size) is written here.
 */
class ReleaseFeedTest {
    // The build's own GitLab: CI stamps the house's, so a literal example host fails there.
    private val gitlab = UpdateChecker.GITLAB_BASE

    // The contract's placeholders, filled the way release_feed_test.py fills them.
    private val contract = JSONObject(
        File("../../tests/contracts/release-feed.json").readText()
            .replace("{gitlab-host}", URI(gitlab).host)
            .replace("{github-host}", "github.com")
            .replace("{github-api-host}", "api.github.com")
            .replace("{github-objects-host}", "objects.githubusercontent.com")
    )

    private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

    private fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }

    // The fields a contract `want` names, read off the Update the same way the Python does.
    private fun offered(update: ReleaseFeed.Update, key: String): Any? = when (key) {
        "versionCode" -> update.versionCode
        "versionName" -> update.versionName
        "url" -> update.downloadUrl
        "notes" -> update.notes
        else -> error("the contract names an unknown field $key")
    }

    @Test
    fun `every download url in the contract`() {
        for (c in contract.getJSONArray("downloads").objects()) {
            val adapter = if (c.isNull("adapter")) null else c.getString("adapter")
            assertEquals(
                c.getString("name"),
                c.getBoolean("allowed"),
                ReleaseFeed.isAllowedDownload(c.getString("url"), adapter),
            )
        }
    }

    @Test
    fun `every pick in the contract`() {
        for (c in contract.getJSONArray("picks").objects()) {
            val name = c.getString("name")
            val update = ReleaseFeed.pick(
                c.getJSONArray("feed").toString(),
                c.getLong("installed"),
                c.getBoolean("prerelease"),
            )
            if (c.isNull("want")) {
                assertNull(name, update)
                continue
            }
            assertNotNull(name, update)
            val want = c.getJSONObject("want")
            want.keys().forEach { key ->
                val expected: Any = if (key == "versionCode") want.getLong(key) else want.getString(key)
                assertEquals("$name: $key", expected, offered(update!!, key))
            }
        }
    }

    @Test
    fun `garbage in, nothing out`() {
        for (bad in contract.getJSONArray("garbage").strings()) {
            assertNull("expected null for $bad", ReleaseFeed.pick(bad, 1, true))
        }
    }

    @Test
    fun `every stored listing in the contract`() {
        for (c in contract.getJSONArray("stored").objects()) {
            val name = c.getString("name")
            val update = ReleaseFeed.pickStored(
                c.getJSONObject("listing").toString(),
                c.getString("adapter"),
                c.getLong("installed"),
            )
            if (c.isNull("want")) {
                assertNull(name, update)
                continue
            }
            assertNotNull(name, update)
            val want = c.getJSONObject("want")
            want.keys().forEach { key ->
                val expected: Any = if (key == "versionCode") want.getLong(key) else want.getString(key)
                assertEquals("$name: $key", expected, offered(update!!, key))
            }
        }
    }

    @Test
    fun `every stored garbage is refused`() {
        val garbage = contract.getJSONObject("storedGarbage")
        for (raw in garbage.getJSONArray("raw").strings()) {
            assertNull(raw, ReleaseFeed.pickStored(raw, garbage.getString("adapter"), 1))
        }
    }

    @Test
    fun `every newest case in the contract`() {
        for (c in contract.getJSONArray("newest").objects()) {
            val name = c.getString("name")
            val gitlabPick = ReleaseFeed.pick(c.getJSONArray("feed").toString(), c.getLong("installed"), false)
            val listing = if (c.isNull("listing")) "null" else c.getJSONObject("listing").toString()
            val jarvisPick = ReleaseFeed.pickStored(listing, c.getString("adapter"), c.getLong("installed"))
            val best = ReleaseFeed.newest(gitlabPick, jarvisPick)?.versionCode
            if (c.isNull("want")) {
                assertNull(name, best)
            } else {
                assertEquals(name, c.getLong("want"), best)
            }
        }
    }

    @Test
    fun `every version tag in the contract`() {
        for (c in contract.getJSONArray("versions").objects()) {
            val tag = c.getString("tag")
            val code = if (c.isNull("code")) null else c.getLong("code")
            assertEquals("code of $tag", code, ReleaseFeed.versionCodeOf(tag, null))
            assertEquals("name of $tag", c.getString("version"), ReleaseFeed.versionNameOf(tag))
        }
    }

    // Kotlin only: the contract does not carry the size, which the phone shows before it downloads.
    @Test
    fun `a stored apk reports its size`() {
        val listing = """{"artifacts":[{"id":"6ae7aa8eb31e36eca029337de92647d693c99fbcff7ddda50e2663fdaeddc486","mime":"${ReleaseFeed.APK_MIME}","title":"Jarvis 1.0.1673.apk","size":134262024}]}"""
        assertEquals(134262024L, ReleaseFeed.pickStored(listing, "https://jarvis.example.test/hermes", 1671)?.sizeBytes)
    }

    // --- Kotlin only: the releases URL and the resume rule are not in the contract ----

    @Test
    fun `the releases url is the gitlab api for the url-encoded project path`() {
        assertEquals(
            "$gitlab/api/v4/projects/some-group%2Fthe-app/releases?per_page=" + UpdateChecker.PAGE_SIZE,
            UpdateChecker.releasesUrl("some-group/the-app"),
        )
        // The default is the build's own project (CI's CI_PROJECT_PATH), never a name typed here.
        assertEquals(BuildConfig.UPDATE_REPO, UpdateChecker.DEFAULT_REPO)
    }

    // The owner, 2026-09-25: a slow download "times out and doesnt complete, instead of
    // just continuing". A dropped read resumes from what was written.
    @Test
    fun `a dropped download resumes where it stopped, or restarts when the server ignores the range`() {
        assertEquals(4_000_000L, UpdateChecker.resumeOffset(206, 4_000_000L))
        assertEquals(0L, UpdateChecker.resumeOffset(200, 4_000_000L))
        assertEquals(0L, UpdateChecker.resumeOffset(200, 0L))
        assertEquals(null, UpdateChecker.resumeOffset(416, 4_000_000L))
        assertEquals(null, UpdateChecker.resumeOffset(404, 0L))
        // A 206 to a request that asked for no range is not a resume.
        assertEquals(null, UpdateChecker.resumeOffset(206, 0L))
    }
}
