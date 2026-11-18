package ai.aura.app.car.voice

import ai.aura.app.assist.hermes.HermesClient
import ai.aura.app.assist.hermes.HermesClientTest.FakeSocket
import ai.aura.app.assist.hermes.HermesClientTest.ManualClock
import ai.aura.app.car.dash.CarShell
import ai.aura.app.channel.Backoff
import ai.aura.app.config.JarvisConfig
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.test.runTest
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * T-0743: a line JARVIS says on purpose (`hud_say`) during a car turn was dropped by the car's
 * session, and the car page played it through its web view, which reaches the car only through
 * the screen-share grant. It is the car's own voice now, which needs none.
 */
@RunWith(RobolectricTestRunner::class)
class HermesCarSayTest {

    @After
    fun reset() {
        CarShell.onSay = null
    }

    @Test
    fun `a hud_say line in a car turn is spoken by the car's own voice`() = runTest {
        val said = ArrayList<String>()
        CarShell.onSay = { said.add(it) }
        val car = HermesCarJarvis(
            ApplicationProvider.getApplicationContext(), JarvisConfig(ApplicationProvider.getApplicationContext()), backgroundScope,
            newClient = { listener ->
                HermesClient(
                    "http://adapter.lan:8643", "devkey", listener, ManualClock(),
                    socketFactory = { request, _ -> FakeSocket(request) },
                    backoff = Backoff(baseMs = 500, maxMs = 30_000), random = { 0.0 },
                )
            },
        )

        car.onEvent("hud.say", "s-1", JSONObject("""{"text":"  Fuel is low.  ","voice":"jarvis"}"""))
        car.onEvent("hud.say", "s-1", JSONObject("""{"text":"   "}"""))
        shadowOf(Looper.getMainLooper()).idle()

        assertEquals(listOf("Fuel is low."), said)
    }
}
