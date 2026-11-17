package ai.jarvis.app.car.link

/**
 * Which socket may carry the AAP session (ADR-0047). Headway bound its whole
 * process to the car's network; Jarvis cannot, because the adapter, the
 * transcriber and the voice live on the internet side for the whole drive. So
 * only this one socket is bound, and an unbound socket is allowed only when it
 * would reach the car anyway -- the car is the phone's default network, which
 * is the case when the driver joined its Wi-Fi by hand.
 *
 * Does not cover a VPN in lockdown mode, which drops even a bound socket
 * (docs/car/BLOCKERS.md B-019); that fails at connect and is named there.
 */
sealed interface SocketRoute {
    data object BoundToCar : SocketRoute
    data object Default : SocketRoute
    data class Refuse(val why: String) : SocketRoute
}

fun chooseRoute(bindSucceeded: Boolean, carIsDefaultNetwork: Boolean, vpnActive: Boolean): SocketRoute = when {
    bindSucceeded -> SocketRoute.BoundToCar
    carIsDefaultNetwork -> SocketRoute.Default
    vpnActive -> SocketRoute.Refuse("could not bind to the car's network, and an unbound socket would go into the VPN")
    else -> SocketRoute.Refuse("could not bind to the car's network, and an unbound socket would go out over mobile data")
}
