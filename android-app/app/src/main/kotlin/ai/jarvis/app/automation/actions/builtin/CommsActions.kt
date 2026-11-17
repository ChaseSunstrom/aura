package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.ui.theme.ActiveTheme
import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract
import android.telephony.SmsManager
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.ResolveResult
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.ui.Notify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Anything that reaches another human being is Tier 3, every time, with the
 * real number and the real message body on screen. `read_contacts` is Tier 2
 * because it is read-only but is other people's personal data.
 */

/**
 * Turning "text Mum" into a number, on the device, before anybody is asked to
 * approve it.
 *
 * Shared by [SendSms] and [PlaceCall] through [JarvisAction.resolve]. The
 * alternative — the model calls `read_contacts`, reads a number out of the
 * result, then calls `send_sms` with it — is two device round trips with two
 * consent surfaces in between, and an 8B planner does not reliably complete it.
 * The reported symptom was a text that was never sent, with permissions granted
 * and nothing in the log to say why.
 *
 * Ambiguity is refused rather than guessed. Three people called "Chris" is a
 * question for the user, and the model has `ask_user` to ask it with; picking
 * the alphabetically-first Chris and sending them a message is not a recovery.
 */
internal object ContactResolver {

    /** Parameter names that may carry either a number or a person. */
    val TARGET_KEYS = listOf("number", "to", "contact", "recipient", "name")

    sealed class Outcome {
        data class Number(val number: String, val name: String?) : Outcome()
        data class Ambiguous(val candidates: List<Pair<String, String>>) : Outcome()
        object NotFound : Outcome()
        object NoPermission : Outcome()
    }

    /** The value the caller aimed this action at, whichever key they used. */
    fun target(params: JSONObject): String? =
        TARGET_KEYS.firstNotNullOfOrNull { key -> params.str(key) }

    /**
     * Whether resolving THESE parameters will touch the address book.
     *
     * A literal number needs no lookup, so it must not raise "Allow Jarvis to
     * access your Contacts?". That matters more than a saved tap: the resolver
     * runs BEFORE the Tier-3 consent gate — it has to, so the prompt can show
     * the real number — which means a permission it asks for is one a server
     * can ask for without any human approving anything. Bounding it to the
     * calls that genuinely need contacts is what keeps that from being a way to
     * pester somebody into granting them.
     *
     * Nothing here reads the address book; it is the same string test
     * [resolveTarget] uses to decide the same question a moment later.
     */
    fun lookupPermissions(params: JSONObject): List<String> {
        val wanted = target(params) ?: return emptyList()
        if (PhoneNumbers.isPlausible(wanted)) return emptyList()
        return listOf(Manifest.permission.READ_CONTACTS)
    }

