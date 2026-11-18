package ai.aura.app.automation.actions.builtin

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** read_contacts' second pass: a name as speech-to-text spelled it (the owner, 2026-10-07). */
class ContactNameTest {

    @Test
    fun `a name heard one letter off still finds the contact`() {
        assertTrue(ReadContacts.soundsLike("Issa", "Isa Vuijk 😍😍😍"))
        assertTrue(ReadContacts.soundsLike("isa", "Issa"))
        assertTrue(ReadContacts.soundsLike("Cameron", "Kameron Smith"))
    }

    @Test
    fun `a different name does not`() {
        assertFalse(ReadContacts.soundsLike("Issa", "Kaleb"))
        assertFalse(ReadContacts.soundsLike("Al", "Ali Baker"))
        assertFalse(ReadContacts.soundsLike("", "Isa"))
    }
}
