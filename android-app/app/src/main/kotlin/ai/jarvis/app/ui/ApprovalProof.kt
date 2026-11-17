package ai.jarvis.app.ui

import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/** The key the fingerprint unlocks; seam so a test can stand a software key in for the Keystore. */
interface ApprovalKey {
    /** Base64 X.509 SPKI DER of the public half, or null when there is no key to offer. */
    fun publicKey(): String?

    /**
     * A Signature primed to sign, or null when the key is gone (it never falls back to a plain one).
     * Throws KeyPermanentlyInvalidatedException when an enrolment change killed it.
     */
    fun signer(): Signature?
}

/** The prompt that makes the person touch the sensor before [signer] may be used. */
interface ApprovalAuth {
    /** [onDone] gets the unlocked signer, or null when the person was refused or backed out. */
    fun authenticate(activity: FragmentActivity, signer: Signature, onDone: (Signature?) -> Unit)
}

/**
 * T-0945 on the phone: a sensitive Hermes approval is allowed only with a signature, made by a
 * hardware-backed P-256 key that the Keystore releases for one signature after a fingerprint.
 * No PIN or pattern stands in for it. The adapter holds the public half and forwards an allow only when
 * the signature verifies over the ask's challenge.
 *
 * What this does NOT cover: that the key lives in secure hardware is the Keystore's promise (a
 * phone without it still makes a software-backed key), and a phone with no fingerprint and no
 * screen lock cannot make the key at all, so it can never allow a sensitive ask. The wire field
 * names are bound to `tests/contracts/approval-event.json` by the card's test.
 */
object ApprovalProof {
    const val CONFIRM_PHONE = "phone"
    const val CONFIRM_FIELD = "confirm"
    const val CHALLENGE_FIELD = "challenge"
    const val SIGNATURE_FIELD = "signature"
    const val REGISTER_PATH = "/api/approval/key"
    const val REGISTER_FIELD = "public_key"

    /** The only answers the adapter will forward for a phone-confirmed ask. */
    val CHOICES: List<String> = listOf("once", "deny")

    /** What the screen says when the person's fingerprint was not accepted. */
    const val REFUSED_NOTE = "The fingerprint was not accepted. Try again, or deny."

    /** The key died with an enrolment change; trying again cannot help, only pairing again can. */
    const val KEY_CHANGED_NOTE = "Fingerprints changed: pair this phone again"

    private const val TAG ="JarvisApprovalProof"
    private const val ALIAS = "jarvis.approval.signing"
    private const val CODE_OK = 200
    private const val CODE_CONFLICT = 409
    private const val CODE_FORBIDDEN = 403

    /** Another phone already signs for the house (T-1030): pairing again cannot change that. */
    private const val CODE_OTHER_PHONE = 423

    @Volatile
    var key: ApprovalKey = KeystoreApprovalKey

    @Volatile
    var auth: ApprovalAuth = BiometricAuth

    /** Put the real key and prompt back (tests). */
    fun reset() {
        key = KeystoreApprovalKey
        auth = BiometricAuth
    }

    /**
     * Sign [challenge] after the person proves themselves. [onDone] gets the base64 DER signature
     * (no line breaks), or null for every way it can fail: no key, a refused or cancelled prompt,
     * a signer that throws. A null is never turned into a signature further on.
     */
    fun sign(activity: FragmentActivity, challenge: String, onDone: (String?) -> Unit) =
        signWithNote(activity, challenge) { signature, _ -> onDone(signature) }

    /**
     * [sign], and with it the sentence for the screen when there is no signature: [KEY_CHANGED_NOTE]
     * when an enrolment change killed the key (pairing again is the only way out), else
     * [REFUSED_NOTE]; empty with a signature. The phone's consent screen is the one place both the
     * phone and the car ask, so one note serves both.
     */
    fun signWithNote(activity: FragmentActivity, challenge: String, onDone: (String?, String) -> Unit) {
        val signer = try {
            key.signer()
        } catch (e: KeyPermanentlyInvalidatedException) {
            Log.w(TAG, "the signing key was invalidated by an enrolment change", e)
            onDone(null, KEY_CHANGED_NOTE)
            return
        } catch (t: Throwable) {
            Log.w(TAG, "the signing key could not be opened", t)
            null
        }
        if (signer == null) {
            onDone(null, REFUSED_NOTE)
            return
        }
        auth.authenticate(activity, signer) { unlocked ->
            val signature = unlocked?.let {
                try {
                    it.update(challenge.toByteArray(Charsets.UTF_8))
                    Base64.encodeToString(it.sign(), Base64.NO_WRAP)
                } catch (t: Throwable) {
                    Log.w(TAG, "the signature could not be made", t)
                    null
                }
            }
            onDone(signature, if (signature == null) REFUSED_NOTE else "")
        }
    }

