package ai.aura.app.car.setup

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GateReportTest {
    @Test fun `a car that is not on the air is told to turn its Wi-Fi on`() {
        assertEquals(Remedy.TURN_ON_CAR_WIFI, remedyFor(Gate.ACCESS_POINT, "NOT_FOUND", accessPointStatic = null, vpnActive = false))
        assertEquals(Remedy.TURN_ON_CAR_WIFI, remedyFor(Gate.ACCESS_POINT, "NO_RESPONSE", accessPointStatic = false, vpnActive = false))
    }

    @Test fun `no address from a car that announced STATIC means a saved network with a static IP`() {
        assertEquals(Remedy.SAVE_NETWORK_WITH_STATIC_IP, remedyFor(Gate.ADDRESS, "IP_PROVISIONING", accessPointStatic = true, vpnActive = false))
    }

    @Test fun `no address from a car that hands out addresses is the MAC randomisation`() {
        assertEquals(Remedy.FIX_MAC_RANDOMISATION, remedyFor(Gate.ADDRESS, "IP_PROVISIONING", accessPointStatic = false, vpnActive = false))
    }

    @Test fun `unknown addressing gets the static IP remedy, the one proven on a real car`() {
        assertEquals(Remedy.SAVE_NETWORK_WITH_STATIC_IP, remedyFor(Gate.ADDRESS, "IP_PROVISIONING", accessPointStatic = null, vpnActive = false))
    }

    @Test fun `a refused or unreachable socket under a VPN names the VPN`() {
        assertEquals(Remedy.EXCLUDE_FROM_VPN, remedyFor(Gate.SESSION, null, accessPointStatic = true, vpnActive = true))
        assertEquals(Remedy.EXCLUDE_FROM_VPN, remedyFor(Gate.TLS, null, accessPointStatic = true, vpnActive = true))
    }

    @Test fun `a certificate the car refused is retried with the next one`() {
        assertEquals(Remedy.RETRY_CERTIFICATE, remedyFor(Gate.TLS, null, accessPointStatic = true, vpnActive = false))
    }

    @Test fun `a Bluetooth exchange the car cut short means another phone has it`() {
        assertEquals(Remedy.FORGET_OTHER_PHONE, remedyFor(Gate.BLUETOOTH, null, accessPointStatic = null, vpnActive = false))
    }

    @Test fun `an association failure has no remedy button, only a retry`() {
        assertNull(remedyFor(Gate.ASSOCIATION, "AUTHENTICATION", accessPointStatic = null, vpnActive = false))
    }

    @Test fun `the tracker keeps the furthest gate reached and the failure against it`() {
        val gates = CarGates()
        gates.reached(Gate.BLUETOOTH)
        gates.passed(Gate.BLUETOOTH)
        gates.reached(Gate.ACCESS_POINT)
        gates.failed(Gate.ACCESS_POINT, "NOT_FOUND", accessPointStatic = null, vpnActive = false)
        val report = gates.state.value
        assertEquals(Gate.ACCESS_POINT, report.reached)
        assertEquals(false, report.passed)
        assertEquals(Remedy.TURN_ON_CAR_WIFI, report.remedy)
        gates.reset()
        assertEquals(GateReport.START, gates.state.value)
    }
}