    /**
     * Look [query] up in contacts.
     *
     * Distinct *numbers* are what count as candidates, not distinct rows: one
     * person with a mobile listed twice under two accounts is not a choice
     * anybody needs to make, and treating it as one would refuse the commonest
     * lookup on a phone with two synced address books.
     */
    fun lookup(ctx: Context, query: String, limit: Int = 8): Outcome {
        if (!ctx.granted(Manifest.permission.READ_CONTACTS)) return Outcome.NoPermission
        val uri = Uri.withAppendedPath(
            ContactsContract.CommonDataKinds.Phone.CONTENT_FILTER_URI,
            Uri.encode(query),
        )
        val projection = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
        )
        // Keyed by the last nine digits, which is what makes "07700 900123"
        // and "+44 7700 900123" one number rather than a choice to put in
        // front of somebody. Insertion-ordered so the first spelling the
        // address book offers is the one shown.
        val found = LinkedHashMap<String, Pair<String, String>>() // key -> (number, name)
        try {
            ctx.contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
                val nameCol =
                    cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                val numberCol =
                    cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                while (cursor.moveToNext() && found.size <= limit) {
                    val number = if (numberCol >= 0) cursor.getString(numberCol) else null
                    if (number.isNullOrBlank()) continue
                    val key = number.filter { it.isDigit() }.takeLast(9)
                    if (key.isEmpty()) continue
                    val name = (if (nameCol >= 0) cursor.getString(nameCol) else null).orEmpty()
                    found.getOrPut(key) { number.trim() to name }
                }
            }
        } catch (e: SecurityException) {
            return Outcome.NoPermission
        } catch (e: Exception) {
            return Outcome.NotFound
        }
        return when (found.size) {
            0 -> Outcome.NotFound
            1 -> found.values.first().let { (number, name) ->
                Outcome.Number(number, name.takeIf { it.isNotBlank() })
            }
            else -> Outcome.Ambiguous(found.values.map { (number, name) -> name to number })
        }
    }

    /**
     * The shared [JarvisAction.resolve] body: leave a real number alone, turn a
     * name into one, refuse anything else.
     *
     * The resolved parameters keep the original spelling under `contact` so the
     * consent prompt can read "Mum · +44…" rather than a bare number — the
     * human is being asked about a person, and the number is the part that
     * proves which one.
     */
    fun resolveTarget(ctx: Context, params: JSONObject, verb: String): ResolveResult {
        val wanted = target(params) ?: return ResolveResult.Unchanged
        if (PhoneNumbers.isPlausible(wanted)) {
            // Already a number. Normalise the key so `execute` does not have to
            // know which of the five spellings the model chose.
            if (params.str("number") == wanted) return ResolveResult.Unchanged
            return ResolveResult.Resolved(params.copyWith("number" to wanted))
        }
        return when (val outcome = lookup(ctx, wanted)) {
            is Outcome.Number -> ResolveResult.Resolved(
                params.copyWith("number" to outcome.number, "contact" to (outcome.name ?: wanted)),
                "resolved \"$wanted\" to ${outcome.name ?: "an unnamed contact"} ${outcome.number}",
            )
            is Outcome.Ambiguous -> ResolveResult.Failed(
                "\"$wanted\" matches ${outcome.candidates.size} contacts " +
                    outcome.candidates.joinToString(", ") { (name, number) ->
                        "${name.ifBlank { "unnamed" }} ($number)"
                    } +
                    ". Ask which one before trying to $verb them."
            )
            Outcome.NotFound -> ResolveResult.Failed(
                "no contact matches \"$wanted\", and it is not a phone number. " +
                    "Ask for the number."
            )
            Outcome.NoPermission -> ResolveResult.Failed(
                "\"$wanted\" is a name, not a number, and ${ActiveTheme.name} has no contacts " +
                    "permission to look it up. Grant Contacts, or give the number."
            )
        }
    }
}

/** A shallow copy with [pairs] set — the original must never be mutated. */
private fun JSONObject.copyWith(vararg pairs: Pair<String, Any?>): JSONObject {
    val copy = JSONObject()
    for (key in keys()) copy.put(key, get(key))
    for ((key, value) in pairs) if (value != null) copy.put(key, value)
    return copy
}

/** Shared phone-number sanity checks. Pure enough to reason about in review. */
internal object PhoneNumbers {

    private val ALLOWED = Regex("^[+0-9 ()\\-.]{3,25}$")

    /** Emergency services — never dialled by an automation. */
    private val EMERGENCY = setOf(
        "911", "112", "999", "000", "111", "110", "119", "118", "102", "108", "115", "122"
    )

    fun isPlausible(number: String): Boolean {
        if (!ALLOWED.matches(number)) return false
        val digits = number.count { it.isDigit() }
        return digits in 3..20
    }

    fun isEmergency(number: String): Boolean {
        val digits = number.filter { it.isDigit() }
        return digits in EMERGENCY
    }
}

/** Tier 3 — messages another person. Confirmed every single time. */
object SendSms : JarvisAction {
    override val id = "send_sms"
    override val tier = ActionTier.CONFIRM
    override val description = "Send an SMS text message."
    override val paramsSchema = mapOf(
        "to" to "string: contact name OR phone number — a name is looked up on the device",
        "body" to "string: message text"
    )
    override val capability = "sms"
    override val requiredPermissions = listOf(Manifest.permission.SEND_SMS)

