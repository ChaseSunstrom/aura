package ai.jarvis.app.automation.notify

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/** The rule and its cases are `tests/contracts/otp_extract.json`, which the Python mirror runs too. */
class OtpExtractorTest {
    private val contract = JSONObject(File("../../tests/contracts/otp_extract.json").readText())

    @Test
    fun `the keywords are the contract's`() {
        val kws = contract.getJSONArray("keywords")
        assertEquals((0 until kws.length()).map { kws.getString(it) }, OtpExtractor.KEYWORDS)
    }

    @Test
    fun `every case in the contract`() {
        val cases = contract.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val want = if (c.isNull("code")) null else c.getString("code")
            assertEquals(c.getString("name"), want, OtpExtractor.extract(c.getString("text")))
        }
    }
}
