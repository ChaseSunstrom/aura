package ai.jarvis.app.car.link

import android.content.Context
import android.net.Network
import androidx.test.core.app.ApplicationProvider
import aap_protobuf.service.wifiprojection.message.AccessPointTypeOuterClass.AccessPointType
import aap_protobuf.service.wifiprojection.message.WifiSecurityModeOuterClass.WifiSecurityMode
import dev.headway.transport.wireless.CarNetworkCredentials
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowNetwork

/**
 * T-1109: the suggestion's wait answers with the network the link already holds.
 *
 * `adoptExistingCarNetwork` answers null while a network is held, so a wait that only polled it
 * ran its whole timeout on a reconnect that never lost the network. What is not under test: a real
 * adoption from `getAllNetworks` (no Wi-Fi stack here); the held network is placed through its
 * backing fields, the state `adoptExistingCarNetwork` and the join leave behind.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarWifiAwaitTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    private fun credentials(ssid: String) = CarNetworkCredentials(
        ssid = ssid,
        passphrase = "made-up-passphrase",
        bssid = "02:00:00:00:00:01",
        securityMode = WifiSecurityMode.WPA2_PERSONAL,
        accessPointType = AccessPointType.DYNAMIC,
    )

    private fun hold(wifi: CarWifiNetwork, held: Network, ssid: String) {
        for ((name, value) in listOf("network" to held, "joinedSsid" to ssid)) {
            CarWifiNetwork::class.java.getDeclaredField(name).apply { isAccessible = true }.set(wifi, value)
        }
    }

    @Test
    fun `a network already held for this car is returned at once`() {
        val wifi = CarWifiNetwork(context)
        val held = ShadowNetwork.newInstance(7)
        hold(wifi, held, "car-ap")
        // The default timeout is 150 s: returning is the proof it did not poll.
        val got = runBlocking { wifi.awaitSuggestedCarNetwork(credentials("car-ap")) }
        assertSame(held, got)
    }

    @Test
    fun `nothing held still polls and runs out the timeout`() {
        val wifi = CarWifiNetwork(context)
        try {
            runBlocking { wifi.awaitSuggestedCarNetwork(credentials("car-ap"), timeoutMillis = 0) }
            fail("expected the wait to time out")
        } catch (e: CarWifiException) {
            assertTrue(e.message.orEmpty().contains("never joined car-ap"))
        }
    }

    @Test
    fun `another car's network is not returned for this car`() {
        val wifi = CarWifiNetwork(context)
        hold(wifi, ShadowNetwork.newInstance(8), "other-car")
        try {
            runBlocking { wifi.awaitSuggestedCarNetwork(credentials("car-ap"), timeoutMillis = 0) }
            fail("expected the wait to time out")
        } catch (e: CarWifiException) {
            assertEquals(true, e.message.orEmpty().contains("car-ap"))
        }
    }
}
