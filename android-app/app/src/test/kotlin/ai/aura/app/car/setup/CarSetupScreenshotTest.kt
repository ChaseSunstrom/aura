package ai.aura.app.car.setup

import ai.aura.app.ui.theme.JarvisTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Each moment of the car setup (ADR-0047), drawn from a SetupUi so no Bluetooth is needed. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class CarSetupScreenshotTest {
    @get:Rule
    val compose = createComposeRule()

    private val malibu = CarChoice("MyChevrolet", "AA:BB:CC:DD:EE:FF")

    private fun shot(name: String, ui: SetupUi) {
        compose.setContent { JarvisTheme { CarSetupScreen(ui, SetupActions()) } }
        compose.onRoot().captureRoboImage("src/test/screenshots/$name.png")
    }

    @Test fun `asking for the grants`() = shot("car-setup-permissions", SetupUi(SetupStep.PERMISSIONS))

    @Test fun `no paired car`() = shot("car-setup-no-car", SetupUi(SetupStep.PICK_CAR))

    @Test fun `picking the car`() = shot("car-setup-pick", SetupUi(SetupStep.PICK_CAR, cars = listOf(malibu, CarChoice("Headphones", "11:22:33:44:55:66"))))

    /** The connect step's first face, the only moment whose primary is "Connect". */
    @Test fun `the car picked, before the first attempt`() = shot("car-setup-connect", SetupUi(SetupStep.CONNECT, chosen = malibu))

    @Test fun `joining, with pairing offered`() = shot(
        "car-setup-joining",
        SetupUi(SetupStep.CONNECT, chosen = malibu, running = true, canPair = true, gates = GateReport(Gate.ACCESS_POINT, passed = false, verdict = null, remedy = null)),
    )

    @Test fun `no address from a STATIC car, with the values to type`() = shot(
        "car-setup-static-ip",
        SetupUi(
            SetupStep.CONNECT, chosen = malibu, paired = true, canPair = true,
            gates = GateReport(Gate.ADDRESS, passed = false, verdict = "IP_PROVISIONING", remedy = Remedy.SAVE_NETWORK_WITH_STATIC_IP),
            staticIp = staticIpFor("192.168.5.1"),
        ),
    )

    /** T-1324: what was a Toast is a line on the screen; the long failure sentence is the worst case for width. */
    @Test fun `a failure notice, in full`() = shot(
        "car-setup-notice",
        SetupUi(
            SetupStep.CONNECT, chosen = malibu, canPair = true,
            notice = SetupNotice("The car's network was not found. Keep the car on and showing Android Auto, then try again.", failed = true),
        ),
    )

    @Test fun `a notice is a polite live region, and none shows when there is none`() {
        // One setContent per test: the notice is state the one composition reads.
        var notice by mutableStateOf<SetupNotice?>(SetupNotice("Copied 192.168.5.1"))
        compose.setContent { JarvisTheme { CarSetupScreen(SetupUi(SetupStep.CONNECT, chosen = malibu, notice = notice), SetupActions()) } }
        compose.onNode(hasText("Copied 192.168.5.1"))
            .assert(SemanticsMatcher.expectValue(SemanticsProperties.LiveRegion, LiveRegionMode.Polite))
        notice = null
        compose.onAllNodes(hasText("Copied", substring = true)).assertCountEquals(0)
    }

    @Test fun `on the car screen`() = shot(
        "car-setup-done",
        SetupUi(SetupStep.CONNECT, chosen = malibu, running = true, canPair = true, paired = true, gates = GateReport(Gate.SESSION, passed = true, verdict = null, remedy = null)),
    )
}
