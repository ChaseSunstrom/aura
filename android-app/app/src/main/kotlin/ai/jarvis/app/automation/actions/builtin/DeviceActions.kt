package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.ui.theme.ActiveTheme
import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Looper
import android.os.PowerManager
import android.os.StatFs
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.boolOr
import ai.jarvis.app.automation.actions.clampPercent
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.longOr
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import kotlin.coroutines.resume

/**
 * Device and system state. Reads are Tier 1; anything that changes a system
 * setting is Tier 2 even when it is trivially reversible, except volume and
 * torch, which the shared brief names as Tier 1.
 *
 * Every guarded API is behind an explicit runtime check that returns
 * `permission X not granted` rather than throwing — a denied permission is a
 * normal answer, not a crash.
 */

/** Tier 1 — the one big read the model uses to orient itself. */
object GetDeviceState : JarvisAction {
    override val id = "get_device_state"
    override val tier = ActionTier.AUTO
    override val description =
        "Read this phone's state: battery, charging, network type, wifi SSID, screen, " +
            "ringer/DND, volumes, free storage, model and Android version."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "device_state"
    override val requiredPermissions = listOf(Manifest.permission.ACCESS_NETWORK_STATE)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val out = JSONObject()

            runCatching {
                ctx.getSystemService(BatteryManager::class.java)?.let { bm ->
                    out.put("battery_percent", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
                    out.put("charging", bm.isCharging)
                }
            }

            runCatching {
                ctx.getSystemService(PowerManager::class.java)?.let { pm ->
                    out.put("screen_on", pm.isInteractive)
                    out.put("power_save", pm.isPowerSaveMode)
                }
            }

            if (ctx.granted(Manifest.permission.ACCESS_NETWORK_STATE)) {
                runCatching {
                    val cm = ctx.getSystemService(ConnectivityManager::class.java)
                    val caps = cm?.activeNetwork?.let { cm.getNetworkCapabilities(it) }
                    out.put(
                        "network",
                        when {
                            caps == null -> "none"
                            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                            caps.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH) -> "bluetooth"
                            else -> "other"
                        }
                    )
                    out.put("network_validated", caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true)
                    out.put("vpn", caps?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true)
                    out.put("metered", caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) == false)
                }
            } else {
                out.put("network", "unknown")
            }

            // Android 10+ hides the SSID from apps without fine location, and
            // returns the "<unknown ssid>" placeholder instead. Ask only when
            // we can actually get an answer.
            if (ctx.granted(Manifest.permission.ACCESS_FINE_LOCATION)) {
                runCatching {
                    val wm = ctx.applicationContext.getSystemService(WifiManager::class.java)
                    @Suppress("DEPRECATION")
                    val ssid = wm?.connectionInfo?.ssid?.trim('"').orEmpty()
                    if (ssid.isNotEmpty() && ssid != "<unknown ssid>") out.put("wifi_ssid", ssid)
                }
            }

            runCatching {
                val am = ctx.getSystemService(AudioManager::class.java)
                if (am != null) {
                    out.put("ringer_mode", ringerName(am.ringerMode))
                    val volumes = JSONObject()
                    for ((name, stream) in STREAMS) {
                        val max = am.getStreamMaxVolume(stream)
                        if (max > 0) {
                            volumes.put(name, am.getStreamVolume(stream) * 100 / max)
                        }
                    }
                    out.put("volume_percent", volumes)
                    out.put("music_active", am.isMusicActive)
                }
            }

            runCatching {
                val nm = ctx.getSystemService(NotificationManager::class.java)
                out.put("dnd", dndName(nm?.currentInterruptionFilter))
                out.put("dnd_access_granted", nm?.isNotificationPolicyAccessGranted == true)
            }

            runCatching {
                val stat = StatFs(ctx.filesDir.absolutePath)
                out.put("storage_free_mb", stat.availableBytes / (1024 * 1024))
                out.put("storage_total_mb", stat.totalBytes / (1024 * 1024))
            }

            out.put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
            out.put("android_sdk", Build.VERSION.SDK_INT)
            out.put("android_release", Build.VERSION.RELEASE)
            out.put("uptime_s", SystemClock.elapsedRealtime() / 1000)

            ActionResult.ok(out)
        }

    private fun ringerName(mode: Int) = when (mode) {
        AudioManager.RINGER_MODE_SILENT -> "silent"
        AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
        AudioManager.RINGER_MODE_NORMAL -> "normal"
        else -> "unknown"
    }

    private fun dndName(filter: Int?) = when (filter) {
        NotificationManager.INTERRUPTION_FILTER_ALL -> "off"
        NotificationManager.INTERRUPTION_FILTER_PRIORITY -> "priority"
        NotificationManager.INTERRUPTION_FILTER_ALARMS -> "alarms_only"
        NotificationManager.INTERRUPTION_FILTER_NONE -> "total_silence"
        else -> "unknown"
    }

    internal val STREAMS = linkedMapOf(
        "music" to AudioManager.STREAM_MUSIC,
        "ring" to AudioManager.STREAM_RING,
        "alarm" to AudioManager.STREAM_ALARM,
        "notification" to AudioManager.STREAM_NOTIFICATION,
        "call" to AudioManager.STREAM_VOICE_CALL,
        "system" to AudioManager.STREAM_SYSTEM
    )
}

