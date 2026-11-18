package ai.aura.app.assist

/**
 * Who says a line when the server cannot (v2 §6.10, AUDIT V10), by the worn theme pack's
 * `voice.offline.fallback`. [BUNDLED_VOICE] (JARVIS): a cached clip of his voice, then the
 * bundled voice ([BundledVoice]), and only without both the system engine, announced.
 * [CLIPS_ONLY] (AURA, the AURA brief §3): her own clips and no other voice ever; her
 * own voice on the phone ([BundledVoice.Model.KOKORO], T-0872) says a line with none only once
 * this phone has measured it keeping up in real time ([ownRealtime]); until then the line stays
 * unsaid and her offline notice, itself a clip, says why. The orders are
 * `tests/contracts/offline-voice.json` ("speech"); the mirror is `tools/offline_voice_test.py`.
 * Does not make the bundled voice the CosyVoice persona: it is the Piper voice that persona's
 * reference clip was made from (docs/runbooks/voice.md).
 */
object OfflineVoice {
    enum class Speaker { CLIP, BUNDLED, SYSTEM_ANNOUNCED, ON_DEVICE, NOTICE_ONLY }

    const val BUNDLED_VOICE = "bundled-voice"
    const val CLIPS_ONLY = "prerendered-clips"

    fun choose(
        hasClip: Boolean,
        bundledReady: Boolean,
        fallback: String = BUNDLED_VOICE,
        ownRealtime: Boolean = false,
    ): Speaker = when {
        hasClip -> Speaker.CLIP
        fallback == CLIPS_ONLY -> if (ownRealtime) Speaker.ON_DEVICE else Speaker.NOTICE_ONLY
        bundledReady -> Speaker.BUNDLED
        else -> Speaker.SYSTEM_ANNOUNCED
    }
}
