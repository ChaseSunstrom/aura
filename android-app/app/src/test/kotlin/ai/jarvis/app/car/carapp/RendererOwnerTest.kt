package ai.jarvis.app.car.carapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RendererOwnerTest {
    @Test fun `no conflict when Jarvis owns the renderer permission`() {
        assertNull(rendererConflict(owner = "ai.jarvis.app", self = "ai.jarvis.app"))
    }

    @Test fun `no conflict when nobody defines it, which is a build without the host`() {
        assertNull(rendererConflict(owner = null, self = "ai.jarvis.app"))
    }

    @Test fun `Headway holding it is named, with the remedy`() {
        val message = rendererConflict(owner = "dev.headway.app", self = "ai.jarvis.app")!!
        assertTrue(message, message.contains("Headway"))
        assertTrue(message, message.contains("Uninstall", ignoreCase = true))
    }

    @Test fun `any other package holding it is named by its package`() {
        assertEquals(
            "com.example.cars holds the car-app permission, so AURA cannot show other apps' car " +
                "screens. Uninstall com.example.cars to let AURA host them.",
            rendererConflict(owner = "com.example.cars", self = "ai.jarvis.app"),
        )
    }
}
