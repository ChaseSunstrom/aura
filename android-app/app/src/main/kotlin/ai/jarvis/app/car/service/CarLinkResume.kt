package ai.jarvis.app.car.service

import android.content.Context

/**
 * Which car the link was serving, kept across process death.
 *
 * START_REDELIVER_INTENT only redelivers the LAST intent, and the notification buttons
 * ("phone screen", hold, cover) return START_STICKY, so a kill after one of them restarts the
 * service with a null intent and no car address. This is that address. It does not start
 * anything: the service still has to be started by the OS or the car's ACL connection.
 */
object CarLinkResume {
    /** A drive is hours, not days; an address older than this is a different outing. */
    const val MAX_AGE_MS = 12 * 60 * 60 * 1000L

    private const val FILE = "car_link_resume"
    private const val KEY_ADDRESS = "address"
    private const val KEY_AT = "at"

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun remember(context: Context, address: String, now: Long = System.currentTimeMillis()) {
        prefs(context).edit().putString(KEY_ADDRESS, address).putLong(KEY_AT, now).apply()
    }

    fun forget(context: Context) {
        prefs(context).edit().clear().apply()
    }

    /**
     * The address to link: the intent's, else the one already held, else (only when the OS
     * restarted the service with no intent) the remembered one, if it is recent.
     */
    fun resolve(
        context: Context,
        intentAddress: String?,
        current: String?,
        restarted: Boolean,
        now: Long = System.currentTimeMillis(),
    ): String? {
        intentAddress?.let { return it }
        current?.let { return it }
        if (!restarted) return null
        val p = prefs(context)
        val at = p.getLong(KEY_AT, 0L)
        if (at <= 0L || now - at > MAX_AGE_MS) return null
        return p.getString(KEY_ADDRESS, null)
    }
}
