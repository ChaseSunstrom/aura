package ai.aura.app.assist.hermes

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * The panel surface's reducer, every rule and every kind — the JVM mirror of
 * `aura-web/src/lib/hermes/panels.test.ts`. Pure, so no socket and no phone.
 *
 * The kinds and the one example each carries come from
 * `tests/contracts/hud-panels.json`; a change there that this file does not
 * follow is caught by `android-app/tools/hud_panels_test.py`.
 */
class HudPanelsTest {

    private val now = 1_000_000L

    private fun show(json: String) = HudPanels.panelFromShow(JSONObject(json), now)

    // --- panelFromShow --------------------------------------------------------

    @Test
    fun `a show with no id is not a panel we can hold`() {
        assertNull(HudPanels.panelFromShow(JSONObject("""{"kind":"text"}"""), now))
    }

    @Test
    fun `a show reads loosely, with sensible defaults`() {
        val p = show("""{"id":"p1"}""")!!
        assertEquals("p1", p.id)
        assertEquals("text", p.kind)
        assertEquals("agent", p.origin)
        assertEquals(0, p.rev)
        assertNull(p.ttlS)
        assertNull(p.expiresAt)
        assertEquals(now, p.createdAt)
    }

    @Test
    fun `created_at is parsed as an instant, or falls back to now`() {
        val iso = "2026-09-03T00:41:12Z"
        val p = show("""{"id":"p1","created_at":"$iso"}""")!!
        assertEquals(Instant.parse(iso).toEpochMilli(), p.createdAt)
        // A number is epoch ms; junk falls back to arrival.
        assertEquals(42L, show("""{"id":"p2","created_at":42}""")!!.createdAt)
        assertEquals(now, show("""{"id":"p3","created_at":"not a date"}""")!!.createdAt)
    }

    @Test
    fun `a ttl sets a local deadline, zero or missing does not`() {
        val p = show("""{"id":"p1","ttl_s":30}""")!!
        assertEquals(30.0, p.ttlS!!, 0.0)
        assertEquals(now + 30_000L, p.expiresAt)
        assertNull(show("""{"id":"p2","ttl_s":0}""")!!.ttlS)
        assertNull(show("""{"id":"p3"}""")!!.expiresAt)
    }

    @Test
    fun `every contract kind's example parses and keeps its payload`() {
        for ((kind, payload) in CONTRACT_EXAMPLES) {
            val row = JSONObject().put("id", "p_$kind").put("kind", kind)
                .put("title", kind).put("origin", "agent").put("rev", 1)
                .put("payload", JSONObject(payload))
            val panel = HudPanels.panelFromShow(row, now)
            assertNotNull("kind $kind should parse", panel)
            assertEquals(kind, panel!!.kind)
            // The payload is carried verbatim: the first field of the example is present.
            val firstKey = JSONObject(payload).keys().next()
            assertTrue("kind $kind keeps payload.$firstKey", panel.payload.has(firstKey))
        }
        // The contract's kinds are the ones the reducer names.
        assertEquals(
            listOf(
                "text", "table", "image", "chart", "camera", "code", "terminal", "web", "device", "telemetry",
                "goal", "approval", "clarify", "notice", "video", "audio", "document",
            ),
            HudPanels.KINDS,
        )
    }

    // --- applyShow / applyUpdate / applyDismiss -------------------------------

    @Test
    fun `applyShow inserts, replaces on a higher rev, ignores a lower rev`() {
        val a = show("""{"id":"a","rev":1,"title":"one"}""")!!
        var panels = HudPanels.applyShow(emptyList(), a)
        assertEquals(1, panels.size)

        val a2 = show("""{"id":"a","rev":2,"title":"two"}""")!!
        panels = HudPanels.applyShow(panels, a2)
        assertEquals("two", panels[0].title)

        val stale = show("""{"id":"a","rev":1,"title":"stale"}""")!!
        val same = HudPanels.applyShow(panels, stale)
        assertSame("a lower rev is ignored, and the list is untouched", panels, same)
    }

