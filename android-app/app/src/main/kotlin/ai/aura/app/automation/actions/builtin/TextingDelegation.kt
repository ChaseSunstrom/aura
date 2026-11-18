package ai.aura.app.automation.actions.builtin

import ai.aura.app.automation.actions.granted
import android.Manifest
import android.content.Context
import android.provider.Telephony
import android.telephony.PhoneNumberUtils

/**
 * Which texts the owner's "read and reply to my texts without asking" switch covers
 * (PolicyStore.textingDelegated, 2026-10-07): reading them, and a REPLY — a send to a
 * number that is exactly one address-book contact AND has texted this phone in the
 * last [REPLY_WINDOW_MS]. Everything else still asks on the phone.
 *
 * Numbers are compared in canonical E.164 ([PhoneNumberUtils.formatNumberToE164] in
 * this phone's region), and must be identical: not a digit suffix ("+1 … 700 900 123"
 * and "+44 7700 900123" share nine digits) and not areSamePhoneNumber, whose loose
 * matching is a second parser beside the one SmsManager uses. A number that does not
 * canonicalise is never delegated.
 *
 * Does NOT cover: a hostile text steering the model to message a contact who has
 * written lately. The window, contacts-only and the daily cap bound that; the owner
 * turns the switch off to close it.
 */
internal object TextingDelegation {
    const val REPLY_WINDOW_MS = 7L * 24 * 60 * 60 * 1000

    fun isReplyToContact(ctx: Context, number: String): Boolean {
        if (!PhoneNumbers.isPlausible(number) || PhoneNumbers.isEmergency(number)) return false
        val iso = country(ctx)
        val wanted = e164(number, iso) ?: return false
        val found = runCatching { ContactResolver.lookup(ctx, number) }.getOrNull()
        if (found !is ContactResolver.Outcome.Number) return false
        if (e164(found.number, iso) != wanted) return false
        return wroteLately(ctx, wanted, iso)
    }

    private fun wroteLately(ctx: Context, wanted: String, iso: String): Boolean {
        if (!ctx.granted(Manifest.permission.READ_SMS)) return false
        val since = System.currentTimeMillis() - REPLY_WINDOW_MS
        return runCatching {
            ctx.contentResolver.query(
                Telephony.Sms.Inbox.CONTENT_URI, arrayOf(Telephony.Sms.ADDRESS),
                "${Telephony.Sms.DATE} >= ?", arrayOf(since.toString()), null,
            )?.use { c ->
                var seen = false
                while (!seen && c.moveToNext()) {
                    val address = c.getString(0) ?: continue
                    seen = e164(address, iso) == wanted
                }
                seen
            } ?: false
        }.getOrDefault(false)
    }

    private fun e164(number: String, iso: String): String? =
        PhoneNumberUtils.formatNumberToE164(number.trim(), iso)?.takeIf { it.startsWith("+") }

    // The phone's region from its locale, not TelephonyManager: the call-detection
    // mirror bans that class outright (it needs READ_PHONE_STATE for other reads).
    private fun country(ctx: Context): String =
        ctx.resources.configuration.locales[0].country.ifBlank { "US" }.lowercase()
}
