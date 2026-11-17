package ai.jarvis.app.car.bluetooth

import aap_protobuf.service.ServiceOuterClass
import ai.jarvis.app.car.HeadwaySettings
import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.content.Context
import dev.headway.protocol.channel.BluetoothChannel
import dev.headway.protocol.io.MessageChannel
import dev.headway.protocol.session.HeadUnitProfile
import java.io.EOFException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Answers the car's Bluetooth service: one pairing request when the session starts,
 * then every reply the car sends, in words, in the drive log.
 *
 * The car's steering-wheel skip buttons answered "Action unavailable" with every key
 * bound and the playback status sent, and the phone's hands-free and media profiles
 * stayed down for the whole projection session (drive logs, 2026-10-06). This is the
 * part of the protocol the phone never spoke. See [BluetoothChannel].
 *
 * The phone's own address: Android gives an ordinary app only the placeholder
 * [BluetoothChannel.PLACEHOLDER_ADDRESS]. The real one is used when the platform gives
 * it, or when the owner has stored it ([HeadwaySettings.KEY_PHONE_BT_ADDRESS], from
 * Settings › About phone › Bluetooth address); otherwise the placeholder is sent and
 * the log says so, so the car's answer can be read for what it is.
 */
class CarBluetoothStream(
    private val context: Context,
    private val connection: MessageChannel,
    private val channelId: Int,
    private val service: ServiceOuterClass.Service,
    private val onStep: (String) -> Unit = {},
) {
    private var reader: Job? = null

    fun start(scope: CoroutineScope) {
        val (address, source) = phoneAddress()
        val method = BluetoothChannel.methodFor(service.bluetoothService)
        onStep(
            "bluetooth: telling the car this phone is ${redacted(address)} ($source), " +
                "pairing by ${method.name}; the car is ${redacted(service.bluetoothService.carAddress)}"
        )
        reader = scope.launch {
            runCatching { connection.send(BluetoothChannel.pairingRequest(channelId, address, method)) }
                .onFailure { onStep("bluetooth: the pairing request did not go: $it") }
            while (true) {
                val message = try {
                    connection.receive()
                } catch (closed: EOFException) {
                    return@launch
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (failed: Exception) {
                    onStep("bluetooth: could not read the channel: $failed")
                    return@launch
                }
                onStep(BluetoothChannel.describe(message))
            }
        }
    }

    fun stop() {
        reader?.cancel()
        reader = null
    }

    @SuppressLint("HardwareIds", "MissingPermission")
    private fun phoneAddress(): Pair<String, String> {
        val platform = runCatching {
            context.getSystemService(BluetoothManager::class.java)?.adapter?.address
        }.getOrNull()
        if (!platform.isNullOrBlank() && platform != BluetoothChannel.PLACEHOLDER_ADDRESS) {
            return platform to "from the platform"
        }
        val stored = HeadwaySettings.of(context).getString(HeadwaySettings.KEY_PHONE_BT_ADDRESS, null)
        if (!stored.isNullOrBlank() && ADDRESS.matches(stored.trim())) {
            return stored.trim().uppercase() to "from settings"
        }
        return BluetoothChannel.PLACEHOLDER_ADDRESS to
            "the platform hides the real one; store it from Settings › About phone › Bluetooth address"
    }

    /** The first half of an address is the maker; the second identifies the device. */
    private fun redacted(address: String): String =
        if (ADDRESS.matches(address)) address.take(8) + ":xx:xx:xx" else address

    companion object {
        private val ADDRESS = Regex("[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")

        /** The stream for [profile], or null when the car offers no Bluetooth service. */
        fun of(
            profile: HeadUnitProfile,
            connectionFor: (Int) -> MessageChannel,
            context: Context,
            onStep: (String) -> Unit = {},
        ): CarBluetoothStream? {
            val service = profile.services.firstOrNull { it.hasBluetoothService() } ?: return null
            return CarBluetoothStream(context.applicationContext, connectionFor(service.id), service.id, service, onStep)
        }
    }
}