    @Test
    fun `applyUpdate merges title and payload, ignores an unknown id and a lower rev`() {
        val a = show("""{"id":"a","rev":1,"title":"one","payload":{"markdown":"hi"}}""")!!
        val panels = listOf(a)

        val unknown = HudPanels.applyUpdate(panels, JSONObject("""{"id":"z","rev":2}"""), now)
        assertSame("an update for a panel we never saw is ignored", panels, unknown)

        val merged = HudPanels.applyUpdate(panels, JSONObject("""{"id":"a","rev":2,"title":"two"}"""), now)
        assertEquals("two", merged[0].title)
        assertEquals("payload is kept when the update omits it", "hi", merged[0].payload.optString("markdown"))

        val stale = HudPanels.applyUpdate(merged, JSONObject("""{"id":"a","rev":1,"title":"stale"}"""), now)
        assertSame(merged, stale)
    }

    @Test
    fun `an update with a fresh ttl restarts the local clock, without one the deadline stays`() {
        val a = show("""{"id":"a","rev":1,"ttl_s":10}""")!!
        val panels = listOf(a)
        val later = now + 5_000L

        val refreshed = HudPanels.applyUpdate(panels, JSONObject("""{"id":"a","rev":2,"ttl_s":20}"""), later)
        assertEquals(later + 20_000L, refreshed[0].expiresAt)

        val kept = HudPanels.applyUpdate(panels, JSONObject("""{"id":"a","rev":2,"title":"x"}"""), later)
        assertEquals("no ttl on the update keeps the old deadline", a.expiresAt, kept[0].expiresAt)
    }

    @Test
    fun `applyDismiss removes one, clears all on the star, and no-ops on an unknown id`() {
        val a = show("""{"id":"a"}""")!!
        val b = show("""{"id":"b"}""")!!
        val panels = listOf(a, b)

        assertEquals(listOf(b), HudPanels.applyDismiss(panels, "a"))
        assertEquals("* clears the surface", 0, HudPanels.applyDismiss(panels, HudPanels.DISMISS_ALL).size)
        assertEquals("*", HudPanels.DISMISS_ALL)
        assertSame("an unknown id changes nothing", panels, HudPanels.applyDismiss(panels, "z"))
        assertSame("* on an empty surface changes nothing", emptyList<Panel>(), HudPanels.applyDismiss(emptyList(), "*"))
    }

    // --- seed / parse / prune / order -----------------------------------------

    @Test
    fun `seedFromList applies rows through applyShow, so a stale row does not clobber a newer one`() {
        val held = show("""{"id":"a","rev":5,"title":"live"}""")!!
        val list = JSONArray()
            .put(JSONObject("""{"id":"a","rev":2,"title":"stale"}"""))
            .put(JSONObject("""{"id":"b","rev":1,"title":"new"}"""))
        val seeded = HudPanels.seedFromList(listOf(held), list, now)
        assertEquals(2, seeded.size)
        assertEquals("live", seeded.first { it.id == "a" }.title)
        assertEquals("new", seeded.first { it.id == "b" }.title)
    }

    @Test
    fun `parsePanelList tolerates a string array, a wrapper, and junk`() {
        assertEquals(2, HudPanels.parsePanelList("""[{"id":"a"},{"id":"b"}]""").length())
        assertEquals(1, HudPanels.parsePanelList("""{"panels":[{"id":"a"}]}""").length())
        assertEquals(0, HudPanels.parsePanelList("not json").length())
        assertEquals(0, HudPanels.parsePanelList("").length())
        assertEquals(1, HudPanels.parsePanelList(JSONArray().put(JSONObject("""{"id":"a"}"""))).length())
    }

    @Test
    fun `pruneExpired drops the timed-out, keeps the deathless and the not-yet-due`() {
        val expired = show("""{"id":"a","ttl_s":1}""")!!      // expires at now+1000
        val future = show("""{"id":"b","ttl_s":3600}""")!!
        val forever = show("""{"id":"c"}""")!!
        val panels = listOf(expired, future, forever)
        val kept = HudPanels.pruneExpired(panels, now + 2_000L)
        assertEquals(listOf("b", "c"), kept.map { it.id })
        assertSame("nothing due yet is left untouched", panels, HudPanels.pruneExpired(panels, now))
    }

