package ai.aura.app.car.setup

import android.content.Context
import org.json.JSONObject

/**
 * What worked for one car, kept by its Bluetooth address. Headway already remembered
 * the certificate per car; this also keeps the join choices, so the second drive
 * starts where the first one succeeded rather than re-learning them.
 *
 * Does not hold the passphrase: the car sends it on every handshake, and a copy at
 * rest would be one more place for it to leak from.
 */
data class CarProfile(
    val btAddress: String,
    val ssid: String,
    val bssid: String? = null,
    val staticIp: Boolean = false,
    val gateway: String? = null,
    val certificate: String? = null,
    val pinBssid: Boolean = false,
    val hiddenSsid: Boolean = false,
) {
    fun encode(): String = JSONObject()
        .put("btAddress", btAddress)
        .put("ssid", ssid)
        .putOpt("bssid", bssid)
        .put("staticIp", staticIp)
        .putOpt("gateway", gateway)
        .putOpt("certificate", certificate)
        .put("pinBssid", pinBssid)
        .put("hiddenSsid", hiddenSsid)
        .toString()

    companion object {
        fun decode(text: String): CarProfile? = runCatching {
            val o = JSONObject(text)
            CarProfile(
                btAddress = o.getString("btAddress"),
                ssid = o.optString("ssid"),
                bssid = o.optString("bssid").ifEmpty { null },
                staticIp = o.optBoolean("staticIp", false),
                gateway = o.optString("gateway").ifEmpty { null },
                certificate = o.optString("certificate").ifEmpty { null },
                pinBssid = o.optBoolean("pinBssid", false),
                hiddenSsid = o.optBoolean("hiddenSsid", false),
            )
        }.getOrNull()
    }
}

/** Every car's profile, in one preference. */
object CarProfiles {
    private const val PREFS = "car_profiles"
    private const val KEY = "profiles"

    fun encodeAll(profiles: Map<String, CarProfile>): String =
        JSONObject().apply { profiles.forEach { (address, p) -> put(address, JSONObject(p.encode())) } }.toString()

    fun decodeAll(text: String?): Map<String, CarProfile> = runCatching {
        val o = JSONObject(text ?: return emptyMap())
        o.keys().asSequence().mapNotNull { key -> CarProfile.decode(o.getJSONObject(key).toString())?.let { key to it } }.toMap()
    }.getOrDefault(emptyMap())

    fun all(context: Context): Map<String, CarProfile> =
        decodeAll(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null))

    fun of(context: Context, btAddress: String): CarProfile? = all(context)[btAddress]

    fun save(context: Context, profile: CarProfile) {
        val next = all(context) + (profile.btAddress to profile)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, encodeAll(next)).apply()
    }

    /** Merges into the car's profile, creating it if this is the car's first handshake. */
    fun update(context: Context, btAddress: String, ssid: String, change: (CarProfile) -> CarProfile) {
        save(context, change(of(context, btAddress) ?: CarProfile(btAddress = btAddress, ssid = ssid)))
    }
}

data class StaticIp(val address: String, val gateway: String, val prefix: Int)

/**
 * The static configuration for a car that hands out no addresses, from the head
 * unit's own address (its endpoint, which is its gateway): the same /24, host .100,
 * or .101 when the car itself is .100. Null for anything that is not a dotted IPv4
 * address -- the setup screen then shows the fields empty rather than a guess.
 */
fun staticIpFor(gateway: String): StaticIp? {
    val parts = gateway.split(".")
    if (parts.size != 4 || parts.any { it.toIntOrNull()?.let { n -> n in 0..255 } != true }) return null
    val host = if (parts[3] == "100") "101" else "100"
    return StaticIp("${parts[0]}.${parts[1]}.${parts[2]}.$host", gateway, 24)
}