/**
 * Tier 1 — what is warming the phone (the owner, 2026-10-08: "my phone is still running
 * really hot and it's killing my battery"). The server can see what reaches it and
 * nothing else; this is the phone's own account, read by the house without a prompt.
 *
 * Says what Android lets an app see of itself: its CPU time since it started, its own
 * recordings, its own running services, the routines' location watch and the in-app
 * tailnet. Does not cover other apps or the radios' own draw, which Android keeps from
 * an app; the battery's current is the whole phone's.
 */
object PowerReport : JarvisAction {
    override val id = "power_report"
    override val tier = ActionTier.AUTO
    override val description =
        "What is warming this phone and draining it: thermal status, battery temperature and " +
            "current, ${ActiveTheme.name}'s own CPU share, its recordings, location watch, services and tailnet; " +
            "and `ledger`, the last seven days of what each feature (wake, location, link, car) was on for, " +
            "the battery drop it shared and the peak temperature."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "device_state"
    override val requiredPermissions = emptyList<String>()

    @Suppress("DEPRECATION") // getRunningServices still answers an app about its own services
    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val out = JSONObject()
            runCatching {
                ctx.getSystemService(PowerManager::class.java)?.let { pm ->
                    out.put("thermal_status", THERMAL[pm.currentThermalStatus] ?: pm.currentThermalStatus.toString())
                    if (Build.VERSION.SDK_INT >= 30) {
                        val headroom = pm.getThermalHeadroom(10)
                        if (!headroom.isNaN()) out.put("thermal_headroom", Math.round(headroom * 100) / 100.0)
                    }
                    out.put("screen_on", pm.isInteractive)
                    out.put("power_save", pm.isPowerSaveMode)
                }
            }
            runCatching {
                val battery = ctx.registerReceiver(null, android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED))
                battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)?.takeIf { it != Int.MIN_VALUE }?.let {
                    out.put("battery_temp_c", it / 10.0)
                }
                ctx.getSystemService(BatteryManager::class.java)?.let { bm ->
                    out.put("battery_percent", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
                    out.put("charging", bm.isCharging)
                    // Microamps, negative while discharging on most phones; reported as mA drawn.
                    val now = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW)
                    if (now != Int.MIN_VALUE && now != 0) out.put("current_ma", -now / 1000)
                }
            }
            runCatching {
                val cpuMs = android.os.Process.getElapsedCpuTime()
                val upMs = SystemClock.elapsedRealtime() - android.os.Process.getStartElapsedRealtime()
                out.put("app_cpu_ms", cpuMs)
                out.put("app_uptime_ms", upMs)
                if (upMs > 0) out.put("app_cpu_percent", Math.round(cpuMs * 1000.0 / upMs) / 10.0)
            }
            runCatching {
                // Which of its threads, over one second now: the total alone could not say what
                // ran a whole core on the owner's phone (T-0154).
                val before = ThreadCpu.sample()
                val start = SystemClock.elapsedRealtime()
                kotlinx.coroutines.delay(1000)
                val after = ThreadCpu.sample()
                val window = SystemClock.elapsedRealtime() - start
                val ticks = android.system.Os.sysconf(android.system.OsConstants._SC_CLK_TCK)
                out.put(
                    "busy_threads",
                    JSONArray(
                        ThreadCpu.busiest(before, after, ticks, window).map { (name, share) ->
                            JSONObject().put("name", name).put("cpu_percent", share)
                        },
                    ),
                )
            }
            runCatching {
                val am = ctx.getSystemService(AudioManager::class.java)
                val recordings = am?.activeRecordingConfigurations.orEmpty()
                out.put("recordings", recordings.size)
                out.put("recording_sources", JSONArray(recordings.map { it.clientAudioSource }))
                out.put("audio_mode", am?.mode ?: -1)
            }
            runCatching {
                val manager = ctx.getSystemService(android.app.ActivityManager::class.java)
                val mine = manager?.getRunningServices(64).orEmpty().filter { it.service.packageName == ctx.packageName }
                out.put("services", JSONArray(mine.map { it.service.className.substringAfterLast('.') + if (it.foreground) " (foreground)" else "" }))
            }
            // Seven days of per-feature time, estimated drop and peak temperature (T-0582): a reading
            // is taken now so that the day is never older than this ask, then the ledger is answered.
            runCatching {
                PowerLedger.tick(ctx)
                out.put("ledger", PowerLedger.report(ctx))
            }
            out.put("location_watch", ai.jarvis.app.routines.RoutineWatcher.watchingPlaces)
            out.put("tailnet", runCatching { ai.jarvis.app.tailnet.TailnetLink.state() }.getOrDefault("unknown"))
            ActionResult.ok(out)
        }

    private val THERMAL = mapOf(
        PowerManager.THERMAL_STATUS_NONE to "none",
        PowerManager.THERMAL_STATUS_LIGHT to "light",
        PowerManager.THERMAL_STATUS_MODERATE to "moderate",
        PowerManager.THERMAL_STATUS_SEVERE to "severe",
        PowerManager.THERMAL_STATUS_CRITICAL to "critical",
        PowerManager.THERMAL_STATUS_EMERGENCY to "emergency",
        PowerManager.THERMAL_STATUS_SHUTDOWN to "shutdown",
    )
}

