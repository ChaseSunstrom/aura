package ai.aura.app.assist

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/** AUDIT B6: the wake listener waits on the car's phase as it changes, not on a 500 ms poll. */
class CarTurnWaitTest {

    @Test
    fun `the wait ends the moment the car's turn goes busy and comes back`() = runTest {
        val phases = MutableStateFlow(Phase.IDLE)
        launch { delay(1_000); phases.value = Phase.LISTENING; delay(3_210); phases.value = Phase.IDLE }
        WakeWordService.awaitCarTurn(phases)
        assertEquals(4_210, currentTime)
    }

    @Test
    fun `an error ends the turn as idle does`() = runTest {
        val phases = MutableStateFlow(Phase.THINKING)
        launch { delay(2_000); phases.value = Phase.ERROR }
        WakeWordService.awaitCarTurn(phases)
        assertEquals(2_000, currentTime)
    }

    @Test
    fun `a turn that never starts is given up on`() = runTest {
        WakeWordService.awaitCarTurn(MutableStateFlow(Phase.IDLE))
        assertEquals(5_000, currentTime)
    }

    @Test
    fun `a turn that never ends is cut at the cap`() = runTest {
        WakeWordService.awaitCarTurn(MutableStateFlow(Phase.SPEAKING))
        assertEquals(90_000, currentTime)
    }
}