    /**
     * Give the adapter the public key, once per adapter and key. [post] sends it and returns the
     * HTTP status (-1 when it never got one). Only a 200 ends it; every other answer is tried
     * again next session. Returns a sentence to show the person for 409, 423 and 403, once per
     * answer, else null. Blocking:
     * call it off the main thread.
     */
    fun register(prefs: SharedPreferences, serverUrl: String, post: (String) -> Int): String? {
        val pub = try {
            key.publicKey()
        } catch (t: Throwable) {
            Log.w(TAG, "no signing key to register", t)
            null
        } ?: return null
        val slot = "registered|$serverUrl"
        val before = prefs.getString(slot, null)
        if (before == "$pub|$CODE_OK") return null
        // A refusal is asked again every session: pairing again, or the other phone being
        // revoked, changes the answer, and nothing here can know that it happened. Only the
        // sentence is remembered, so it is said once per answer and not on every connect.
        val code = post(pub)
        if (code != CODE_OK && code != CODE_CONFLICT && code != CODE_FORBIDDEN && code != CODE_OTHER_PHONE) return null
        prefs.edit().putString(slot, "$pub|$code").apply()
        if (before == "$pub|$code") return null
        return when (code) {
            CODE_CONFLICT -> "This phone's approval key does not match the one the server has. Pair this phone again."
            CODE_OTHER_PHONE -> "Another phone already signs approvals for this house. Remove it under Devices in the console, then open this app again."
            CODE_FORBIDDEN -> "Only a phone paired as a phone can sign approvals; this link uses the master key."
            else -> null
        }
    }

    /** The Android Keystore's P-256 key: signs once per fingerprint, dies with an enrolment change. */
    private object KeystoreApprovalKey : ApprovalKey {
        private fun store(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

        private fun create(): java.security.PublicKey {
            val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(true)
                // Timeout 0: every signature needs its own prompt, not a window after one.
                // Biometric only: the owner asked for a fingerprint, and a PIN can be watched.
                .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
                // A new fingerprint on the phone must not inherit this key's trust.
                .setInvalidatedByBiometricEnrollment(true)
                .build()
            return KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
                .apply { initialize(spec) }.generateKeyPair().public
        }

        override fun publicKey(): String? {
            val existing = store().getCertificate(ALIAS)?.publicKey
            val pub = existing ?: create()
            return Base64.encodeToString(pub.encoded, Base64.NO_WRAP)
        }

        override fun signer(): Signature? {
            val private = store().getKey(ALIAS, null) as? PrivateKey ?: return null
            return try {
                Signature.getInstance("SHA256withECDSA").apply { initSign(private) }
            } catch (e: KeyPermanentlyInvalidatedException) {
                // The key can never sign again. Drop it so the next registration makes a new one
                // (the server then says to pair again, which is right: the trust was reset), and
                // let the caller tell the person why: a plain null reads as a refused fingerprint.
                store().deleteEntry(ALIAS)
                throw e
            }
        }
    }

    /** BiometricPrompt around a CryptoObject: the Keystore releases the signer only on success. */
    private object BiometricAuth : ApprovalAuth {
        override fun authenticate(activity: FragmentActivity, signer: Signature, onDone: (Signature?) -> Unit) {
            val allowed = BiometricManager.Authenticators.BIOMETRIC_STRONG
            val info = BiometricPrompt.PromptInfo.Builder()
                .setTitle("Confirm on this phone")
                .setSubtitle("Allow this action")
                .setAllowedAuthenticators(allowed)
                // Required once the device credential is not an option.
                .setNegativeButtonText("Cancel")
                .setConfirmationRequired(false)
                .build()
            val callback = object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onDone(result.cryptoObject?.signature)
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    onDone(null)
                }
                // onAuthenticationFailed: one rejected attempt; the sheet stays up for another.
            }
            try {
                BiometricPrompt(activity, ContextCompat.getMainExecutor(activity), callback)
                    .authenticate(info, BiometricPrompt.CryptoObject(signer))
            } catch (t: Throwable) {
                Log.w(TAG, "the fingerprint prompt could not be shown", t)
                onDone(null)
            }
        }
    }
}