    /** The lookup below happens before the consent prompt, so this is asked
     *  for before it. See [JarvisAction.resolvePermissions]. */
    override val resolvePermissions = listOf(Manifest.permission.READ_CONTACTS)

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    override fun resolvePermissionsFor(params: JSONObject): List<String> =
        ContactResolver.lookupPermissions(params)

    /** "Text Mum" becomes a number here, before the consent prompt is drawn. */
    override suspend fun resolve(ctx: Context, params: JSONObject): ResolveResult =
        withContext(Dispatchers.IO) { ContactResolver.resolveTarget(ctx, params, "text") }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!ctx.granted(Manifest.permission.SEND_SMS)) {
            return ActionResult.missingPermission(Manifest.permission.SEND_SMS)
        }
        // `resolve` has already put a real number under `number` and the human
        // has approved that exact payload. Reading anything else here would be
        // executing something other than what was shown.
        val number = params.str("number")
            ?: return ActionResult.error("no recipient — pass a contact name or a number as 'to'")
        val body = params.str("body") ?: return ActionResult.error("body is required")
        if (!PhoneNumbers.isPlausible(number)) {
            return ActionResult.error("'$number' does not look like a phone number")
        }
        if (PhoneNumbers.isEmergency(number)) {
            return ActionResult.error("${ActiveTheme.name} will not message emergency services; do it by hand")
        }
        if (body.length > 2000) return ActionResult.error("message body is too long (max 2000 chars)")

        val sms = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                ctx.getSystemService(SmsManager::class.java)
            } else {
                @Suppress("DEPRECATION")
                SmsManager.getDefault()
            }
        } catch (e: Exception) {
            null
        } ?: return ActionResult.error("no SMS service on this device")

        // The shown body, in the carrier's alphabet where it can be (SmsText): the em dash
        // a model writes put long texts on the path Android refuses with getGroupIdLevel1.
        val text = SmsText.plain(body)
        return try {
            val parts = try {
                sms.divideMessage(text)
            } catch (e: SecurityException) {
                android.util.Log.i("JarvisSms", "divideMessage refused (${e.message}); splitting it here")
                SmsText.ucs2Parts(text)
            }
            val results = SmsSent.await(ctx, parts.size) { sent ->
                if (parts.size > 1) {
                    sms.sendMultipartTextMessage(number, null, parts, ArrayList(sent), null)
                } else {
                    sms.sendTextMessage(number, null, text, sent.first(), null)
                }
            }
            val failed = results?.firstOrNull { it != android.app.Activity.RESULT_OK }
            if (failed != null) {
                return ActionResult.error("the text was not sent: ${SmsSent.reason(failed)}")
            }
            ActionResult.ok(
                json(
                    "number" to number, "parts" to parts.size, "chars" to text.length, "confirmed" to (results != null),
                ).also { out -> smsStore(ctx, number)?.let { store -> store.keys().forEach { out.put(it, store.get(it)) } } },
            )
        } catch (e: SecurityException) {
            // Not "permission not granted": the check above already passed, so this is
            // Android refusing for its own reason (no default SMS SIM, a restricted
            // sideloaded app, a carrier policy). Reported as a missing permission, it
            // read as one the owner had already allowed (the 2026-10-08 drive).
            android.util.Log.w("JarvisSms", "sendTextMessage refused with SEND_SMS held", e)
            ActionResult.error("Android refused the text although SMS is allowed: ${e.message ?: e.javaClass.simpleName}")
        } catch (e: IllegalArgumentException) {
            ActionResult.error("SMS rejected: ${e.message ?: "bad number or empty body"}")
        }
    }
}

/**
 * Where the text just sent to [number] landed in the phone's SMS store, which the Messaging
 * app reads. Android writes a non-default app's sent text there itself; the owner could not
 * see Jarvis's texts in GrapheneOS Messaging (2026-10-08) although `stored_in_sms_app` said
 * the row was there, so the answer also carries that row's metadata beside the last sent row
 * another app wrote, and which app is the default: the next send shows what differs.
 * Metadata only -- the answer reaches the model, so no body is read. Null without READ_SMS.
 */
