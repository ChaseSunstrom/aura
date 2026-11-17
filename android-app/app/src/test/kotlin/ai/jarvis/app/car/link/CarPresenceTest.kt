package ai.jarvis.app.car.link

import ai.jarvis.app.car.HeadwaySettings
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * What tells the car link the car is still there (T-0742): its Bluetooth, connected for
 * calls and media for the whole drive. The supervisor keeps reconnecting past its limit
 * only while this says so.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarPresenceTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private val adapter = context.getSystemService(BluetoothManager::class.java).adapter

    private fun acl(action: String, address: String) =
        Intent(action).putExtra(BluetoothDevice.EXTRA_DEVICE, adapter.getRemoteDevice(address))

    @After
    fun reset() {
        CarPresenceReceiver.carBluetoothUp = false
        CarPresenceReceiver.carBluetoothGone = false
    }

    @Test
    fun `the car's own Bluetooth connecting and going is what says it is still there`() {
        HeadwaySettings.rememberCar(context, CAR)
        val receiver = CarPresenceReceiver()
        assertFalse("not known is not there", CarPresenceReceiver.carBluetoothUp)

        receiver.onReceive(context, acl(BluetoothDevice.ACTION_ACL_CONNECTED, HEADPHONES))
        assertFalse("headphones are not the car", CarPresenceReceiver.carBluetoothUp)
        receiver.onReceive(context, acl(BluetoothDevice.ACTION_ACL_CONNECTED, CAR))
        assertTrue(CarPresenceReceiver.carBluetoothUp)

        receiver.onReceive(context, acl(BluetoothDevice.ACTION_ACL_DISCONNECTED, HEADPHONES))
        assertTrue("headphones leaving do not take the car away", CarPresenceReceiver.carBluetoothUp)
        receiver.onReceive(context, acl(BluetoothDevice.ACTION_ACL_DISCONNECTED, CAR))
        assertFalse(CarPresenceReceiver.carBluetoothUp)
    }

    /**
     * T-0745: the console page held its web microphone (a call, to Android) until the car's
     * screen was up, so the car showed its call screen through the connect and every reconnect.
     */
    @Test
    fun `the car is here from its Bluetooth connecting, before its screen is up`() {
        HeadwaySettings.rememberCar(context, CAR)
        assertFalse(CarPresenceReceiver.carHere())
        CarPresenceReceiver().onReceive(context, acl(BluetoothDevice.ACTION_ACL_CONNECTED, CAR))
        assertTrue(CarPresenceReceiver.carHere())
        CarPresenceReceiver().onReceive(context, acl(BluetoothDevice.ACTION_ACL_DISCONNECTED, CAR))
        assertFalse(CarPresenceReceiver.carHere())
    }

    private companion object {
        const val CAR = "00:11:22:33:44:55"
        const val HEADPHONES = "66:77:88:AA:BB:CC"
    }
}
