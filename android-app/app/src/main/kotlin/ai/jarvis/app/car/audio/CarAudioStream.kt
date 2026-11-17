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

package ai.jarvis.app.car.audio

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.car.service.ResetReport
import ai.jarvis.app.car.voice.CarVoice
import ai.jarvis.app.ui.theme.ActiveTheme
import aap_protobuf.service.ServiceOuterClass
import aap_protobuf.service.media.shared.message.MediaCodecTypeOuterClass.MediaCodecType
import aap_protobuf.service.media.sink.message.AudioStreamTypeOuterClass.AudioStreamType
import android.content.Context
import android.media.AudioManager
import android.media.projection.MediaProjection
import android.os.SystemClock
import dev.headway.audio.CarAudioSource
import dev.headway.audio.CarAudioSourceException
import dev.headway.audio.PcmBuffer
import dev.headway.audio.PhoneAudioFocus
import dev.headway.protocol.channel.AudioChannel
import dev.headway.protocol.channel.AudioChannelException
import dev.headway.protocol.channel.AudioFocus
import dev.headway.protocol.channel.AudioFocusState
import dev.headway.protocol.channel.MediaAudioRoute
import dev.headway.protocol.channel.PcmFormat
import dev.headway.protocol.control.ControlMessageType
import dev.headway.protocol.framing.AapMessage
import dev.headway.protocol.framing.ChannelId
import dev.headway.protocol.io.MessageChannel
import dev.headway.protocol.session.HeadUnitProfile
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.Executors
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Everything between "the car opened its audio sink channels" and "the car plays
 * what Headway says".
 *
 * ## What travels here
 *
 * Headway's own audio — spoken replies from the voice pipeline and UI prompts —
 * and, by default, **third-party music**.
 *
 * That last part reverses CLAUDE.md, which says *"instruct/steer media audio
 * over the car's normal Bluetooth A2DP link, which coexists with the AAP
 * session"*. The coexistence premise is false on the target vehicle, and a
 * capture of real Android Auto against that same head unit says so plainly:
 * Gearhead logs `disabling A2dp route while in projection`, retries with
 * `A2DP playing while in projection. Trying disabling`, ends at
 * `ANDROID_AUTO_BLUETOOTH_A2DP_DISCONNECTED`, and streams a named third-party
 * player over the AAP media channel instead. The driver's report matches: with
 * Headway projecting, the car is silent.
 *
 * So media is driven here, tapped from the phone with [PhoneAudioCapture] — the
 * unprivileged spelling of the `REMOTE_SUBMIX` recording the same capture shows
 * Gearhead doing. The `mediaAudioOverAap` quirk still turns it off for a unit
 * that genuinely keeps A2DP alive. See ADR 0005.
 *
 * The telephony sink is still left alone, and that is not symmetry: the same
 * capture shows Gearhead *keeping* `STATE_HFP_CONNECTED` throughout. A phone
 * call already reaches the car over Bluetooth HFP, and duplicating it over AAP
 * would put the caller's voice in the cabin twice — and voice audio is not
 * capturable at any privilege level anyway.
 *
 * ## Bring-up order
 *
 * Per channel, identical to video's minus the codec configuration — PCM has
 * nothing to configure:
 *
 * 1. `sendSetup` — offer PCM.
 * 2. `awaitConfig` — the head unit answers with a status, an acknowledgement
 *    window and the configuration *indices* it will accept.
 * 3. `sendStart` — with a session id and the chosen index, which is what fixes
 *    [AudioChannel.format]. **Nothing may be rendered before this point**: the
 *    sample rate is the car's, and synthesising at a guessed rate produces a
 *    prompt that plays at the wrong speed and reports no error (see
 *    [CarAudioSource]).
 *
 * ## Why bring-up runs in the background
 *
 * [start] launches the bring-up and returns immediately, where
 * `CarVideoStream.start` performs its own inline. That difference is deliberate.
 * A real 2021 Chevrolet Infotainment 3 unit closes the session about fifteen
 * seconds after the last channel opens if no video has arrived, and audio setup
 * is the kind of exchange a head unit is entitled never to answer. Blocking the
 * session's start-up sequence on a `Config` that may never come would spend the
 * video deadline on a prompt channel nobody is listening to yet. Every wait in
 * here is bounded for the same reason.
 *
 * ## Focus, and who reads the control channel
 *
 * Audio focus is not part of any audio channel: it is two control-channel
 * messages, `MESSAGE_AUDIO_FOCUS_REQUEST` (18) out and
 * `MESSAGE_AUDIO_FOCUS_NOTIFICATION` (19) back
 * (`aasdk/protobuf/aap_protobuf/service/control/ControlMessageType.proto`
 * L23-L24; see [AudioFocus]). This class owns an [AudioFocus] over the control
 * channel and **only ever sends on it**. It must not read: the session's
 * keepalive loop already reads the control channel, and
 * [dev.headway.protocol.io.ChannelDemultiplexer] gives every view of a channel id
 * the same queue, so a second reader would race for messages — half the pings
 * would land here and go unanswered, and a head unit concludes a phone that does
 * not answer a keepalive has gone away.
 *
 * So notifications are *pushed* in through [onControlMessage], which is the
 * arrangement [PhoneAudioFocus.onCarFocus] documents ("whichever component owns
 * the control channel's demultiplexer view feeds notifications in here"). The
 * cost is that [AudioFocus.state] stays null — it is only updated by the reads
 * this class must not do — so the car's last word is tracked here instead, in
 * [lastCarFocus].
 */
class CarAudioStream(
    /** The sinks Headway will drive: guidance and system audio, in that order. */
    private val sinks: List<AudioChannel>,
    /** Advertised audio sinks left to another link; reported by [describe]. */
    private val untouched: List<ServiceOuterClass.Service>,
    /** Kept for [describe] and because [PhoneAudioFocus] sends through it. */
    private val focus: AudioFocus,
    private val phoneFocus: PhoneAudioFocus,
    private val source: CarAudioSource,
    /**
     * The screen-capture grant, reused to tap playback.
     *
     * The *initial* one. A session very often starts without a grant -- the
     * driver has not answered the consent sheet yet, or Android 15 tore the last
     * one down at the lock screen -- and this used to be the end of the story:
     * [pumpMedia] returned on a null and nothing restarted it, so music never
     * reached the car for the rest of the drive however many times the driver
     * re-shared. A driver reported it as "audio won't play through my car unless
     * I am screen sharing". See [adoptProjection].
     */
    private val projection: MediaProjection? = null,
    /** Used only to ask whether anything is playing; see [pumpMedia]. */
    private val audioManager: AudioManager? = null,
    /**
     * Application context, used only to check RECORD_AUDIO before capturing.
     *
     * Optional so the tests that build this directly need not supply one; a
     * null is read as "cannot check", which errs towards trying rather than
     * towards refusing.
     */
    private val appContext: Context? = null,
    /** Channel id to advertised name, so the log stops guessing. */
    private val names: Map<Int, String> = emptyMap(),
    private val onStep: (String) -> Unit = {},
) {

    private val jobs: MutableList<Job> = mutableListOf()

    /** One prompt at a time; see [withFocus]. */
    private val speaking = Mutex()

    /**
     * Completes when [bringUp] has finished, however it finished. [speak] waits
     * on it so a voice reply arriving during bring-up queues rather than being
     * dropped for a channel that was two hundred milliseconds from ready.
     */
    private val broughtUp = CompletableDeferred<Unit>()

    /**
     * Focus states pushed in by [onControlMessage].
     *
     * Conflated: only the car's latest word matters, and a prompt that waits for
     * a grant is not interested in the state before it. Stale entries are drained
     * before each request, so a grant left over from the previous prompt cannot
     * satisfy the next one.
     */
    private val grants = Channel<AudioFocusState>(Channel.CONFLATED)

    /** Swapped wholesale by [bringUp] rather than mutated, so [speak] sees it consistently. */
    @Volatile
    private var opened: List<AudioChannel> = emptyList()

    @Volatile
    private var refusals: List<String> = emptyList()

    /**
     * Whether the media pump currently holds focus, as a field rather than a local.
     *
     * It has to be shared, because it is not only the pump that can lose it.
     * `PhoneAudioFocus` keeps one focus slot with no ownership or refcount, so a
     * spoken prompt's `withFocus` takes that slot, and its `finally` gives it back
     * to the music only when the music had it (`PhoneAudioFocus.endPrompt`). With the
     * flag as a local in the pump loop it stayed true across that, the
     * `if (!holdingFocus)` guard never re-requested, and every buffer after the
     * first voice reply was streamed to a car that had switched back to its own
     * radio: music silently gone for the rest of the drive after one prompt.
     */
    @Volatile
    internal var mediaFocusHeld: Boolean = false

    /** The car's latest focus notification, since [AudioFocus.state] cannot be. */
    @Volatile
    var lastCarFocus: AudioFocusState? = null
        private set

    /** When [lastCarFocus] arrived, on [SystemClock.elapsedRealtime]; for the reset report (T-1217). */
    @Volatile
    private var lastCarFocusAtMs: Long = 0L

    /** When the media queue last dropped audio, on the same clock; null until it has. */
    @Volatile
    private var lastBacklogDropAtMs: Long? = null

    /** Prompts rendered onto a channel. Diagnostics. */
    @Volatile
    var promptsPlayed: Long = 0L
        private set

    /** PCM buffers handed to the channels. Diagnostics. */
    @Volatile
    var buffersSent: Long = 0L
        private set

    /**
     * `AUDIO_UNDERFLOW_NOTIFICATION` messages seen — the car ran out of audio to
     * play, which is what choppy speech looks like on the wire.
     *
     * Incomplete on purpose, and worth knowing about: this counts only the ones
     * [drainAcks] sees. An underflow arriving while [AudioChannel.sendPcm] is
     * waiting for acknowledgement credit is consumed inside `awaitCredit`, which
     * has no hook. Reporting a partial count and saying so beats reporting a
     * number that looks complete.
     */
    @Volatile
    var underflows: Long = 0L
        private set

    /** Longest single wait for the car to take a buffer, in ms. */
    private var longestSendMillis: Long = 0L

    /**
     * Longest the reader was away from the tap between two reads, in ms.
     *
     * Not the read cycle, which is mostly the blocking read itself and says
     * nothing. This is the window in which `AudioRecord`'s ring fills with
     * nobody draining it, so it is the number to compare against the tap's
     * capacity -- `BUFFERS_IN_FLIGHT` buffers of `BUFFER_MICROS` each, 320 ms
     * as configured. Anything approaching that lost music.
     */
    private var idleGapMillis: Long = 0L

    /** Sends that took longer than [STALL_REPORT_MILLIS]. */
    private var slowSends: Long = 0L

    /** Buffers dropped because the car fell further behind than the queue holds. */
    private var stalls: Long = 0L

    /** Bytes the capture produced while focus was held. Diagnostics. */
    @Volatile
    private var capturedBytes: Long = 0L

    /** The live queue between the capture and the car, if one is running. */
    @Volatile
    private var mediaQueue: MediaQueue? = null

    /** Captured audio forwarded to the car. Diagnostics. */
    @Volatile
    var mediaBytesSent: Long = 0L
        private set

    /** Held so [stop] can close the tap even if the pump is cancelled mid-read. */
    @Volatile
    private var mediaCapture: PhoneAudioCapture? = null

    /**
     * The grant media capture is using, which can arrive and vanish mid-session.
     *
     * A flow rather than a field so [pumpMedia] can wait on it instead of
     * polling or giving up. `AudioPlaybackCaptureConfiguration` is built from a
     * `MediaProjection` and there is no unprivileged substitute, so no grant
     * genuinely means no music over AAP -- but "not yet" and "never" are
     * different answers and only one of them was being given.
     */
    private val liveProjection = MutableStateFlow(projection)

    /**
     * Music goes over the car's media channel and no grant is held, so none reaches the
     * car (CarConnectCheck). False when media is left to Bluetooth: no grant is needed there.
     */
    val musicWaitsOnGrant: Boolean
        get() = channelFor(AudioStreamType.AUDIO_STREAM_MEDIA) != null && liveProjection.value == null

    /**
     * Points media capture at a new grant, or clears it when one is lost.
     *
     * Called by the service on both edges: `adoptProjectionGrant` when the
     * driver shares, `AppPaneHost.onGrantLost` when the platform takes it away.
     */
    fun adoptProjection(projection: MediaProjection?) {
        val had = liveProjection.value != null
        liveProjection.value = projection
        if (projection != null && !had) {
            onStep("media: a screen-sharing grant arrived; music can now reach the car")
        } else if (projection == null && had) {
            onStep("media: the screen-sharing grant went away, so music stops reaching the car")
        }
    }

    /** True once a guidance channel is started and a prompt can actually be spoken. */
    val canSpeak: Boolean
        get() = channelFor(AudioStreamType.AUDIO_STREAM_GUIDANCE) != null

    // --- lifecycle ------------------------------------------------------------

    /**
     * Starts the focus relay and the channel bring-up, both in [scope], and
     * returns at once. See the class KDoc for why this does not wait.
     *
     * @return false when the head unit advertised nothing Headway can drive,
     *   which is a real answer rather than an error: the session stays up, video
     *   and input are unaffected, and the log says what was missing.
     */
    suspend fun start(scope: CoroutineScope, sessionId: Int = DEFAULT_SESSION_ID): Boolean {
        if (sinks.isEmpty()) {
            onStep("the head unit advertised no guidance or system audio sink; ${ActiveTheme.name} is mute")
            broughtUp.complete(Unit)
            return false
        }

        // PhoneAudioFocus queues AudioManager's callbacks and states plainly that
        // "nothing here launches anything" -- the session owns the coroutine that
        // drains them, and without it a phone call would never be relayed to the
        // car.
        live.set(this)
        jobs += scope.launch { phoneFocus.relayPhoneFocusChanges() }
        jobs += scope.launch {
            bringUp(sessionId)
            pumpMedia()
        }
        return true
    }

    // --- media ----------------------------------------------------------------

    // Sends the phone's music to the car for as long as there is music.
    //
    // ## Why this is not simply "capture, forward, forever"
    //
    // Holding `AUDIO_FOCUS_GAIN` on the control channel is what makes the head
    // unit unmute the Android Auto source — verified against a real capture,
    // where the car answers `LOSS` before the request and `STATE_GAIN` after,
    // and Gearhead only then enables the stream. It is also what silences the
    // car's own radio. So focus has to be taken when there is something to play
    // and handed back when there is not, or Headway mutes the driver's radio for
    // the whole drive and plays digital silence over it.
    //
    // `AudioManager.isMusicActive` is the unprivileged answer to "is there
    // something to play". It is polled rather than observed because no callback
    // for it exists below the notification-listener grant, and the poll is
    // cheap.
    //
    // ## Why the read loop needs no clock
    //
    // [PhoneAudioCapture.read] blocks until the tap has a buffer, and the tap
    // fills at real time. So the loop below runs at exactly the rate the music
    // plays, and back-pressure from the car's acknowledgement window
    // (`MAX_UNACK: 16` on this head unit, not openauto's 1) simply slows the
    // reads. No pacing code, and no queue to drift.
    /**
     * Whether the first [length] bytes of [buffer] are silence.
     *
     * 16-bit little-endian, which is the only format the media sink negotiates
     * (`MEDIA(5) ready: 48000 Hz 16-bit stereo` on the target vehicle) and what
     * `PhoneAudioCapture` is configured to produce. Reads every other byte --
     * the high half of each sample -- because the low half cannot carry a
     * meaningful amplitude on its own, and this runs on the thread pumping
     * music.
     */
    // A member only so the constant travels with it; the work is the top-level
    // function below, which is pure and has a unit test.
    private fun isSilence(buffer: ByteArray, length: Int): Boolean =
        isPcm16Silence(buffer, length, SILENCE_LEVEL)

    private suspend fun pumpMedia() {
        val channel = channelFor(AudioStreamType.AUDIO_STREAM_MEDIA) ?: return
        val format = channel.format ?: run {
            onStep("media: the channel started without a negotiated format; not sending music")
            return
        }
        // Outer loop: one pass per grant. A capture ends when the driver's grant
        // does -- Android 15 and later stop projection at every lock screen --
        // and the first version of this treated that as the end of media for
        // the session.
        var failures = 0
        while (currentCoroutineContext().isActive) {
            val projection = awaitProjection() ?: return
            val started = pumpMediaWith(channel, projection, format)
            // Only for a grant that is gone: a capture that would not start under a live one
            // is the retry's below, and waiting here for another grant would stall it.
            if (mediaFocusHeld && liveProjection.value !== projection) holdFocusForGrant(projection)
            // Somebody else has already moved on -- a new grant arrived, or the
            // platform's callback reported this one lost. Nothing to decide.
            if (liveProjection.value !== projection) {
                failures = 0
                continue
            }
            // This is the correction to a fix that was worse than the bug. The
            // grant is *still live*: the capture ended for its own reasons --
            // `AudioRecord` refusing to initialise, an audioserver restart
            // returning ERROR_DEAD_OBJECT, `streamPcm` throwing on a link
            // hiccup. Clearing it here, as the first version did unconditionally,
            // parked the pump forever on a grant nobody would ever re-issue and
            // told the driver to share a screen they were already sharing.
            failures = if (started) 0 else failures + 1
            if (failures >= MEDIA_CAPTURE_ATTEMPTS) {
                onStep(
                    "media: playback capture would not start $failures times running; giving up " +
                        "on this screen-sharing grant. Sharing again will start it over"
                )
                // compareAndSet, so a grant that arrived while this was deciding
                // is never clobbered.
                liveProjection.compareAndSet(projection, null)
                failures = 0
                continue
            }
            // Always a pause before going round, so a capture that fails
            // instantly and forever cannot spin the CPU for the drive.
            sleepBetweenCaptures()
        }
    }

    /**
     * Whether RECORD_AUDIO is granted, which playback capture also needs.
     *
     * Null-safe about the context: the stream is constructible in tests without
     * one, and a missing context should not be reported as a missing permission.
     */
    private fun hasRecordPermission(): Boolean {
        val context = appContext ?: return true
        return runCatching {
            context.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
        }.getOrDefault(true)
    }

    /**
     * Keeps the car on Android Auto audio while the grant [ended] is replaced (T-0990).
     *
     * Android 15 and later end the grant at every lock; the driver re-shares within seconds.
     * Giving focus back at once made the car leave Android Auto audio and return for each lock
     * (the owner, 2026-11-12: "doing the wrong audio mode"). Bounded by
     * [GRANT_GAP_HOLD_MILLIS]: with no new grant the radio gets its turn back.
     *
     * Does not cover what the car plays meanwhile: nothing is sent, so it is silent.
     */
    private suspend fun holdFocusForGrant(ended: MediaProjection) {
        val waitingSince = SystemClock.elapsedRealtime()
        while (true) {
            val next = withTimeoutOrNull(GRANT_GAP_HOLD_MILLIS) {
                liveProjection.first { it != null && it !== ended }
            }
            if (next != null) return
            // Not mid-turn: giving focus back while she listens or thinks is the flip
            // [turnHoldsFocus] exists to stop. Bounded by the same cap as a turn.
            val inTurn = inVoiceTurn(CarVoice.view.value.phase, prompt = false)
            if (!turnHoldsFocus(inTurn, SystemClock.elapsedRealtime() - waitingSince)) break
        }
        // The lock a prompt takes: see the idle release in [readCaptureInto].
        speaking.withLock {
            if (mediaFocusHeld) {
                releaseMediaFocus("no screen-sharing grant for ${GRANT_GAP_HOLD_MILLIS / 1000} s")
                mediaFocusHeld = false
            }
        }
    }

    /** A pause between capture attempts, overridable by tests. */
    private suspend fun sleepBetweenCaptures() {
        delay(MEDIA_RETRY_MILLIS)
    }

    /**
     * Waits for a screen-sharing grant, saying once why nothing is playing.
     *
     * The message is the point. Without a grant Headway cannot tap the phone's
     * audio at all -- `AudioPlaybackCaptureConfiguration` takes a
     * `MediaProjection` and there is no unprivileged alternative -- and the car
     * has already switched its source to Android Auto, so Bluetooth A2DP is not
     * being listened to either. Silence with no explanation is what the driver
     * actually experienced.
     */
    private suspend fun awaitProjection(): MediaProjection? {
        liveProjection.value?.let { return it }
        onStep(
            "media: no screen-sharing grant, so there is nothing to tap. Music reaches the car " +
                "over the AAP media channel, which is captured from the phone's own playback, " +
                "and Android only allows that capture through a screen-sharing grant -- so " +
                "share your screen from ${ActiveTheme.name}'s notification to hear music in the car"
        )
        return runCatching { liveProjection.first { it != null } }.getOrNull()
    }

    /** @return whether the capture actually started; see [pumpMedia]'s retry. */
    private suspend fun pumpMediaWith(
        channel: AudioChannel,
        projection: MediaProjection,
        format: PcmFormat,
    ): Boolean {
        // Checked before the grant is blamed. `AudioPlaybackCapture` records
        // through `AudioRecord`, so it needs RECORD_AUDIO as well as the
        // projection -- and without it `AudioRecord` simply refuses, which the
        // retry above would have reported as the screen-sharing grant being at
        // fault and told the driver to share their screen again. That remedy
        // cannot work, and following it three times is how the driver would
        // find that out.
        if (!hasRecordPermission()) {
            onStep(
                "media: the microphone permission is not granted, and playback capture records " +
                    "through AudioRecord -- so music cannot be sent. Grant ${ActiveTheme.name} the " +
                    "microphone permission on the phone; sharing the screen again will not help"
            )
            return false
        }
        onStep(PhoneAudioCapture.describeCapturePolicy())
        val capture = PhoneAudioCapture(projection, format, onStep)
        if (!capture.start()) return false
        mediaCapture = capture

        val buffer = ByteArray(capture.bufferBytes)
        // Everything the car might make us wait for happens on the other side
        // of this. See MediaQueue: `sendPcm` waits for the head unit's credit,
        // and while it waited nothing was reading `AudioRecord`, whose buffer
        // is a ring that overwrites rather than waits. A drive lost 34 seconds
        // of music that way with no error anywhere.
        val queued = MediaQueue()
        mediaQueue = queued
        // A thread of its own, at the priority Android reserves for audio.
        //
        // `AudioRecord.read` blocks, and it was blocking a `Dispatchers.Default`
        // worker -- a pool sized to the CPU count, shared with video encoding,
        // the demultiplexer pump and the sensor channel. `MediaQueue` stops the
        // *car* delaying this loop; it cannot stop the dispatcher doing it, and
        // the tap holds only a few hundred milliseconds before it overwrites.
        // Reading the phone's own audio on time is exactly what
        // THREAD_PRIORITY_URGENT_AUDIO exists for, and it is public and
        // unprivileged.
        val readerThread = Executors.newSingleThreadExecutor { runnable ->
            Thread({
                runCatching {
                    android.os.Process.setThreadPriority(
                        android.os.Process.THREAD_PRIORITY_URGENT_AUDIO,
                    )
                }
                runnable.run()
            }, "headway-audio-capture")
        }
        val reading = readerThread.asCoroutineDispatcher()
        // Set only where the reader came back by itself: a failed link or a cancel gives focus back.
        var endedByItself = false
        try {
            coroutineScope {
                val sender = launch { drainToCar(queued, channel) }
                try {
                    withContext(reading) { readCaptureInto(queued, capture, buffer) }
                } finally {
                    // The reader is the only producer, so once it is done the
                    // sender has a finite amount left to send.
                    //
                    // `NonCancellable`, because a bare `join` in a `finally`
                    // throws the moment the scope is cancelling -- which is
                    // exactly when this runs if the sender is what failed --
                    // and would replace the failure that caused it with a
                    // cancellation naming nothing.
                    //
                    // Bounded, and that is not a nicety. `sendPcm` waits for
                    // the head unit's credit, `awaitCredit` has no timeout, and
                    // neither does the socket write beneath it -- FramedConnection
                    // says so in as many words. The reader returns *normally* on
                    // two paths a driver hits constantly: the projection being
                    // revoked at a lock screen, and the audioserver restarting.
                    // On those the scope is not cancelling, so nothing else
                    // would ever stop this waiting.
                    //
                    // Everything after this point is recovery -- giving the
                    // car's audio focus back so its radio returns, closing the
                    // capture, closing the reader's thread, and letting the
                    // outer loop wait for a new grant. None of it may sit
                    // behind a head unit that has stopped answering. Waiting
                    // here forever meant a locked phone left the car ducked and
                    // silent, with re-sharing the screen doing nothing for the
                    // rest of the drive.
                    queued.close()
                    withContext(NonCancellable) {
                        val drained = withTimeoutOrNull(DRAIN_TIMEOUT_MILLIS) { sender.join() }
                        if (drained == null) {
                            onStep(
                                "media: the car did not take the last of the queued audio " +
                                    "within $DRAIN_TIMEOUT_MILLIS ms, so it is being dropped " +
                                    "rather than held on to"
                            )
                            sender.cancel()
                        }
                    }
                }
            }
            endedByItself = true
            return true
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failed: Exception) {
            // The link dying mid-song is ordinary. Audio must never be the
            // component that decides the session is over.
            onStep("media stream ended: ${failed.message ?: failed::class.java.simpleName}")
            return true
        } finally {
            // Closing the dispatcher shuts the executor down with it, so the
            // thread does not outlive the capture that needed it.
            runCatching { reading.close() }
            mediaQueue = null
            if (mediaFocusHeld && !grantGapHoldsFocus(endedByItself, liveProjection.value === projection)) {
                runCatching { releaseMediaFocus("the capture ended") }
                mediaFocusHeld = false
            }
            runCatching { capture.close() }
            onStep(capture.describe())
        }
    }

    /**
     * Drains [queued] to the car, and is the only thing allowed to wait for it.
     *
     * Runs beside the reader rather than in front of it. Every suspension in
     * here -- the credit window, the socket -- used to be a suspension of the
     * capture as well.
     */
    private suspend fun drainToCar(queued: MediaQueue, channel: AudioChannel) {
        while (true) {
            val pcm = queued.take() ?: return
            val started = SystemClock.elapsedRealtime()
            val sent = channel.streamPcm(pcm)
            val took = SystemClock.elapsedRealtime() - started
            if (took > longestSendMillis) longestSendMillis = took
            // A send is one 20 ms buffer. Anything past STALL_REPORT_MILLIS is
            // the car's credit window closed, and it is the count rather than
            // the worst case that says whether that is happening constantly or
            // happened once.
            if (took > STALL_REPORT_MILLIS) slowSends++
            buffersSent += sent
            mediaBytesSent += pcm.size
        }
    }

    /**
     * Reads the capture until it ends, deciding focus and handing buffers over.
     *
     * @return normally when the capture is finished with; the caller closes it.
     */
    private suspend fun readCaptureInto(
        queued: MediaQueue,
        capture: PhoneAudioCapture,
        buffer: ByteArray,
    ) {
        var idleSince = 0L
        var hushSince = 0L
        var turnSince = 0L
        val ducker = MusicDucker()
        var hushes = 0
        var wasListening = false
        var empties = 0
        while (currentCoroutineContext().isActive) {
            val read = capture.read(buffer)
            // Measured here and nowhere else. The three figures this used
            // to print -- worst wait, worst gap, stalls -- were declared,
            // printed and never once assigned, so a session summary read
            // "0 ms, 0 ms, 0 stall(s)" whatever had happened, and that was
            // taken as evidence the audio path was healthy while it was
            // losing half a minute of music a drive.
            // The far end of this is at the bottom of the loop. Between them
            // is the only interval that can lose audio: the tap is filling and
            // nothing is draining it.
            //
            // The first version stamped both ends at a read's *return*, so it
            // measured the whole cycle with the blocking read inside it --
            // and `AudioRecord.read` blocks for a buffer period by design, so
            // it had a 40 ms floor and its worst case was dominated by how
            // long the tap had nothing to give, which is harmless. That is not
            // the question. The question is whether this loop was ever away
            // from the tap long enough to overrun its ring, and only time
            // outside the read counts towards that.
            val returned = SystemClock.elapsedRealtime()
            if (read <= 0) {
                if (read < 0) break
                // A run of empty reads is how a dead capture looks from
                // here. `AudioRecord.read` on a projection Android has
                // ended does not fail -- it returns nothing, forever -- so
                // this loop span silently while the car played nothing, and
                // the session summary reported "0 read error(s)" because
                // there genuinely were none. A drive log had four and a
                // half minutes of it.
                empties++
                if (empties == EMPTY_READS_BEFORE_GIVING_UP) {
                    onStep(
                        "media: the capture has returned nothing $empties times running. " +
                            "That is what a screen-sharing grant Android has revoked looks " +
                            "like from here; waiting for a new one",
                    )
                    // The capture *did* start, and then the grant went
                    // away. That is not a failed start, so returning here
                    // ends the reader; `pumpMediaWith` reports success and
                    // the outer loop goes back to waiting for a new grant
                    // rather than counting down to giving up.
                    return
                }
                continue
            }
            empties = 0

            // The samples first, and `isMusicActive` only when they are
            // silent. Two things come out of that order.
            //
            // Correctness: `AudioManager.isMusicActive()` describes the
            // phone's music stream, not this capture, and it goes false
            // across a gapless track change, a buffering stall and an app
            // switching decoders. Three seconds of that used to release
            // focus and stop the stream, and the car needed a moment to
            // switch its source back when it returned -- which is the
            // "music cuts out for a few seconds every few minutes" a driver
            // reported. Audio that is arriving is proof that audio is
            // playing, and no framework flag can contradict it.
            //
            // Cost: it was a binder call for every buffer, twenty-five
            // times a second, on the thread pumping music. Now it is
            // consulted only when the buffer is silent, which is when there
            // is nothing else to do anyway.
            val quiet = isSilence(buffer, read)
            // A voice turn is not the music stopping. The player is paused for the listen,
            // so ten seconds of it used to give the car's focus back mid-turn: the car left
            // Android Auto audio and took a moment to return after she had finished (the
            // owner, 2026-11-11: "switches the car to the wrong audio mode").
            val phase = CarVoice.view.value.phase
            val prompt = speaking.isLocked || phase == Phase.SPEAKING
            val inTurn = inVoiceTurn(phase, prompt)
            if (!inTurn) turnSince = 0L else if (turnSince == 0L) turnSince = returned
            val held = mediaFocusHeld && turnHoldsFocus(inTurn, returned - turnSince)
            val playing = !quiet || held || PhoneAudioCapture.isMusicPlaying(audioManager)
            if (playing) {
                idleSince = 0L
                // Re-reads the shared flag every buffer, so focus taken away
                // by a spoken prompt is noticed and taken back rather than
                // assumed. See [mediaFocusHeld].
                if (!mediaFocusHeld) {
                    // Same lock, same reason: taking focus mid-prompt would
                    // overwrite the prompt's own request in the single slot.
                    speaking.withLock {
                        if (!mediaFocusHeld) mediaFocusHeld = requestMediaFocus()
                    }
                }
            } else if (mediaFocusHeld) {
                // Not released on the first silent buffer: a track change is
                // a second of silence, and giving the radio back and taking
                // it again across every gap would make the car click.
                //
                // `returned` is the stamp taken when this buffer's read came
                // back, at the top of the loop. A second clock read here would
                // shadow it and buy nothing on a ten-second timer.
                if (idleSince == 0L) idleSince = returned
                if (returned - idleSince >= MEDIA_IDLE_RELEASE_MILLIS) {
                    // Under the same lock a prompt takes. `PhoneAudioFocus`
                    // keeps one slot, so releasing it here while a spoken
                    // reply is mid-render would send the car a RELEASE for
                    // the *prompt's* focus and un-duck the radio underneath
                    // it. `speaking` is the existing serialiser for exactly
                    // this resource; the pump had simply never taken it.
                    speaking.withLock {
                        releaseMediaFocus("${MEDIA_IDLE_RELEASE_MILLIS / 1000} s of silence")
                        mediaFocusHeld = false
                    }
                }
            }

            val listening = phase == Phase.LISTENING
            if (!mediaFocusHeld) {
                // Kept in step here too: a listen that ended with focus released must not
                // be remembered and re-asked for when the music next starts.
                wasListening = listenOwed(wasListening, phase, prompt)
                continue
            }
            // Nothing is relayed while she listens: the car went on playing the music into
            // its own microphone, the floor was measured on it and the driver was never
            // heard; on the phone's microphone the music was what she heard (the owner,
            // 2026-11-11). The player is not paused -- a few unheard seconds, no key sent.
            if (!listening) {
                hushSince = 0L
            } else if (hushSince == 0L) {
                hushSince = returned
                if (hushes++ == 0) onStep("media: nothing is sent to the car while ${ActiveTheme.name} listens")
            }
            if (reassertMediaAfterListen(wasListening, listening, mediaFocusHeld, prompt || phase == Phase.THINKING)) {
                // The car's microphone was opened for the listen and it may have left media
                // for voice; the head unit only returns on a fresh GAIN. Under the lock a
                // prompt takes, as the first request above.
                speaking.withLock {
                    onStep("media: a listen ended; asking the car for media focus again")
                    mediaFocusHeld = requestMediaFocus()
                }
            }
            wasListening = listenOwed(wasListening, phase, prompt)
            if (musicHushed(listening, returned - hushSince)) continue
            // Lowered here, whatever the car does: it answers a prompt's request with
            // GUIDANCE_ONLY and whether it then lowers MEDIA is its own choice, which the
            // owner heard as "isn't getting ducked sometimes" (2026-11-11).
            ducker.apply(buffer, read, prompt)
            // A copy, always. The buffer is reused by the next read, and
            // the sender may not have looked at this one yet -- which is
            // the entire point of it being somewhere else.
            val lost = queued.offer(buffer.copyOf(read))
            if (lost > 0) {
                lastBacklogDropAtMs = SystemClock.elapsedRealtime()
                stalls += lost
                if (stalls == lost.toLong()) {
                    onStep(
                        "media: the car is behind by more than the queue holds, so the " +
                            "oldest audio is being dropped. This is counted from here on " +
                            "and reported at the end of the session"
                    )
                }
            }
            capturedBytes += read
            // Closes the interval opened after the read. Deliberately computed
            // here and nowhere else: the paths that `continue` above never
            // reach it, which is right -- they did no work worth measuring,
            // and a variable carried across iterations to cover them would go
            // stale on exactly those paths and inflate the next real one.
            val away = SystemClock.elapsedRealtime() - returned
            if (away > idleGapMillis) idleGapMillis = away
        }
    }

    /** Takes focus for continuous playback, on both the phone and the car. */
    private suspend fun requestMediaFocus(): Boolean {
        val granted = runCatching { phoneFocus.requestForMedia() }.getOrDefault(false)
        onStep(
            if (granted) "media: focus taken; the car should switch to Android Auto audio"
            else "media: the phone refused audio focus; sending anyway"
        )
        // Sent regardless. The phone's own focus stack is not the car's, and the
        // car has been observed to grant AAP focus that Android had declined.
        return true
    }

    private suspend fun releaseMediaFocus(why: String) {
        runCatching { phoneFocus.release() }
        onStep("media: focus released: $why")
    }

    /**
     * Stops relaying focus and releases the phone-side resources.
     *
     * No `Stop` is sent on the audio channels, for the same reason
     * `CarVideoStream.stop` does not: this runs when the session is already being
     * torn down, sending is suspending, and the link is usually gone by now. The
     * head unit sees the connection close, which is the same information.
     */
    fun stop() {
        jobs.forEach { it.cancel() }
        jobs.clear()
        // Abandons Android's focus. The car's was already released at the end of
        // every prompt by withFocus, so nothing is left holding the radio down.
        runCatching { phoneFocus.close() }
        runCatching { source.close() }
        // Closed here as well as in the pump's finally: a cancelled coroutine
        // parked inside AudioRecord.read does not run its finally until the read
        // returns, and a live tap holds a microphone-class resource.
        runCatching { mediaCapture?.close() }
        mediaCapture = null
        // Compare-and-set, for the same reason `CarVideoStream` does it: a new
        // session may already have published itself before this one's teardown
        // arrives, and unpublishing it would leave a live stream unreachable.
        live.compareAndSet(this, null)
    }

    /**
     * A channel's advertised name, falling back to Headway's constant table.
     *
     * See [nameOf]: the table is Headway's own numbering and a real car assigns
     * its own, so using it as a name misreports every channel on this vehicle.
     */
    private fun nameFor(channelId: Int): String =
        names[channelId] ?: ChannelId.describe(channelId)

    /**
     * The reset report's inputs (T-1217), read where they are: each open sink's own counters,
     * the car's last focus word with when it came, and when the queue last dropped audio.
     * Read-only; the clock is [SystemClock.elapsedRealtime].
     */
    fun resetFacts(): Triple<List<ResetReport.Sink>, ResetReport.Focus?, Long?> = Triple(
        opened.map { ResetReport.Sink(nameFor(it.channelId), it.mediaMessagesSent, it.unacknowledgedMessages) },
        lastCarFocus?.let { ResetReport.Focus(it.state.name, lastCarFocusAtMs) },
        lastBacklogDropAtMs,
    )

    /** What opened, what did not, and what the car has said about focus. */
    fun describe(): String {
        val parts = mutableListOf<String>()
        parts += if (opened.isEmpty()) {
            "no channel open"
        } else {
            // Named from the advertisement, not from ChannelId: this car assigns
            // its guidance sink the id Headway's own table calls
            // MEDIA_SINK_VIDEO, and the log said exactly that for a whole drive.
            opened.joinToString {
                // The negotiated format either way. A deferred sink has no
                // `format` until its first use -- `sendStart` is what sets it --
                // and printing null for a channel that is set up and waiting
                // would read as a failure rather than as the resting state.
                val shape = it.format
                    ?: planned[it.channelId]?.let { index -> it.advertisedFormats.getOrNull(index) }
                val state = if (it.sessionId == null || it.stopped) " (idle)" else ""
                "${nameFor(it.channelId)} at $shape$state"
            }
        }
        // The channels' own count, not this class's. `underflows` is only ever
        // incremented by the prompt path's `drainAcks`, and music does not go
        // through it -- so for every drive that was only playing music the old
        // figure was structurally zero however badly the car was starving.
        val carUnderflows = opened.sumOf { it.underflowsSeen }
        parts += "$promptsPlayed prompt(s), $buffersSent buffer(s), " +
            "$carUnderflows underflow(s) reported by the car"
        if (mediaBytesSent > 0 || channelFor(AudioStreamType.AUDIO_STREAM_MEDIA) != null) {
            parts += "music ${mediaBytesSent / 1024} KiB sent"
            // Always stated once music has flowed. These are the numbers that
            // answer "why did it cut out", and a summary that only mentions
            // them when they are bad cannot show that they were fine.
            if (mediaBytesSent > 0) {
                // Every one of these is now actually assigned. They were not:
                // three counters printed at the end of every session, never
                // written to, reading "0 ms, 0 ms, 0 stall(s)" through a drive
                // that lost 34 seconds of music. A figure that cannot move is
                // worse than no figure, because it is read as a measurement.
                val queue = mediaQueue
                parts += "worst wait for the car ${longestSendMillis} ms, " +
                    "worst ${idleGapMillis} ms away from the tap of ~320 available, " +
                    "$slowSends send(s) over ${STALL_REPORT_MILLIS} ms, " +
                    "$stalls buffer(s) dropped behind the car" +
                    (queue?.let { ", queue peaked at ${it.deepest}" } ?: "")
                // The one figure that would have found this a week ago: what
                // the capture produced against what reached the car. They
                // differ only by whatever is still in the queue, so a gap here
                // is audio that was recorded and never played.
                // "while playing", because both of these are gated on media
                // focus being held and the "KiB read" printed on the capture's
                // own line beside them is not. Comparing this pair is valid;
                // comparing either against that one is not, and the label is
                // the only thing that says so.
                parts += "captured ${capturedBytes / 1024} KiB while playing, " +
                    "sent ${mediaBytesSent / 1024} KiB"
                // What the car said back. `sendPcm` waits for credit before
                // every buffer, so acknowledgements are the thing that decides
                // whether the sender runs or waits -- and until now a log could
                // say "worst wait for the car 4200 ms" without being able to
                // say whether the car had acknowledged three buffers or thirty
                // thousand.
                channelFor(AudioStreamType.AUDIO_STREAM_MEDIA)?.let { media ->
                    parts += "music channel: ${media.mediaMessagesSent} message(s) sent, " +
                        "${media.acksReceived} acknowledged, " +
                        "${media.unacknowledgedMessages} outstanding at the end"
                }
            }
        }
        if (refusals.isNotEmpty()) parts += "refused: ${refusals.joinToString("; ")}"
        if (untouched.isNotEmpty()) {
            parts += "left to Bluetooth: " +
                untouched.joinToString { it.mediaSinkService.audioType.name }
        }
        parts += "focus: ${focus.requests.size} request(s) sent, car last said " +
            (lastCarFocus?.state?.name ?: "nothing")
        return "audio: " + parts.joinToString(" | ")
    }

    // --- bring-up -------------------------------------------------------------

    /**
     * Whether this sink is started as soon as it is set up.
     *
     * ## The bug this exists to not have
     *
     * `Start` is not "I might use this channel", it is "audio is coming". A
     * drive log has Headway sending it on the *guidance* sink at connect and
     * then sending `0 prompt(s)` for the rest of the session -- so the head
     * unit spent the whole drive believing a voice stream was live. The driver
     * reported that the car's volume knob would only adjust voice volume, and
     * that turning it up changed nothing they could hear, which is exactly what
     * an empty-but-active guidance stream looks like from the cabin: guidance
     * outranks media in a car's audio policy, so the knob binds to it, and there
     * is nothing on it to make louder.
     *
     * Media is the exception and stays eager. It is the stream that carries
     * music the moment a capture grant exists, its `Start` is what negotiates
     * the format the capture is resampled into, and it is working -- so it
     * keeps the behaviour it has.
     *
     * Deferred channels are still set up, so [canSpeak] and the negotiated
     * format are known from bring-up. Only the announcement waits.
     */
    private fun startsEagerly(stream: AudioStreamType): Boolean =
        stream == AudioStreamType.AUDIO_STREAM_MEDIA

    /**
     * Sends `Start` for a deferred sink when something is about to be put on it.
     *
     * A channel [stopAfterPrompt] stopped is started again under the next session id,
     * so the car can tell one line's acknowledgements from the last one's. Does nothing
     * for a channel that is started and not stopped, which is every eager one.
     */
    private suspend fun ensureStarted(channel: AudioChannel) {
        val last = channel.sessionId
        if (last != null && !channel.stopped) return
        val index = planned[channel.channelId] ?: return
        channel.sendStart(sessionId = last?.plus(1) ?: startedSessionId, configurationIndex = index)
        onStep("${nameFor(channel.channelId)} started for this prompt: ${channel.format}")
    }

    /**
     * Tells the car a deferred sink's audio has ended, as Android Auto does after each
     * prompt.
     *
     * The owner's car, 2026-11-10 (T-1001): the guidance stream was started for the first
     * spoken line and left started, and the car stayed in its prompt state for the rest
     * of the drive, music broken until the screen share was redone. Focus going back to
     * the music is not enough for this head unit; the stream has to end too.
     *
     * Does not cover a head unit that refuses the next `Start`: that prompt is lost and
     * logged by [withFocus]. A link that died is logged here and left to the supervisor.
     */
    private suspend fun stopAfterPrompt(channel: AudioChannel) {
        if (startsEagerly(channel.stream) || channel.sessionId == null || channel.stopped) return
        // A prompt cut short (barge-in, the timeout) is cancelled when it gets here, and
        // that is the prompt whose stream most needs ending. Bounded: the send shares the
        // link's lock with video, and a wedged head unit must not hold [speaking] for good.
        val sent = runCatching {
            withContext(NonCancellable) { withTimeoutOrNull(RELEASE_MILLIS) { channel.sendStop() } }
        }
        val failure = sent.exceptionOrNull()
        // A sequencing bug here, not the car: loud, as everywhere else in this class.
        if (failure is AudioChannelException) throw failure
        if (failure != null || sent.getOrNull() == null) {
            onStep(
                "could not stop ${nameFor(channel.channelId)} after the prompt: " +
                    (failure?.message ?: "the link did not take it within $RELEASE_MILLIS ms")
            )
        } else {
            // Said, because its absence was the 1793 bug and a log could not tell (T-0990).
            onStep("stopped ${nameFor(channel.channelId)} after the prompt")
        }
    }

    /** Configuration index chosen at Setup, by channel id, for [ensureStarted]. */
    private val planned = mutableMapOf<Int, Int>()

    /** The session id [bringUp] used, so a deferred Start matches the eager ones. */
    private var startedSessionId: Int = DEFAULT_SESSION_ID

    private suspend fun bringUp(sessionId: Int) {
        startedSessionId = sessionId
        val started = mutableListOf<AudioChannel>()
        val refused = mutableListOf<String>()
        try {
            for (channel in sinks) {
                if (open(channel, sessionId, refused)) started += channel
            }
        } finally {
            // Published on every path, including cancellation: a speak() waiting
            // on broughtUp must not be left suspended by a session that went
            // away, and whatever did open before it went away is still the truth.
            opened = started.toList()
            refusals = refused.toList()
            broughtUp.complete(Unit)
        }
        onStep(describe())
    }

    /**
     * Sets up and starts one sink, or records why it could not be.
     *
     * A head unit refusing a channel — or never answering — is an ordinary
     * outcome that belongs in the log, not an exception that takes down a session
     * whose video is working. Only cancellation propagates.
     */
    private suspend fun open(
        channel: AudioChannel,
        sessionId: Int,
        refused: MutableList<String>,
    ): Boolean {
        val name = nameFor(channel.channelId)
        try {
            channel.sendSetup()
            val config = withTimeoutOrNull(SETUP_TIMEOUT_MILLIS) { channel.awaitConfig() }
            if (config == null) {
                refused += "$name did not answer Setup within $SETUP_TIMEOUT_MILLIS ms"
                onStep("$name never answered Setup; ${ActiveTheme.name} will not use it this session")
                return false
            }
            if (!config.ready) {
                refused += "$name answered ${config.status}"
                onStep("the head unit refused $name: ${config.status}")
                return false
            }

            val index = chosenIndex(channel, config.configurationIndices, name)
            if (index == null) {
                refused += "$name offered no usable configuration"
                return false
            }

            planned[channel.channelId] = index
            // Only the media sink is started here. See `startLazily` for why the
            // others wait, and `ensureStarted` for what wakes them.
            if (startsEagerly(channel.stream)) {
                channel.sendStart(sessionId = sessionId, configurationIndex = index)
                onStep(
                    "$name ready: ${channel.format}, window ${config.maxUnacked ?: "unstated"}, " +
                        "session $sessionId"
                )
            } else {
                onStep(
                    "$name negotiated ${channel.advertisedFormats.getOrNull(index)}, window " +
                        "${config.maxUnacked ?: "unstated"}; not started, because nothing is being sent " +
                        "on it yet"
                )
            }
            return true
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failed: Exception) {
            // Includes EOFException when the link dies mid bring-up. Swallowed
            // here on purpose: the demultiplexer pump notices the same close and
            // the supervisor reconnects, and audio must not be the component that
            // decides a session is over.
            refused += "$name failed: ${failed.message ?: failed::class.java.simpleName}"
            onStep("could not bring up $name: ${failed.message}")
            return false
        }
    }

    /**
     * Picks the configuration index to start with.
     *
     * openauto offers exactly one index, zero
     * (`openauto/src/autoapp/Service/MediaSink/AudioMediaSinkService.cpp`
     * L167-L183), and taking the first offered is what
     * `CarVideoStream.start` does with the video equivalent.
     *
     * The one departure: a head unit that answers `STATUS_READY` while naming no
     * index at all is tolerated when it advertised a single `AudioConfiguration`,
     * because then there is nothing to guess — one format was offered and it is
     * the only thing `Start` could mean. With two or more advertised formats and
     * no index, this refuses rather than picking, since choosing wrong yields
     * audio at the wrong sample rate and no error anywhere.
     */
    private fun chosenIndex(
        channel: AudioChannel,
        offered: List<Int>,
        name: String,
    ): Int? {
        offered.firstOrNull()?.let { return it }
        if (channel.advertisedFormats.size == 1) {
            onStep(
                "$name accepted setup but named no configuration index; using its only " +
                    "advertised format (${channel.advertisedFormats.first()})"
            )
            return 0
        }
        onStep(
            "$name accepted setup but named no configuration index and advertised " +
                "${channel.advertisedFormats.size} formats; refusing to guess a sample rate"
        )
        return null
    }

    // --- speaking -------------------------------------------------------------

    /**
     * Says [text] in the car, through the guidance channel.
     *
     * This is the call the Phase 5 voice pipeline makes for a spoken reply.
     * Guidance rather than system audio because that is the stream a head unit
     * treats as navigation-and-assistant speech — it is the one openauto
     * configures at 16 kHz mono for exactly this
     * (`openauto/src/autoapp/Service/ServiceFactory.cpp` L140-L144) — and because
     * `AUDIO_FOCUS_STATE_GAIN_TRANSIENT_GUIDANCE_ONLY` exists precisely so a car
     * can grant this stream and withhold the others.
     *
     * @return false when there is no guidance channel, the phone's audio focus
     *   was refused, or no text-to-speech engine is installed — all of which are
     *   conditions a caller should report rather than crash on.
     */
    suspend fun speak(text: String): Boolean {
        val channel = awaitChannel(AudioStreamType.AUDIO_STREAM_GUIDANCE) ?: run {
            onStep("cannot speak: no guidance audio channel is open")
            return false
        }
        return withFocus(channel) { source.speak(text, it) }
    }

    /**
     * Plays an already-rendered buffer — a UI sound, a captured prompt — on the
     * system audio channel, resampled into whatever format the car negotiated.
     *
     * Separate from [speak] because system audio is what a car mixes over its own
     * source rather than ducking it for, which is the right treatment for a blip
     * and the wrong one for a sentence.
     */
    suspend fun play(
        buffer: PcmBuffer,
        stream: AudioStreamType = AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO,
    ): Boolean {
        val channel = awaitChannel(stream) ?: run {
            onStep("cannot play: no ${stream.name} channel is open")
            return false
        }
        // The acknowledgements pace the stream in real time, so a fixed 15 s cut every
        // reply longer than that off mid-sentence; the line's own length is allowed.
        val lineMillis = buffer.samples.size * 1000L / maxOf(1, buffer.format.sampleRateHz * buffer.format.bitsPerSample / 8 * buffer.format.channelCount)
        return withFocus(channel, PROMPT_TIMEOUT_MILLIS + lineMillis) {
            val target = it.format ?: throw CarAudioSourceException(
                "${nameFor(it.channelId)} has no negotiated format"
            )
            it.streamPcm(source.render(filtered(buffer, target.sampleRateHz), target))
        }
    }

    /** A mono line on its way down to the car's rate, low-passed first; anything else as it came. */
    private fun filtered(buffer: PcmBuffer, rateHz: Int): PcmBuffer {
        val from = buffer.format
        if (from.channelCount != 1 || from.bitsPerSample != 16 || from.sampleRateHz <= rateHz) return buffer
        val mono = dev.headway.protocol.channel.PcmFormat(rateHz, 16, 1)
        return PcmBuffer(mono, PcmStreamResampler(from.sampleRateHz, rateHz).feed(buffer.samples))
    }

    /**
     * This session's head start for a streamed line: [STREAM_HEAD_START_SECONDS] until a line
     * runs dry, grown by it, and halfway back toward the default after each line that does not
     * (see [nextStreamHeadStart]). Readable for its test.
     */
    @Volatile var streamHeadStartSeconds = STREAM_HEAD_START_SECONDS
        private set

    /**
     * [play] for a voice still being made: each piece is sent as it arrives, under one
     * hold of focus, so the car hears the first sentence while the rest is synthesised
     * (the 2026-10-08 drive: replies started only after the whole line was made, at
     * CosyVoice's ~0.55x real time). A line starts once [STREAM_HEAD_START_SECONDS] has
     * gathered, and again after the link fell behind; see [PcmChunker.next]. Stretches stay
     * whole, so the stateless resampler's seams are rare and inaudible.
     * True when anything reached the car. A chunk not acknowledged within
     * [PROMPT_TIMEOUT_MILLIS] ends the line; [STREAM_LINE_MILLIS] bounds the whole.
     */
    suspend fun playStream(
        rateHz: Int,
        pieces: kotlinx.coroutines.channels.ReceiveChannel<ByteArray>,
        stream: AudioStreamType = AudioStreamType.AUDIO_STREAM_GUIDANCE,
    ): Boolean {
        val channel = awaitChannel(stream) ?: run {
            onStep("cannot play: no ${stream.name} channel is open")
            return false
        }
        val perSecond = rateHz * 2
        val startedWith = streamHeadStartSeconds
        val headStart = (perSecond * startedWith).toInt()
        var played = 0
        val ok = withFocus(channel, STREAM_LINE_MILLIS) {
            val target = it.format ?: throw CarAudioSourceException(
                "${nameFor(it.channelId)} has no negotiated format"
            )
            val chunker = PcmChunker(headStart, (perSecond * STREAM_HEAD_START_MAX_SECONDS).toInt())
            // One resampler for the line: the pieces are one stream to the car.
            val resampler = PcmStreamResampler(rateHz, target.sampleRateHz)
            val carMono = dev.headway.protocol.channel.PcmFormat(target.sampleRateHz, 16, 1)
            var ended = false
            try {
                while (true) {
                    val bytes = chunker.next(pieces)
                    if (bytes == null) {
                        ended = true
                        break
                    }
                    // A wedged head unit is still caught per stretch, as a [play] is.
                    played += withTimeoutOrNull(PROMPT_TIMEOUT_MILLIS) {
                        // A piece of one sample can come out as none: nothing to send yet.
                        val ready = resampler.feed(bytes)
                        if (ready.isEmpty()) 0 else it.streamPcm(source.render(PcmBuffer(carMono, ready), target))
                    } ?: throw CarAudioSourceException("the head unit stopped acknowledging mid-line")
                }
            } finally {
                // A line cut short (cancelled, a wedged head unit) says nothing about the link.
                if (ended || chunker.dry > 0) {
                    streamHeadStartSeconds =
                        nextStreamHeadStart(startedWith, chunker.size.toDouble() / perSecond, chunker.dry)
                }
                if (chunker.dry > 0) {
                    onStep(
                        "a streamed line ran dry ${chunker.dry} time(s): the link fell behind the car; " +
                            "lines now gather $streamHeadStartSeconds s before they start"
                    )
                }
            }
            played
        }
        // What reached the car, not whether the whole line did: a head unit that stalls
        // mid-line must not have the turn re-say all of it in the fallback voice.
        if (!ok && played > 0) onStep("a streamed line ended early after $played buffer(s)")
        return played > 0
    }

    /**
     * Takes focus on both sides, runs [render], drains the acknowledgements, and
     * gives focus back — in that order, which is the whole of the Phase 4
     * criterion.
     *
     * Skipping the release is the failure it is really about: the prompt ends,
     * the car's radio stays ducked, and nothing on the wire says why. So the
     * release is in a `finally`, reached even when synthesis fails.
     *
     * Serialised on [speaking] because two prompts sharing one channel would
     * interleave their buffers into gibberish, and because [CarAudioSource]
     * serialises synthesis anyway.
     */
    private suspend fun withFocus(
        channel: AudioChannel,
        timeoutMillis: Long = PROMPT_TIMEOUT_MILLIS,
        render: suspend (AudioChannel) -> Int,
    ): Boolean = speaking.withLock {
        // A grant left over from the previous prompt must not satisfy this one.
        while (grants.tryReceive().isSuccess) Unit

        // The announcement this channel did not make at bring-up. Before focus,
        // because the car sizes its ducking from the streams it believes are
        // live, and because `render` reads the format `Start` selects.
        //
        // A link that died between bring-up and this prompt costs the prompt,
        // not the session -- the demultiplexer notices the same close and the
        // supervisor reconnects. `AudioChannelException` is let through for the
        // reason given at the end of this function: it means a sequencing bug
        // here, and silence would hide it.
        val announced = runCatching { ensureStarted(channel) }.exceptionOrNull()
        if (announced != null) {
            if (announced is AudioChannelException || announced is CancellationException) {
                throw announced
            }
            onStep(
                "could not start ${nameFor(channel.channelId)} for this prompt: " +
                    "${announced.message}"
            )
            return@withLock false
        }

        // Phone first, then car: if AudioManager refuses, the car is never asked,
        // so the radio is never ducked for a prompt that will not play.
        // PhoneAudioFocus.acquire sends the AAP request as part of this.
        if (!phoneFocus.requestForPrompt(mayDuck = true)) {
            onStep("the phone refused audio focus; the prompt is dropped rather than queued")
            // Started above for a line that will not play: left started, the car is back
            // in the prompt state this stop exists to end.
            stopAfterPrompt(channel)
            return@withLock false
        }

        try {
            awaitGrant(channel.stream)
            // Bounded, and it has to be. With openauto's `max_unacked = 1` the
            // stream is strictly lock-step, so a head unit that wedges its audio
            // stack without closing TCP stops acknowledging and both
            // `AudioChannel.sendPcm`'s credit wait and the drain below wait
            // forever -- while holding [speaking] and the car's ducked radio.
            // That would make every later prompt in the session hang too, and
            // the release in the finally below would never run.
            val sent = withTimeoutOrNull(timeoutMillis) {
                val buffers = render(channel)
                drainAcks(channel)
                buffers
            }
            if (sent == null) {
                onStep(
                    "the head unit stopped acknowledging within $timeoutMillis ms; " +
                        "abandoning the prompt and giving focus back"
                )
                false
            } else {
                buffersSent += sent
                promptsPlayed++
                true
            }
        } catch (unavailable: CarAudioSourceException) {
            // No engine, blank text, an unsupported sample format: the prompt is
            // lost, the session is not.
            onStep("could not render the prompt: ${unavailable.message}")
            false
        } finally {
            // Before focus goes back, so the car hears "the prompt ended" and then "the
            // music has the speakers", in the order Android Auto sends them.
            stopAfterPrompt(channel)
            // `PhoneAudioFocus` holds one slot, so the prompt's focus replaced any GAIN the
            // media pump held. Music that had the car before the prompt gets it back in one
            // step; a release first sent the car to its radio and back (the owner, 2026-11-10).
            // Otherwise the pump takes focus on its next playing buffer.
            // Not cancellable, for the reason the stop above is not: a line cut short by a
            // barge-in got here cancelled, and its first suspension left the car ducked.
            mediaFocusHeld = withContext(NonCancellable) {
                withTimeoutOrNull(RELEASE_MILLIS) { phoneFocus.endPrompt() }
            } ?: false
        }
        // AudioChannelException is deliberately not caught. It means this class
        // drove the channel out of sequence -- a bug here, not a car being
        // awkward -- and swallowing it would hide it behind silence.
    }

    /**
     * Waits briefly for the head unit to grant focus for [stream].
     *
     * Not waiting at all would be wrong: the car ducks its own source when it
     * grants, and a prompt that starts first has its opening word played over the
     * radio. Waiting indefinitely would be worse — nothing in the protocol
     * obliges a head unit to answer, and a silent voice assistant is a harder
     * failure to diagnose than an overlapping one — so after
     * [FOCUS_GRANT_TIMEOUT_MILLIS] the prompt plays anyway and says so.
     *
     * @return whether a grant covering [stream] actually arrived.
     */
    private suspend fun awaitGrant(stream: AudioStreamType): Boolean {
        val granted = withTimeoutOrNull(FOCUS_GRANT_TIMEOUT_MILLIS) {
            var state = grants.receive()
            // A GAIN_MEDIA_ONLY grant does not cover a guidance prompt; keep
            // waiting rather than treating any notification as a yes.
            while (!state.allows(stream)) state = grants.receive()
            state
        }
        if (granted == null) {
            onStep(
                "no focus grant for ${stream.name} within $FOCUS_GRANT_TIMEOUT_MILLIS ms; " +
                    "playing anyway -- the car may not duck its own source"
            )
            return false
        }
        return true
    }

    /**
     * Reads until nothing is outstanding, counting underflows on the way.
     *
     * [AudioChannel.awaitAllAcks] would do the waiting, but it discards every
     * event that is not an acknowledgement, and the underflow notification is the
     * one signal that would explain choppy playback in a car.
     *
     * Unbounded by itself; [withFocus] is where the bound lives, because the
     * budget that matters covers synthesis and streaming as well.
     */
    private suspend fun drainAcks(channel: AudioChannel) {
        while (channel.unacknowledgedMessages > 0) {
            when (channel.receiveEvent()) {
                is AudioChannel.Event.Underflow -> underflows++
                else -> Unit
            }
        }
    }

    // --- control channel ------------------------------------------------------

    /**
     * Hands a control-channel message to the focus bridge.
     *
     * Called by whoever reads the control channel — this class must not, see the
     * class KDoc. Everything that is not a focus notification is left alone, so
     * the caller can go on treating the message as its own.
     *
     * @return true when this was an audio focus notification and has been applied.
     */
    fun onControlMessage(message: AapMessage): Boolean {
        if (message.channelId != ChannelId.CONTROL.id) return false
        if (message.messageId != ControlMessageType.AUDIO_FOCUS_NOTIFICATION.id) return false

        val state = AudioFocus.parseNotification(message.payload)
        lastCarFocus = state
        lastCarFocusAtMs = SystemClock.elapsedRealtime()
        val volunteered = if (state.unsolicited) " (unsolicited)" else ""
        onStep("car audio focus: ${state.state.name}$volunteered")
        // Lets the car revoke Headway's phone-side focus too, so other apps are
        // not held quiet for audio the car will not play.
        phoneFocus.onCarFocus(state)
        // And the media pump's own flag, which is the other half of the fix that
        // made it shared. A prompt taking focus away was handled; the *car*
        // taking it away was not -- and that is the more ordinary event, since
        // a head unit revokes focus whenever its own source wants the speakers.
        // Left set, the pump goes on streaming PCM into a car playing its radio
        // and never asks for focus back, because its "do I hold it" test is this
        // flag.
        if (state.mustStop && mediaFocusHeld) {
            mediaFocusHeld = false
            onStep("media: the car took focus back, so music focus will be re-requested")
        }
        grants.trySend(state)
        return true
    }

    // --- helpers --------------------------------------------------------------

    private fun channelFor(stream: AudioStreamType): AudioChannel? =
        opened.firstOrNull { it.stream == stream }

    /** [channelFor], but tolerant of being called while bring-up is still running. */
    private suspend fun awaitChannel(stream: AudioStreamType): AudioChannel? {
        channelFor(stream)?.let { return it }
        withTimeoutOrNull(READY_TIMEOUT_MILLIS) { broughtUp.await() }
        return channelFor(stream)
    }

    companion object {

        /**
         * The live stream, so a screen-sharing grant can find it mid-session.
         *
         * Null between sessions. Media capture needs a `MediaProjection` and the
         * grant usually arrives *after* the session is up -- a link that comes
         * up on its own has no grant to travel with it -- so the service needs
         * somewhere to deliver one. See [adoptProjection].
         */
        private val live = java.util.concurrent.atomic.AtomicReference<CarAudioStream?>(null)

        /** The live stream, or null between sessions. */
        val current: CarAudioStream? get() = live.get()

        /**
         * How long to wait before trying a failed playback capture again.
         *
         * Long enough that a capture failing instantly cannot spin, short
         * enough that a transient refusal -- an audioserver restart is the
         * realistic one -- costs a few seconds of music rather than the drive.
         */
        const val MEDIA_RETRY_MILLIS: Long = 3_000

        /**
         * Consecutive failed capture starts before the grant is set aside.
         *
         * Not one: `AudioRecord` refusing once is ordinary. Not unbounded
         * either, because a grant that can never be captured from should stop
         * being retried and let the driver re-share.
         */
        const val MEDIA_CAPTURE_ATTEMPTS: Int = 3

        /**
         * A send this slow is audible, so it is worth a line of its own.
         *
         * 250 ms is roughly six buffers at the 40 ms this capture produces, and
         * well past anything the acknowledgement window absorbs normally.
         */
        private const val STALL_REPORT_MILLIS: Long = 250L

        /**
         * How long the end of a grant waits for the car to take what is queued.
         *
         * One queue depth of audio is two seconds, and a head unit keeping up
         * clears it in far less. Past this it is not keeping up, and holding on
         * to a second of stale music is worth less than getting the car's radio
         * back and the capture closed. See the drain in `pumpMediaWith`.
         */
        private const val DRAIN_TIMEOUT_MILLIS: Long = 2_000L

        /** Stalls reported individually before the summary count takes over. */
        private const val MAX_REPORTED_STALLS: Long = 12L

        /**
         * Empty reads that mean the capture is gone rather than merely quiet.
         *
         * `PhoneAudioCapture.read` blocks until the tap has a buffer, so an
         * empty read is already unusual; a hundred of them in a row is not a
         * quiet passage. Generous on purpose -- the cost of being wrong is one
         * unnecessary wait for a grant, and the cost of being trigger-happy is
         * dropping a capture that was about to produce.
         */
        private const val EMPTY_READS_BEFORE_GIVING_UP: Int = 100
        /**
         * Any non-zero value works; the head unit echoes it back in every
         * acknowledgement so the two sides can tell streams apart. The same id on
         * both channels is fine — it scopes to a channel, not to a session.
         */
        const val DEFAULT_SESSION_ID: Int = 1

        /**
         * How long to wait for a `Config`.
         *
         * Well inside the ~15 s the observed Chevrolet allows before it closes a
         * session with no video, so even both channels timing out cannot cost the
         * video deadline. Nothing in the references states a figure; this is a
         * budget, not a protocol constant.
         */
        const val SETUP_TIMEOUT_MILLIS: Long = 3_000L

        /** How long [speak] waits for bring-up before concluding there is no channel. */
        const val READY_TIMEOUT_MILLIS: Long = 10_000L

        /**
         * How long a whole prompt — synthesis, streaming and acknowledgement —
         * may take before it is abandoned.
         *
         * A budget, not a protocol constant. openauto answers `Setup` with
         * `max_unacked = 1` (`openauto/src/autoapp/Service/MediaSink/AudioMediaSinkService.cpp`
         * L167-L183), so the stream is lock-step and a single lost
         * acknowledgement stalls it. Fifteen seconds is far longer than any
         * prompt in this grammar and short enough that a wedged head unit gives
         * the radio back.
         */
        const val PROMPT_TIMEOUT_MILLIS: Long = 15_000L

        /**
         * How much of a streamed voice is gathered before the car hears it, at the line's start
         * and again after the link fell behind. The voice service makes a line far faster than
         * it is said, so this is the phone's link's slack: a stall shorter than it rides through.
         */
        const val STREAM_HEAD_START_SECONDS = 1.0

        /** The most a slow link can make a line wait: past this a pause is better than more silence. */
        const val STREAM_HEAD_START_MAX_SECONDS = 4.0

        /** The longest streamed line; each chunk still has [PROMPT_TIMEOUT_MILLIS] to be acknowledged. */
        const val STREAM_LINE_MILLIS: Long = 180_000L

        /** How long a prompt's end (the Stop, the focus hand-back) may wait on the link. */
        const val RELEASE_MILLIS: Long = 2_000L

        /**
         * How long to wait for a focus grant before speaking regardless. Short:
         * this is dead air in front of every spoken reply, and Phase 5's budget
         * is 2 s from end of speech to action.
         */
        const val FOCUS_GRANT_TIMEOUT_MILLIS: Long = 750L

        // Silence before Headway gives the car's audio back.
        //
        // Holding focus mutes the car's own radio, so it cannot be held
        // indefinitely — but releasing it on the first silent buffer would
        // hand the radio back and take it again across every track change, and
        // a head unit switching source twice a song is worse than a short
        // pause. Three seconds is longer than any gap between tracks and short
        // enough that a driver who stops the music gets their radio back before
        // they wonder why it is quiet.
        /**
         * How long the capture must be silent before the car gets its radio back.
         *
         * Ten seconds, not three. Releasing focus is audible at both ends --
         * the car switches source away and back -- so the window has to be
         * longer than any gap that is not really a stop. Three seconds is
         * inside the range of a podcast's pause, a long track gap and an app
         * reloading its decoder, and a driver reported the resulting dropouts.
         * Nothing is lost by waiting: silence is streamed to the car during the
         * window, which is what it was already doing between tracks.
         */
        const val MEDIA_IDLE_RELEASE_MILLIS: Long = 10_000L

        /**
         * Amplitude at or below which a 16-bit sample counts as silence.
         *
         * Not zero. A capture of "nothing playing" is not digitally silent --
         * resampling and mixing leave a least-significant-bit of noise -- and a
         * zero test would hold the car's audio focus for the whole drive.
         */
        private const val SILENCE_LEVEL: Int = 64

        /**
         * The streams Headway always drives, in bring-up order.
         *
         * Telephony is absent because it belongs on Bluetooth HFP, and that is
         * not a guess: a capture of real Android Auto against a 2021 Chevrolet
         * Infotainment 3 unit shows Gearhead holding `STATE_HFP_CONNECTED`
         * throughout while it explicitly tears down A2DP.
         *
         * Media is conditional rather than absent — see [streamsFor].
         */
        val DRIVEN_STREAMS: List<AudioStreamType> = listOf(
            AudioStreamType.AUDIO_STREAM_GUIDANCE,
            AudioStreamType.AUDIO_STREAM_SYSTEM_AUDIO,
        )

        /**
         * The streams to drive given a routing choice.
         *
         * Media comes first when it is driven at all. It is the stream with a
         * continuous producer behind it, so a head unit that only honours the
         * first `Start` it sees gets the one that matters, and a driver whose
         * session dies during bring-up loses a prompt rather than their music.
         */
        fun streamsFor(mediaOverAap: Boolean): List<AudioStreamType> =
            if (mediaOverAap) listOf(AudioStreamType.AUDIO_STREAM_MEDIA) + DRIVEN_STREAMS
            else DRIVEN_STREAMS

        /**
         * Finds the head unit's audio sinks by what they contain, not by channel
         * id.
         *
         * Same argument as `CarVideoStream.videoServiceOf`: the ids in
         * [ChannelId] are Headway's own convention, while the authority is the
         * `Service.id` the head unit put in its `ServiceDiscoveryResponse`.
         * Matching on the constant would work against the emulator, which
         * advertises six channels, and mis-address a car that advertises thirteen
         * and numbers them differently. What identifies an audio sink is that it
         * is a media sink offering PCM with an `AudioStreamType` and at least one
         * `AudioConfiguration` — the same four conditions
         * [AudioChannel.fromService] checks before it will build a channel.
         */
        fun audioSinksOf(profile: HeadUnitProfile): List<ServiceOuterClass.Service> =
            profile.services.filter {
                it.hasMediaSinkService() &&
                    it.mediaSinkService.availableType == MediaCodecType.MEDIA_CODEC_AUDIO_PCM &&
                    it.mediaSinkService.hasAudioType() &&
                    it.mediaSinkService.audioConfigsCount > 0
            }

        /**
         * Builds the stream for a profile, or null when the car offers no audio
         * sink at all.
         *
         * The channels are built with [AudioChannel]'s constructor rather than
         * its [AudioChannel.fromService] factory, which takes a whole
         * `FramedConnection`; a live session multiplexes thirteen channels over
         * one connection, so each channel gets a demultiplexer view instead. The
         * stream type and the sample formats are still taken from the same
         * `Service` message the factory reads them from, so none of the three is
         * a phone-side assumption.
         *
         * @param connectionFor per-channel view lookup. Called for the control
         *   channel too, for sending focus requests only — see the class KDoc.
         * @param context used for `AudioManager` and the text-to-speech engine.
         *   Only the application context is retained ([CarAudioSource] and
         *   [PhoneAudioFocus] both take one deliberately).
         */
        fun of(
            profile: HeadUnitProfile,
            connectionFor: (Int) -> MessageChannel,
            context: Context,
            mediaOverAap: Boolean = true,
            projection: MediaProjection? = null,
            onStep: (String) -> Unit = {},
        ): CarAudioStream? {
            val advertised = audioSinksOf(profile)

            // Advertised sinks Headway could not even consider, which used to
            // appear in no list at all. A head unit that offers a system-audio
            // sink with an empty audio_configs is indistinguishable in the log
            // from one that offers no system-audio sink -- and the two have
            // different fixes.
            val rejected = profile.services.filter {
                it.hasMediaSinkService() &&
                    it.mediaSinkService.availableType == MediaCodecType.MEDIA_CODEC_AUDIO_PCM &&
                    advertised.none { usable -> usable.id == it.id }
            }
            if (rejected.isNotEmpty()) {
                onStep(
                    "audio: ignoring " + rejected.joinToString { service ->
                        val sink = service.mediaSinkService
                        "channel ${service.id} (" +
                            (if (!sink.hasAudioType()) "no stream type" else sink.audioType.name) +
                            ", ${sink.audioConfigsCount} format(s))"
                    }
                )
            }

            if (advertised.isEmpty()) return null

            // Not `mediaOverAap && projection != null`. The channel has to be
            // opened before a grant exists, because the grant usually arrives
            // *after* the session -- a link that comes up on its own has none
            // travelling with it, and the driver answers the consent
            // notification later.
            //
            // Making the channel conditional on the grant is what made the fix
            // for that inert: with no media channel, `pumpMedia` returns at its
            // `channelFor(...) ?: return` and never reaches the code that waits
            // for a grant, so a driver who shared afterwards still got silence
            // for the whole drive. An open channel with nothing on it costs the
            // car nothing; it is fed only when there is something to feed it.
            val wanted = streamsFor(mediaOverAap)
            if (mediaOverAap && projection == null) {
                onStep(
                    "audio: media is routed over AAP and there is no screen capture grant yet, " +
                        "so the media channel opens empty and starts carrying music the moment " +
                        "screen sharing is allowed"
                )
            }
            val driven = wanted.mapNotNull { stream ->
                advertised.firstOrNull { it.mediaSinkService.audioType == stream }
            }
            // Compared by id rather than by message equality: two services can
            // carry byte-identical sink descriptions and still be different
            // channels.
            val untouched = advertised.filterNot { candidate ->
                driven.any { it.id == candidate.id }
            }

            val focus = AudioFocus(connectionFor(ChannelId.CONTROL.id), onStep = onStep)
            val channels = driven.map { service ->
                AudioChannel(
                    connection = connectionFor(service.id),
                    channelId = service.id,
                    stream = service.mediaSinkService.audioType,
                    advertisedFormats = service.mediaSinkService.audioConfigsList
                        .map(PcmFormat::from),
                    // The one gate that decides whether music can be sent at
                    // all: AudioChannel.transmits is false for MEDIA on the
                    // A2DP route, and requireTransmitting throws out of
                    // sendSetup. Passed explicitly so the decision is visible
                    // where it is made.
                    route = if (service.mediaSinkService.audioType ==
                        AudioStreamType.AUDIO_STREAM_MEDIA
                    ) {
                        MediaAudioRoute.AAP_MEDIA_CHANNEL
                    } else {
                        MediaAudioRoute.BLUETOOTH_A2DP
                    },
                    onStep = onStep,
                )
            }

            return CarAudioStream(
                sinks = channels,
                untouched = untouched,
                focus = focus,
                phoneFocus = PhoneAudioFocus(context, focus, onStep),
                source = CarAudioSource(context, onStep = onStep),
                projection = projection,
                audioManager = context.getSystemService(AudioManager::class.java),
                appContext = context.applicationContext,
                names = profile.services.associate { it.id to nameOf(it) },
                onStep = onStep,
            )
        }

        /**
         * A channel's name taken from what the head unit advertised.
         *
         * [ChannelId.describe] is Headway's own numbering table, and a real car
         * assigns its own ids — which is how a guidance sink on this Chevrolet
         * came to be logged as `MEDIA_SINK_VIDEO`. The advertisement is the
         * authority.
         */
        private fun nameOf(service: ServiceOuterClass.Service): String {
            val sink = service.mediaSinkService
            if (!service.hasMediaSinkService() || !sink.hasAudioType()) {
                return "${ChannelId.describe(service.id)}?id=${service.id}"
            }
            return sink.audioType.name.removePrefix("AUDIO_STREAM_") + "(${service.id})"
        }
    }
}

/**
 * Whether the first [length] bytes of [buffer] are 16-bit PCM silence.
 *
 * Top-level and pure so it can be tested without an audio device: everything
 * else on the media path needs a live capture, a car and a link.
 *
 * Little-endian, which is the only format the media sink negotiates -- the
 * target vehicle reports `MEDIA(5) ready: 48000 Hz 16-bit stereo` -- and what
 * `PhoneAudioCapture` is configured to produce. A trailing odd byte cannot form
 * a sample and is ignored rather than guessed at.
 *
 * @param level amplitude at or below which a sample counts as silent. Not zero:
 *   a capture of "nothing playing" carries a least-significant-bit of noise
 *   from resampling and mixing, and an exact-zero test would hold the car's
 *   audio focus for a whole drive.
 */
internal fun isPcm16Silence(buffer: ByteArray, length: Int, level: Int): Boolean {
    val usable = minOf(length, buffer.size)
    var index = 0
    while (index + 1 < usable) {
        val sample = (buffer[index].toInt() and 0xff) or (buffer[index + 1].toInt() shl 8)
        if (sample > level || sample < -level) return false
        index += 2
    }
    return true
}

/**
 * Gathers a streamed voice into chunks of at least [size] bytes of whole 16-bit
 * samples. An HTTP read can end mid-sample; the odd byte waits for the next piece
 * rather than shifting every sample after it. Pure, for its test.
 */
class PcmChunker(size: Int, private val max: Int = size) {
    /**
     * The head start now: [next] doubles it, up to [max], each time the line runs dry, so a
     * link that has shown it is slow costs one longer wait instead of a cut every second (the
     * owner's drive, 2026-11-11: "her speech kept being stuttery").
     */
    var size: Int = size
        private set

    private val pending = java.io.ByteArrayOutputStream()
    private var started = false

    /** How often [next] found the queue empty mid-line: each one is a pause the car heard. */
    var dry = 0
        private set

    /**
     * The next stretch of [pieces] to send, null at the line's end. The car runs lock-step on
     * its acknowledgements, a few 20 ms buffers ahead at most, so while it plays the last
     * stretch whatever has arrived goes at once: waiting for a full chunk over a link near real
     * time left it with nothing, a cut every half second (T-0917). Only an empty queue -- the
     * line's start, or the link falling behind -- waits, for [size] bytes or the end. Does not
     * cover a link slower than real time for longer than [size] lasts: that still pauses, once
     * per head start rather than once per chunk.
     */
    suspend fun next(pieces: kotlinx.coroutines.channels.ReceiveChannel<ByteArray>): ByteArray? {
        val midLine = started
        if (midLine) {
            while (true) add(pieces.tryReceive().getOrNull() ?: break)?.let { return it }
            if (pending.size() >= 2) return take()
        }
        started = true
        val before = size
        if (midLine) size = minOf(size * 2, max)
        // Counted only when audio follows: the line's close landing just after its last piece
        // is its end, not a pause.
        // A block, not `if (…) dry++; return it` on one line: detekt reads that `;` as an empty else.
        for (piece in pieces) {
            val chunk = add(piece) ?: continue
            if (midLine) dry++
            return chunk
        }
        // The line ended with nothing more: that was no stall, so it does not grow the head start.
        return flush()?.also { if (midLine) dry++ } ?: run {
            size = before
            null
        }
    }

    /** A chunk once [size] bytes have gathered, else null. */
    fun add(piece: ByteArray): ByteArray? {
        pending.write(piece)
        return if (pending.size() >= size) take() else null
    }

    /** What is left at the end of the line, whole samples only; null when nothing is. */
    fun flush(): ByteArray? = take()

    private fun take(): ByteArray? {
        val bytes = pending.toByteArray()
        val even = bytes.size and 1.inv()
        if (even == 0) return null
        pending.reset()
        pending.write(bytes, even, bytes.size - even)
        return bytes.copyOf(even)
    }
}

/**
 * The head start the session's next streamed line gathers, from the one this line started with
 * ([currentSeconds]), what its chunker grew to ([grownSeconds]) and how often it ran [dry]. A dry
 * line keeps what it grew to, up to the cap; a line that did not moves the head start halfway back
 * to the default, never below it, so one slow moment (a phone pause) stops delaying every later
 * reply within a few clean lines, while a link that is slow again grows it at once. Pure, for its
 * test. Does not tell a slow link from a slow moment: the halving is a guess to tune from a drive.
 */
fun nextStreamHeadStart(currentSeconds: Double, grownSeconds: Double, dry: Int): Double {
    val default = CarAudioStream.STREAM_HEAD_START_SECONDS
    if (dry > 0) return grownSeconds.coerceIn(default, CarAudioStream.STREAM_HEAD_START_MAX_SECONDS)
    return maxOf(default, default + (currentSeconds - default) / 2)
}

/** How long one listen may keep the music from the car: its ten-second cap and the close. */
const val LISTEN_HUSH_MAX_MILLIS: Long = 15_000L

/**
 * Whether the relayed music is withheld: while she listens, and never past
 * [LISTEN_HUSH_MAX_MILLIS], so a phase left on listening costs the drive seconds of music
 * rather than all of it. Pure, for its test.
 */
fun musicHushed(listening: Boolean, forMillis: Long): Boolean = listening && forMillis < LISTEN_HUSH_MAX_MILLIS

/**
 * Whether the car is asked for media focus again: a listen has just ended ([wasListening],
 * no longer [listening]) while music is being relayed and no line is being said. A false wake
 * opened the car's microphone, the car switched to voice mode, and nothing asked it back
 * (the owner, 2026-11-12, build 1840). Does not cover a line being said: it hands focus back
 * itself. Pure, for its test.
 */
fun reassertMediaAfterListen(wasListening: Boolean, listening: Boolean, relaying: Boolean, prompt: Boolean): Boolean =
    wasListening && !listening && relaying && !prompt

/**
 * Whether a listen's focus re-ask is still owed after this pass: while she listens, and on
 * through her thinking, so the ask waits for the turn's end. A spoken reply ([prompt]) hands
 * focus back itself, so it cancels the debt: without this every answered turn sent the car an
 * extra GAIN just before her reply asked for its own focus (review of T-1179).
 */
fun listenOwed(was: Boolean, phase: Phase, prompt: Boolean): Boolean =
    phase == Phase.LISTENING || (was && phase == Phase.THINKING && !prompt)

/**
 * Whether a voice turn is under way: she is listening, thinking or speaking, or a line is
 * being said. Not the error a failed listen leaves on the screen until the next press:
 * counted as a turn, it kept the car's media focus for [TURN_HOLD_MAX_MILLIS] after a
 * listen that reached nobody. Pure, for its test.
 */
fun inVoiceTurn(phase: Phase, prompt: Boolean): Boolean =
    prompt || phase == Phase.LISTENING || phase == Phase.THINKING || phase == Phase.SPEAKING

/** The longest a voice turn keeps the car's media focus with nothing playing. */
const val TURN_HOLD_MAX_MILLIS = 90_000L

/** How long media focus is kept for a new screen-sharing grant: a lock and a re-share, with room. */
const val GRANT_GAP_HOLD_MILLIS = 20_000L

/**
 * Whether media focus outlives a capture that just ended: only when the reader came back by
 * itself ([endedByItself]; not a dead link or a cancel) and the grant it read through is gone
 * (not [grantStillLive]: a live grant is retried at once and its failure is not a lock).
 */
fun grantGapHoldsFocus(endedByItself: Boolean, grantStillLive: Boolean): Boolean = endedByItself && !grantStillLive

/**
 * Whether a voice turn (she listens, thinks or speaks) keeps the car's media focus although
 * the music is silent. Bounded, so a phase that never returned to idle cannot hold a silent
 * car for the rest of the drive.
 *
 * Does not cover silence outside a turn: that is the idle timer's, as before.
 */
fun turnHoldsFocus(inTurn: Boolean, forMillis: Long): Boolean = inTurn && forMillis < TURN_HOLD_MAX_MILLIS

/** What music is multiplied by while she speaks: about 12 dB down. */
const val DUCK_GAIN = 0.25f

/**
 * Lowers relayed 16-bit PCM while a prompt plays and restores it after, moving the gain in a
 * straight line across one buffer each way so the car never hears a step.
 *
 * Does not cover the car's own ducking: a head unit that also lowers MEDIA makes the music
 * quieter still for the length of the prompt. One instance per stream, one thread.
 */
class MusicDucker {
    private var gain = 1f

    fun apply(pcm: ByteArray, length: Int, duck: Boolean) {
        val target = if (duck) DUCK_GAIN else 1f
        if (gain == 1f && target == 1f) return
        val samples = length / 2
        val step = if (samples > 1) (target - gain) / (samples - 1) else 0f
        var g = gain
        for (i in 0 until samples) {
            val v = ((pcm[2 * i + 1].toInt() shl 8) or (pcm[2 * i].toInt() and 0xff)).toShort()
            val out = (v * g).toInt()
            pcm[2 * i] = out.toByte()
            pcm[2 * i + 1] = (out shr 8).toByte()
            g += step
        }
        gain = target
    }
}

/**
 * Mono 16-bit speech from one rate to another as a stream: feed it the pieces of a line in
 * order and it answers as if it had been given the whole. On the way down it low-passes
 * first (a windowed sinc below the new ceiling), so 24 kHz speech on a 16 kHz channel loses
 * what the channel cannot carry instead of having it folded back as noise; the position
 * between samples is kept as a whole-number fraction, so no piece boundary repeats or drops
 * a sample. [CarAudioSource]'s resampler does neither, which is fine for one whole prompt
 * and was a seam per network piece for her streamed voice (the owner, 2026-11-11: "messed
 * up and choppy").
 *
 * Does not cover: more than one channel, the last input sample of a line (it waits for a
 * neighbour that never comes: 1/24000 s), or the filter's first [TAPS] samples, which ramp
 * in from silence.
 */
class PcmStreamResampler(private val fromHz: Int, private val toHz: Int) {
    private val taps: DoubleArray = if (toHz < fromHz) lowPass(CUTOFF * toHz / fromHz) else doubleArrayOf(1.0)
    private val history = DoubleArray(taps.size - 1)
    private var odd: Byte? = null
    private var previous = 0.0

    /** The next output's position past [previous], in units of 1/[toHz] of an input sample. */
    private var position = 0L
    private var primed = false

    fun feed(piece: ByteArray): ByteArray {
        val bytes = odd?.let { byteArrayOf(it) + piece } ?: piece
        odd = if (bytes.size % 2 == 1) bytes[bytes.size - 1] else null
        val frames = bytes.size / 2
        if (frames == 0) return ByteArray(0)
        if (fromHz == toHz) return bytes.copyOf(frames * 2)

        val input = DoubleArray(history.size + frames)
        history.copyInto(input)
        for (i in 0 until frames) {
            input[history.size + i] = ((bytes[2 * i + 1].toInt() shl 8) or (bytes[2 * i].toInt() and 0xff)).toDouble()
        }
        val clean = DoubleArray(frames) { n ->
            var sum = 0.0
            for (k in taps.indices) sum += taps[k] * input[history.size + n - k]
            sum
        }
        input.copyInto(history, 0, frames, input.size)

        // Index -1 is the last sample of the piece before; the first piece has none, and
        // starts on its own first sample.
        if (!primed) {
            primed = true
            position = toHz.toLong()
        }
        val out = java.io.ByteArrayOutputStream(frames * toHz / fromHz * 2 + 4)
        val unit = toHz.toLong()
        while (position < frames * unit) {
            val lower = (position / unit).toInt() - 1
            val a = if (lower < 0) previous else clean[lower]
            val value = a + (clean[lower + 1] - a) * (position % unit) / unit.toDouble()
            val sample = Math.round(value).coerceIn(Short.MIN_VALUE.toLong(), Short.MAX_VALUE.toLong()).toInt()
            out.write(sample)
            out.write(sample shr 8)
            position += fromHz
        }
        position -= frames * unit
        previous = clean[frames - 1]
        return out.toByteArray()
    }

    private companion object {
        /** Odd, so the filter's delay is a whole number of samples: 15, 0.6 ms at 24 kHz. */
        const val TAPS = 31

        /** The pass band ends at 0.45 of the new rate: 7.2 kHz for the car's 16 kHz. */
        const val CUTOFF = 0.45

        /** A Hamming-windowed sinc with [cutoff] in cycles per input sample, summing to one. */
        fun lowPass(cutoff: Double): DoubleArray {
            val middle = (TAPS - 1) / 2
            val h = DoubleArray(TAPS) { k ->
                val x = 2 * Math.PI * cutoff * (k - middle)
                val sinc = if (k == middle) 2 * cutoff else Math.sin(x) / (Math.PI * (k - middle))
                sinc * (0.54 - 0.46 * Math.cos(2 * Math.PI * k / (TAPS - 1)))
            }
            val sum = h.sum()
            return DoubleArray(TAPS) { h[it] / sum }
        }
    }
}
