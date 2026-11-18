package ai.aura.app.assist.island

import ai.aura.app.assist.island.IslandRest.Network
import ai.aura.app.assist.island.IslandRest.Presence
import ai.aura.app.assist.island.IslandRest.Status
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the resting island says (T-0890): "shows it with a few small things", and with the status
 * option the phone's own status "instead of showing the default phone statuses at the top like
 * the wifi, dnd, cell, battery, time". Pure. Does not cover reading the phone (the host).
 */
class IslandStatusTest {
    @Test
    fun `without the status it is the persona and whether it listens`() {
        val face = IslandRest.face("Aura", Presence.LISTENING, null)
        assertEquals("AURA", face.left)
        assertEquals("LISTENING", face.right)
        assertEquals(null, face.network)
        val muted = IslandRest.face("Aura", Presence.MUTED, null)
        assertEquals("MUTED", muted.right)
        assertEquals("OFFLINE", IslandRest.face("Aura", Presence.OFFLINE, null).right)
    }

    @Test
    fun `with the status it is the time, the battery, the network and do not disturb`() {
        val face = IslandRest.face("Aura", Presence.LISTENING, Status("09:41", battery = 87, charging = true, network = Network.WIFI, bars = 3, dnd = true))
        assertEquals("09:41", face.left)
        assertEquals("87%", face.right)
        assertTrue(face.charging)
        assertEquals(Network.WIFI, face.network)
        assertEquals(3, face.bars)
        assertTrue(face.dnd)
    }

    @Test
    fun `the persona's core listens, rests while muted and says offline while the link is down`() {
        assertEquals(ai.aura.app.reactor.ReactorState.LISTENING, IslandRest.core(Presence.LISTENING))
        assertEquals(ai.aura.app.reactor.ReactorState.IDLE, IslandRest.core(Presence.MUTED))
        assertEquals(ai.aura.app.reactor.ReactorState.OFFLINE, IslandRest.core(Presence.OFFLINE))
    }

    @Test
    fun `an unknown battery and no network say so rather than a number`() {
        val face = IslandRest.face("Aura", Presence.MUTED, Status("21:05", battery = null, charging = false, network = Network.NONE, bars = 0, dnd = false))
        assertEquals("", face.right)
        assertEquals(Network.NONE, face.network)
        assertFalse(face.dnd)
    }

    @Test
    fun `wifi strength is four bars at most and none below the floor`() {
        assertEquals(4, IslandRest.wifiBars(-50))
        assertEquals(3, IslandRest.wifiBars(-60))
        assertEquals(2, IslandRest.wifiBars(-70))
        assertEquals(1, IslandRest.wifiBars(-85))
        assertEquals(0, IslandRest.wifiBars(-95))
        assertEquals(0, IslandRest.wifiBars(Int.MIN_VALUE))
    }
}
