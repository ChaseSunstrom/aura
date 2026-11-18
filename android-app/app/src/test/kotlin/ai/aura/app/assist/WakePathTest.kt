package ai.aura.app.assist

import ai.aura.app.assist.WakeWordService.WakePath
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * T-0566: with the on-device models missing, the wake listener streamed the microphone to the
 * house and the notification said only "Say Hey Jarvis at any time".
 */
class WakePathTest {

    @Test
    fun `the phone hears it when its own detector opened`() =
        assertEquals(WakePath.PHONE, WakeWordService.wakePath(localOpened = true, onDevice = true, serverFallback = false))

    @Test
    fun `without the models the house listens, and the notification says so`() {
        assertEquals(WakePath.SERVER, WakeWordService.wakePath(localOpened = false, onDevice = true, serverFallback = true))
        assertTrue(WakeWordService.noticeFor(WakePath.SERVER).contains("house"))
    }

    @Test
    fun `refusing the fallback opens nothing until the models land`() {
        assertEquals(WakePath.NONE, WakeWordService.wakePath(localOpened = false, onDevice = true, serverFallback = false))
        assertTrue(WakeWordService.noticeFor(WakePath.NONE).contains("Not listening"))
    }

    @Test
    fun `a phone set to the server path keeps it`() =
        assertEquals(WakePath.SERVER, WakeWordService.wakePath(localOpened = false, onDevice = false, serverFallback = false))
}
