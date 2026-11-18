package ai.aura.app.car.link

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothServerSocket
import android.bluetooth.BluetoothSocket
import androidx.annotation.RequiresPermission
import java.io.Closeable
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Answers the car's own connection attempt (ADR-0047).
 *
 * A head unit set to connect-device-first opens RFCOMM to the phone at ignition, on
 * the Android Auto wireless service, and waits for the phone to answer -- that is how
 * Google's app starts a drive with the phone in a pocket. Headway only ever dialled
 * the car itself, so that attempt died unanswered, and whether the car had already
 * started or abandoned its own was one more thing that made one attempt differ from
 * the next. This registers the same service record and parks each socket a profiled
 * car opens in [slot], for CarLinkService's next session to take instead of dialling.
 *
 * Does not start a session: the Bluetooth ACL receiver already does (CarPresence),
 * and the socket waits here until it asks. Unproven against the real car
 * (docs/car/drive-checklist.md step 3); behind the `answerCarPoke` quirk.
 */
class CarPokeListener(
    private val adapter: BluetoothAdapter,
    private val isKnownCar: (String) -> Boolean,
    private val slot: PokeSlot<BluetoothSocket>,
    private val onStep: (String) -> Unit,
) : Closeable {
    private val running = AtomicBoolean(false)

    @Volatile
    private var server: BluetoothServerSocket? = null

    @RequiresPermission(Manifest.permission.BLUETOOTH_CONNECT)
    fun start() {
        if (!running.compareAndSet(false, true)) return
        Thread({ acceptLoop() }, "car-poke-listener").apply { isDaemon = true }.start()
    }

    @Suppress("MissingPermission")
    private fun acceptLoop() {
        val listening = try {
            adapter.listenUsingRfcommWithServiceRecord(SERVICE_NAME, BluetoothCarLink.SERVICE_UUID)
        } catch (e: IOException) {
            onStep("could not listen for the car's own connection: ${e.message}")
            running.set(false)
            return
        } catch (e: SecurityException) {
            onStep("could not listen for the car's own connection: BLUETOOTH_CONNECT not granted")
            running.set(false)
            return
        }
        server = listening
        onStep("listening for the car to connect to the phone")
        while (running.get()) {
            val socket = try {
                listening.accept()
            } catch (e: IOException) {
                // close() interrupts accept with an IOException; anything else ends
                // the loop too, and the next service start listens again.
                break
            }
            val address = socket.remoteDevice?.address
            if (address != null && isKnownCar(address)) {
                onStep("the car ($address) connected to the phone; its socket waits for the session")
                slot.offer(address, socket)
            } else {
                runCatching { socket.close() }
            }
        }
        running.set(false)
    }

    override fun close() {
        running.set(false)
        runCatching { server?.close() }
        server = null
        slot.clear()
    }

    companion object {
        /** The service name Google's app registers; head units match on the UUID. */
        const val SERVICE_NAME: String = "AAWireless"
    }
}
