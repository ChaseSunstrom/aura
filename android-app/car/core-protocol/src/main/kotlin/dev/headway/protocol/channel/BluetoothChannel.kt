/*
 * This file is part of Headway.
 * Copyright (C) 2026 The Headway Authors
 *
 * Headway is free software: you can redistribute it and/or modify it under the
 * terms of the GNU General Public License as published by the Free Software
 * Foundation, either version 3 of the License, or (at your option) any later
 * version.
 *
 * Headway is distributed in the hope that it will be useful, but WITHOUT ANY
 * WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
 * A PARTICULAR PURPOSE. See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along with
 * Headway. If not, see <https://www.gnu.org/licenses/>.
 */

package dev.headway.protocol.channel

import aap_protobuf.service.bluetooth.BluetoothServiceOuterClass.BluetoothService
import aap_protobuf.service.bluetooth.message.BluetoothAuthenticationDataOuterClass.BluetoothAuthenticationData
import aap_protobuf.service.bluetooth.message.BluetoothAuthenticationResultOuterClass.BluetoothAuthenticationResult
import aap_protobuf.service.bluetooth.message.BluetoothPairingMethodOuterClass.BluetoothPairingMethod
import aap_protobuf.service.bluetooth.message.BluetoothPairingRequestOuterClass.BluetoothPairingRequest
import aap_protobuf.service.bluetooth.message.BluetoothPairingResponseOuterClass.BluetoothPairingResponse
import dev.headway.protocol.framing.AapMessage

/**
 * Message ids on the Bluetooth service channel.
 *
 * Source: `core-protocol/src/main/proto/aap_protobuf/service/bluetooth/BluetoothMessageId.proto`.
 * aasdk's head-unit `BluetoothService` receives `PAIRING_REQUEST` and answers with
 * `PAIRING_RESPONSE`, which fixes the direction: the request is the phone's to send.
 */
object BluetoothMessageId {
    /** Phone → HU. `BluetoothPairingRequest { phone_address, pairing_method }`. */
    const val PAIRING_REQUEST: Int = 32769

    /** HU → phone. `BluetoothPairingResponse { status, already_paired }`. */
    const val PAIRING_RESPONSE: Int = 32770

    /** HU → phone. `BluetoothAuthenticationData { auth_data }`: a passkey to confirm. */
    const val AUTHENTICATION_DATA: Int = 32771

    /** HU → phone. `BluetoothAuthenticationResult { status }`. */
    const val AUTHENTICATION_RESULT: Int = 32772
}

/**
 * The phone half of the Bluetooth service: telling the car which phone this is.
 *
 * ## Why this exists
 *
 * Drive logs from a 2021 Chevrolet (build 1493, 2026-10-06) showed the steering-wheel
 * skip buttons answering "Action unavailable" while every input key was bound and the
 * playback status was being sent -- and not one key event ever reached the phone.
 * In the same logs the phone's hands-free and media profiles were disconnected for the
 * whole projection session and reconnected only after it ended. The car advertises
 * this service in every session, and Headway opened it and never said a word on it.
 * A projecting phone announces itself here with a pairing request; a head unit that
 * never hears one has no paired phone to route calls or media controls to.
 *
 * Not claimed: that this alone brings the buttons back. It is the one step of the
 * protocol the phone side was missing; the next drive's log says what the car made
 * of it.
 */
object BluetoothChannel {

    /** The platform's answer to `BluetoothAdapter.getAddress()` for an app without the system permission. */
    const val PLACEHOLDER_ADDRESS: String = "02:00:00:00:00:00"

    /** Pairing methods in the order a phone with a screen would rather use them. */
    private val PREFERRED = listOf(
        BluetoothPairingMethod.BLUETOOTH_PAIRING_NUMERIC_COMPARISON,
        BluetoothPairingMethod.BLUETOOTH_PAIRING_PASSKEY_ENTRY,
        BluetoothPairingMethod.BLUETOOTH_PAIRING_PIN,
        BluetoothPairingMethod.BLUETOOTH_PAIRING_OOB,
    )

    /** The method to ask for: the most usable one the car says it supports. */
    fun methodFor(service: BluetoothService): BluetoothPairingMethod {
        val offered = service.supportedPairingMethodsList.toSet()
        return PREFERRED.firstOrNull { it in offered }
            ?: BluetoothPairingMethod.BLUETOOTH_PAIRING_NUMERIC_COMPARISON
    }

    fun pairingRequest(channelId: Int, phoneAddress: String, method: BluetoothPairingMethod): AapMessage =
        AapMessage(
            channelId = channelId,
            control = false,
            encrypted = true,
            messageId = BluetoothMessageId.PAIRING_REQUEST,
            payload = BluetoothPairingRequest.newBuilder()
                .setPhoneAddress(phoneAddress)
                .setPairingMethod(method)
                .build()
                .toByteArray(),
        )

    fun decodeRequest(payload: ByteArray): BluetoothPairingRequest = BluetoothPairingRequest.parseFrom(payload)

    /** One line for the drive log saying what the car answered; never the passkey itself. */
    fun describe(message: AapMessage): String = runCatching {
        when (message.messageId) {
            BluetoothMessageId.PAIRING_RESPONSE -> BluetoothPairingResponse.parseFrom(message.payload).let {
                "bluetooth: the car answered the pairing request: ${it.status.name}, " +
                    if (it.alreadyPaired) "already paired" else "not yet paired"
            }
            BluetoothMessageId.AUTHENTICATION_DATA -> BluetoothAuthenticationData.parseFrom(message.payload).let {
                "bluetooth: the car sent a ${it.authData.length}-character passkey to confirm" +
                    (if (it.hasPairingMethod()) " (${it.pairingMethod.name})" else "")
            }
            BluetoothMessageId.AUTHENTICATION_RESULT -> BluetoothAuthenticationResult.parseFrom(message.payload).let {
                "bluetooth: authentication ${it.status.name}"
            }
            else -> "bluetooth: message ${message.messageId} (${message.payload.size} bytes)"
        }
    }.getOrElse { "bluetooth: message ${message.messageId} did not decode: ${it.message}" }
}