/** Tier 1 — named as Tier 1 in the shared brief ("set volume"). */
object SetVolume : JarvisAction {
    override val id = "set_volume"
    override val tier = ActionTier.AUTO
    override val description = "Set a volume stream to a percentage of its maximum."
    override val paramsSchema = mapOf(
        "stream" to "string: music (default) | ring | alarm | notification | call | system",
        "level" to "int 0-100: percentage of the stream's maximum"
    )
    override val capability = "media"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val am = ctx.getSystemService(AudioManager::class.java)
            ?: return ActionResult.error("no audio service")
        val name = (params.str("stream") ?: "music").lowercase()
        val stream = GetDeviceState.STREAMS[name]
            ?: return ActionResult.error("unknown stream '$name'; use one of ${GetDeviceState.STREAMS.keys}")
        if (!params.has("level")) return ActionResult.error("level (0-100) is required")
        val percent = params.intOr("level", 0).clampPercent()
        val max = am.getStreamMaxVolume(stream)
        val target = Math.round(max * percent / 100f)
        return try {
            am.setStreamVolume(stream, target, 0)
            ActionResult.ok(json("stream" to name, "level" to percent, "raw" to target, "max" to max))
        } catch (e: SecurityException) {
            // Touching ring/notification while DND is on needs policy access.
            ActionResult.error(
                "changing the $name volume needs Do Not Disturb access; " +
                    "grant it in Settings > Notifications > Do Not Disturb access"
            )
        }
    }
}

/** Tier 2 — silencing the phone is recoverable but you would want to know. */
object SetRingerMode : JarvisAction {
    override val id = "set_ringer_mode"
    override val tier = ActionTier.NOTIFY
    override val description = "Set the ringer to normal, vibrate or silent."
    override val paramsSchema = mapOf("mode" to "string: normal | vibrate | silent")
    override val capability = "media"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val am = ctx.getSystemService(AudioManager::class.java)
            ?: return ActionResult.error("no audio service")
        val mode = when ((params.str("mode") ?: "").lowercase()) {
            "normal", "loud" -> AudioManager.RINGER_MODE_NORMAL
            "vibrate" -> AudioManager.RINGER_MODE_VIBRATE
            "silent", "mute" -> AudioManager.RINGER_MODE_SILENT
            else -> return ActionResult.error("mode must be normal, vibrate or silent")
        }
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (mode != AudioManager.RINGER_MODE_NORMAL && nm?.isNotificationPolicyAccessGranted != true) {
            return ActionResult.error(
                "silencing the ringer needs Do Not Disturb access; grant it in " +
                    "Settings > Notifications > Do Not Disturb access"
            )
        }
        return try {
            am.ringerMode = mode
            ActionResult.ok(json("mode" to (params.str("mode") ?: "").lowercase()))
        } catch (e: SecurityException) {
            ActionResult.error("Do Not Disturb access is required to change the ringer mode")
        }
    }
}

