package ai.aura.app.car.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SocketRouteTest {
    @Test fun `a bound socket is always the route when binding works`() {
        assertEquals(SocketRoute.BoundToCar, chooseRoute(bindSucceeded = true, carIsDefaultNetwork = false, vpnActive = true))
    }

    @Test fun `an unbound socket is only used when the car is already the default network`() {
        assertEquals(SocketRoute.Default, chooseRoute(bindSucceeded = false, carIsDefaultNetwork = true, vpnActive = false))
    }

    @Test fun `with a VPN up an unbound socket would go into the tunnel, so refuse and say why`() {
        val r = chooseRoute(bindSucceeded = false, carIsDefaultNetwork = false, vpnActive = true)
        assertTrue(r is SocketRoute.Refuse && r.why.contains("VPN"))
    }

    @Test fun `without a VPN an unbound socket would go out over mobile data, so refuse`() {
        val r = chooseRoute(bindSucceeded = false, carIsDefaultNetwork = false, vpnActive = false)
        assertTrue(r is SocketRoute.Refuse && r.why.contains("mobile data"))
    }
}
