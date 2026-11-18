package ai.aura.app.ui

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

/**
 * The cases are `tests/contracts/approval-event.json` ("card"), which the Python parse and the
 * console's parser read too.
 */
class ApprovalCardTest {
    private val card = JSONObject(File("../../tests/contracts/approval-event.json").readText())
        .getJSONObject("card")

    @Test fun `every case in the table`() {
        val cases = card.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val got = ApprovalCard.parse(c.getString("reason"))
            val want = c.optJSONObject("card")
            if (want == null) {
                assertNull(c.getString("name"), got)
                continue
            }
            val expected = ApprovalCard(
                what = want.getString("what"),
                why = want.getString("why"),
                impact = want.getString("impact"),
                undoable = want.getString("undoable") == "yes",
                home = want.getString("home") == "yes",
            )
            assertEquals(c.getString("name"), expected, got)
            assertFalse("${c.getString("name")}: the detail still holds a card line",
                ApprovalCard.withoutCard(c.getString("reason")).endsWith("Home: ${want.getString("home")}"))
        }
    }

    @Test fun `each chip answers with the table's choice, and the home chip only when home`() {
        val chips = card.getJSONObject("chips")
        val all = listOf("once", "session", "always", "deny")
        val home = ApprovalCard("a", "b", "c", undoable = true, home = true)
        assertEquals(chips.length(), ApprovalCard.Rule.entries.size)
        for (key in chips.keys()) {
            val rule = ApprovalCard.Rule.entries.single { it.label.lowercase() == key.lowercase() }
            val chip = chips.getJSONObject(key)
            assertEquals(key, chip.getString("choice"), rule.choice)
            val needsHome = chip.optString("needs") == "Home: yes"
            assertEquals("$key offered at home", true, rule in ApprovalCard.rules(home, all))
            assertEquals("$key offered away", !needsHome, rule in ApprovalCard.rules(home.copy(home = false), all))
            assertEquals("$key offered with no card", !needsHome, rule in ApprovalCard.rules(null, all))
            assertFalse("$key offered when Hermes did not offer ${rule.choice}",
                rule in ApprovalCard.rules(home, all - rule.choice))
        }
    }

    /** T-0945: the fields a sensitive ask carries and the key registration the phone makes. */
    @Test fun `the phone-confirmation wire names are the contract's`() {
        val c = JSONObject(File("../../tests/contracts/approval-event.json").readText())
            .getJSONObject("phone_confirmation")
        assertEquals(c.getString("confirm_value"), ApprovalProof.CONFIRM_PHONE)
        assertEquals(c.getString("signature_field"), ApprovalProof.SIGNATURE_FIELD)
        assertEquals(c.getString("confirm_field"), ApprovalProof.CONFIRM_FIELD)
        assertEquals(c.getString("challenge_field"), ApprovalProof.CHALLENGE_FIELD)
        val register = c.getJSONObject("register")
        assertEquals(register.getString("path"), ApprovalProof.REGISTER_PATH)
        assertEquals(register.getString("body_field"), ApprovalProof.REGISTER_FIELD)
    }

    /** A phone-confirmed ask offers no wider rule than "once": the adapter forwards nothing else. */
    @Test fun `a phone-confirmed ask offers only the once rule`() {
        val offered = JSONObject(File("../../tests/contracts/approval-event.json").readText())
            .getJSONObject("phone_confirmation").getJSONArray("choices")
        val choices = List(offered.length()) { offered.getString(it) }
        val rules = ApprovalCard.rules(ApprovalCard("a", "b", "c", undoable = true, home = true), choices)
        assertEquals(listOf("once"), rules.map { it.choice })
    }
}
