package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.policy.ActionTier
import android.Manifest
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject

/**
 * What is around the phone: Wi-Fi networks in range, Bluetooth devices advertising
 * nearby, and the ones paired with it. The phone-side half of "who is around"; the
 * house's netscan is the home-LAN half.
 *
 * Tier 2 (NOTIFY): the networks and devices around a phone say where it is. What it
 * does not do: identify phones by Bluetooth -- a phone's BLE address changes every
 * few minutes and most advertise no name, so the useful finds are watches, earbuds,
 * cars, TVs and trackers. Android also throttles Wi-Fi scans to four in two minutes,
 * so a fast repeat returns the previous scan (`wifi_fresh: false`).
 */
object ScanNearby : JarvisAction {
    override val id = "scan_nearby"
    override val tier = ActionTier.NOTIFY
    override val description = "List Wi-Fi networks in range, nearby Bluetooth devices and paired devices."
    override val paramsSchema = mapOf("seconds" to "int: how long to listen for Bluetooth (default 6, max 15)")
    override val capability = "location"
    override val requiredPermissions = listOf(
        Manifest.permission.ACCESS_FINE_LOCATION,
        Manifest.permission.BLUETOOTH_SCAN,
        Manifest.permission.BLUETOOTH_CONNECT,
    )
    override val timeoutMs = 30_000L

    /** One network row before it becomes JSON. */
    data class Net(val ssid: String, val bssid: String, val rssi: Int, val mhz: Int)

    /** Pure: one row per network name (its strongest access point), strongest first; hidden ones last. */
    fun topNetworks(seen: List<Net>, limit: Int = 25): List<Net> =
        seen.groupBy { it.ssid.ifBlank { "\u0000" + it.bssid } }
            .map { (_, aps) -> aps.maxBy { it.rssi } }
            .sortedWith(compareBy<Net> { it.ssid.isBlank() }.thenByDescending { it.rssi })
            .take(limit)

    private fun granted(ctx: Context, p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    @Suppress("MissingPermission", "DEPRECATION")
    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val out = JSONObject()
        val wifi = ctx.applicationContext.getSystemService(WifiManager::class.java)
        if (wifi != null && granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION)) {
            val fresh = runCatching { wifi.startScan() }.getOrDefault(false)
            if (fresh) delay(3_000L)
            val nets = runCatching { wifi.scanResults }.getOrDefault(emptyList()).map {
                Net(it.SSID.orEmpty(), it.BSSID.orEmpty(), it.level, it.frequency)
            }
            out.put("wifi_fresh", fresh)
            out.put("wifi", JSONArray(topNetworks(nets).map {
                JSONObject().put("ssid", it.ssid.ifBlank { "(hidden)" }).put("rssi", it.rssi)
                    .put("band", if (it.mhz >= 5_900) "6GHz" else if (it.mhz >= 4_900) "5GHz" else "2.4GHz")
            }))
        } else {
            out.put("wifi_error", "needs the precise location permission")
        }

        val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter == null || !adapter.isEnabled) {
            out.put("bluetooth_error", "Bluetooth is off")
        } else if (!granted(ctx, Manifest.permission.BLUETOOTH_SCAN) || !granted(ctx, Manifest.permission.BLUETOOTH_CONNECT)) {
            out.put("bluetooth_error", "needs the Nearby devices permission")
        } else {
            out.put("paired", JSONArray(adapter.bondedDevices.orEmpty().map { JSONObject().put("name", it.name).put("address", it.address) }))
            val found = HashMap<String, JSONObject>()
            val callback = object : ScanCallback() {
                override fun onScanResult(callbackType: Int, result: ScanResult) {
                    val name = result.scanRecord?.deviceName ?: runCatching { result.device.name }.getOrNull()
                    synchronized(found) {
                        found[result.device.address] = JSONObject().put("name", name).put("address", result.device.address)
                            .put("rssi", result.rssi)
                    }
                }
            }
            val scanner = adapter.bluetoothLeScanner
            val seconds = params.intOr("seconds", 6).coerceIn(1, 15)
            runCatching {
                scanner?.startScan(null, ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), callback)
                delay(seconds * 1_000L)
            }
            runCatching { scanner?.stopScan(callback) }
            val rows = synchronized(found) { found.values.toList() }
            // Named first, then strongest: an unnamed address is rarely something the owner can place.
            out.put("bluetooth", JSONArray(rows.sortedWith(compareBy<JSONObject> { it.isNull("name") }.thenByDescending { it.optInt("rssi") }).take(30)))
            out.put("bluetooth_total", rows.size)
        }
        return ActionResult.ok(out)
    }
}
