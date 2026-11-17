package ai.jarvis.app.audio

import android.media.AudioDeviceInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * T-0744: the car's hands-free link is a Bluetooth SCO output, so a phone in the car resolved
 * it to a worn headset, and with headset mode on every conversation turn pointed communication
 * audio at it: a call to the car, narrowband, its music muted until the turn let go.
 */
class HeadsetMonitorCarTest {

    private val carOutputs = setOf(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)

    @Test
    fun `the car's hands-free is not a headset while the car is here`() {
        val kind = HeadsetMonitor.kindOf(carOutputs, carHere = true)
        assertEquals(HeadsetKind.BLUETOOTH_A2DP, kind)
        val profile = CaptureProfile.forRoute(AudioRoute(kind = kind, headsetModeEnabled = true))
        assertFalse("no communication route to the car", profile.requestCommunicationDevice)
        assertFalse(profile.useVoiceCommunication)
    }

    @Test
    fun `an earpiece's hands-free is still a headset when no car is here`() {
        assertEquals(HeadsetKind.BLUETOOTH_SCO, HeadsetMonitor.kindOf(carOutputs, carHere = false))
    }

    @Test
    fun `a wired or LE headset stays a headset in the car`() {
        assertEquals(
            HeadsetKind.WIRED_HEADSET,
            HeadsetMonitor.kindOf(carOutputs + AudioDeviceInfo.TYPE_WIRED_HEADSET, carHere = true),
        )
        assertEquals(
            HeadsetKind.BLE_HEADSET,
            HeadsetMonitor.kindOf(carOutputs + AudioDeviceInfo.TYPE_BLE_HEADSET, carHere = true),
        )
    }
}
