package ai.jarvis.app.ui

import ai.jarvis.app.ApprovalActivity
import ai.jarvis.app.assist.hermes.HudState
import android.content.Context
import android.content.Intent
import android.os.Looper
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.util.Base64
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.fragment.app.FragmentActivity
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.time.Duration

/**
 * T-0945 on the phone: a sensitive Hermes approval (`confirm: "phone"`) is allowed only with a
 * signature the adapter can check, made by a key behind the fingerprint.
 *
 * What is faked: the key is a software P-256 pair ([SoftKey]) and the prompt is [FakeAuth],
 * which answers at once. The Android Keystore and the real BiometricPrompt do not run under
 * Robolectric, so that the platform keeps the key and demands a fingerprint is the device's to
 * prove, not this suite's. What is real: the bridge, the consent activity, the signature's
 * bytes (verified here with the public key the phone would register) and the wire fields,
 * which come from `tests/contracts/approval-event.json`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class ApprovalBiometricTest {

    @get:Rule
    val compose = createEmptyComposeRule()

    private val contract = JSONObject(File("../../tests/contracts/approval-event.json").readText())
        .getJSONObject("phone_confirmation")

    private class SoftKey : ApprovalKey {
        private val pair = KeyPairGenerator.getInstance("EC")
            .apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        var gone = false

        /** A fingerprint enrolment change: the Keystore throws this from initSign. */
        var invalidated = false
        override fun publicKey(): String? = Base64.encodeToString(pair.public.encoded, Base64.NO_WRAP)
        override fun signer(): Signature? {
            if (invalidated) throw KeyPermanentlyInvalidatedException()
            return if (gone) null else Signature.getInstance("SHA256withECDSA").apply { initSign(pair.private) }
        }

        fun verifies(challenge: String, signature: String): Boolean {
            val pub = KeyFactory.getInstance("EC")
                .generatePublic(X509EncodedKeySpec(Base64.decode(publicKey(), Base64.DEFAULT)))
            return Signature.getInstance("SHA256withECDSA").run {
                initVerify(pub)
                update(challenge.toByteArray(Charsets.UTF_8))
                verify(Base64.decode(signature, Base64.DEFAULT))
            }
        }
    }

    private class FakeAuth(var accept: Boolean = true) : ApprovalAuth {
        var asked = 0
        override fun authenticate(activity: FragmentActivity, signer: Signature, onDone: (Signature?) -> Unit) {
            asked++
            onDone(if (accept) signer else null)
        }
    }

    private val key = SoftKey()
    private val auth = FakeAuth()
    private val sent = ArrayList<String>()
    private val signed = ArrayList<Pair<String, String>>()

    private fun install() {
        ApprovalProof.key = key
        ApprovalProof.auth = auth
    }

    @After
    fun reset() {
        ApprovalProof.reset()
        ApprovalBridge.carRoute = null
    }

    private fun host(): FragmentActivity = Robolectric.buildActivity(FragmentActivity::class.java).setup().get()

    private fun challenge(id: String) = "$id\nsend_message:sensitive:0123456789abcdef0123456789abcdef"

    private fun raise(id: String, choices: List<String> = listOf("once", "deny")): Boolean =
        ApprovalBridge.raiseHermesRequest(
            RuntimeEnvironment.getApplication(), id, "send_message", "{}", CARD, choices, 60,
            challenge = challenge(id), signedSend = { choice, sig -> signed.add(choice to sig) },
        ) { sent.add(it) }

    // --- the proof ------------------------------------------------------------------

    @Test
    fun `a fingerprint signs the challenge and the registered key verifies it`() {
        install()
        var got: String? = null
        ApprovalProof.sign(host(), challenge("r1")) { got = it }
        assertEquals(1, auth.asked)
        assertTrue("the adapter's check of the signature", key.verifies(challenge("r1"), got!!))
        assertTrue("another challenge is not covered", !key.verifies(challenge("r2"), got!!))
        assertTrue("base64 with no line breaks", !got!!.contains("\n"))
    }

    @Test
    fun `a refused fingerprint gives no signature`() {
        install()
        auth.accept = false
        var called = false
        var got: String? = "unset"
        ApprovalProof.sign(host(), challenge("r1")) { called = true; got = it }
        assertTrue(called)
        assertNull(got)
    }

    @Test
    fun `a key the phone no longer has is not asked about at all`() {
        install()
        key.gone = true
        var got: String? = "unset"
        ApprovalProof.sign(host(), challenge("r1")) { got = it }
        assertNull(got)
        assertEquals("no prompt for a key that cannot sign", 0, auth.asked)
    }

    @Test
    fun `a key an enrolment change killed says to pair again, every other failure keeps the old note`() {
        install()
        fun note(): Pair<String?, String> {
            var out: Pair<String?, String>? = null
            ApprovalProof.signWithNote(host(), challenge("r1")) { sig, note -> out = sig to note }
            return out!!
        }
        key.invalidated = true
        assertEquals(null to ApprovalProof.KEY_CHANGED_NOTE, note())
        assertEquals("Fingerprints changed: pair this phone again", ApprovalProof.KEY_CHANGED_NOTE)
        assertEquals("no prompt for a key that cannot sign", 0, auth.asked)
        key.invalidated = false
        key.gone = true
        assertEquals(null to ApprovalProof.REFUSED_NOTE, note())
        key.gone = false
        auth.accept = false
        assertEquals(null to ApprovalProof.REFUSED_NOTE, note())
        auth.accept = true
        val (sig, said) = note()
        assertNotNull(sig)
        assertEquals("", said)
    }

    // --- registering the key, once ----------------------------------------------------

    private val prefs get() = RuntimeEnvironment.getApplication().getSharedPreferences("approval-key-test", Context.MODE_PRIVATE)

    @Test
    fun `the key is registered once per adapter`() {
        install()
        prefs.edit().clear().commit()
        val posts = ArrayList<String>()
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts.add(it); 200 })
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts.add(it); 200 })
        assertEquals(listOf(key.publicKey()), posts)
        ApprovalProof.register(prefs, "https://other.example") { posts.add(it); 200 }
        assertEquals("another adapter is a new registration", 2, posts.size)
    }

    @Test
    fun `a conflict says to pair again once, and is asked again until the server says yes`() {
        install()
        prefs.edit().clear().commit()
        var posts = 0
        val first = ApprovalProof.register(prefs, "https://adapter.example") { posts++; 409 }
        assertNotNull(first)
        assertTrue(first!!.contains("pair", ignoreCase = true))
        assertNull("said once", ApprovalProof.register(prefs, "https://adapter.example") { posts++; 409 })
        // Paired again: the same key is now accepted, and that ends it.
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts++; 200 })
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts++; 200 })
        assertEquals(3, posts)
    }

    @Test
    fun `another phone signing is not a reason to pair again, and it says what to do once`() {
        install()
        prefs.edit().clear().commit()
        val status = contract.getJSONObject("register").getInt("other_phone_status")
        var posts = 0
        val first = ApprovalProof.register(prefs, "https://adapter.example") { posts++; status }
        assertTrue(first!!.contains("Devices") && !first.contains("pair", ignoreCase = true))
        assertNull("said once", ApprovalProof.register(prefs, "https://adapter.example") { posts++; status })
        // The other phone was removed in the console: the same key is now taken.
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts++; 200 })
        assertEquals(3, posts)
    }

    @Test
    fun `the master key is told so once`() {
        install()
        prefs.edit().clear().commit()
        var posts = 0
        val first = ApprovalProof.register(prefs, "https://adapter.example") { posts++; 403 }
        assertTrue(first!!.contains("master", ignoreCase = true))
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts++; 403 })
        assertEquals("asked again: a phone pairing changes the answer", 2, posts)
    }

    @Test
    fun `an unreachable adapter is tried again next time`() {
        install()
        prefs.edit().clear().commit()
        var posts = 0
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts++; -1 })
        assertNull(ApprovalProof.register(prefs, "https://adapter.example") { posts++; 502 })
        assertEquals(2, posts)
    }

    // --- the wire ---------------------------------------------------------------------

    @Test
    fun `the request's confirm and challenge fields are the contract's`() {
        val hud = HudState()
        val payload = JSONObject()
            .put("request_id", "r1").put("tool", "send_message").put("command", "{}")
            .put(contract.getString("confirm_field"), contract.getString("confirm_value"))
            .put(contract.getString("challenge_field"), challenge("r1"))
            .put("choices", org.json.JSONArray(listOf("once", "session", "always", "deny")))
        hud.apply("approval.request", payload)
        val a = hud.approvals.single()
        assertEquals("phone", a.confirm)
        assertEquals(challenge("r1"), a.challenge)
        val offered = contract.getJSONArray("choices")
        assertEquals("only what the adapter will forward", List(offered.length()) { offered.getString(it) }, a.choices)
    }

    @Test
    fun `an ask without a confirm field is the old ask`() {
        val hud = HudState()
        hud.apply("approval.request", JSONObject().put("request_id", "r2").put("tool", "t").put("command", "x"))
        val a = hud.approvals.single()
        assertEquals("", a.confirm)
        assertEquals(listOf("once", "session", "always", "deny"), a.choices)
    }

    // --- the bridge --------------------------------------------------------------------

    @Test
    fun `an allow with no signature is a deny on the wire`() {
        assertTrue(raise("b1"))
        ApprovalBridge.deliverChoice("b1", "once")
        assertEquals(listOf("deny"), sent)
        assertTrue("nothing signed was sent", signed.isEmpty())
    }

    @Test
    fun `an allow with a signature is sent signed, never bare`() {
        assertTrue(raise("b2"))
        ApprovalBridge.deliverChoice("b2", "once", "c2ln")
        assertEquals(listOf("once" to "c2ln"), signed)
        assertTrue("no bare allow beside it", sent.isEmpty())
    }

    @Test
    fun `a deny needs no signature`() {
        assertTrue(raise("b3"))
        ApprovalBridge.deliverChoice("b3", "deny")
        assertEquals(listOf("deny"), sent)
    }

    @Test
    fun `a yes from the old Boolean path cannot allow a phone-confirmed ask`() {
        assertTrue(raise("b4"))
        ApprovalBridge.deliver("b4", true)
        assertEquals(listOf("deny"), sent)
    }

    @Test
    fun `the car is never asked a phone-confirmed ask`() {
        var asked = 0
        ApprovalBridge.carRoute = { _, _, _ -> asked++; true }
        assertTrue(raise("b5"))
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals("the car's plain yes would be a once without a signature", 0, asked)
    }

    @Test
    fun `an ask without a challenge still reaches the car`() {
        var asked = 0
        ApprovalBridge.carRoute = { _, _, _ -> asked++; null }
        ApprovalBridge.raiseHermesRequest(RuntimeEnvironment.getApplication(), "b6", "light_on", "{}", "", listOf("once", "deny"), 60) { sent.add(it) }
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(1, asked)
    }

    // --- the card ----------------------------------------------------------------------

    private fun open(id: String) {
        val app = RuntimeEnvironment.getApplication()
        assertTrue(raise(id))
        val intent = Intent(app, ApprovalActivity::class.java)
            .putExtra(ApprovalBridge.EXTRA_REQUEST_ID, id)
            .putExtra(ApprovalBridge.EXTRA_ACTION_ID, "send_message")
            .putExtra(ApprovalBridge.EXTRA_REASON, CARD)
            .putExtra(ApprovalBridge.EXTRA_PARAMS, "{}")
            .putExtra(ApprovalBridge.EXTRA_CHOICES, arrayOf("once", "deny"))
            .putExtra(ApprovalBridge.EXTRA_CHALLENGE, challenge(id))
        Robolectric.buildActivity(ApprovalActivity::class.java, intent).setup()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(900))
        Snapshot.sendApplyNotifications()
        compose.waitForIdle()
    }

    @Test
    fun `allow on the card asks for the fingerprint and sends the signature`() {
        install()
        open("c1")
        compose.onNodeWithText("ALLOW").performClick()
        compose.waitForIdle()
        assertEquals(1, auth.asked)
        val (choice, sig) = signed.single()
        assertEquals("once", choice)
        assertTrue(key.verifies(challenge("c1"), sig))
        assertTrue(sent.isEmpty())
    }

    @Test
    fun `a refused fingerprint leaves the ask open and sends nothing`() {
        install()
        auth.accept = false
        open("c2")
        compose.onNodeWithText("ALLOW").performClick()
        compose.waitForIdle()
        assertEquals(1, auth.asked)
        assertTrue(signed.isEmpty() && sent.isEmpty())
        assertTrue("still waiting for an answer", ApprovalBridge.isPending("c2"))
        compose.onNodeWithText("DENY").performClick()
        compose.waitForIdle()
        assertEquals(listOf("deny"), sent)
    }

    @Test
    fun `a key an enrolment change killed puts the pair-again note on the card`() {
        install()
        key.invalidated = true
        open("c3")
        compose.onNodeWithText("ALLOW").performClick()
        compose.waitForIdle()
        compose.onNodeWithText(ApprovalProof.KEY_CHANGED_NOTE).assertExists()
        assertTrue(signed.isEmpty() && sent.isEmpty())
        assertTrue("still waiting for an answer", ApprovalBridge.isPending("c3"))
    }

    private companion object {
        const val CARD = "The model wants to text Sam.\nWhat: Text Sam\nWhy: You asked\n" +
            "Impact: Sam gets a text\nUndoable: yes\nHome: no"
    }
}
