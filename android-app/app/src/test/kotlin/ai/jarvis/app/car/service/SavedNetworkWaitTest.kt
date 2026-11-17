package ai.jarvis.app.car.service

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The rule only (T-1100); CarLinkService's use of it and CarWifiNetwork's state are not under test. */
class SavedNetworkWaitTest {
    @Test
    fun `a reconnect that still holds the car's network does not wait to join it`() {
        assertTrue(SavedNetworkWait.skip(holdsNetwork = true, joinedSsid = "car-ap", wanted = "car-ap"))
    }

    @Test
    fun `a cold start, a lost network and another car's network still wait`() {
        assertFalse(SavedNetworkWait.skip(holdsNetwork = false, joinedSsid = null, wanted = "car-ap"))
        assertFalse(SavedNetworkWait.skip(holdsNetwork = false, joinedSsid = "car-ap", wanted = "car-ap"))
        assertFalse(SavedNetworkWait.skip(holdsNetwork = true, joinedSsid = "other-car", wanted = "car-ap"))
    }
}
