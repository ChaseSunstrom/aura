package ai.aura.app.car.video

import dev.headway.video.ScreenEncoder
import org.junit.Assert.assertEquals
import org.junit.Test

class EncodedFpsTest {
    @Test
    fun aSixtyFpsCarIsEncodedAtThirty() {
        assertEquals(30, ScreenEncoder.encodedFps(60))
    }

    @Test
    fun aSlowerCarKeepsItsOwnRate() {
        assertEquals(30, ScreenEncoder.encodedFps(30))
        assertEquals(24, ScreenEncoder.encodedFps(24))
    }
}