private suspend fun smsStore(ctx: Context, number: String): JSONObject? = withContext(Dispatchers.IO) {
    if (!ctx.granted(Manifest.permission.READ_SMS)) return@withContext null
    val digits = number.filter { it.isDigit() }.takeLast(10)
    val cols = arrayOf(
        android.provider.Telephony.Sms.ADDRESS, android.provider.Telephony.Sms.THREAD_ID,
        android.provider.Telephony.Sms.TYPE, android.provider.Telephony.Sms.STATUS,
        android.provider.Telephony.Sms.READ, android.provider.Telephony.Sms.SEEN,
        android.provider.Telephony.Sms.CREATOR, android.provider.Telephony.Sms.DATE,
    )
    // The provider writes the row as the send completes; one short look is enough.
    kotlinx.coroutines.delay(500)
    runCatching {
        var ours: JSONObject? = null
        var theirs: JSONObject? = null
        ctx.contentResolver.query(
            android.provider.Telephony.Sms.Sent.CONTENT_URI, cols, null, null,
            "${android.provider.Telephony.Sms.DATE} DESC LIMIT 50",
        )?.use { c ->
            while (c.moveToNext() && (ours == null || theirs == null)) {
                val row = JSONObject()
                for (i in 1 until cols.size) row.put(cols[i], c.getString(i))
                val mine = c.getString(6) == ctx.packageName
                val recent = c.getLong(7) >= System.currentTimeMillis() - 120_000
                if (ours == null && recent && c.getString(0).orEmpty().filter { it.isDigit() }.endsWith(digits)) {
                    ours = row
                } else if (theirs == null && !mine) {
                    theirs = row
                }
            }
        }
        json(
            "stored_in_sms_app" to (ours != null), "sms_row" to ours, "messaging_row" to theirs,
            "default_sms_app" to android.provider.Telephony.Sms.getDefaultSmsPackage(ctx),
        )
    }.getOrNull()
}

/** Tier 3 — starts a real call to a real person. */
object PlaceCall : JarvisAction {
    override val id = "place_call"
    override val tier = ActionTier.CONFIRM
    override val description = "Place a phone call immediately."
    override val paramsSchema = mapOf(
        "to" to "string: contact name OR phone number — a name is looked up on the device"
    )
    override val capability = "telephony"
    override val requiredPermissions = listOf(Manifest.permission.CALL_PHONE)

    /** Same as [SendSms]: the lookup runs ahead of the prompt, so its grant does too. */
    override val resolvePermissions = listOf(Manifest.permission.READ_CONTACTS)

    override fun resolvePermissionsFor(params: JSONObject): List<String> =
        ContactResolver.lookupPermissions(params)

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    /** "Ring Mum" becomes a number here, before the consent prompt is drawn. */
    override suspend fun resolve(ctx: Context, params: JSONObject): ResolveResult =
        withContext(Dispatchers.IO) { ContactResolver.resolveTarget(ctx, params, "call") }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!ctx.granted(Manifest.permission.CALL_PHONE)) {
            return ActionResult.missingPermission(Manifest.permission.CALL_PHONE)
        }
        val number = params.str("number")
            ?: return ActionResult.error("no recipient — pass a contact name or a number as 'to'")
        if (!PhoneNumbers.isPlausible(number)) {
            return ActionResult.error("'$number' does not look like a phone number")
        }
        // ACTION_CALL cannot dial emergency numbers anyway; say so honestly
        // rather than failing with a SecurityException.
        if (PhoneNumbers.isEmergency(number)) {
            return ActionResult.error(
                "Android does not allow apps to dial emergency numbers; use the dialer by hand"
            )
        }
        val intent = Intent(Intent.ACTION_CALL, Uri.parse("tel:${Uri.encode(number)}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            ctx.startActivity(intent)
            ActionResult.ok(json("number" to number))
        } catch (e: SecurityException) {
            ActionResult.missingPermission(Manifest.permission.CALL_PHONE)
        } catch (e: ActivityNotFoundException) {
            ActionResult.error("no dialer app on this device")
        }
    }
}

