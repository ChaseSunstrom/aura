package ai.jarvis.app.calls

import android.os.Build
import android.telecom.Call
import android.telecom.CallScreeningService

/**
 * Screens an incoming call from a number that is not in the contacts (T-0616), by the owner's rule:
 * ring (the default: the service does nothing), silence, or reject.
 *
 * Only runs once the owner grants the call-screening role; nothing here asks for it. The number
 * is read from `details.handle`, compared to the address book here and never logged or sent: the
 * call event carries the dialer's label only. A saved contact always rings. Does not cover: a
 * hidden number (no handle) is a stranger; a rejected call still reaches the call log as the
 * platform records it.
 */
class JarvisCallScreeningService : CallScreeningService() {
    override fun onScreenCall(details: Call.Details) {
        val response = CallResponse.Builder()
        val number = details.handle?.schemeSpecificPart.orEmpty()
        val incoming = Build.VERSION.SDK_INT < 29 || details.callDirection == Call.Details.DIRECTION_INCOMING
        val contact = incoming && number.isNotEmpty() && ContactMatch.saved(this, number)
        when (CallEvents.screenResponse(CallActions.savedRule(this), CallEvents.relationshipOf(contact, false))) {
            "silence" -> response.setSilenceCall(true)
            "reject" -> response.setDisallowCall(true).setRejectCall(true).setSkipNotification(false)
        }
        respondToCall(details, response.build())
    }
}

/** The one number lookup the screening needs: PhoneLookup, which matches formats the way the dialer does. */
internal object ContactMatch {
    fun saved(ctx: android.content.Context, number: String): Boolean = try {
        val uri = android.net.Uri.withAppendedPath(
            android.provider.ContactsContract.PhoneLookup.CONTENT_FILTER_URI, android.net.Uri.encode(number),
        )
        ctx.contentResolver.query(uri, arrayOf(android.provider.ContactsContract.PhoneLookup._ID), null, null, null)
            ?.use { it.moveToFirst() } ?: false
    } catch (_: Exception) {
        // No contacts permission: a stranger, so the owner's rule applies rather than a silent ring.
        false
    }
}