    // --- through HudState -----------------------------------------------------

    @Test
    fun `HudState folds the three panel events and flags PANELS`() {
        val hud = HudState()
        val shown = hud.apply("hud.panel.show", JSONObject("""{"id":"p1","kind":"telemetry","title":"GPUs","rev":1,"payload":{"gpus":[]}}"""), now)
        assertTrue(HudState.Change.PANELS in shown)
        assertEquals(1, hud.panels.size)

        val updated = hud.apply("hud.panel.update", JSONObject("""{"id":"p1","rev":2,"title":"GPUs (live)"}"""), now)
        assertTrue(HudState.Change.PANELS in updated)
        assertEquals("GPUs (live)", hud.panels[0].title)

        // A stale show is not a change.
        val stale = hud.apply("hud.panel.show", JSONObject("""{"id":"p1","rev":1,"title":"old"}"""), now)
        assertTrue(HudState.Change.PANELS !in stale)

        val dismissed = hud.apply("hud.panel.dismiss", JSONObject("""{"id":"*"}"""), now)
        assertTrue(HudState.Change.PANELS in dismissed)
        assertTrue(hud.panels.isEmpty())
    }

    @Test
    fun `panels survive a new turn and seed from a list`() {
        val hud = HudState()
        hud.apply("hud.panel.show", JSONObject("""{"id":"p1","kind":"text","rev":1,"payload":{"markdown":"hi"}}"""), now)
        hud.turnStarted()
        assertEquals("a panel is session state, not turn state", 1, hud.panels.size)
        val list = JSONArray().put(JSONObject("""{"id":"p2","kind":"notice","rev":1,"payload":{"text":"warmed up"}}"""))
        assertTrue(hud.seedPanels(list, now))
        assertEquals(2, hud.panels.size)
    }

    companion object {
        /** The examples `tests/contracts/hud-panels.json` carries, kind → payload JSON. */
        private val CONTRACT_EXAMPLES: List<Pair<String, String>> = listOf(
            "text" to """{"markdown":"The kitchen is **22 °C**."}""",
            "table" to """{"columns":["GPU","VRAM","Temp"],"rows":[["0","93.6 %","50 °C"]]}""",
            "image" to """{"url":"/hermes/api/files/out/jarvis_00001_.png","alt":"a red fox"}""",
            "chart" to """{"series":[{"name":"load1","points":[[0,0.4],[60,0.9]]}],"unit":""}""",
            "camera" to """{"camera":"Garden","url":"/api/camera/garden/still","taken_at":"2026-09-03T00:41:12Z"}""",
            "code" to """{"language":"python","text":"print('hi')","path":"hi.py"}""",
            "terminal" to """{"command":"uptime","output":" 00:41 up 4 days","exit_code":0}""",
            "web" to """{"url":"https://n8n.example.ts.net","title":"n8n"}""",
            "device" to """{"entity_id":"light.kitchen","name":"Kitchen","state":"on","attributes":{"brightness":200}}""",
            "telemetry" to """{"gpus":[{"gpu":"0","utilization":0,"vram_pct":93.6,"temperature":50,"power":22}]}""",
            "notice" to """{"level":"info","text":"Model warmed up"}""",
            // The media kinds (2026-09-18): a file the store serves, its mime, and where it came from.
            "video" to """{"url":"/hermes/api/artifacts/3b1f0a9c6d2e4f5a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a","mime":"video/mp4","title":"The garden at dusk","provenance":{"tool":"fetch_video","source_url":"https://www.youtube.com/watch?v=aqz-KE-bpKQ"}}""",
            "audio" to """{"url":"/hermes/api/artifacts/9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d","mime":"audio/mpeg","title":"Episode 12: the cold study","provenance":{"tool":"fetch_audio","source_url":"https://example.org/podcast/ep12.mp3"}}""",
            "document" to """{"url":"/hermes/api/artifacts/0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9","mime":"application/pdf","title":"Attention is all you need","provenance":{"tool":"extract_page","source_url":"https://arxiv.org/pdf/1706.03762"}}""",
        )
    }
}
