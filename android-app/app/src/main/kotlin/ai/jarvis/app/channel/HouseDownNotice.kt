package ai.jarvis.app.channel

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.MainActivity
import ai.jarvis.app.ui.Notify
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import android.util.Log
import ai.jarvis.app.ui.theme.ActiveTheme
import java.text.DateFormat
import java.util.Date

/**
 * The wiring for [HouseDown] (T-0578): the house link says each failed or answered attempt, the
 * default network's handle comes from a callback of its own, and the one notice is posted on the
 * alerts channel and then rewritten to "back", not removed, when the house answers.
 *
 * The handle is the default network's only when it is Wi-Fi and not a VPN: away from home a tunnel
 * over a cafe's Wi-Fi is not home. No SSID, no location permission.
 *
 * Not covered: the notice's text is the phone's clock at the time; a phone whose clock was set
 * during the outage names a wrong minute.
 */
class HouseDownNotice(
    context: Context,
    private val clock: () -> Long = { SystemClock.elapsedRealtime() },
) {
    private val app = context.applicationContext
    private val decider = HouseDown()

    @Volatile
    private var net: Long? = null

    @Volatile
    private var quietAt = ""

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
            val wifi = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) &&
                !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
            net = if (wifi) network.networkHandle else null
            decider.networkChanged(net)
        }

        override fun onLost(network: Network) {
            net = null
            decider.networkChanged(null)
        }
    }

    private var registered = false

    fun start() {
        if (registered) return
        val cm = app.getSystemService(ConnectivityManager::class.java) ?: return
        runCatching { cm.registerDefaultNetworkCallback(callback); registered = true }
            .onFailure { Log.w(TAG, "could not watch the network; no house-down notice", it) }
    }

    fun stop() {
        if (!registered) return
        runCatching { app.getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(callback) }
        registered = false
        net = null
        decider.networkChanged(null)
    }

    fun answered() {
        if (decider.answered(clock(), net)) postBack()
    }

    fun failed() = down(decider.failed(clock(), net))

    fun tick() = down(decider.tick(clock(), net))

    private fun down(since: Long?) {
        if (since == null) return
        val wall = System.currentTimeMillis() - (clock() - since)
        quietAt = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(wall))
        post(Notify.Template.ALERT, "The house is not answering", "No answer since $quietAt, 15 minutes and counting.")
    }

    private fun postBack() = post(Notify.Template.RECOVERED, "The house is back", "It was quiet from $quietAt and is answering again.")

    private fun post(template: Notify.Template, title: String, text: String) {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return
        val open = PendingIntent.getActivity(
            app, 0,
            Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val b = Notification.Builder(app, JarvisApp.CHANNEL_ALERTS)
            .setSmallIcon(ActiveTheme.statusIcon)
            .setColor(Notify.color(template))
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setAutoCancel(true)
        // A missed notice must not take the connect loop down with it.
        runCatching { nm.notify(ID, b.build()) }.onFailure { Log.w(TAG, "could not post the house notice", it) }
    }

    private companion object {
        const val TAG = "JarvisHouseDown"
        const val ID = 0x48d0
    }
}