/** Tier 2 — the brief lists "change ringer/DND" as Tier 2. */
object ToggleDnd : JarvisAction {
    override val id = "toggle_dnd"
    override val tier = ActionTier.NOTIFY
    override val description = "Turn Do Not Disturb on or off, or set its filter."
    override val paramsSchema = mapOf(
        "enabled" to "bool: true = priority only, false = off (ignored when filter is given)",
        "filter" to "string: off | priority | alarms_only | total_silence"
    )
    override val capability = "device_settings"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val nm = ctx.getSystemService(NotificationManager::class.java)
            ?: return ActionResult.error("no notification service")
        if (!nm.isNotificationPolicyAccessGranted) {
            return ActionResult.error(
                "Do Not Disturb access not granted; grant it in " +
                    "Settings > Notifications > Do Not Disturb access, then retry"
            )
        }
        val filter = when (params.str("filter")?.lowercase()) {
            "off", "all" -> NotificationManager.INTERRUPTION_FILTER_ALL
            "priority" -> NotificationManager.INTERRUPTION_FILTER_PRIORITY
            "alarms_only", "alarms" -> NotificationManager.INTERRUPTION_FILTER_ALARMS
            "total_silence", "none" -> NotificationManager.INTERRUPTION_FILTER_NONE
            null -> if (params.boolOr("enabled", true)) {
                NotificationManager.INTERRUPTION_FILTER_PRIORITY
            } else {
                NotificationManager.INTERRUPTION_FILTER_ALL
            }
            else -> return ActionResult.error("filter must be off, priority, alarms_only or total_silence")
        }
        return try {
            nm.setInterruptionFilter(filter)
            ActionResult.ok(json("filter" to filter))
        } catch (e: SecurityException) {
            ActionResult.error("Do Not Disturb access was revoked")
        }
    }
}

/** Tier 2 — WRITE_SETTINGS is a special access, not a runtime permission. */
object SetBrightness : JarvisAction {
    override val id = "set_brightness"
    override val tier = ActionTier.NOTIFY
    override val description = "Set the system screen brightness (0-100%), switching off auto-brightness."
    override val paramsSchema = mapOf("level" to "int 0-100: percentage of maximum brightness")
    override val capability = "device_settings"
    override val requiredPermissions = listOf(Manifest.permission.WRITE_SETTINGS)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!Settings.System.canWrite(ctx)) {
            return ActionResult.error(
                "permission ${Manifest.permission.WRITE_SETTINGS} not granted; " +
                    "enable 'Modify system settings' for ${ActiveTheme.name}"
            )
        }
        if (!params.has("level")) return ActionResult.error("level (0-100) is required")
        val percent = params.intOr("level", 0).clampPercent()
        val raw = (percent * 255 / 100).coerceIn(1, 255)
        return try {
            Settings.System.putInt(
                ctx.contentResolver,
                Settings.System.SCREEN_BRIGHTNESS_MODE,
                Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL
            )
            Settings.System.putInt(ctx.contentResolver, Settings.System.SCREEN_BRIGHTNESS, raw)
            ActionResult.ok(json("level" to percent, "raw" to raw))
        } catch (e: SecurityException) {
            ActionResult.missingPermission(Manifest.permission.WRITE_SETTINGS)
        }
    }
}