/**
 * Tier 2 — read-only, but it is other people's data, so it asks once.
 * Results are marked untrusted: a contact name is attacker-controllable text.
 */
object ReadContacts : JarvisAction {
    override val id = "read_contacts"
    override val tier = ActionTier.NOTIFY
    override val description = "Look up contacts by name or number."

    /** A contact name is whatever the person who synced it typed. */
    override val untrustedOutput = true
    override val paramsSchema = mapOf(
        "query" to "string: name or number fragment to search for",
        "limit" to "int: maximum matches (default 10)"
    )
    override val capability = "contacts"
    override val requiredPermissions = listOf(Manifest.permission.READ_CONTACTS)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            if (!ctx.granted(Manifest.permission.READ_CONTACTS)) {
                return@withContext ActionResult.missingPermission(Manifest.permission.READ_CONTACTS)
            }
            val query = params.str("query")
            val limit = params.optInt("limit", 10).coerceIn(1, 50)
            val projection = arrayOf(
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER,
                ContactsContract.CommonDataKinds.Phone.TYPE
            )
            val uri = if (query != null) {
                Uri.withAppendedPath(
                    ContactsContract.CommonDataKinds.Phone.CONTENT_FILTER_URI,
                    Uri.encode(query)
                )
            } else {
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI
            }
            val results = JSONArray()
            fun scan(from: Uri, keep: (String) -> Boolean) {
                ctx.contentResolver.query(
                    from,
                    projection,
                    null,
                    null,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC"
                )?.use { cursor ->
                    val nameCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                    val numberCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                    while (cursor.moveToNext() && results.length() < limit) {
                        val name = if (nameCol >= 0) cursor.getString(nameCol) else null
                        if (!keep(name.orEmpty())) continue
                        results.put(
                            json(
                                "name" to name,
                                "number" to (if (numberCol >= 0) cursor.getString(numberCol) else null)
                            )
                        )
                    }
                }
            }
            try {
                scan(uri) { true }
                // Nothing by the letters: a name as speech-to-text spelled it ("Issa" for the
                // contact "Isa"), so Jarvis said nobody was there (the owner, 2026-10-07).
                if (results.length() == 0 && query != null && query.any { it.isLetter() }) {
                    scan(ContactsContract.CommonDataKinds.Phone.CONTENT_URI) { soundsLike(query, it) }
                }
            } catch (e: SecurityException) {
                return@withContext ActionResult.missingPermission(Manifest.permission.READ_CONTACTS)
            } catch (e: Exception) {
                return@withContext ActionResult.error("contacts lookup failed: ${e.message ?: "unknown"}")
            }
            ActionResult.ok(
                json("contacts" to results, "count" to results.length()).markUntrusted()
            )
        }

    /**
     * Whether a word of [name] is [query] give or take a letter in four: "Issa" and "Isa",
     * "Cameron" and "Kameron". A loose match only offers candidates; Jarvis still reads
     * the name back before a text goes.
     */
    internal fun soundsLike(query: String, name: String): Boolean {
        val words = { t: String -> t.lowercase().split(Regex("[^\\p{L}]+")).filter { it.length >= 3 } }
        val asked = words(query)
        return asked.isNotEmpty() && words(name).any { w -> asked.any { q -> 1.0 - edits(q, w).toDouble() / maxOf(q.length, w.length) >= 0.7 } }
    }

    private fun edits(a: String, b: String): Int {
        var row = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val next = IntArray(b.length + 1).also { it[0] = i }
            for (j in 1..b.length) {
                next[j] = minOf(row[j] + 1, next[j - 1] + 1, row[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
            }
            row = next
        }
        return row[b.length]
    }
}

/** Tier 1 — a local notification on this phone only. Nothing leaves the device. */
object SendNotification : JarvisAction {
    override val id = "send_notification"
    override val tier = ActionTier.AUTO
    override val description = "Post a local notification on this phone."
    override val paramsSchema = mapOf(
        "title" to "string: notification title",
        "text" to "string: notification body",
        "priority" to "string (optional): low | default | high"
    )
    override val capability = "notifications"
    override val requiredPermissions = listOf(Manifest.permission.POST_NOTIFICATIONS)

