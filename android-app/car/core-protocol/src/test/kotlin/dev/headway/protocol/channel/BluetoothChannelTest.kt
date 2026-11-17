package dev.headway.protocol.channel

import aap_protobuf.service.bluetooth.BluetoothServiceOuterClass.BluetoothService
import aap_protobuf.service.bluetooth.message.BluetoothPairingMethodOuterClass.BluetoothPairingMethod
import aap_protobuf.service.bluetooth.message.BluetoothPairingResponseOuterClass.BluetoothPairingResponse
import aap_protobuf.shared.MessageStatusOuterClass.MessageStatus
import dev.headway.protocol.framing.AapMessage
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class BluetoothChannelTest {
    @Test
    fun `the pairing request carries the phone and the method, on the service channel, encrypted`() {
        val message = BluetoothChannel.pairingRequest(
            channelId = 6,
            phoneAddress = "AA:BB:CC:DD:EE:FF",
            method = BluetoothPairingMethod.BLUETOOTH_PAIRING_NUMERIC_COMPARISON,
        )
        assertEquals(6, message.channelId)
        assertEquals(BluetoothMessageId.PAIRING_REQUEST, message.messageId)
        assertFalse(message.control)
        assertTrue(message.encrypted)
        val decoded = BluetoothChannel.decodeRequest(message.payload)
        assertEquals("AA:BB:CC:DD:EE:FF", decoded.phoneAddress)
        assertEquals(BluetoothPairingMethod.BLUETOOTH_PAIRING_NUMERIC_COMPARISON, decoded.pairingMethod)
    }

    @Test
    fun `the method is one the car offers, the most usable first`() {
        fun service(vararg m: BluetoothPairingMethod) =
            BluetoothService.newBuilder().setCarAddress("11:22:33:44:55:66").addAllSupportedPairingMethods(m.toList()).build()
        assertEquals(
            BluetoothPairingMethod.BLUETOOTH_PAIRING_PIN,
            BluetoothChannel.methodFor(service(BluetoothPairingMethod.BLUETOOTH_PAIRING_OOB, BluetoothPairingMethod.BLUETOOTH_PAIRING_PIN)),
        )
        assertEquals(
            BluetoothPairingMethod.BLUETOOTH_PAIRING_NUMERIC_COMPARISON,
            BluetoothChannel.methodFor(service()),
        )
    }

    @Test
    fun `the car's answer is said in words`() {
        val answer = AapMessage(
            channelId = 6, control = false, encrypted = true,
            messageId = BluetoothMessageId.PAIRING_RESPONSE,
            payload = BluetoothPairingResponse.newBuilder()
                .setStatus(MessageStatus.STATUS_SUCCESS).setAlreadyPaired(true).build().toByteArray(),
        )
        assertEquals(
            "bluetooth: the car answered the pairing request: STATUS_SUCCESS, already paired",
            BluetoothChannel.describe(answer),
        )
    }
}
