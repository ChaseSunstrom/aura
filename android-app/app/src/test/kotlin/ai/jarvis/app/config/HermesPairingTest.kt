package ai.jarvis.app.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The adapter's `{url, key, kind}` QR, and what a scanned square may not do. */
class HermesPairingTest {

    /** `GET /api/pair` on the live adapter, 2026-09-02. */
    private val real = """{"url":"http://127.0.0.1:8643","key":"devadapterkey0123456789","model":"qwen3.8-27b","kind":"hermes-adapter"}"""

    @Test
    fun `the adapter's own payload pairs`() {
        val ok = HermesPairing.parse(real) as HermesPairing.Result.Ok
        assertEquals("http://127.0.0.1:8643", ok.pairing.url)
        assertEquals("devadapterkey0123456789", ok.pairing.key)
        assertEquals("qwen3.8-27b", ok.pairing.model)
    }

    @Test
    fun `the url is normalised like a typed one`() {
        val ok = HermesPairing.parse("""{"url":"https://jarvis.example.test/","key":"0123456789abcdef","kind":"hermes-adapter"}""") as HermesPairing.Result.Ok
        assertEquals("https://jarvis.example.test", ok.pairing.url)
    }

    @Test
    fun `anything that is not the adapter's object is not a payload`() {
        assertTrue(HermesPairing.parse("") is HermesPairing.Result.NotAPayload)
        assertTrue(HermesPairing.parse("jarvis://pair?v=1&u=x&c=y") is HermesPairing.Result.NotAPayload)
        assertTrue(HermesPairing.parse("a-bare-token-0123456789") is HermesPairing.Result.NotAPayload)
        assertTrue(HermesPairing.parse("{not json") is HermesPairing.Result.NotAPayload)
        assertTrue(HermesPairing.parse("""{"url":"http://x","key":"0123456789abcdef"}""") is HermesPairing.Result.NotAPayload)
        assertTrue(HermesPairing.parse("""{"url":"http://x","key":"0123456789abcdef","kind":"other"}""") is HermesPairing.Result.NotAPayload)
    }

    @Test
    fun `the adapter's object with a bad address or key is refused, never a token`() {
        assertTrue(HermesPairing.parse("""{"kind":"hermes-adapter","key":"0123456789abcdef"}""") is HermesPairing.Result.Refused)
        assertTrue(HermesPairing.parse("""{"kind":"hermes-adapter","url":"ftp://x","key":"0123456789abcdef"}""") is HermesPairing.Result.Refused)
        assertTrue(HermesPairing.parse("""{"kind":"hermes-adapter","url":"http://x:8643","key":"short"}""") is HermesPairing.Result.Refused)
        assertTrue(HermesPairing.parse("""{"kind":"hermes-adapter","url":"http://x:8643","key":"has a space in it"}""") is HermesPairing.Result.Refused)
        assertTrue(HermesPairing.parse("""{"kind":"hermes-adapter","url":"http://x:8643","key":"${"k".repeat(300)}"}""") is HermesPairing.Result.Refused)
    }

    @Test
    fun `a huge square is refused before it is parsed`() {
        assertTrue(HermesPairing.parse("{" + " ".repeat(600) + "}") is HermesPairing.Result.Refused)
        assertTrue(HermesPairing.parse("{\"kind\":\"hermes-adapter\",\"url\":\"http://x\"}") is HermesPairing.Result.Refused)
    }
}
