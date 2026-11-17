package ai.jarvis.app.assist

/**
 * Where a voice turn is: what the conversation engines tell their surface
 * through [JarvisConversation.Ui.onMode], beside a caption.
 *
 * This used to be `JarvisOrbView.Mode`, an enum owned by the old orb View
 * that doubled as the conversation's own flag for barge-in and TTS gating.
 * The orb is gone (GLASS_HUD §10: the reactor has eleven states, derived from
 * `ReactorInputs` by `ReactorState.derive`, and this five-value phase can
 * express almost none of them), but the conversation still has a phase — the
 * microphone is open, the recogniser is working, the reply is playing — and
 * seven surfaces implement `onMode` against it. So the enum moved here and
 * kept its five values, and nothing about what it means changed. What the
 * reactor shows comes from [JarvisConversation.Ui.onReactor] instead, which
 * is built from the HUD's own state rather than from this.
 *
 * `ERROR` is the phase a surface shows for a moment after a turn failed; the
 * engines never set it as their own state.
 */
enum class Phase {
    /** Nothing running. */
    IDLE,
    /** The microphone is open for the person. */
    LISTENING,
    /** The recogniser or the model is working on what they said. */
    THINKING,
    /** The reply is playing. */
    SPEAKING,
    /** The last turn failed; a surface lingers here before listening again. */
    ERROR,
}