    private const val CHANNEL_ID = "jarvis_actions"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !ctx.granted(Manifest.permission.POST_NOTIFICATIONS)
        ) {
            return ActionResult.missingPermission(Manifest.permission.POST_NOTIFICATIONS)
        }
        val title = params.str("title") ?: return ActionResult.error("title is required")
        val text = params.str("text").orEmpty()
        val nm = ctx.getSystemService(NotificationManager::class.java)
            ?: return ActionResult.error("no notification service")

        val importance = when (params.str("priority")?.lowercase()) {
            "low" -> NotificationManager.IMPORTANCE_LOW
            "high" -> NotificationManager.IMPORTANCE_HIGH
            else -> NotificationManager.IMPORTANCE_DEFAULT
        }
        runCatching {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "${ActiveTheme.name}", importance).apply {
                    description = "Notifications ${ActiveTheme.name} was asked to post"
                }
            )
        }

        return try {
            val notification = Notification.Builder(ctx, CHANNEL_ID)
                // The Jarvis reactor. Same drawable as the approval prompt and
                // the automation service, so everything this app posts is
                // recognisably from the same app in the status bar.
                .setSmallIcon(ai.jarvis.app.ui.theme.ActiveTheme.statusIcon)
                .setColor(Notify.color(Notify.Template.NOTICE))
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .build()
            val notificationId = (System.currentTimeMillis() % Int.MAX_VALUE).toInt()
            nm.notify(notificationId, notification)
            ActionResult.ok(json("notification_id" to notificationId, "title" to title))
        } catch (e: Exception) {
            ActionResult.error("could not post the notification: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}

/**
 * Android's own word on whether a text left the phone. Without it every send answered
 * "ok" the moment it was queued, so a text the radio never sent was reported as sent
 * (the owner, 2026-10-08: "I don't think this time though"). What it does not cover:
 * delivery to the other phone, and the Messages app's thread -- a text sent by an app
 * that is not the default SMS app is the system's to record, and Google Messages may
 * not show it.
 */
object SmsSent {
    /** Under the action's 15 s timeout; a sent result normally arrives in a second or two. */
    const val WAIT_MS = 10_000L

    /** Each part's result code, or null when Android did not answer within [WAIT_MS]. */
    suspend fun await(ctx: Context, parts: Int, send: (List<android.app.PendingIntent>) -> Unit): List<Int>? {
        val action = "ai.jarvis.app.SMS_SENT." + java.util.UUID.randomUUID()
        val codes = java.util.Collections.synchronizedList(ArrayList<Int>())
        val done = kotlinx.coroutines.CompletableDeferred<Unit>()
        val receiver = object : android.content.BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                codes += resultCode
                if (codes.size >= parts) done.complete(Unit)
            }
        }
        val app = ctx.applicationContext
        androidx.core.content.ContextCompat.registerReceiver(
            app, receiver, android.content.IntentFilter(action), androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        return try {
            val sent = (0 until parts).map { n ->
                android.app.PendingIntent.getBroadcast(
                    app, n, Intent(action).setPackage(app.packageName),
                    android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_ONE_SHOT,
                )
            }
            send(sent)
            kotlinx.coroutines.withTimeoutOrNull(WAIT_MS) { done.await() }?.let { codes.toList() }
        } finally {
            runCatching { app.unregisterReceiver(receiver) }
        }
    }

    /** Pure: what an SmsManager result code means, in words the model can repeat. */
    fun reason(code: Int): String = when (code) {
        SmsManager.RESULT_ERROR_RADIO_OFF -> "the phone's radio is off (airplane mode?)"
        SmsManager.RESULT_ERROR_NO_SERVICE -> "no mobile service right now"
        SmsManager.RESULT_ERROR_NULL_PDU -> "Android could not encode the message"
        SmsManager.RESULT_ERROR_GENERIC_FAILURE -> "the carrier or modem refused it"
        else -> "Android reported error $code"
    }
}
