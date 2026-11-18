package ai.aura.app.automation.actions.builtin

import ai.aura.app.automation.actions.builtin.ScanNearby.Net
import org.junit.Assert.assertEquals
import org.junit.Test

class ScanNearbyTest {
    @Test
    fun oneRowPerNetworkStrongestFirstHiddenLast() {
        val rows = ScanNearby.topNetworks(
            listOf(
                Net("Home", "a", -70, 2412), Net("Home", "b", -40, 5180),
                Net("", "c", -20, 2412), Net("Neighbour", "d", -60, 2437),
            ),
        )
        assertEquals(listOf("Home", "Neighbour", ""), rows.map { it.ssid })
        assertEquals(-40, rows[0].rssi)
    }
}
