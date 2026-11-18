package ai.aura.app.assist

/**
 * Who hears the user when the server may be out (v2 §6.11, AUDIT V11). The table and the
 * wording are `tests/contracts/offline-voice.json` ("listen"); `tools/offline_voice_test.py`
 * is the mirror.
 *
 * The platform recogniser owns the microphone and cannot be handed audio, so a turn whose
 * audio is already captured (the Hermes path) can only be heard by the bundled model
 * ([OnPhoneSpeech]); a turn whose microphone is not open yet can use either. When neither is
 * on the phone the answer is [Hearer.NONE] with a line to show: never a silent retry on a
 * server that is not there. Does not cover accuracy: Moonshine and the platform recogniser
 * mishear more than the server's Whisper.
 */
object OnDeviceStt {
    enum class Hearer { SERVER, BUNDLED, PLATFORM, NONE, GATE }

    const val NONE_SAYS = "I can't hear you without the server: this phone has no speech recognition of its own to use."
    const val GATE_SAYS = "I can't check your voice while the server is out, so I can't take that by voice."

    fun choose(
        audioInHand: Boolean,
        serverReachable: Boolean,
        bundledModel: Boolean,
        platformRecognizer: Boolean,
        gateEnforcing: Boolean,
    ): Hearer = when {
        serverReachable -> Hearer.SERVER
        // Words typed on this phone would walk past a speaker gate that checks sound.
        gateEnforcing -> Hearer.GATE
        audioInHand -> if (bundledModel) Hearer.BUNDLED else Hearer.NONE
        else -> if (platformRecognizer) Hearer.PLATFORM else Hearer.NONE
    }

    /** The line to show for a [Hearer] that cannot hear, null for the ones that can. */
    fun says(hearer: Hearer): String? = when (hearer) {
        Hearer.NONE -> NONE_SAYS
        Hearer.GATE -> GATE_SAYS
        else -> null
    }
}