/** Tier 1 — the brief names the torch as Tier 1. */
object ToggleTorch : JarvisAction {
    override val id = "toggle_torch"
    override val tier = ActionTier.AUTO
    override val description = "Turn the camera flash (torch) on or off."
    override val paramsSchema = mapOf("on" to "bool: true to switch the torch on")
    override val capability = "device_settings"

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_FLASH)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val cm = ctx.getSystemService(CameraManager::class.java)
            ?: return ActionResult.error("no camera service")
        val on = params.boolOr("on", true)
        return try {
            val cameraId = cm.cameraIdList.firstOrNull { camId ->
                val chars = cm.getCameraCharacteristics(camId)
                chars.get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true &&
                    chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
            } ?: cm.cameraIdList.firstOrNull { camId ->
                cm.getCameraCharacteristics(camId).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
            } ?: return ActionResult.error("no camera with a flash on this device")
            cm.setTorchMode(cameraId, on)
            ActionResult.ok(json("on" to on, "camera_id" to cameraId))
        } catch (e: CameraAccessException) {
            ActionResult.error("camera unavailable: ${e.message ?: "in use by another app"}")
        } catch (e: IllegalArgumentException) {
            ActionResult.error("torch not controllable on this device")
        }
    }
}

/** Tier 1 — trivially reversible, and the user notices immediately. */
object VibrateAction : JarvisAction {
    override val id = "vibrate"
    override val tier = ActionTier.AUTO
    override val description = "Vibrate the phone for a duration, or with a pattern."
    override val paramsSchema = mapOf(
        "duration_ms" to "int: milliseconds (default 400, max 5000)",
        "pattern_ms" to "array of ints: off/on millisecond pairs, overrides duration_ms"
    )
    override val capability = "device_settings"
    override val requiredPermissions = listOf(Manifest.permission.VIBRATE)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!ctx.granted(Manifest.permission.VIBRATE)) {
            return ActionResult.missingPermission(Manifest.permission.VIBRATE)
        }
        val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            ctx.getSystemService(Vibrator::class.java)
        }
        if (vibrator == null || !vibrator.hasVibrator()) {
            return ActionResult.error("this device has no vibrator")
        }
        val patternJson = params.optJSONArray("pattern_ms")
        return try {
            if (patternJson != null && patternJson.length() > 0) {
                val pattern = LongArray(minOf(patternJson.length(), 32)) { i ->
                    patternJson.optLong(i, 0L).coerceIn(0L, 5_000L)
                }
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, -1))
                ActionResult.ok(json("pattern_ms" to JSONArray(pattern.toList())))
            } else {
                val ms = params.longOr("duration_ms", 400L).coerceIn(1L, 5_000L)
                vibrator.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
                ActionResult.ok(json("duration_ms" to ms))
            }
        } catch (e: Exception) {
            ActionResult.error("vibrate failed: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}

/**
 * Tier 1 for a coarse fix (the brief's "get coarse location"); raised to
 * Tier 2 when fine precision is asked for, via [tierFor].
 *
 * Google Play Services is not present on GrapheneOS, so this is plain
 * [LocationManager] — no FusedLocationProviderClient anywhere.
 */
object GetLocation : JarvisAction {
    override val id = "get_location"
    override val tier = ActionTier.AUTO
    override val description = "Get this phone's current location."
    override val paramsSchema = mapOf(
        "accuracy" to "string: coarse (default) | fine",
        "max_age_ms" to "int: accept a cached fix this old (default 300000)",
        "timeout_ms" to "int: how long to wait for a fresh fix (default 20000)"
    )
    override val capability = "location"
    override val requiredPermissions = listOf(
        Manifest.permission.ACCESS_COARSE_LOCATION,
        Manifest.permission.ACCESS_FINE_LOCATION
    )
    override val timeoutMs = 45_000L

    /** Fine location is more than "read-only trivia", so it asks. Raise only. */
    override fun tierFor(params: JSONObject): ActionTier =
        if (params.str("accuracy")?.lowercase() == "fine") ActionTier.NOTIFY else ActionTier.AUTO

    /**
     * A coarse question needs the coarse grant and nothing else.
     *
     * [execute] below has always known this — it refuses only on COARSE unless
     * `accuracy=fine` — but the dispatcher briefly treated [requiredPermissions]
     * as all-or-nothing, so a user who answered the system dialog with
     * **Approximate** made every later "where am I" fail permanently. The
     * declared list is what this action can ever need; this is what the call in
     * hand needs.
     *
     * Fine is asked for WITH coarse, never alone: from Android 12 the platform
     * ignores a request for `ACCESS_FINE_LOCATION` that does not carry
     * `ACCESS_COARSE_LOCATION` with it, and an ignored request shows no dialog
     * at all — so on a phone that already held coarse, the precise upgrade
     * would have been unreachable.
     */
    override fun permissionsFor(params: JSONObject): List<String> =
        if (params.str("accuracy")?.lowercase() == "fine") {
            listOf(
                Manifest.permission.ACCESS_COARSE_LOCATION,
                Manifest.permission.ACCESS_FINE_LOCATION
            )
        } else {
            listOf(Manifest.permission.ACCESS_COARSE_LOCATION)
        }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val wantFine = params.str("accuracy")?.lowercase() == "fine"
        if (!ctx.granted(Manifest.permission.ACCESS_COARSE_LOCATION)) {
            return ActionResult.missingPermission(Manifest.permission.ACCESS_COARSE_LOCATION)
        }
        if (wantFine && !ctx.granted(Manifest.permission.ACCESS_FINE_LOCATION)) {
            return ActionResult.missingPermission(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        val lm = ctx.getSystemService(LocationManager::class.java)
            ?: return ActionResult.error("no location service")

        val hasFine = ctx.granted(Manifest.permission.ACCESS_FINE_LOCATION)
        val providers = buildList {
            if (wantFine && hasFine) add(LocationManager.GPS_PROVIDER)
            add(LocationManager.NETWORK_PROVIDER)
            add(LocationManager.PASSIVE_PROVIDER)
        }.filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }

        if (providers.isEmpty()) return ActionResult.error("location is switched off on this device")

        val maxAge = params.longOr("max_age_ms", 300_000L).coerceAtLeast(0L)
        val now = System.currentTimeMillis()
        var best: Location? = null
        for (p in providers) {
            val loc = try {
                @Suppress("MissingPermission")
                lm.getLastKnownLocation(p)
            } catch (e: SecurityException) {
                null
            } ?: continue
            if (now - loc.time > maxAge) continue
            if (best == null || loc.time > best.time) best = loc
        }

        if (best == null) {
            val waitMs = params.longOr("timeout_ms", 20_000L).coerceIn(1_000L, 40_000L)
            best = awaitFix(lm, providers.first(), waitMs)
        }
        val fix = best ?: return ActionResult.error("no location fix available")

        return ActionResult.ok(
            json(
                "latitude" to fix.latitude,
                "longitude" to fix.longitude,
                "accuracy_m" to if (fix.hasAccuracy()) fix.accuracy.toDouble() else null,
                "altitude_m" to if (fix.hasAltitude()) fix.altitude else null,
                "speed_mps" to if (fix.hasSpeed()) fix.speed.toDouble() else null,
                "bearing" to if (fix.hasBearing()) fix.bearing.toDouble() else null,
                "provider" to fix.provider,
                "age_ms" to (System.currentTimeMillis() - fix.time),
                "requested_accuracy" to if (wantFine) "fine" else "coarse"
            )
        )
    }

    /**
     * One-shot fix. `getCurrentLocation` only exists from API 30, and minSdk is
     * 29, so this uses `requestLocationUpdates` with an explicit Looper and
     * removes itself the moment it has an answer or the timeout expires.
     */
    private suspend fun awaitFix(lm: LocationManager, provider: String, waitMs: Long): Location? =
        withTimeoutOrNull(waitMs) {
            suspendCancellableCoroutine<Location?> { cont ->
                val listener = object : LocationListener {
                    override fun onLocationChanged(location: Location) {
                        runCatching { lm.removeUpdates(this) }
                        if (cont.isActive) cont.resume(location)
                    }

                    @Deprecated("required on API 29, no-op default from API 30")
                    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit

                    override fun onProviderEnabled(provider: String) = Unit
                    override fun onProviderDisabled(provider: String) = Unit
                }
                try {
                    @Suppress("MissingPermission")
                    lm.requestLocationUpdates(provider, 0L, 0f, listener, Looper.getMainLooper())
                } catch (e: SecurityException) {
                    if (cont.isActive) cont.resume(null)
                    return@suspendCancellableCoroutine
                }
                cont.invokeOnCancellation { runCatching { lm.removeUpdates(listener) } }
            }
        }
}

/** Tier 1 — list the sensors, or take one reading. */
object GetSensors : JarvisAction {
    override val id = "get_sensors"
    override val tier = ActionTier.AUTO
    override val description =
        "List the phone's sensors, or read one (light, proximity, pressure, temperature, humidity, steps)."
    override val paramsSchema = mapOf(
        "type" to "string (optional): light | proximity | pressure | temperature | humidity | steps | accelerometer",
        "timeout_ms" to "int: how long to wait for a reading (default 3000)"
    )
    override val capability = "sensors"
    override val timeoutMs = 12_000L

    /**
     * Declared so the device manifest and SYSTEM CHECK know this action can
     * need it. [execute] has always checked for it — and for the whole life of
     * the app that check could only fail, because the permission was not even
     * in the manifest. It is now; and declaring it here is what makes the
     * dispatcher ask for it, which was the other half nobody had.
     */
    override val requiredPermissions = listOf("android.permission.ACTIVITY_RECOGNITION")

    /**
     * …but only for the step counter.
     *
     * Every other sensor here needs nothing, and raising "Allow Jarvis to
     * access your physical activity?" to answer "how warm is it" would be a
     * dialog nobody could explain — which is the fastest way to teach somebody
     * to tap Deny on everything.
     */
    override fun permissionsFor(params: JSONObject): List<String> =
        if (TYPES[params.str("type")?.lowercase()] == Sensor.TYPE_STEP_COUNTER) {
            requiredPermissions
        } else {
            emptyList()
        }

    private val TYPES = mapOf(
        "light" to Sensor.TYPE_LIGHT,
        "proximity" to Sensor.TYPE_PROXIMITY,
        "pressure" to Sensor.TYPE_PRESSURE,
        "temperature" to Sensor.TYPE_AMBIENT_TEMPERATURE,
        "humidity" to Sensor.TYPE_RELATIVE_HUMIDITY,
        "steps" to Sensor.TYPE_STEP_COUNTER,
        "accelerometer" to Sensor.TYPE_ACCELEROMETER,
        "gravity" to Sensor.TYPE_GRAVITY,
        "magnetic" to Sensor.TYPE_MAGNETIC_FIELD
    )

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val sm = ctx.getSystemService(SensorManager::class.java)
            ?: return ActionResult.error("no sensor service")
        val typeName = params.str("type")?.lowercase()
            ?: return ActionResult.ok(
                json(
                    "sensors" to JSONArray().also { arr ->
                        for (s in sm.getSensorList(Sensor.TYPE_ALL)) {
                            arr.put(
                                json(
                                    "name" to s.name,
                                    "type" to s.type,
                                    "vendor" to s.vendor,
                                    "max_range" to s.maximumRange.toDouble()
                                )
                            )
                        }
                    }
                )
            )

        val type = TYPES[typeName]
            ?: return ActionResult.error("unknown sensor '$typeName'; use one of ${TYPES.keys}")
        if (type == Sensor.TYPE_STEP_COUNTER &&
            !ctx.granted(Manifest.permission.ACTIVITY_RECOGNITION)
        ) {
            return ActionResult.missingPermission(Manifest.permission.ACTIVITY_RECOGNITION)
        }
        val sensor = sm.getDefaultSensor(type)
            ?: return ActionResult.error("this device has no $typeName sensor")

        val waitMs = params.longOr("timeout_ms", 3_000L).coerceIn(200L, 10_000L)
        val values = withTimeoutOrNull(waitMs) {
            suspendCancellableCoroutine<FloatArray?> { cont ->
                val listener = object : SensorEventListener {
                    override fun onSensorChanged(event: SensorEvent) {
                        runCatching { sm.unregisterListener(this) }
                        if (cont.isActive) cont.resume(event.values.copyOf())
                    }

                    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
                }
                if (!sm.registerListener(listener, sensor, SensorManager.SENSOR_DELAY_FASTEST)) {
                    if (cont.isActive) cont.resume(null)
                    return@suspendCancellableCoroutine
                }
                cont.invokeOnCancellation { runCatching { sm.unregisterListener(listener) } }
            }
        } ?: return ActionResult.error("no reading from the $typeName sensor")

        return ActionResult.ok(
            json(
                "type" to typeName,
                "sensor" to sensor.name,
                "values" to JSONArray(values.map { it.toDouble() })
            )
        )
    }
}
