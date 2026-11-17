package ai.jarvis.app.car.setup

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CarProfileTest {
    private val malibu = CarProfile(
        btAddress = "AA:BB:CC:DD:EE:FF", ssid = "myChevrolet0000", bssid = "02:11:22:33:44:55",
        staticIp = true, gateway = "192.168.5.1", certificate = "internal", pinBssid = true, hiddenSsid = false,
    )

    @Test fun `a profile round-trips through its JSON`() {
        assertEquals(malibu, CarProfile.decode(malibu.encode()))
    }

    @Test fun `a missing field decodes to the default rather than failing`() {
        val decoded = CarProfile.decode("""{"btAddress":"AA:BB:CC:DD:EE:FF","ssid":"car"}""")!!
        assertEquals(CarProfile(btAddress = "AA:BB:CC:DD:EE:FF", ssid = "car"), decoded)
    }

    @Test fun `garbage decodes to nothing`() {
        assertNull(CarProfile.decode("not json"))
        assertNull(CarProfile.decode("""{"ssid":"no address"}"""))
    }

    @Test fun `profiles are kept per car and never cross`() {
        val book = CarProfiles.decodeAll(CarProfiles.encodeAll(mapOf(malibu.btAddress to malibu)))
        assertEquals(malibu, book[malibu.btAddress])
        assertNull(book["11:22:33:44:55:66"])
    }

    @Test fun `a static IP is picked on the car's subnet, never the gateway's own`() {
        assertEquals(StaticIp("192.168.5.100", "192.168.5.1", 24), staticIpFor("192.168.5.1"))
        assertEquals(StaticIp("10.0.0.101", "10.0.0.100", 24), staticIpFor("10.0.0.100"))
        assertNull(staticIpFor("not an address"))
    }
}
