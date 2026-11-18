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

package ai.aura.app.car.service

import ai.aura.app.car.voice.CarSpeaker
import ai.aura.app.car.voice.CarTurn
import ai.aura.app.car.voice.HermesCarJarvis
import ai.aura.app.car.voice.JarvisVoiceLines
import ai.aura.app.car.voice.JarvisTranscriber
import ai.aura.app.car.link.CarPokeListener
import ai.aura.app.car.setup.CarGates
import ai.aura.app.car.setup.CarProfiles
import ai.aura.app.car.setup.Gate
import ai.aura.app.ui.Notify
import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisThemes
import aap_protobuf.aaw.StatusOuterClass.Status
import aap_protobuf.service.media.shared.message.MediaCodecTypeOuterClass.MediaCodecType
import aap_protobuf.service.wifiprojection.message.AccessPointTypeOuterClass.AccessPointType
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import ai.aura.app.BuildConfig
import ai.aura.app.R
import ai.aura.app.car.audio.CarAudioStream
import ai.aura.app.car.link.CarPresenceReceiver
import ai.aura.app.car.dash.CarShell
import ai.aura.app.car.dash.CarSurface
import ai.aura.app.car.input.CarInputStream
import ai.aura.app.car.input.HeadwayAccessibilityService
import ai.aura.app.car.link.BluetoothCarLink
import ai.aura.app.car.link.CarCompanion
import ai.aura.app.car.link.CarWifiException
import ai.aura.app.car.link.CarWifiNetwork
import ai.aura.app.car.link.PhoneCertificateStore
import ai.aura.app.car.log.SessionLog
import ai.aura.app.car.quirks.HeadUnitIdentity
import ai.aura.app.car.media.CarMediaStatusStream
import ai.aura.app.car.quirks.HeadUnitQuirks
import ai.aura.app.car.quirks.QuirkStore
import ai.aura.app.car.sensor.CarSensorStream
import ai.aura.app.car.HeadwaySettings
import ai.aura.app.car.video.AppPaneHost
import ai.aura.app.car.video.CarAppDisplay
import ai.aura.app.car.video.CarVideoStream
import ai.aura.app.car.video.PhoneRotation
import ai.aura.app.car.voice.CarVoiceStream
import ai.aura.app.car.voice.PhoneMediaControl
import dev.headway.protocol.control.ControlKeepalive
import dev.headway.protocol.framing.ChannelId
import dev.headway.protocol.framing.MessageFragmenter
import dev.headway.protocol.io.ChannelDemultiplexer
import dev.headway.protocol.io.FramedConnection
import dev.headway.protocol.io.MessageChannel
import dev.headway.protocol.session.AapSession
import dev.headway.protocol.session.AuthenticationRejectedException
import dev.headway.protocol.session.HeadUnitProfile
import dev.headway.protocol.session.PhoneIdentity
import dev.headway.transport.LinkState
import dev.headway.transport.SessionSupervisor
import dev.headway.transport.TcpTransport
import dev.headway.transport.tls.AapTls
import dev.headway.transport.tls.TlsSession
import dev.headway.transport.wireless.CarEndpoint
import dev.headway.video.EncoderConfiguration
import dev.headway.video.ScreenEncoder
import dev.headway.voice.VoiceCommand
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import dev.headway.protocol.control.ControlMessageType

/**
 * Marks a failure that happened while joining the car's access point.
 *
 * The distinction earns its keep in the retry delay. A join failure is the one
 * failure class that may have left Android's own approval activity on screen in
 * an error state — releasing the request broadcasts `onAbort` to it, and it
 * shows "the application has cancelled the request" until it is dismissed or
 * finishes. Coming straight back 500 ms later with a fresh request does not get
 * a fresh prompt; it gets the stale one. Everything else — Bluetooth, TCP, TLS
 * — should still retry promptly, so this cannot just be a longer global backoff.
 *
 * Carries the cause's message verbatim so the notification and the UI keep
 * saying what actually went wrong.
 */
class CarNetworkJoinException(cause: Throwable) :
    RuntimeException(cause.message ?: cause::class.simpleName ?: "join failed", cause)

/**
 * Holds the car link for as long as the user wants to be connected.
 *
 * Everything with a lifetime longer than an activity lives here: the Bluetooth
 * handshake, the bound Wi-Fi network, the TCP socket, the AAP session, and the
 * retry loop that rebuilds all four when the car goes away.
 *
 * ## Why this has to be a foreground service, and how it survives the lock screen
 *
 * Three separate platform behaviours make a background service unworkable, and
 * a foreground one sufficient:
 *
 * 1. **The notification is not optional.** A service started with
 *    `startForegroundService` that has not posted its notification within about
 *    five seconds is killed with `ForegroundServiceDidNotStartInTimeException`.
 *    [startForegroundNow] is therefore the first statement of
 *    [onStartCommand], before any device or permission is even looked at — a
 *    connection failure must produce a notification saying so, not a dead
 *    process.
 * 2. **`WifiNetworkSpecifier` requests are only honoured for a foreground app
 *    or a foreground service.** AOSP's `WifiNetworkFactory.acceptRequest` gates
 *    them on `isRequestFromForegroundAppOrService`, which passes at importance
 *    `<= IMPORTANCE_FOREGROUND_SERVICE` (125) — exactly what a running
 *    `FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE` service supplies. Without the
 *    foreground service, an activity that goes away drops the process to
 *    `IMPORTANCE_VISIBLE` (200) or worse and the request is refused outright.
 *
 *    Note the check is made **once**, when the request arrives
 *    (`WifiNetworkFactory.java` L813-L818). There is no UID-importance listener
 *    and no re-check, so the platform does not tear an established car network
 *    down when Headway is backgrounded — an earlier version of this comment
 *    claimed it did. Past the moment of the request, the foreground service
 *    earns its keep through point 3 rather than point 2.
 * 3. **Process state.** A foreground service keeps the process out of the cached
 *    and frozen states and exempt from Doze's network restrictions, so the
 *    socket keeps being read while the screen is off.
 *
 * The manifest declares `mediaProjection|connectedDevice`, but only
 * `connectedDevice` is passed to `startForeground` here. Claiming the
 * `mediaProjection` type without holding a live projection token throws
 * `SecurityException` on Android 14+; the video path re-enters the foreground
 * with both types once the user has granted a projection.
 *
 * ## What it does not hold
 *
 * No `WifiLock` and no `WakeLock`. `WifiLock.acquire` is enforced against
 * `WAKE_LOCK`, which the manifest does not declare, so acquiring one would throw
 * rather than help. If a real car shows the 5 GHz link powering down with the
 * screen off, adding that permission and a `WIFI_MODE_FULL_LOW_LATENCY` lock is
 * the first thing to try.
 */
open class CarLinkService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var linkJob: Job? = null

    /** Remembered across a `START_REDELIVER_INTENT` restart. */
    @Volatile
    private var carAddress: String? = null

    /** When the current drive's first car session began (0 until one has); a reconnect keeps it. */
    @Volatile
    private var driveStartedMs = 0L

    /** The voice notice, once a drive whatever the number of links (JarvisVoiceLines). */
    private val noticeSaid = java.util.concurrent.atomic.AtomicBoolean(false)

    /** When the house last answered the car (a status call or a rendered line); 0 for never. */
    @Volatile private var houseSeenAtMs = 0L

    /**
     * The car's Wi-Fi network, held across reconnect attempts rather than per
     * attempt.
     *
     * This is deliberate and it is the difference between a car that connects
     * once and a car that never connects. `WifiNetworkSpecifier` requests are
     * approved by the user through a system sheet, and AOSP banks that approval
     * per app *and access point* — `mUserApprovedAccessPointMap` in
     * `WifiNetworkFactory` — so the second request for the same SSID connects
     * with no sheet at all. But the approval is only stored once a connection
     * *succeeds*, and the request is torn down the moment its callback is
     * unregistered.
     *
     * Scoping the network to one attempt therefore meant every retry cycle threw
     * away an association that was working, put the sheet back in front of the
     * user, and never banked anything. Holding it here means the sheet is tapped
     * once, ever, and a failure further up the stack — Bluetooth, TCP, TLS —
     * retries against a phone that is still on the car's access point.
     */
    @Volatile
    private var carWifi: CarWifiNetwork? = null

    /**
     * How many joins have failed. Diagnostics only; it no longer steers anything.
     *
     * ## What this counter used to do, and why that was the bug
     *
     * It alternated the match strategy: `pinBssid = quirks.pinBssid ?:
     * (failedJoins % 2 == 0)`, so an even count pinned the BSSID the head unit
     * announced and an odd count matched on the SSID alone. The intent was to
     * stop the project committing to a guess it had already been burned by
     * twice.
     *
     * It could not work, for two independent reasons, and the drive on
     * 2026-08-14 shows both:
     *
     * 1. **It only counted real failures.** The increment sat behind
     *    `if (e !is CancellationException)`, and every failure in that log is a
     *    cancellation, because the user gives up and presses Disconnect. The
     *    count stayed at zero.
     * 2. **It does not survive the service.** Disconnect stops the service, so
     *    the next Connect starts from zero regardless.
     *
     * Zero means even means pinned, so every attempt in that log pinned — the
     * log says `requesting myChevrolet0000 (ce:44:26:aa:bb:cc)` five times out
     * of five — and never once tried the other strategy it existed to try.
     *
     * ## Why the alternation is gone rather than fixed
     *
     * Because reading AOSP settles the question the alternation existed to
     * hedge. `WifiNetworkFactory.doesScanResultMatchWifiNetworkSpecifier`
     * (L1662-L1677) requires the scan result to match the SSID pattern **and**
     * the BSSID pattern. An SSID-only request carries an all-zero BSSID mask,
     * which matches every BSSID — so **any access point a pinned request can
     * match, an SSID-only request also matches, and more besides.** Pinning
     * cannot find a car that SSID-only misses; it can only miss one SSID-only
     * would have found, which is exactly what a dual-radio head unit does.
     *
     * The single successful pinned join on record is fully explained by the
     * access point being on `ce:44:26:aa:bb:cc` that day; the platform's own
     * scan has logged this car on `ce:22:26:aa:bb:cc` as well. So SSID-only is
     * now unconditional, `CarWifiNetwork.specFor` already defaults to it, and
     * the quirk file can still force pinning for a genuine two-cars-one-SSID
     * case.
     */
    @Volatile
    private var failedJoins: Int = 0

    /**
     * How many times a head unit has rejected authentication this run.
     *
     * Doubles as the index into [AapTls.bundledPhoneCredentials]. The phone-role
     * certificate expired in 2022 and cannot be reissued, but two *unexpired*
     * certificates signed by the same Google Automotive Link CA ship with the
     * references, issued for the head-unit role. Whether a car accepts one from
     * the phone depends on whether it checks the chain and dates only, or the
     * subject as well — unknowable from the references and cheap to find out.
     *
     * So each authentication rejection advances to the next candidate and the
     * supervisor reconnects. Only rejections advance it: a Wi-Fi or TCP failure
     * says nothing about the certificate, and burning candidates on those would
     * mean the car never gets offered the same one twice in a row.
     */
    @Volatile
    private var rejectedCertificates: Int = 0

    /**
     * Addressing failures this run. See [MAX_ADDRESSING_ATTEMPTS].
     *
     * Per run rather than per attempt, and never reset on success: the point is
     * to bound how many of the head unit's addresses one Connect press can
     * consume, and a session that came up and then dropped has not earned a
     * fresh budget.
     */
    @Volatile
    private var addressingFailures: Int = 0

    /**
     * The screen-capture grant, held for the life of the service.
     *
     * Not per session: consent is a user gesture, and asking again on every
     * reconnect would put a dialog in front of someone who is driving.
     * `ScreenEncoder.stop()` deliberately leaves the projection alone for the
     * same reason.
     */
    @Volatile
    private var projection: android.media.projection.MediaProjection? = null

    private val notificationManager: NotificationManager?
        get() = getSystemService(NotificationManager::class.java)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        warnIfAnotherHostHoldsTheRenderer()
        watchBluetoothProfiles()
        listenForTheCar()
    }

    /**
     * Answer a car that connects to the phone first (CarPokeListener). Only a car this
     * phone has completed a handshake with is answered, so nothing else that speaks the
     * Android Auto service record can open a session by knocking.
     */
    private fun listenForTheCar() {
        if (!HeadwaySettings.answerCarPoke(this)) return
        val adapter = BluetoothCarLink.adapterOf(this) ?: return
        if (checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
        carPoke = CarPokeListener(
            adapter,
            isKnownCar = { address -> address == HeadwaySettings.carAddress(this) || CarProfiles.of(this, address) != null },
            slot = pokes,
            onStep = ::step,
        ).also { it.start() }
    }

    private var carPoke: CarPokeListener? = null

    /** The car's current turn with Jarvis, while a session is up. */
    @Volatile
    private var carTurn: CarTurn? = null
    /** Reads new messages aloud during the drive; stopped with the session. */
    private var carMessages: ai.aura.app.car.voice.CarMessageAnnouncer? = null

    /** Jarvis's voice on the car's guidance channel, which ducks the music. */
    private suspend fun playGuidance(audio: CarAudioStream, pcm: ByteArray, rateHz: Int): Boolean =
        audio.play(
            dev.headway.audio.PcmBuffer(dev.headway.protocol.channel.PcmFormat(rateHz, 16, 1), pcm),
            aap_protobuf.service.media.sink.message.AudioStreamTypeOuterClass.AudioStreamType.AUDIO_STREAM_GUIDANCE,
        )

    /**
     * The car's fixed lines in Jarvis's voice for when the server is out (brief §2.3), one per
     * session. The outage notice is said once a drive (JarvisVoiceLines).
     */
    private fun voiceLines(audio: CarAudioStream, voice: () -> String) = JarvisVoiceLines(
        dir = java.io.File(filesDir, "jarvis-voice"),
        voice = voice,
        play = { pcm, rate -> playGuidance(audio, pcm, rate) },
        engine = { text -> audio.speak(text) },
        fallback = { ActiveTheme.theme.offlineFallback },
        announced = noticeSaid,
        houseUp = { houseSeenAtMs != 0L && android.os.SystemClock.elapsedRealtime() - houseSeenAtMs < HOUSE_SEEN_MS },
        bundledReady = { ai.aura.app.assist.BundledVoice.ready(applicationContext) },
        bundled = { text ->
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
                ai.aura.app.assist.BundledVoice.open(applicationContext)?.render(text)
            }?.let { (rate, pcm) -> playGuidance(audio, pcm, rate) } ?: false
        },
        ownRealtime = { ai.aura.app.assist.BundledVoice.realtime(applicationContext, HER_VOICE) },
        own = { text ->
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
                ai.aura.app.assist.BundledVoice.open(applicationContext, HER_VOICE)?.render(text)
            }?.let { (rate, pcm) -> playGuidance(audio, pcm, rate) } ?: false
        },
    )

    /**
     * One sentence of a reply in her voice on the phone, or null when the house is to say it
     * (`hud.car_voice`, CarTurn.usesPhone). The log line is how a drive shows which voice spoke
     * and whether the phone kept up.
     */
    private suspend fun ownSentence(text: String): Pair<Int, ByteArray>? {
        val voice = ai.aura.app.assist.BundledVoice
        val mode = CarShell.carVoice
        // The voice on the phone is AURA's: another pack's replies stay the house's, in its own voice.
        if (ActiveTheme.theme.offlineFallback != ai.aura.app.assist.OfflineVoice.CLIPS_ONLY) return null
        if (!CarTurn.usesPhone(mode, voice.ready(applicationContext, HER_VOICE), voice.realtime(applicationContext, HER_VOICE))) return null
        val asked = android.os.SystemClock.elapsedRealtime()
        val made = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) { voice.open(applicationContext, HER_VOICE)?.render(text) }
        val took = android.os.SystemClock.elapsedRealtime() - asked
        step(
            if (made == null) "the phone's voice could not make a sentence ($took ms, car voice $mode): the house says the rest"
            else "a reply's sentence from the phone's voice: $took ms for ${made.second.size / 2 * 1000L / made.first} ms of audio (car voice $mode)",
        )
        return made
    }

    /** The car's speakers for Jarvis; anything the server could not voice goes through [lines]. */
    private fun carSpeaker(audio: CarAudioStream, lines: JarvisVoiceLines): CarSpeaker = object : CarSpeaker {
        override suspend fun play(pcm: ByteArray, rateHz: Int): Boolean =
            playGuidance(audio, pcm, rateHz)

        override suspend fun stream(rateHz: Int, pieces: kotlinx.coroutines.channels.ReceiveChannel<ByteArray>): Boolean =
            audio.playStream(rateHz, pieces)

        override suspend fun say(text: String) {
            lines.say(text)
        }
    }

    /**
     * Renders the car's fixed lines in Jarvis's current voice, one at a time and only those
     * missing or a day old, so a drive's first question does not queue behind a dozen renders.
     */
    private suspend fun warmVoiceLines(jarvis: HermesCarJarvis, lines: JarvisVoiceLines) {
        val stale = JarvisVoiceLines.FIXED.filter { lines.stale(it, VOICE_LINE_MAX_AGE_MS) }
        var rendered = 0
        for (text in stale) {
            var rate = 24_000
            val pcm = java.io.ByteArrayOutputStream()
            val voice = jarvis.voice()
            val made = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                jarvis.speak(text, onStart = { rate = it }, onPiece = { pcm.write(it) })
            }
            // A switch mid-render: this clip is the old voice's and would be kept under the new one's key.
            if (jarvis.voice() != voice) break
            if (!made || pcm.size() == 0 || !lines.remember(text, rate, pcm.toByteArray())) break
            rendered++
        }
        if (stale.isNotEmpty()) step("voice lines: $rendered of ${stale.size} stale rendered in ${ActiveTheme.name}'s voice")
    }

    /**
     * An approval asked for during a car turn: said aloud and answered by voice, with a
     * small card on the car screen as the other way to answer (the owner, 2026-09-25: "I
     * can't approve over voice ... the approve shouldn't take up the whole screen, only a
     * tiny notification, or verbally approve, which it should ask verbally to approve and
     * what its doing"). The two-button sheet remains only where there is no web display.
     * No car screen and no voice is a no, as CarTurn's timeout is.
     */
    private suspend fun approveOnTheCar(approval: ai.aura.app.assist.hermes.HudState.Approval): Boolean =
        askOnTheCar(
            title = "${ActiveTheme.name} wants to ${approval.tool.replace('_', ' ')}",
            detail = approval.params.ifBlank { approval.reason }.take(APPROVAL_DETAIL_CHARS),
            spoken = "${approvalWords(approval)} Shall I go ahead?",
        )

    /**
     * One yes-or-no on the car: the small card, the sheet where there is no web display,
     * and the question aloud with the next thing heard as the answer. Also the car's door
     * for the phone's own consent prompts ([ApprovalBridge.carRoute]): a text or a call
     * Jarvis was asked for from the car needs no unlocked phone.
     */
    private suspend fun askOnTheCar(title: String, detail: String, spoken: String): Boolean =
        kotlinx.coroutines.suspendCancellableCoroutine { waiting ->
            val main = android.os.Handler(android.os.Looper.getMainLooper())
            val id = System.nanoTime().toString()
            val done = java.util.concurrent.atomic.AtomicBoolean(false)
            var tries = 0
            fun answer(ok: Boolean) {
                if (!done.compareAndSet(false, true)) return
                step("car ask answered ${if (ok) "yes" else "no"}: $title")
                voiceAnswer = null
                main.post { CarShell.active()?.clearHudAsk() }
                if (waiting.isActive) waiting.resumeWith(Result.success(ok))
            }
            // The next thing heard on the car's microphone is the answer, not a new question.
            voiceAnswer = { heard ->
                when (yesOrNo(heard)) {
                    true -> answer(true)
                    false -> answer(false)
                    null -> if (++tries >= APPROVAL_TRIES) answer(false) else {
                        sayOnCar?.invoke(JarvisVoiceLines.YES_OR_NO)
                        main.postDelayed({ listenOnCar?.invoke() }, APPROVAL_LISTEN_DELAY_MS)
                    }
                }
                true
            }
            waiting.invokeOnCancellation {
                voiceAnswer = null
                main.post { CarShell.active()?.clearHudAsk() }
            }
            main.post {
                val shell = CarShell.active()
                val carded = shell?.askOnHud(id, title, detail) { ok -> answer(ok) } == true
                // Logged: a drive's log said nothing about an ask, so "it asked on the
                // phone, not the car" could not be told from "the car never asked".
                step("car ask: $title (card=$carded, sheet=${!carded && shell != null}, voice=${sayOnCar != null})")
                if (!carded && shell != null) {
                    shell.confirm(title = title, detail = detail, confirmLabel = "Allow", onDeclined = { answer(false) }) { answer(true) }
                }
                if (sayOnCar == null && shell == null) answer(false)
            }
            sayOnCar?.invoke(spoken)
            main.postDelayed({ if (!done.get()) listenOnCar?.invoke() }, APPROVAL_LISTEN_DELAY_MS)
        }

    /**
     * For a car that joins from the phone's saved network ([HeadwaySettings.joinsSavedNetwork]):
     * wait for Android to put the phone on it by itself, as it does a few seconds after the
     * car's Bluetooth comes up, instead of requesting the network -- which drops that saved
     * association, and gets a per-connect random MAC and no DHCP hostname, which this car
     * will not lease to (only Google's Android Auto is exempt on GrapheneOS). Null after
     * [SAVED_NETWORK_WAIT_MS], and the request is tried as before.
     */
    private suspend fun awaitSavedCarNetwork(wifi: CarWifiNetwork, credentials: dev.headway.transport.wireless.CarNetworkCredentials): android.net.Network? {
        if (!HeadwaySettings.joinsSavedNetwork(this, credentials.ssid)) return null
        if (SavedNetworkWait.skip(wifi.holdsNetwork, wifi.heldSsid, credentials.ssid)) {
            step("still on ${credentials.ssid} from before the link dropped; not waiting to join it")
            return null
        }
        step(
            "waiting up to ${SAVED_NETWORK_WAIT_MS / 1000}s for the phone to join ${credentials.ssid} " +
                "from its saved network; a network ${ActiveTheme.name} requests gets a new random MAC and no " +
                "DHCP hostname, which this car will not lease to, so ${ActiveTheme.name} does not request it"
        )
        val deadline = System.currentTimeMillis() + SAVED_NETWORK_WAIT_MS
        while (System.currentTimeMillis() < deadline) {
            kotlinx.coroutines.delay(SAVED_NETWORK_POLL_MS)
            wifi.adoptExistingCarNetwork(credentials)?.let { return it }
        }
        step(
            "the phone did not join ${credentials.ssid} on its own. Save that network in Android's " +
                "Wi-Fi settings with Privacy 'Use per-network randomized MAC', 'Send device name to " +
                "network' on and auto-connect on (DHCP then works, as it does for Android Auto; a " +
                "static IP 192.168.5.x, gateway 192.168.5.1 also works); requesting it instead"
        )
        return null
    }

    /** The ringing-call listener for this session; removed with it. */
    private var callWatch: ai.aura.app.car.phone.CarPhone.Listener? = null

    /** The next utterance on the car's microphone, when an approval is waiting for it. */
    @Volatile
    private var voiceAnswer: ((String) -> Boolean)? = null
    /** Speak a short line on the car (its own TTS), and open its microphone: set per session. */
    @Volatile
    private var sayOnCar: ((String) -> Unit)? = null
    @Volatile
    private var listenOnCar: (() -> Unit)? = null

    /**
     * Another package owning the car-app permission is invisible otherwise: the Maps tab
     * falls back to the turn card and nothing says why (RendererOwner.kt). Said once per
     * service start, on the ask channel, because it has a remedy the driver can act on.
     */
    private fun warnIfAnotherHostHoldsTheRenderer() {
        val message = ai.aura.app.car.carapp.rendererConflict(this) ?: return
        SessionLog.shared.info(TAG, message)
        val notification = NotificationCompat.Builder(this, ASK_CHANNEL_ID)
            .setColor(Notify.color(Notify.Template.ASK))
            .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
            .setContentTitle("Another app holds the car-app permission")
            .setContentText(message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setAutoCancel(true)
            .build()
        runCatching { notificationManager?.notify(RENDERER_CONFLICT_NOTIFICATION_ID, notification) }
    }

    /**
     * Home is out of reach in the car and Jarvis has no tunnel of its own (T-0713): the remedy,
     * once per car session, where the owner looks. The car log alone cannot upload without home.
     */
    private fun tellHomeRemedy(remedy: String) {
        step("connect check: $remedy")
        val notification = NotificationCompat.Builder(this, ASK_CHANNEL_ID)
            .setColor(Notify.color(Notify.Template.ASK))
            .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
            .setContentTitle(CarConnectCheck.REMEDY_TITLE)
            .setContentText(remedy)
            .setStyle(NotificationCompat.BigTextStyle().bigText(remedy))
            .setContentIntent(
                PendingIntent.getActivity(
                    this, HOME_REMEDY_NOTIFICATION_ID,
                    Intent(this, ai.aura.app.SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                ),
            )
            .setAutoCancel(true)
            .build()
        runCatching { notificationManager?.notify(HOME_REMEDY_NOTIFICATION_ID, notification) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Before anything that can fail or throw. See the class KDoc.
        startForegroundNow()

        if (intent?.action == ACTION_STOP) {
            CarLinkResume.forget(this)
            stopSelf()
            return START_NOT_STICKY
        }

        // The way back out from the phone-screen cover. It used to be a tap on
        // the cover itself, which had to go: a touchable full-screen window sits
        // above everything and swallowed every gesture injected from the car.
        // The shade opens through the cover now that it is not touchable, so
        // this action is always reachable.
        if (intent?.action == ACTION_SHOW_PHONE) {
            runCatching { HeadwayAccessibilityService.instance.value?.hideBlackout() }
            // The action only belongs on the notification while the cover is up,
            // and it has just come down.
            refreshNotification()
            step("the driver asked for the phone screen back")
            return START_STICKY
        }

        // A hold, not a one-shot. Something on the phone has to be looked at --
        // a system dialog Headway cannot draw over and cannot show on the car --
        // and it has to stay visible for as long as it is on screen, not until
        // the next thing that fancies covering the phone again.
        if (intent?.action == ACTION_HOLD_PHONE) {
            phoneHolds.incrementAndGet()
            runCatching { HeadwayAccessibilityService.instance.value?.hideBlackout() }
            refreshNotification()
            step("the phone screen was uncovered for something that needs looking at")
            return START_STICKY
        }

        // The matching release. Re-covers only when nothing else is holding,
        // and only if the driver asked for a cover in the first place --
        // `coverPhoneScreen` re-checks the setting and the sharing mode.
        if (intent?.action == ACTION_COVER_PHONE) {
            phoneHolds.updateAndGet { if (it > 0) it - 1 else 0 }
            runCatching { coverPhoneScreen() }
            refreshNotification()
            return START_STICKY
        }

        // A null intent is the OS bringing the service back after the process died; the car
        // it was linking comes from CarLinkResume, not from an intent that was never kept.
        carAddress = CarLinkResume.resolve(
            this, intent?.getStringExtra(EXTRA_CAR_ADDRESS), carAddress, restarted = intent == null,
        )
        carAddress?.let { CarLinkResume.remember(this, it) }
        adoptProjectionGrant(intent)

        // Re-delivered intents and a second tap on "connect" must not start a
        // second supervisor; two of them would fight over the same radio.
        if (linkJob?.isActive != true) {
            // A fresh press of Connect is a fresh addressing budget. The user
            // pressing it again after being told what to change in Settings is
            // the whole point of the instructions, and refusing to try would
            // make them uncheckable.
            addressingFailures = 0
            linkJob = scope.launch {
                supervisor().run()
                // `run` returns only when the supervisor has given up -- an
                // attempt limit reached, or a failure it will not retry. Before
                // this the service stayed alive afterwards with a foreground
                // notification and nothing running behind it: a permanent
                // "Disconnected" in the shade, holding wake state and a
                // microphone/projection foreground type, doing nothing.
                //
                // Stopping is the honest end, and it is not a dead end: an ACL
                // connection from the car starts a fresh session with no user
                // action, which is the same path that started this one.
                step("nothing left to retry; stopping until the car comes back")
                CarLinkResume.forget(this@CarLinkService)
                stopSelf()
            }
        }
        return START_REDELIVER_INTENT
    }

    override fun onDestroy() {
        runCatching { carPoke?.close() }
        carPoke = null
        scope.cancel()
        linkJob = null
        // The one place the car network is released. Everywhere else keeps it,
        // because everywhere else is a retry that wants it.
        releaseCarWifi()
        // A display id outlives the display when the driver changes the
        // Developer options entry, and a stale one aims every injected gesture
        // into nothing.
        CarAppDisplay.clear()
        // The one virtual display the grant allows is kept between sessions on
        // purpose (see AppPaneHost.detach); this is where it really goes.
        AppPaneHost.release()
        // The car is gone (ACL disconnect stops this service), so screen sharing ends
        // with it: held, the grant kept the phone sharing after the drive, its
        // indicator up, for nothing. Cleared before stop() so the grant-lost
        // callback sees a successor-less service and offers nothing.
        val grant = projection
        projection = null
        runCatching { grant?.stop() }
        profileReceiver?.let { runCatching { unregisterReceiver(it) } }
        profileReceiver = null
        publish(LinkState.Idle)
        super.onDestroy()
    }

    private fun releaseCarWifi() {
        val wifi = carWifi
        carWifi = null
        runCatching { wifi?.close() }
    }

    private fun supervisor() = SessionSupervisor(
        runSession = { onUp ->
            resetReport = null
            runSession(onUp)
        },
        // The backstop for a car that is simply not there. `CarPresenceReceiver`
        // stops the session outright on ACL disconnect, which is the ordinary
        // path and needs no limit; this catches the cases where that signal
        // never arrives -- a session started by hand and then driven away from,
        // a broadcast dropped under memory pressure, a unit that holds its
        // Bluetooth link up while refusing projection. Giving up costs one
        // automatic restart, because an ACL connection starts a fresh
        // supervisor; not giving up costs a night of joins that cannot succeed.
        maxConsecutiveFailures = MAX_CONSECUTIVE_FAILURES,
        // Past the limit, a car whose Bluetooth is still connected is still here: mid-drive
        // no fresh ACL connection comes to restart a supervisor that gave up (T-0742).
        stillThere = {
            CarPresenceReceiver.carBluetoothUp.also { up ->
                if (up) step("the car has not answered in $MAX_CONSECUTIVE_FAILURES attempts, but its Bluetooth is still connected; still trying")
            }
        },
        onState = { state ->
            publish(state)
            updateNotification(describe(state))
            Log.i(TAG, "link state: $state")
            // Into the *exportable* log too, not only logcat. This was logcat
            // only, and it hid the one line that mattered: a real car log showed
            // the session authenticate, complete discovery, begin opening
            // channels -- and then simply start over, with nothing anywhere
            // saying why. WaitingToRetry carries the cause, and the whole point
            // of the export is that a drive can be diagnosed without adb.
            when (state) {
                is LinkState.WaitingToRetry -> {
                    step("the session ended: ${state.cause}")
                    carWifi?.linkReport()?.let { step("as it ended, $it") }
                    resetReport?.let { report -> runCatching { report(state.cause) }.getOrNull()?.let { step(it) } }
                    resetReport = null
                }
                is LinkState.GaveUp -> {
                    step("giving up: ${state.cause}")
                    resetReport = null
                }
                else -> Unit
            }
        },
        terminalFailure = { failure ->
            // Addressing failures get exactly MAX_ADDRESSING_ATTEMPTS, and the
            // second one earns its keep as a measurement rather than as
            // optimism -- see that constant. Past it the loop stops, because
            // every further attempt is another address consumed on a unit that
            // is not releasing them. Everything else retries.
            if (platformReasonOf(failure) != DHCP_FAILURE) {
                false
            } else {
                addressingFailures++
                if (addressingFailures < MAX_ADDRESSING_ATTEMPTS) {
                    step(
                        "addressing failed ($addressingFailures of " +
                            "$MAX_ADDRESSING_ATTEMPTS). Trying once more, which is a test " +
                            "rather than a hope: if the head unit's address table was " +
                            "simply full, a second association can get a slot; if it is " +
                            "refusing this phone outright, the next failure will look " +
                            "identical. Either way the answer goes in this log"
                    )
                    false
                } else {
                    step(
                        "addressing failed $addressingFailures times, which is the answer " +
                            "to the question the retry was asking: this head unit is not " +
                            "going to issue an address to ${ActiveTheme.name} on its own. The two " +
                            "GrapheneOS toggles above are the fix; a static IP is the " +
                            "fallback. See BLOCKERS.md B-006"
                    )
                    true
                }
            }
        },
        delayOverrideFor = { failure ->
            // See CarNetworkJoinException: a fresh request straight after a
            // failed join lands on Android's stale approval dialog rather than
            // producing a new one. Give it time to clear.
            if (failure is CarNetworkJoinException) JOIN_RETRY_DELAY_MILLIS else null
        },
    )

    /**
     * The AAP port this head unit last announced, if it ever has.
     *
     * Keyed by the car's Bluetooth address, not stored globally: a port learned
     * from one car must never be tried against a different one. The old single
     * key meant pairing a second car whose head unit listens on 5288 would keep
     * dialling the first car's 7001 and fail every cycle, with nothing to
     * unlearn it.
     *
     * Persisted rather than held in memory because the useful case is the run
     * *after* a restart: a head unit that names its port only sometimes should
     * not send the phone back to guessing every time the service starts.
     */
    private fun rememberedPort(address: String): Int? =
        getSharedPreferences(PREFS, MODE_PRIVATE).getInt(portKey(address), 0).takeIf { it > 0 }

    private fun rememberPort(address: String, port: Int) {
        if (port <= 0 || port == rememberedPort(address)) return
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .putInt(portKey(address), port)
            .apply()
        step("remembering that the head unit at $address listens on port $port")
    }

    private fun portKey(address: String): String = "$KEY_LEARNED_PORT.$address"

    /**
     * Which bundled certificate this head unit accepted last time.
     *
     * Exactly the argument [rememberedPort] makes for the learned port: the
     * useful case is the run *after* a restart. `rejectedCertificates` is a
     * plain field on the service object, so a fresh Connect -- or a reboot, or
     * an app update -- starts again at candidate 0, which is the one that
     * expired in 2022. A real log shows eight full attempts burned on it.
     *
     * Keyed by Bluetooth address, again like the port: two cars can accept
     * different certificates, and a value learned from one must never be tried
     * against the other.
     *
     * Stored as the id rather than the index, because the index means nothing if
     * the built-in order ever changes and `credentialFor` already takes an id.
     */
    private fun rememberedCertificate(address: String): String? =
        getSharedPreferences(PREFS, MODE_PRIVATE).getString(certificateKey(address), null)

    private fun rememberCertificate(address: String, id: String) {
        if (id == rememberedCertificate(address)) return
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .putString(certificateKey(address), id)
            .apply()
        step("remembering that the head unit at $address accepts the '$id' certificate")
    }

    /**
     * Forgets the remembered choice after a rejection.
     *
     * A head unit software update can revoke what used to work. Without this the
     * phone would pin itself to a dead choice and rotate away from it on every
     * single session, forever.
     */
    private fun forgetCertificate(address: String) {
        if (rememberedCertificate(address) == null) return
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .remove(certificateKey(address))
            .apply()
        step("the remembered certificate for $address was rejected; forgetting it")
    }

    private fun certificateKey(address: String): String = "$KEY_ACCEPTED_CERT.$address"

    /**
     * Registers the car as a Wi-Fi suggestion, when the quirk file asks.
     *
     * The only public API that carries a MAC randomization preference, and
     * therefore the only in-app route to the half of GrapheneOS's Android Auto
     * fix that is reachable at all. `CarWifiProvisioning.suggest` has the full
     * account of what it costs.
     */
    private fun suggestCarNetwork(
        credentials: dev.headway.transport.wireless.CarNetworkCredentials,
        hiddenSsid: Boolean,
    ) {
        val problem = ai.aura.app.car.link.CarWifiProvisioning.suggest(
            getSystemService(android.net.wifi.WifiManager::class.java),
            ai.aura.app.car.link.CarWifiProvisioning.CarCredentials(
                ssid = credentials.ssid,
                passphrase = credentials.passphrase,
                hiddenSsid = hiddenSsid,
            ),
        )
        if (problem == null) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putBoolean(KEY_SUGGESTED, true).apply()
            step(
                "registered ${credentials.ssid} as a Wi-Fi suggestion with a per-network " +
                    "MAC. Android decides when to join it, so this attempt waits rather " +
                    "than requesting the network itself. The first time, accept the " +
                    "notification asking whether ${ActiveTheme.name} may connect to Wi-Fi"
            )
        } else {
            step("could not register the car as a Wi-Fi suggestion: $problem")
        }
    }

    /** Undoes [suggestCarNetwork] when the quirk file stops asking for it. */
    private fun withdrawSuggestionIfAny(
        credentials: dev.headway.transport.wireless.CarNetworkCredentials,
        hiddenSsid: Boolean,
    ) {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_SUGGESTED, false)) return
        ai.aura.app.car.link.CarWifiProvisioning.withdraw(
            getSystemService(android.net.wifi.WifiManager::class.java),
            ai.aura.app.car.link.CarWifiProvisioning.CarCredentials(
                ssid = credentials.ssid,
                passphrase = credentials.passphrase,
                hiddenSsid = hiddenSsid,
            ),
        )
        prefs.edit().putBoolean(KEY_SUGGESTED, false).apply()
        step("withdrew the Wi-Fi suggestion for ${credentials.ssid}, as the quirk file " +
            "no longer asks for it")
    }

    /**
     * Keeps the car's Wi-Fi credentials for the settings screen to offer.
     *
     * The one-tap "Set up this car's Wi-Fi" button
     * ([ai.aura.app.car.link.CarWifiProvisioning.addNetworksIntent]) needs an
     * SSID and a passphrase, and both arrive over Bluetooth in the middle of a
     * connection attempt — by the time the user is reading the settings screen,
     * the service may not be running and the car may not be in range. Without
     * this the button could only work during a live attempt, which is exactly
     * when the user is not looking at it.
     *
     * On storing a passphrase: it goes in the app's private `SharedPreferences`,
     * the same protection domain as the imported certificate key, and the point
     * of the button is to hand it straight to Android's own Wi-Fi database
     * where it will live anyway. `SessionLog` still scrubs it, so it cannot
     * reach an exported log. The next handshake overwrites it; nothing else
     * removes it short of clearing the app's data.
     */
    private fun rememberCarWifi(
        ssid: String,
        passphrase: String,
        hidden: Boolean,
        bssid: String = "",
    ) {
        if (ssid.isEmpty()) return
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .putString(KEY_CAR_SSID, ssid)
            .putString(KEY_CAR_PASSPHRASE, passphrase)
            .putBoolean(KEY_CAR_HIDDEN, hidden)
            // Stored as a *hint* for the companion chooser, which uses it to
            // narrow the list to one row when the announced radio is the one on
            // the air. Never used to pin a request on its own; see failedJoins.
            .putString(KEY_CAR_BSSID, bssid)
            .apply()
    }

    /**
     * Connects to the head unit, allowing for a listener that is not up yet.
     *
     * A refusal straight after association is not the same as a wrong address.
     * `ECONNREFUSED` means the head unit answered — it is on the network and
     * reachable — and only that nothing is bound to the port yet, which is
     * exactly what it looks like while the unit is still starting its AAP
     * server. Giving up on the first one throws away a session that was seconds
     * from working, and hands the supervisor a full backoff cycle instead.
     *
     * Bounded rather than open-ended: a head unit that is never going to listen
     * has to fail the attempt so reconnection can start over.
     */
    // A caught Throwable is inspected rather than split into catch blocks on
    // purpose (Headway): withTimeout's TimeoutCancellationException IS a
    // CancellationException, and here it means "this attempt timed out" -- a
    // failure to report and retry -- while any other cancellation must propagate.
    // Separate catch blocks cannot express "a cancellation, but not that one".
    @Suppress("InstanceOfCheckForException", "CyclomaticComplexMethod")
    private suspend fun connectWithRetry(
        wifi: CarWifiNetwork,
        totalMillis: Long = 20_000,
        retryDelayMillis: Long = 1_000,
        // Re-read on every attempt: the head unit may name its endpoint only
        // while this loop is already dialling a guess.
        endpointNow: () -> CarEndpoint,
        // Has the head unit named its endpoint on Bluetooth yet (its WIFI_START_REQUEST)?
        named: () -> Boolean,
    ): java.net.Socket {
        val began = System.nanoTime()
        var attempt = 0
        var endpoint = endpointNow()
        while (true) {
            attempt++
            val latest = endpointNow()
            if (latest != endpoint) {
                step("switching the AAP connect from $endpoint to $latest, which the head unit just named")
                endpoint = latest
            }
            try {
                return wifi.openSocket(endpoint.ipAddress, endpoint.port)
            } catch (e: kotlinx.coroutines.CancellationException) {
                // The user pressed Disconnect, or the service is going away.
                // CancellationException is an Exception, so the catch below
                // would rewrap it as "the head unit never accepted an AAP
                // connection" -- blaming the car for the user's own action.
                throw e
            } catch (e: Exception) {
                val refusedNow = e is java.net.ConnectException &&
                    e.message?.contains("refused", ignoreCase = true) == true
                val waited = (System.nanoTime() - began) / 1_000_000
                if (waited >= DialPatience.millis(refusedNow, named(), totalMillis)) {
                    // Two very different failures reach here and the old message
                    // asserted the wrong one. ECONNREFUSED means the head unit
                    // answered and nothing is bound to the port. Anything else —
                    // and in particular a socket that cannot be created or
                    // routed at all — is what a revoked Network permission looks
                    // like on GrapheneOS, where the platform "pretends the
                    // network is down" rather than throwing something nameable.
                    // Telling that user their port is wrong sends them to edit a
                    // quirk file about a setting in Settings.
                    val refused = refusedNow
                    throw IllegalStateException(
                        if (refused) {
                            "the head unit refused an AAP connection on $endpoint after " +
                                "$attempt attempts over ${waited}ms. It is reachable, and it " +
                                (if (named()) "had named this endpoint, so the port is wrong"
                                else "never sent its start request, so it was not ready to project") +
                                ": ${e.message}"
                        } else {
                            "could not reach $endpoint at all after $attempt attempts over " +
                                "${waited}ms (${e.message}). If ${ActiveTheme.name}'s Network " +
                                "permission is switched off — GrapheneOS has one, and it makes " +
                                "the platform pretend every network is down — turn it back on: " +
                                "the car link needs it even though it never uses the internet."
                        },
                        e,
                    )
                }
                step("$endpoint not accepting yet (attempt $attempt): ${e.message}")
                kotlinx.coroutines.delay(retryDelayMillis)
            }
        }
    }

    /**
     * One attempt at the whole chain, from radio to open channels.
     *
     * Returning normally means the session ended cleanly; throwing hands the
     * failure to [SessionSupervisor], which backs off and calls this again. Every
     * resource is scoped to this function so that both outcomes release the
     * Bluetooth socket, the network request and the TCP socket — a leaked
     * network request is worse than a failed connection, because the phone stays
     * associated to an access point with no internet.
     *
     * The Bluetooth permission is not checked here. It is granted in the UI
     * before the service is ever started, and if it is somehow missing the
     * platform throws `SecurityException`, which [BluetoothCarLink] converts into
     * a link failure with a readable message — a state the supervisor and the
     * notification already handle. A second check here would only duplicate it.
     */
    @SuppressLint("MissingPermission")
    // A caught Throwable is inspected rather than split into catch blocks on
    // purpose (Headway): withTimeout's TimeoutCancellationException IS a
    // CancellationException, and here it means "this attempt timed out" -- a
    // failure to report and retry -- while any other cancellation must propagate.
    // Separate catch blocks cannot express "a cancellation, but not that one".
    @Suppress("InstanceOfCheckForException")
    private suspend fun runSession(onUp: () -> Unit = {}) {
        // Which build produced this log. Every real-car diagnosis so far has had
        // to start by guessing that from which log messages are present, and
        // guessing wrong sends the reader after a bug that was already fixed --
        // especially while a signing problem is keeping people on older builds.
        step("${ActiveTheme.name} build ${BuildConfig.VERSION_CODE} (${BuildConfig.VERSION_NAME})")

        // Cleared per attempt. These are service-level fields, so a failed
        // attempt used to leave its map in place and the *next* attempt's frame
        // log was labelled with the previous session's channel names — which is
        // how one export ended up showing "tx SENSOR ... 10 0a" immediately
        // followed by "channel BLUETOOTH open". Two different naming schemes for
        // the same channel, in adjacent lines, in a file whose whole purpose is
        // to be read after the fact.
        channelNames = emptyMap()
        highRateChannels = DEFAULT_HIGH_RATE_CHANNELS
        // Per attempt, like everything above it. Left standing, the second
        // session in a service lifetime -- which is every automatic reconnect,
        // the thing CLAUDE.md makes a first-class feature -- reported frame
        // counts and byte totals belonging mostly to earlier sessions, and an
        // fps figure averaged across the gap between them. It also meant the
        // "first few frames in full" logging never fired again after the first
        // session, because the counter it checks was already past its limit.
        frameLogStates.clear()
        pingsAnswered = 0L
        worstPingAnswerMillis = 0L
        worstPingQueueMillis = 0L
        worstPingSendMillis = 0L

        val adapter = BluetoothCarLink.adapterOf(this)
            ?: throw IllegalStateException("this device has no Bluetooth adapter")
        if (!adapter.isEnabled) throw IllegalStateException("Bluetooth is off")

        val car = resolveCar(adapter)
        Log.i(TAG, "connecting to ${car.address}")
        // Where this attempt gets to, for the setup screen (CarGates, ADR-0047).
        val gates = CarGates.shared
        val vpnUp = { CarWifiNetwork.vpnAdvice(this@CarLinkService) != null }
        gates.reached(Gate.BLUETOOTH)

        // Before anything can fail because of it. A VPN produces a timeout with
        // no cause attached, three steps later.
        CarWifiNetwork.vpnAdvice(this@CarLinkService)?.let(::step)

        // Resolved before the link, because two of these knobs shape the Wi-Fi
        // association and one shapes the Bluetooth exchange itself. Identity is
        // unknown until service discovery, so this yields the catch-all profile
        // plus any user entry matching every unit -- which is where a "my head
        // unit needs X to connect at all" override has to live, since the user
        // cannot get far enough to learn the unit's identity.
        val quirks = QuirkStore.inAppStorage(this).quirksFor(HeadUnitIdentity())
        // Published for runChannels, which is a separate entry point and cannot
        // see this local. Audio routing is a quirk, and getting it wrong is the
        // difference between music and silence.
        sessionQuirks = quirks
        if (quirks != HeadUnitQuirks.DEFAULT) {
            step("applying head unit quirks: ${quirks.describe()}")
        }

        BluetoothCarLink(
            car,
            adapter,
            announceWifiChannel = quirks.announceWifiChannel,
            onStep = ::step,
            // The car's own connection, if it opened one since the last session.
            accepted = pokes.take(car.address),
        ).use { link ->
            val credentials = try {
                link.fetchCredentials()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Throwable) {
                gates.failed(Gate.BLUETOOTH, null, accessPointStatic = null, vpnActive = vpnUp())
                throw e
            }
            // Remembered here, and only here: the address is now known to belong
            // to something that speaks Android Auto over RFCOMM, which is what
            // `CarPresenceReceiver` needs before it will start a session on its
            // own. Recording the driver's pick instead would arm the automatic
            // start against a device that may never have worked.
            HeadwaySettings.rememberCar(this@CarLinkService, car.address)
            val announcedStatic: Boolean? =
                if (credentials.accessPointTypeAnnounced) credentials.accessPointType == AccessPointType.STATIC else null
            CarProfiles.update(this@CarLinkService, car.address, credentials.ssid) { profile ->
                profile.copy(
                    ssid = credentials.ssid,
                    bssid = credentials.bssid ?: profile.bssid,
                    staticIp = announcedStatic ?: profile.staticIp,
                    gateway = credentials.endpoint?.ipAddress ?: profile.gateway,
                )
            }
            gates.passed(Gate.BLUETOOTH)
            gates.reached(Gate.ACCESS_POINT)
            // From here to the end of the attempt, something has to keep
            // reading Bluetooth. The head unit does not go quiet once it has
            // handed over the credentials -- it pings, and it re-announces --
            // and a peer whose keepalives are never answered has every reason
            // to decide the phone gave up. This used to be dead air for the
            // entire Wi-Fi join, which is the longest part of the bring-up.
            // What the head unit names on Bluetooth after the credentials: the
            // Chevrolet's only word on its port (7001). Read by every connect
            // retry below, and remembered for the next session.
            val announced = java.util.concurrent.atomic.AtomicReference<CarEndpoint?>(null)
            val rfcommService = scope.launch {
                link.serviceLink { late ->
                    announced.set(late)
                    rememberPort(car.address, late.port)
                }
            }
            try {
            // CarNetworkCredentials.toString already redacts the passphrase, but
            // register it with the scrubber too, so it cannot leak through some
            // other log line before the export is shared for diagnosis.
            SessionLog.shared.protect(credentials.passphrase)
            Log.i(TAG, "head unit offered $credentials")
            // Kept so the settings screen can offer one-tap Wi-Fi setup when the
            // car is nowhere near. See rememberCarWifi.
            rememberCarWifi(
                credentials.ssid,
                credentials.passphrase,
                quirks.hiddenSsid,
                credentials.bssid,
            )

            // Every capture that reached TCP had the car announce its endpoint;
            // the attempts that timed out joining had not received one. A unit
            // that names where to connect is a unit that is ready to project,
            // which is also when its access point is on the air -- so the
            // absence is worth flagging before spending the join timeout.
            if (credentials.endpoint == null) {
                step(
                    "the car sent credentials but no endpoint this time, which has " +
                        "so far correlated with its access point not being up yet; " +
                        "if the join stalls, open Android Auto on the car screen"
                )
            }

            // access_point_type is one field inside the credentials line, and it
            // is the single most consequential thing the head unit says about
            // addressing: STATIC means "do not expect me to hand out an
            // address". If this unit sends STATIC, the DHCP failure is not a
            // GrapheneOS MAC problem at all, it is the head unit doing exactly
            // what it announced, and every remedy aimed at the MAC is wasted.
            // Worth its own line rather than being read out of a toString.
            // Gated on the field being *present*, which it was not on the drive
            // that prompted the original note. STATIC is 0, so an absent field 5
            // decodes as STATIC, and Headway was announcing a DHCP problem the
            // car had never mentioned -- and sending the diagnosis after it. See
            // CarNetworkCredentials.accessPointTypeAnnounced for the bytes.
            if (credentials.accessPointTypeAnnounced &&
                credentials.accessPointType == AccessPointType.STATIC
            ) {
                step(
                    "NOTE: this head unit explicitly announced access_point_type=STATIC, " +
                        "which in the protocol means its access point does not assign " +
                        "addresses (AccessPointType.proto: STATIC=0, DYNAMIC=1). If the " +
                        "join fails for want of an address, that is this unit behaving as " +
                        "announced rather than a phone-side fault, and a static IP on a " +
                        "saved car network is the answer rather than any MAC setting. " +
                        "Please report this line -- no reference implementation sends " +
                        "STATIC to a phone"
                )
            }

            // The suggestion path, when the quirk file asks for it. Registered
            // before the join is attempted because the platform, not Headway,
            // decides when to associate -- a suggestion added after a failed
            // join would do nothing for this attempt.
            if (quirks.suggestCarNetwork) {
                suggestCarNetwork(credentials, quirks.hiddenSsid)
            } else {
                // Turning the quirk off has to actually revert. Suggestions
                // outlive the process, so an in-memory flag would leave one
                // registered forever after a restart -- hence the stored one.
                withdrawSuggestionIfAny(credentials, quirks.hiddenSsid)
            }

            run {
                // Reused across attempts, not scoped to this one. See carWifi.
                val wifi = carWifi ?: CarWifiNetwork(this, onStep = ::step).also { carWifi = it }
                // SSID-only unless the quirk file forces otherwise, and unless a
                // companion-device association names a BSSID -- which is the one
                // case where pinning buys something, because
                // WifiNetworkFactory's CompanionDeviceManager bypass keys on the
                // BSSID *in the request* and skips the approval sheet entirely.
                // See failedJoins for why the old alternation was removed.
                val companion = CarCompanion.of(this@CarLinkService)
                val associated = companion.associatedBssid(credentials.ssid)
                // A quirk file forcing pinBssid=false on a paired car silently
                // disables the pairing, because Android looks it up by the BSSID
                // in the request. Say so rather than lose the bypass quietly.
                companion.pairingNeedsPinnedBssid(credentials.ssid, quirks.pinBssid)
                    ?.let { step("WARNING: $it") }
                val pinBssid = quirks.pinBssid ?: (associated != null)
                val network = try {
                    // If the user has already put the phone on the car's Wi-Fi
                    // themselves, use that and skip the approval sheet entirely.
                    // This is the escape hatch for a phone where the sheet keeps
                    // going unanswered, and it costs one cheap lookup.
                    (wifi.adoptExistingCarNetwork(credentials) ?: awaitSavedCarNetwork(wifi, credentials))
                        ?.also { HeadwaySettings.rememberSavedNetworkCar(this@CarLinkService, credentials.ssid) }
                        // A network still held from before a drop goes to join(), which hands it
                        // back without asking for anything: the suggestion's wait polls for a
                        // network the link does not hold and would run out its whole timeout.
                        ?: if (quirks.suggestCarNetwork &&
                            !SavedNetworkWait.skip(wifi.holdsNetwork, wifi.heldSsid, credentials.ssid)
                        ) {
                            // Deliberately NOT falling through to join(). With a
                            // suggestion registered, requesting the same access
                            // point opens a *second* connection to it on STA+STA
                            // hardware, with a fresh random MAC -- which is the
                            // exact thing the suggestion exists to stop. So wait
                            // for the platform to associate and adopt that.
                            wifi.awaitSuggestedCarNetwork(credentials)
                        } else {
                            wifi.join(
                                // The associated BSSID replaces the announced one
                                // when there is one: it came from a live scan the
                                // system did during the companion chooser, so it
                                // is the radio the car actually brings up rather
                                // than the one it names over Bluetooth.
                                if (associated != null) {
                                    credentials.copy(bssid = associated)
                                } else {
                                    credentials
                                },
                                hiddenSsid = quirks.hiddenSsid,
                                pinBssid = pinBssid,
                            )
                        }
                } catch (e: Throwable) {
                    // Tell the head unit before dropping Bluetooth. Otherwise it
                    // is left mid-bootstrap believing the phone is still coming,
                    // and the next attempt reconnects into a projection state
                    // machine that was never told the last one failed.
                    if (e !is kotlinx.coroutines.CancellationException) {
                        // A car that refused a requested join an address is joined from
                        // the phone's saved network from now on (awaitSavedCarNetwork).
                        if (platformReasonOf(e) == DHCP_FAILURE) {
                            HeadwaySettings.rememberSavedNetworkCar(this@CarLinkService, credentials.ssid)
                        }
                        runCatching {
                            link.reportWifiFailed(Status.STATUS_WIFI_NETWORK_UNAVAILABLE)
                        }
                        // Said here, at the failure, and not only in the
                        // start-of-attempt log line. The first Headway join that
                        // ever succeeded happened moments after Android Auto had
                        // been connected -- i.e. with these profiles up -- and
                        // every failure since has been with both down. That is
                        // one observation, not a proof, but it is the strongest
                        // correlation in the record and the user cannot act on
                        // it if it is buried thirty lines above the error.
                        if (runCatching {
                                BluetoothCarLink.audioProfilesDisconnected(adapter)
                            }.getOrDefault(false)
                        ) {
                            step(
                                "note: the car is connected for neither calls nor media " +
                                    "audio. Head units generally start projection only for a " +
                                    "phone they consider present, so connect the car for " +
                                    "Phone calls and Media audio in Bluetooth settings, and " +
                                    "check Android Auto is still enabled for this phone on " +
                                    "the car screen, before the next attempt"
                            )
                        }
                    }
                    // join() closes itself on failure, and a closed
                    // CarWifiNetwork cannot be joined again, so it must not be
                    // the one the next attempt picks up.
                    releaseCarWifi()
                    if (e !is kotlinx.coroutines.CancellationException) {
                        val verdict = generateSequence(e) { it.cause }
                            .firstNotNullOfOrNull { (it as? CarWifiException)?.platformReason }
                        val gate = when (verdict) {
                            "AUTHENTICATION", "ASSOCIATION" -> Gate.ASSOCIATION
                            "IP_PROVISIONING" -> Gate.ADDRESS
                            else -> Gate.ACCESS_POINT
                        }
                        gates.failed(gate, verdict, announcedStatic, vpnUp())
                        failedJoins++
                        step(
                            "$failedJoins failed join(s) this session; matching " +
                                when {
                                    quirks.pinBssid != null -> "as the quirk file says"
                                    pinBssid -> "on the BSSID this phone is paired with"
                                    else -> "on the SSID alone, which matches any radio"
                                }
                        )
                    }
                    throw CarNetworkJoinException(e)
                }
                Log.i(TAG, "bound to car network $network")
                gates.passed(Gate.ADDRESS)
                gates.reached(Gate.TLS)

                // Say so immediately, not after working out where to connect.
                // This is the announcement that the association happened, and a
                // head unit waiting for it has no reason to be accepting AAP
                // connections yet -- which is why a reachable one refused
                // outright. Sending it first also gives a unit that answers with
                // WifiStartRequest the longest possible head start.
                link.reportWifiConnected()

                // The head unit may never say where to connect. The observed
                // Chevrolet sends WifiStartRequest on some attempts and not
                // others, which made the whole link look flaky: the runs where
                // it spoke used its port, the runs where it stayed quiet used
                // the canonical 5288 and got ECONNREFUSED. The port it gave was
                // 7001, so the default was simply wrong for this car.
                //
                // A port learned from this head unit beats a constant from the
                // references, so remember it and prefer it next time.
                val endpoint = credentials.endpoint ?: announced.get() ?: run {
                    val host = wifi.headUnitAddress() ?: throw IllegalStateException(
                        "the head unit offered no endpoint over Bluetooth and the car " +
                            "network reports no DHCP server or gateway to fall back on"
                    )
                    val learned = rememberedPort(car.address)
                    val port = learned ?: TcpTransport.DEFAULT_PORT
                    step(
                        "head unit named no port; using " +
                            if (learned != null) "$port, remembered from an earlier session"
                            else "the default $port"
                    )
                    CarEndpoint(host, port)
                }
                credentials.endpoint?.let { rememberPort(car.address, it.port) }
                Log.i(TAG, "opening the AAP session to $endpoint")

                // Sockets come from the network, never from `new Socket()`:
                // the car AP has no internet and is not the default route.
                //
                // An adopted network is identified only by its gateway matching
                // the address the head unit named, which is strong evidence but
                // not proof — a home router on the same private address would
                // also match. The AAP connect is the proof. If it fails on an
                // adopted network, stop trusting the adoption and let the next
                // attempt request the car's access point properly, rather than
                // re-adopting the same wrong network forever.
                val socket = try {
                    connectWithRetry(
                        wifi,
                        endpointNow = { announced.get() ?: endpoint },
                        named = { DialPatience.named(credentials.endpoint, announced.get()) },
                    )
                } catch (e: kotlinx.coroutines.CancellationException) {
                    throw e
                } catch (e: Exception) {
                    gates.failed(Gate.TLS, null, announcedStatic, vpnUp())
                    if (wifi.isAdopted) {
                        step(
                            "nothing answered on the Wi-Fi network ${ActiveTheme.name} adopted, so it " +
                                "was probably not the car; asking Android to join the car's " +
                                "access point on the next attempt"
                        )
                        releaseCarWifi()
                    }
                    throw e
                }

                // `quirks` was resolved before the join, because two of its
                // knobs decide how the access point is matched. Identity-specific
                // touch corrections are applied later, once discovery has run.
                TcpTransport.wrap(socket).use { transport ->
                    val connection = FramedConnection(
                        transport,
                        fragmenter = MessageFragmenter(quirks.maxFragmentSize),
                        onFrame = ::logFrame,
                    )
                    val certificates = PhoneCertificateStore.inAppStorage(this)
                    val attempt = rejectedCertificates
                    // Quirk file wins, then whatever this car accepted last
                    // time, then the built-in order -- the same three tiers the
                    // learned AAP port already uses. Without the middle one,
                    // every fresh Connect re-tests the certificate the car is
                    // already known to reject: eight wasted attempts in a single
                    // real-car log, each costing a Bluetooth handshake, a Wi-Fi
                    // join and a TCP connect.
                    val preferredCertificate =
                        quirks.certificate ?: rememberedCertificate(car.address)
                    val credential = certificates.credentialFor(attempt, preferredCertificate)
                    val keyMaterial = credential.material
                    step("presenting ${certificates.describe(attempt, preferredCertificate)}")
                    step("why this one: ${credential.rationale}")
                    AapTls.validityProblem(keyMaterial)?.let {
                        // Said before the session rather than after it fails. A
                        // head unit reports this as a bare status code once TCP,
                        // TLS and the version exchange have all succeeded, and a
                        // real Chevrolet renders it on screen as the phone and
                        // vehicle calendars disagreeing -- which sends the reader
                        // to the clock rather than to the certificate.
                        val more = certificates.candidateCount() - attempt - 1
                        step(
                            "WARNING: $it. A head unit that checks this will refuse the " +
                                "session with an authentication failure." +
                                if (more > 0) {
                                    " If it does, ${ActiveTheme.name} will try $more other certificate(s) " +
                                        "on the attempts that follow before giving up."
                                } else {
                                    " Import a valid certificate from Diagnostics; " +
                                        "see BLOCKERS.md B-003"
                                }
                        )
                    }
                    val session = AapSession(
                        connection = connection,
                        tls = TlsSession(AapTls.phoneEngine(keyMaterial)),
                        identity = carIdentity(),
                        announcedVersion = quirks.announcedVersion,
                        onStep = ::step,
                    )
                    val profile = try {
                        session.connect()
                    } catch (e: AuthenticationRejectedException) {
                        gates.failed(Gate.TLS, null, announcedStatic, vpnActive = false)
                        // A remembered choice that just got rejected is worse
                        // than no memory at all: it would be tried first every
                        // session and rejected every session. See
                        // forgetCertificate.
                        forgetCertificate(car.address)
                        // Advance to the next certificate, if there is one, and
                        // let the supervisor reconnect. Only this failure moves
                        // the counter -- see rejectedCertificates.
                        val remaining = certificates.candidateCount() - attempt - 1
                        if (remaining > 0) {
                            rejectedCertificates = attempt + 1
                            step(
                                "the head unit refused ${credential.label}; the next attempt " +
                                    "will present " +
                                    certificates.credentialFor(attempt + 1, quirks.certificate).label
                            )
                        } else {
                            step(
                                "the head unit refused every certificate ${ActiveTheme.name} carries. " +
                                    "Import one from Diagnostics, or set the car's clock into " +
                                    "2014-08..2022-08 to satisfy the phone-role certificate " +
                                    "(which stops Google's Android Auto working meanwhile). " +
                                    "See BLOCKERS.md B-003"
                            )
                        }
                        throw e
                    }

                    // Whatever got us here is the one that works. Remember it,
                    // and say so: it is the answer to a question the references
                    // cannot settle.
                    //
                    // Remembered even when it was the first candidate -- the old
                    // `attempt > 0` guard meant a car that accepts the default
                    // taught us nothing, and the point of storing it is that the
                    // built-in order may change under a user who never sees it.
                    rememberCertificate(car.address, credential.id)
                    CarProfiles.update(this@CarLinkService, car.address, credentials.ssid) { it.copy(certificate = credential.id) }
                    gates.passed(Gate.TLS)
                    gates.reached(Gate.SESSION)
                    if (certificates.candidateCount() > 1 && attempt > 0) {
                        step("the head unit accepted ${credential.label} — " +
                            "remembering it for next time, and " +
                            "\"certificate\": \"${credential.id}\" in the quirk file pins it")
                    }

                    // The link is genuinely up now: version, TLS-or-auth,
                    // service discovery and channel open all completed. Signal it
                    // so LinkState.Connected reaches the UI while the session is
                    // live rather than at the moment it ends. Then a richer
                    // notification than describe(Connected) would give.
                    onUp()
                    gates.passed(Gate.SESSION)
                    // Before the first line about the session, so everything
                    // logged from here on names channels the way the car does.
                    adoptChannelNames(profile)
                    updateNotification(
                        "Connected to ${profile.displayName ?: car.name ?: car.address}"
                    )
                    step("session up: ${profile.displayName ?: "head unit"}, " +
                        "${profile.channelIds.size} channel(s) advertised")

                    coroutineScope {
                        // A phone that drives out of range keeps a socket that
                        // will not fail until a write times out. Watching the
                        // network makes the loss immediate, and throwing from a
                        // child cancels the scope and reaches the supervisor.
                        val watchdog = launch {
                            wifi.awaitLost()
                            throw IllegalStateException("car network lost")
                        }
                        // The radio every 10 s, so a reset can be read against the signal
                        // and the screen (T-0370).
                        val radio = launch {
                            while (true) {
                                wifi.linkReport()?.let(::step)
                                kotlinx.coroutines.delay(LINK_REPORT_MS)
                            }
                        }
                        try {
                            runChannels(connection, profile)
                        } finally {
                            watchdog.cancel()
                            radio.cancel()
                        }
                    }
                }
            }
            } finally {
                rfcommService.cancel()
            }
        }
    }

    /**
     * Runs for as long as the session is up: routes every channel to the thing
     * that drives it, and does not return until the head unit goes away.
     *
     * ## Order, and the deadline that fixes it
     *
     * Video first, and everything else after. A real 2021 Chevrolet
     * Infotainment 3 unit closes the session about fifteen seconds after the
     * last channel opens if no video has arrived — measured at 15 s, 16 s and
     * 19 s across three sessions — so anything that could block in front of the
     * encoder would spend that budget. Audio and voice bring themselves up in
     * the background with their own bounded waits for exactly this reason; input
     * has nothing to wait for.
     *
     * ## One reader per channel
     *
     * Everything below reads a [ChannelDemultiplexer] view, never the
     * connection. Two readers on one channel steal each other's messages, and
     * the failure is not obvious: half the keepalives land in the wrong place
     * and the head unit concludes the phone has gone away. That is also why the
     * control channel has exactly one reader here, and why audio focus
     * notifications are *pushed* into [CarAudioStream.onControlMessage] from it
     * rather than read by the audio stream itself.
     */
    // A caught Throwable is inspected rather than split into catch blocks on
    // purpose (Headway): withTimeout's TimeoutCancellationException IS a
    // CancellationException, and here it means "this attempt timed out" -- a
    // failure to report and retry -- while any other cancellation must propagate.
    // Separate catch blocks cannot express "a cancellation, but not that one".
    @Suppress("InstanceOfCheckForException")
    protected open suspend fun runChannels(
        connection: FramedConnection,
        profile: HeadUnitProfile,
    ) = coroutineScope {
        // Which channels may be made to wait for the wire, named from what this
        // unit actually advertised rather than from a table -- the target
        // vehicle puts video on id 12 and its audio sinks on 2 to 5.
        //
        // Only video. A late video frame is discarded by `VideoPump` and costs
        // nothing; a late *audio* buffer is audio the driver never hears,
        // because the capture behind the sender has nowhere to put what the
        // phone is playing while it waits. See FramedConnection.bulkChannels.
        connection.bulkChannels = profile.services
            .filter {
                it.hasMediaSinkService() &&
                    it.mediaSinkService.availableType == MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP
            }
            .map { it.id }
            .toSet()
        if (connection.bulkChannels.isNotEmpty()) {
            step(
                "wire priority: control first, then audio and everything else, with " +
                    "video ${connection.bulkChannels.joinToString()} made to queue"
            )
        }

        // Every view must be registered before the pump starts, or the first
        // messages for a channel arrive before anyone is subscribed and are
        // counted unroutable. That is the demultiplexer's documented contract.
        val demux = ChannelDemultiplexer(
            connection,
            onUnroutable = { message ->
                // The emulator advertises six channels; this car advertises
                // thirteen. Anything arriving on one nothing subscribed to is a
                // gap in the wiring worth seeing, not noise to swallow.
                step(
                    "no handler for ${ControlMessageType.describe(message.messageId)} on " +
                        "${ChannelId.describe(message.channelId)} (${message.payload.size} bytes)"
                )
            },
        )
        val control = demux.channel(ChannelId.CONTROL.id)

        // The car screen is Headway's own display now, not a mirror of the
        // phone. A real drive showed why: a 1080x2404 phone shown on an 800x480
        // panel used 216 of 800 columns and left the rest black. The surface is
        // built at the head unit's own resolution the moment it says what that
        // is; mirroring stays as a fallback and as a setting.
        // Decided once, before anything reads it. The launcher uses it to pick
        // which display an app starts on and CarInputStream uses it to build the
        // touch transform and to aim the injected gesture, so resolving it after
        // either of those would give the two halves different answers and put
        // touches on a display nobody is looking at. See ADR 0008.
        CarAppDisplay.resolve(this@CarLinkService, ::step)

        val wantDashboard = HeadwaySettings.dashboardOnCarScreen(this@CarLinkService)
        val surfaceFactory: ((EncoderConfiguration, ScreenEncoder.Sink) -> CarSurface?)? =
            if (wantDashboard) {
                { configuration, sink ->
                    CarSurface.create(this@CarLinkService, configuration, sink, ::step)
                }
            } else {
                null
            }

        val video = CarVideoStream.of(
            profile = profile,
            connectionFor = { id -> demux.channel(id) },
            projection = projection,
            surfaceFactory = surfaceFactory,
            requestFocus = sessionQuirks.videoFocusRequest,
            onStep = ::step,
        )
        if (video == null) {
            step("the head unit advertised no H.264 video sink, so there is nothing to send")
        } else if (!wantDashboard && projection == null) {
            step(
                "mirroring is selected but there is no screen capture grant, so no video will " +
                    "be sent. The car will stay on its connecting screen"
            )
        }

        val connectionFor: (Int) -> MessageChannel = { id -> demux.channel(id) }

        // Built before the pump starts, so every view is registered first --
        // the demultiplexer's documented contract, and the reason these are
        // constructed here rather than inside the try below.
        val audio = CarAudioStream.of(
            profile = profile,
            connectionFor = connectionFor,
            context = applicationContext,
            // The car goes silent on Bluetooth while it is projecting, so
            // "leave media to A2DP" means no music at all. See ADR 0005.
            mediaOverAap = sessionQuirks.mediaAudioOverAap,
            projection = projection,
            onStep = ::step,
        )
        if (audio == null) {
            step("the head unit advertised no PCM audio sink, so ${ActiveTheme.name} cannot speak in the car")
        }
        // Set once voice exists. Input is built first because the voice stream
        // does not depend on it, and this closes the loop without an ordering
        // trap: the key cannot arrive before the channels are pumping anyway.
        var startListening: () -> Unit = {}
        var dropListening: () -> Boolean = { false }
        val input = CarInputStream.of(
            profile = profile,
            connectionFor = connectionFor,
            context = applicationContext,
            // A press while Jarvis is thinking or talking is "stop, listen to me": the turn
            // ends where it stands first, or the mic would open over its own reply. While
            // it asks a yes-or-no the press is the answer's mic, so the turn is kept.
            onVoiceKey = {
                carTurn?.let { t ->
                    val s = t.state.value
                    if (s == ai.aura.app.car.voice.CarTurnState.Thinking || s == ai.aura.app.car.voice.CarTurnState.Speaking) t.cancel()
                }
                startListening()
            },
            // The wheel's skip cuts Jarvis off while she talks or listens and skips the
            // track only when she is quiet (the owner, 2026-11-10). Thinking is left to
            // finish: a skip there is the driver's music, not a cancelled question.
            // The previous key, for a few seconds after she has finished, says the reply
            // again instead of restarting the song (the owner, 2026-11-11).
            onSkipKey = { previous ->
                val t = carTurn
                if (t != null && t.state.value == ai.aura.app.car.voice.CarTurnState.Speaking) {
                    t.cancel()
                    true
                } else {
                    dropListening() || (previous && t?.repeat() == true)
                }
            },
            // When the dashboard owns the car display, a touch is dispatched
            // straight into its view tree at the car's own coordinates. The
            // transform, the gesture builder and the accessibility grant are
            // all only needed to aim at somebody else's window.
            // Returns false for a touch that landed inside the live app pane,
            // which is how it reaches the gesture path instead — see
            // `CarSurface.deliver` and ADR 0010.
            deliverDirect = { touch ->
                val surface = video?.surface
                surface != null && surface.isShowing && surface.deliver(touch)
            },
            onStep = ::step,
        )
        if (input == null) {
            step("the head unit advertised no touchscreen, so car touches will not be injected")
        }
        // Registered here, with the others, and for the same reason: every view
        // must exist before `demux.pump()` starts or the first messages for a
        // channel are counted unroutable. That is how sensor traffic was being
        // discarded -- the SENSOR channel is opened on every real-car session
        // because `AapSession.connect` opens everything advertised, and nothing
        // had ever subscribed to it.
        val sensors = CarSensorStream.of(
            profile = profile,
            connectionFor = connectionFor,
            odometerScale = sessionQuirks.odometerScale,
            onStep = ::step,
        )
        if (sensors == null) {
            step("the head unit advertised no sensor service, so the car reports nothing about itself")
        }
        // What is playing, told to the car rather than only drawn on it. Without
        // this the head unit believes no media exists and answers its own
        // steering-wheel and dashboard buttons with "action unavailable" --
        // which a driver reported, and which is not a message Headway can emit.
        // See `CarMediaStatusStream`.
        val mediaStatus = CarMediaStatusStream.of(
            profile = profile,
            connectionFor = connectionFor,
            context = applicationContext,
            onStep = ::step,
        )
        if (mediaStatus == null) {
            step("the head unit advertised no media-playback service, so its own transport " +
                "buttons will have nothing to act on")
        }
        // The car's Bluetooth service: the pairing request a projecting phone sends.
        // Never sent before, and the drive logs had the phone's call and media profiles
        // down for every session and the skip buttons "unavailable". See CarBluetoothStream.
        val bluetooth = ai.aura.app.car.bluetooth.CarBluetoothStream.of(
            profile = profile,
            connectionFor = connectionFor,
            context = applicationContext,
            onStep = ::step,
        )
        // The steering-wheel button talks to Jarvis (ADR-0047): the car's microphone
        // goes to Jarvis's transcriber, the grammar acts on what it recognises, and
        // everything else is a question whose answer plays through the car.
        val config = ai.aura.app.config.JarvisConfig(applicationContext)
        // Only with a Hermes link: a phone paired to the house alone has none, and
        // a HermesClient pointed at the house's address dials a socket it lacks.
        val jarvis = if (config.hermesUrl.isNotEmpty()) HermesCarJarvis(applicationContext, config, scope, ::step) else null
        var transcriber: JarvisTranscriber? = null
        // Keyed by the voice the car speaks in: after a persona switch the drive start renders the new one's lines.
        val lines = audio?.let { voiceLines(it) { jarvis?.voice() ?: config.persona } }
        val turn = if (jarvis != null && audio != null && lines != null) {
            CarTurn(jarvis, carSpeaker(audio, lines), approve = ::approveOnTheCar, scope = scope, followUp = { startListening() }, own = ::ownSentence)
        } else {
            null
        }
        carTurn = turn
        // The drive routine (the owner, 2026-09-25: "things I ask for it to do at
        // certain times or when I start driving"): once per session, a moment after
        // the car's audio is up, Jarvis hears that the drive started. The
        // drive-routine skill decides -- the routine it was asked to remember, or
        // CarTurn.QUIET, which is never spoken.
        // New messages read aloud during the drive, with a reply offered (CarMessageAnnouncer).
        carMessages = if (turn != null && config.carReadMessages) {
            ai.aura.app.car.voice.CarMessageAnnouncer({ text ->
                // Never over Sir or Jarvis: a new ask ends the turn in flight. Wait for a
                // quiet moment, then for this one to be read before the next.
                val idle = ai.aura.app.car.voice.CarTurnState.Idle
                turn.state.first { it == idle }
                turn.ask(text, null)
                kotlinx.coroutines.withTimeoutOrNull(ANNOUNCE_START_MS) { turn.state.first { it != idle } }
                turn.state.first { it == idle }
            }, scope)
        } else {
            null
        }
        // A ringing call, said aloud with the choice asked (the owner, 2026-09-25: "getting
        // called kind of breaks it and jarvis doesnt have an option to answer"). Once per
        // call; the answer comes back through phone_action answer_call / decline_call.
        var ringing: String? = null
        callWatch = if (turn != null) ai.aura.app.car.phone.CarPhone.Listener { call ->
            val who = call?.takeIf { it.ringing }?.who
            if (who != null && who != ringing) {
                ringing = who
                turn.ask(callPrompt(who), null)
            } else if (call == null) {
                ringing = null
            }
        }.also { ai.aura.app.car.phone.CarPhone.observe(it) } else null
        // One "drive started" per drive: the head unit re-forms the link several times a
        // drive, and each session used to start the drive routine again (DriveStart).
        val newDrive = ai.aura.app.car.voice.DriveStart.isNewDrive(
            ai.aura.app.car.voice.DriveStart.lastSeen(applicationContext), System.currentTimeMillis(),
        )
        ai.aura.app.car.voice.DriveStart.markSeen(applicationContext)
        if (newDrive) {
            driveStartedMs = System.currentTimeMillis()
            noticeSaid.set(false)
        }
        if (turn != null && !newDrive) {
            step("a reconnect within ${ai.aura.app.car.voice.DriveStart.GAP_MS / 60_000} min of the last car session: the same drive, so ${ActiveTheme.name} is not told it started again")
        }
        if (turn != null) {
            // A child of this session's own scope, not the service's: a car gone within
            // the settle took its turn with it rather than asking a dead session.
            if (newDrive) launch {
                kotlinx.coroutines.delay(DRIVE_START_DELAY_MS)
                step("telling ${ActiveTheme.name} the drive started")
                turn.ask(CarTurn.DRIVE_START, null)
            }
            // Routines fire on the car's own turn, spoken there, for this session.
            ai.aura.app.routines.RoutineWatcher.carAsk = { prompt -> turn.ask(prompt, null) }
        }
        ai.aura.app.routines.RoutineWatcher.onCar(this@CarLinkService, connected = true)
        val voice = CarVoiceStream.of(
            profile = profile,
            connectionFor = connectionFor,
            context = applicationContext,
            // Media and volume are the phone's to carry out; launching an app
            // and going home are the default executor's.
            onCommand = voiceExecutor(this, audio),
            onStep = ::step,
            recognizer = jarvis?.let { link -> { rate -> JarvisTranscriber(rate, link::transcribe).also { transcriber = it } } },
            onUnrecognised = turn?.let { t ->
                { text ->
                    val heard = transcriber?.lastHeard?.transcript ?: text
                    // An approval waiting on a spoken yes or no takes the words first.
                    if (voiceAnswer?.invoke(heard) != true) t.ask(heard, transcriber?.lastHeard?.ticket, voice = true)
                }
            },
        )
        if (voice == null) {
            step("the head unit advertised no microphone, so voice is unavailable this session")
        } else {
            startListening = { voice.requestListening() }
            dropListening = { voice.dropListening() }
        }
        // Jarvis's own voice (the persona's CosyVoice from the server, as a reply is), and the
        // car's built-in speech only when that cannot be made: speed warnings, turns and
        // approvals were all said in the car's generic voice (the owner, 2026-09-27: "it
        // isnt using the right voice"). One line at a time, so two never talk over each other.
        val sayLock = kotlinx.coroutines.sync.Mutex()
        val prepared = ai.aura.app.car.voice.PreparedLines()
        // One line from the house, whole; null when it could not be made. Not the caller's
        // child: a caller that stops waiting leaves it running, and what it makes is kept.
        val rendering: (String) -> kotlinx.coroutines.Deferred<Pair<Int, ByteArray>?> = { text ->
            scope.async(kotlinx.coroutines.Dispatchers.IO) {
                var rate = 24_000
                val pcm = java.io.ByteArrayOutputStream()
                if (jarvis != null && jarvis.speak(text, onStart = { rate = it }, onPiece = { pcm.write(it) }) && pcm.size() > 0) {
                    houseSeenAtMs = android.os.SystemClock.elapsedRealtime()
                    rate to pcm.toByteArray()
                } else {
                    null
                }
            }
        }
        // [patient]: a line that is not a turn's word (the connect check's) is worth a longer wait
        // for her own voice than a turn seven seconds from its corner.
        val sayLine: ((String, Boolean) -> Unit)? = if (audio == null || lines == null) null else carSpeaker(audio, lines).let { speaker ->
            { text, patient ->
                scope.launch {
                    val asked = android.os.SystemClock.elapsedRealtime()
                    sayLock.withLock {
                        runCatching {
                            // A fixed line kept in her voice needs no house and no wait.
                            val clip = lines.clip(text)
                            if (clip != null && speaker.play(clip.second, clip.first)) {
                                step("said a line from a clip, ${android.os.SystemClock.elapsedRealtime() - asked} ms after it was asked for")
                                return@runCatching
                            }
                            // From memory when the line was prepared or said before; the house
                            // only within a deadline (PreparedLines: a turn said after the corner).
                            val from = prepared.say(
                                "${jarvis?.voice()}\n$text", text, speaker::play, speaker::say,
                                deadlineMs = if (patient) PATIENT_LINE_MS else ai.aura.app.car.voice.PreparedLines.DEADLINE_MS,
                            ) { rendering(text) }
                            // No words in the log: when it was asked for, and how long until it was playing or played.
                            step("said a line from $from, ${android.os.SystemClock.elapsedRealtime() - asked} ms after it was asked for")
                        }
                    }
                }
            }
        }
        sayOnCar = sayLine?.let { say -> { text -> say(text, false) } }
        CarShell.onSay = sayOnCar
        // The coming turns' words, voiced one at a time while there is still time (route.ts `coming`).
        val prepareLock = kotlinx.coroutines.sync.Mutex()
        CarShell.onPrepare = if (sayOnCar == null || jarvis == null) null else { texts ->
            scope.launch {
                prepareLock.withLock {
                    for (text in texts) {
                        val key = "${jarvis.voice()}\n$text"
                        if (prepared.get(key) != null) continue
                        runCatching { rendering(text).await() }.getOrNull()?.let { (rate, pcm) -> prepared.put(key, rate, pcm) }
                    }
                }
            }
        }
        // What will not work this drive, said once and only when something is wrong
        // (CarConnectCheck), so the driver is not left to find out by failure. After the
        // drive-start settle: the car page has had its chance to load and the audio
        // channels are open, so "no media channel" is not mistaken for "no grant needed".
        sayLine?.let { say ->
            // The session's child, so a car that leaves within the settle is not told
            // anything, and a reconnect is not judged on the old session's audio.
            launch {
                kotlinx.coroutines.delay(DRIVE_START_DELAY_MS)
                val home = jarvis?.let { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { it.reachable() } }
                // A house that did not answer is forgotten at once: its last answer must not hide
                // the notice for the outage that has just begun.
                houseSeenAtMs = if (home == true) android.os.SystemClock.elapsedRealtime() else if (home == false) 0L else houseSeenAtMs
                // With home down the line is about home and the page is not asked: waiting on a
                // page that cannot load would hold the outage line eight seconds for nothing.
                val console = if (home == false) null else CarConnectCheck.settled(CONSOLE_TRIES, CONSOLE_GAP_MS) {
                    kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main) { CarShell.active()?.consoleUp() }
                }
                val grantMissing = audio?.musicWaitsOnGrant == true
                val line = CarConnectCheck.line(home, console, grantMissing)
                step("connect check: home=$home console=$console grant missing=$grantMissing; ${line ?: "nothing to say"}")
                line?.let { say(it, home == true) }
                val apps = CarWifiNetwork.vpnApps(applicationContext).map { it.second }
                CarConnectCheck.remedy(home, ai.aura.app.tailnet.TailnetLink.wireGuard, apps)?.let(::tellHomeRemedy)
                if (home == true && jarvis != null && lines != null) {
                    warmVoiceLines(jarvis, lines)
                    // The offline voice, once, while the house answers (a no-op once it is here).
                    kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                        ai.aura.app.assist.BundledVoice.fetch(applicationContext, config.serverUrl, config.token)
                    }?.let { step("offline voice: $it") }
                    // AURA's own (132 MB): only for a clips-only pack, and never on a metered
                    // link: a drive usually starts on the phone's mobile data.
                    val metered = getSystemService(android.net.ConnectivityManager::class.java)?.isActiveNetworkMetered ?: true
                    if (ActiveTheme.theme.offlineFallback == ai.aura.app.assist.OfflineVoice.CLIPS_ONLY && !metered) {
                        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                            ai.aura.app.assist.BundledVoice.fetch(applicationContext, config.serverUrl, config.token, HER_VOICE)
                        }?.let { step("her offline voice: $it") }
                    }
                    // Loaded now, so the first reply's first sentence does not pay for it (T-1161).
                    val her = ai.aura.app.assist.BundledVoice
                    if (ActiveTheme.theme.offlineFallback == ai.aura.app.assist.OfflineVoice.CLIPS_ONLY &&
                        CarTurn.usesPhone(CarShell.carVoice, her.ready(applicationContext, HER_VOICE), her.realtime(applicationContext, HER_VOICE))
                    ) {
                        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) { her.open(applicationContext, HER_VOICE)?.render("Hello.") }
                    }
                }
            }
        }
        // The phone's consent prompts reach the car for this session (a text asked for
        // from the car was a no once the phone locked). Only with a car screen or a voice.
        if (sayOnCar != null || CarShell.active() != null) {
            ai.aura.app.ui.ApprovalBridge.carRoute = { title, detail, spoken -> askOnTheCar(title, detail, spoken) }
        }
        listenOnCar = { startListening() }

        // The rail's microphone. `CarShell` has no handle on the voice pipeline
        // and is constructed by `CarSurface` deep inside video bring-up, so the
        // hook is installed here, where both exist.
        CarShell.onVoiceRequested = voice?.let { stream -> { stream.requestListening() } }

        val pump = launch { demux.pump() }
        try {
            // Keepalives, on the control channel view rather than the raw
            // connection -- the pump owns the socket now. This is also the only
            // reader of the control channel, so audio focus notifications are
            // handed sideways to the audio stream from here.
            launch {
                while (true) {
                    val message = control.receive()
                    when {
                        ControlKeepalive.isPing(message) -> {
                            // Timed, because the answer being late is the one
                            // way this loop can kill a session, and it is
                            // invisible from the cabin. A head unit pings on a
                            // timer and resets the connection when the answers
                            // stop -- which is what three ends in the
                            // 2026-08-19 drive log look like: a reset or a
                            // broken pipe, and no BYEBYE_REQUEST with any of
                            // them. If this number is ever seconds, that is the
                            // answer; if the count stays zero, this unit does
                            // not ping and the theory is dead. Either reading
                            // is worth more than the silence there was before.
                            val started = System.nanoTime()
                            // Split, because the two halves have different
                            // causes and different fixes: time spent queued is
                            // a reader that was not running, time spent in
                            // `answer` is the wire and the lock in front of it.
                            // A single figure that happened to be small would
                            // clear the phone on the strength of a measurement
                            // that never looked at the slow half.
                            val arrived = demux.lastControlArrivalNanos
                            val queued = if (arrived == 0L) 0L else (started - arrived) / 1_000_000
                            ControlKeepalive.answer(control, message, connection.cryptor != null)
                            val took = (System.nanoTime() - started) / 1_000_000
                            pingsAnswered++
                            if (took > worstPingSendMillis) worstPingSendMillis = took
                            if (queued > worstPingQueueMillis) worstPingQueueMillis = queued
                            val total = queued + took
                            if (total > worstPingAnswerMillis) worstPingAnswerMillis = total
                        }

                        audio?.onControlMessage(message) == true -> Unit

                        // The car's reason for ending the session, which the 1457 drives
                        // ended with four times and never said why: USER_SELECTION is the
                        // driver (or the unit's own menu), DEVICE_SWITCH another phone.
                        message.messageId == ControlMessageType.BYEBYE_REQUEST.id -> step(
                            "control: BYEBYE_REQUEST, reason " + runCatching {
                                aap_protobuf.service.control.message.ByeByeRequestOuterClass.ByeByeRequest
                                    .parseFrom(message.payload).reason.name
                            }.getOrDefault("unreadable (${message.payload.size} bytes)")
                        )

                        else -> step(
                            "control: ${ControlMessageType.describe(message.messageId)} " +
                                "(${message.payload.size} bytes)"
                        )
                    }
                }
            }

            // Video is started first and inline; see the KDoc. The other three
            // return as soon as they have launched their own coroutines.
            //
            // Every one of them is wrapped, and that is not defensive
            // boilerplate — it is the amplifier that turned a one-line bug into
            // a dead link. On 2026-08-14 building the car surface threw
            // (a `Presentation` constructed off the main thread; see
            // `CarSurface`), the exception escaped this bare call, unwound
            // straight past everything below to the `finally`, and took the
            // session down 21 ms after it came up. Video is optional; the
            // session is not. A subsystem that will not start must cost only
            // itself, and must say so.
            startSubsystem("video") { video?.start(this) }
            if (video?.surface != null) {
                // The car has its own display and its own UI on it, and nothing
                // is put on the phone: no activity is started, no window is
                // added, and the driver's screen is theirs. That is a
                // requirement rather than a nicety — a session that comes up on
                // its own when the car appears must not take the phone over.
                step("car screen is ${ActiveTheme.name}'s own display; the phone screen is left alone")
                // The one projection this grant allows now belongs to the app
                // pane. In the fallback path it belongs to the encoder instead,
                // which is why this is inside the branch.
                AppPaneHost.attach(
                    context = applicationContext,
                    projection = projection,
                    densityDpi = video.negotiated?.densityDpi ?: 160,
                    onStep = ::step,
                )
                if (projection == null) offerScreenSharing()
            } else {
                step(
                    "no car display, so the car is being sent a raw capture of the phone " +
                        "screen: no panes, no rail, no app pane"
                )
            }
            // Now, in case a measurement already arrived, and again when one
            // does: the capture reports its real size with the first frames,
            // which is after everything here has finished starting.
            AppPaneHost.onSharingKnown = { single ->
                // Arrives on the projection's callback handler, which is the main
                // looper -- `AppPaneHost` registers the callback with one -- so
                // adding the overlay window from here is already on the right
                // thread.
                if (single || AppPaneHost.sharingOtherDisplay) {
                    runCatching { coverPhoneScreen() }
                        .onFailure { step("the phone screen could not be covered: $it") }
                } else {
                    // The other half, and the one that was missing. A cover is
                    // not only raised at bring-up: it survives a grant being
                    // lost, because the link stays up and the session's teardown
                    // -- the only other place that uncovers -- never runs. So a
                    // driver who shared one app, locked the phone, and then
                    // re-shared as "Entire screen" had the *cover* recorded, and
                    // the car showed a black rectangle while frames kept
                    // arriving. Whatever raised it, a measurement that says
                    // "whole display" takes it down.
                    runCatching { HeadwayAccessibilityService.instance.value?.hideBlackout() }
                    refreshNotification()
                }
            }
            // A lost grant has to reach the field the service holds, or nothing
            // ever offers screen sharing again. See `AppPaneHost.onGrantLost`.
            projectionNotice.carConnected(true)
            AppPaneHost.onGrantLost = {
                projection = null
                grantLost()
                // Media capture is built on the same grant, so it stops too --
                // and the stream goes back to waiting rather than ending, so
                // re-sharing brings music back without a reconnect.
                runCatching { CarAudioStream.current?.adoptProjection(null) }
                step(
                    "screen sharing ended -- on Android 15 and later the platform stops it " +
                        "whenever the phone locks, and no app can opt out. Tap the " +
                        "notification to start it again",
                )
                runCatching { offerScreenSharing() }
            }
            // On the main looper, not on this coroutine. `showBlackout` calls
            // `WindowManager.addView`, which builds a `ViewRootImpl` and so a
            // `Handler`, and this runs on `Dispatchers.Default` -- a pool thread
            // with no `Looper`. The throw was swallowed by the `runCatching`, so
            // the cover simply never appeared on this path and the log said only
            // that it could not be covered. The `onSharingKnown` call site above
            // is already on the right thread, which is why it works and this did
            // not.
            Handler(Looper.getMainLooper()).post {
                runCatching { coverPhoneScreen() }
                    .onFailure { step("the phone screen could not be covered: $it") }
            }
            // Sideways for the drive, if the driver asked. This is the only
            // unprivileged way a mirrored app can be made to *lay itself out*
            // wide rather than be scaled down into a strip; see `PhoneRotation`
            // for the four closed routes and the one honest limit.
            if (HeadwaySettings.landscapeApps(this@CarLinkService)) {
                runCatching { PhoneRotation.applyLandscape(this@CarLinkService) { step(it) } }
            }
            startSubsystem("audio") { audio?.start(this) }
            startSubsystem("input") { input?.start(this) }
            startSubsystem("voice") { voice?.start(this) }
            // Last, and isolated like the rest. Sensors are the one subsystem
            // whose complete absence costs the driver nothing but a pane that
            // says the car is quiet.
            startSubsystem("sensors") { sensors?.start(this) }
            startSubsystem("media status") { mediaStatus?.start(this) }
            startSubsystem("bluetooth") { bluetooth?.start(this) }

            pump.join()
        } catch (t: Throwable) {
            // Kept so the teardown can name the real reason. Without it
            // `closeAll()` closes every channel with a null cause, the
            // demultiplexer synthesises "channel CONTROL closed: link ended",
            // and that invented sentence is what the log and the reconnect
            // banner report — indistinguishable from the head unit hanging up,
            // which is exactly how the last drive was misread.
            // The *first* cause wins, and a cancellation never overwrites one.
            //
            // `runSession` is a scope with children, so a child failing --
            // the demultiplexer's read hitting `Connection reset`, say --
            // cancels the scope, and the next suspension point here throws
            // `CancellationException`. Recording that as the failure replaced
            // the real reason with "is cancelling", which in a release build is
            // a minified class name and says nothing at all. A drive log has
            // five session ends, every one of them reported that way, while the
            // supervisor's own line beside it named `Connection reset`,
            // `Broken pipe` and `peer closed` -- the answers that were wanted.
            val cancelled = t is kotlinx.coroutines.CancellationException
            val heldIsCancellation =
                sessionFailure is kotlinx.coroutines.CancellationException
            if (sessionFailure == null || (heldIsCancellation && !cancelled)) {
                sessionFailure = t
            }
            throw t
        } finally {
            // Every describe() before every stop(): the counts are what a log
            // from a real drive is read for, and one of these throwing must not
            // cost the lines after it.
            video?.let { runCatching { step(it.describe()) } }
            // The car surface's own numbers, which nothing printed. Its
            // `describe` existed and had no caller, so no drive log has ever
            // said whether a car touch actually reached the dashboard --
            // "the head unit sent nothing", "they went to the shared app" and
            // "they were dispatched and the view tree ignored them" all read
            // identically. That last one is what a rail whose tabs have gone
            // dead looks like from here.
            video?.surface?.let { runCatching { step(it.describe()) } }
            audio?.let { runCatching { step(it.describe()) } }
            // Taken here, before anything is stopped; printed by the supervisor's session-ended line.
            runCatching {
                val (sinks, focus, drop) = audio?.resetFacts() ?: Triple(emptyList(), null, null)
                val now = SystemClock.elapsedRealtime()
                val bluetooth = profileClock.describe(now)
                resetReport = { cause -> ResetReport.line(cause, sinks, focus, drop, bluetooth, now) }
            }
            input?.let { runCatching { step(it.describe()) } }
            voice?.let { runCatching { step(it.describe()) } }
            sensors?.let { runCatching { step(it.describe()) } }
            mediaStatus?.let { runCatching { step(it.describe()) } }
            // Both counters were kept and neither was ever printed. A frame the
            // session had nowhere to put, or evicted from a full queue, is
            // exactly the kind of thing a driver's log has to be able to show --
            // "the car never sent it" and "Jarvis threw it away" look identical
            // from the cabin.
            runCatching {
                val unroutable = demux.unroutableMessages
                val dropped = demux.droppedMessages
                if (unroutable > 0L || dropped > 0L) {
                    // Named per channel. "38 evicted" was a number nobody could
                    // act on: video the driver never missed and control traffic
                    // the head unit was waiting on read identically.
                    val where = demux.droppedByChannel.entries
                        .sortedByDescending { it.value }
                        .joinToString(", ") { (id, count) ->
                            "${channelNames[id] ?: ChannelId.describe(id)} $count"
                        }
                    step(
                        "frames: $unroutable arrived for a channel this session never opened, " +
                            "$dropped were evicted from a full channel queue" +
                            if (where.isEmpty()) "" else " ($where)"
                    )
                }
            }
            runCatching {
                // The car's own budget, so the number above can be read against
                // something instead of admired. A slowest answer past the
                // announced timeout is not a slow session, it is the reason the
                // session ended: the protocol has STATUS_PING_TIMEOUT for it,
                // and a unit that gives up sends a reset rather than a byebye.
                val budget = profile.pingConfiguration
                val timeout = budget?.takeIf { it.hasTimeoutMs() }?.timeoutMs
                val slow = budget?.takeIf { it.hasHighLatencyThresholdMs() }?.highLatencyThresholdMs
                val verdict = when {
                    pingsAnswered == 0L ->
                        "; this unit did not ping at all, so a late answer cannot be why a " +
                            "session ended"
                    timeout != null && worstPingAnswerMillis >= timeout ->
                        "; that is past the ${timeout} ms this unit allows, which is enough on " +
                            "its own to explain a session the car reset without a byebye"
                    slow != null && worstPingAnswerMillis >= slow ->
                        "; over the ${slow} ms this unit calls slow, though inside its timeout"
                    else -> ""
                }
                step(
                    "keepalive: $pingsAnswered ping(s) from the car answered, " +
                        "slowest answer took $worstPingAnswerMillis ms " +
                        "($worstPingQueueMillis ms waiting to be read, " +
                        "$worstPingSendMillis ms writing the reply)$verdict"
                )
            }
            // Written here, at the end of the session, while every line from
            // the drive is still in the buffer. See `SessionLog.exportSession`.
            runCatching {
                val written = SessionLog.shared.exportSession(
                    applicationContext,
                    sessionFailure?.let { failure ->
                        val cause = generateSequence(failure) { link ->
                            link.cause?.takeIf { it !== link }
                        }.take(CAUSE_DEPTH).lastOrNull() ?: failure
                        val named = cause.message?.takeIf { it.isNotBlank() }
                            ?: cause.javaClass.simpleName
                        "ended: $named"
                    } ?: "ended cleanly",
                )
                written?.let { ai.aura.app.car.log.CarLogUpload.send(applicationContext, it) }
            }
            runCatching { CarShell.onVoiceRequested = null }
            runCatching { AppPaneHost.onSharingKnown = null }
            runCatching { AppPaneHost.onGrantLost = null }
            // Before anything below can end the grant: a car that went first is not told.
            projectionNotice.carConnected(false)
            runCatching { AppPaneHost.detach() }
            runCatching { HeadwayAccessibilityService.instance.value?.hideBlackout() }
            // Before the subsystems, because this is the driver's phone rather
            // than Headway's and a session that ends badly must still give it
            // back. No-op unless a rotation was actually taken.
            runCatching { PhoneRotation.restore(this@CarLinkService) { step(it) } }
            runCatching { video?.stop() }
            runCatching { audio?.stop() }
            runCatching { input?.stop() }
            runCatching { voice?.stop() }
            // A turn in flight ends with the session; nothing is re-spoken after a
            // reconnect (CarTurn).
            runCatching { carMessages?.stop() }
            carMessages = null
            sayOnCar = null
            ai.aura.app.ui.ApprovalBridge.carRoute = null
            ai.aura.app.routines.RoutineWatcher.carAsk = null
            ai.aura.app.routines.RoutineWatcher.onCar(this@CarLinkService, connected = false)
            ai.aura.app.car.voice.DriveStart.markSeen(applicationContext)
            ai.aura.app.car.voice.Parked.remember(applicationContext)
            // The drive ends when no session returns for DriveStart.GAP_MS, not when one drops.
            val endedAt = ai.aura.app.car.voice.DriveStart.lastSeen(applicationContext)
            if (jarvis != null && ai.aura.app.car.voice.DriveEnd.isRealDrive(driveStartedMs, endedAt)) {
                scope.launch {
                    kotlinx.coroutines.delay(ai.aura.app.car.voice.DriveStart.GAP_MS + 1_000L)
                    if (ai.aura.app.car.voice.DriveEnd.stillOver(endedAt, ai.aura.app.car.voice.DriveStart.lastSeen(applicationContext))) {
                        ai.aura.app.car.voice.DriveEnd.tell(applicationContext, scope)
                    }
                }
            }
            CarShell.onSay = null
            CarShell.onPrepare = null
            listenOnCar = null
            voiceAnswer = null
            callWatch?.let { runCatching { ai.aura.app.car.phone.CarPhone.unobserve(it) } }
            callWatch = null
            runCatching { carTurn?.cancel() }
            carTurn = null
            runCatching { jarvis?.close() }
            runCatching { sensors?.stop() }
            runCatching { mediaStatus?.stop() }
            runCatching { bluetooth?.stop() }
            demux.closeAll(sessionFailure)
            sessionFailure = null
        }
    }

    /**
     * Covers the phone screen for the drive, if the driver asked for it.
     *
     * Answers "can I hide the preview window Developer options puts up?" — yes,
     * but only by covering it, and only from the accessibility service, whose
     * `TYPE_ACCESSIBILITY_OVERLAY` outranks the preview's `TYPE_DISPLAY_OVERLAY`
     * in the window layer table. `SYSTEM_ALERT_WINDOW` does not: it is eleven
     * layers below. See `HeadwayAccessibilityService.showBlackout`.
     *
     * Silent when the switch is off, which is the default. When it is on but
     * accessibility is not granted, it says so — the alternative is a switch
     * that appears to do nothing.
     */
    private fun coverPhoneScreen() {
        if (!HeadwaySettings.of(this).getBoolean(HeadwaySettings.KEY_BLANK_PHONE_SCREEN, true)) {
            return
        }
        // Something on the phone is being looked at deliberately. Covering it
        // now is how "That widget was not added" happened: the system's own
        // "Allow Jarvis to add widgets?" dialog was drawn under an opaque
        // accessibility overlay, so the driver saw a black screen, and the
        // dialog finishes itself as CANCELED on the first pause it sees.
        if (phoneHolds.get() > 0) {
            step("the phone screen is being used, so it is left uncovered")
            return
        }
        // The safety gate, and the reason this can be on by default. A blackout
        // is invisible to a single-app capture and fatal to a display capture,
        // and which one is running is measured rather than asked -- see
        // `AppPaneHost.sharingSingleApp`. Until the first measurement arrives the
        // answer is "not yet", so the cover waits for `onSharingKnown` instead of
        // guessing, and a session sharing the whole display never gets one.
        // Two safe cases, not one. A capture of a single *app* excludes system
        // UI, so a blackout is invisible to it; a capture of the *simulated
        // display* records a display the blackout is not on. Only a capture of
        // display 0 is ruined by covering display 0, and that is the case this
        // now refuses rather than refusing both of the others with it -- which
        // is why the Developer-options preview window was still visible on a
        // simulated-display session the README said it would not be.
        if (!AppPaneHost.sharingSingleApp && !AppPaneHost.sharingOtherDisplay) return
        // Compatible with single-app sharing, and not with whole-display
        // sharing. The blackout is a window on display 0: a capture of the
        // *display* captures it, and the app pane would show a perfectly black
        // rectangle while every diagnostic reported frames arriving. A capture
        // of a single *app* does not -- Android excludes "the status bar,
        // navigation bar, notifications, and other system UI elements" from app
        // screen sharing, and an accessibility overlay is none of the app's
        // window either.
        //
        // That distinction is what makes a dark phone possible at all: the
        // screen has to stay on for the shared app to keep drawing, so the best
        // available is a screen that is on and black. Headway cannot tell which
        // kind of sharing the driver chose, so it defers to them: the setting is
        // off by default and its description says it belongs with single-app
        // sharing.
        val service = HeadwayAccessibilityService.instance.value
        if (service == null) {
            step(
                "\"blank the phone screen\" is on, but it needs the accessibility service and " +
                    "that is not granted, so the phone screen is left alone"
            )
            return
        }
        service.showBlackout()
        // The "Show phone screen" action is built from `covering`, which was
        // false when the notification was last posted -- every notification so
        // far in this session predates the cover. Without this re-post the only
        // way back to the phone is the car screen's settings sheet, which is no
        // way back at all for a driver who has left the car.
        refreshNotification()
    }

    /**
     * Starts one optional subsystem, and lets it fail alone.
     *
     * Returns whether it started. The failure is narrated rather than thrown:
     * the session is worth more than any one of these, and a driver whose
     * microphone will not open should still have a car screen.
     */
    private inline fun startSubsystem(name: String, start: () -> Unit): Boolean =
        runCatching { start() }.fold(
            onSuccess = { true },
            onFailure = { failure ->
                step("$name did not start, and the session continues without it: $failure")
                false
            },
        )

    /**
     * What a recognised voice command actually does.
     *
     * [CarVoiceStream.defaultExecutor] already launches apps and returns to the
     * launcher, and logs the other three as needing a handler — correctly, since
     * `core-voice` cannot depend on Android. This supplies two of the three.
     *
     * `Search` stays unhandled, and that is a real limitation rather than an
     * oversight: typing into another app's field needs the focused node, which
     * needs `canRetrieveWindowContent`, which
     * `accessibility_service_config.xml` sets to false as a promise to the user
     * that Headway injects input but never reads the screen. Honouring that
     * promise costs dictation into third-party apps. Tracked in `BLOCKERS.md`.
     */
    private fun voiceExecutor(
        scope: CoroutineScope,
        audio: CarAudioStream?,
    ): (VoiceCommand) -> Unit {
        val media = PhoneMediaControl(applicationContext, ::step)
        val fallback = CarVoiceStream.defaultExecutor(applicationContext, ::step)

        // Spoken back over the car's guidance channel, which is the whole point
        // of having one. Launched rather than awaited: the command should happen
        // now, and the acknowledgement is allowed to arrive a moment later.
        // Failure is logged by CarAudioStream and never reaches the driver as
        // anything worse than silence.
        fun say(text: String) {
            // In JARVIS's own voice when the server can make it; the phone's TTS otherwise,
            // except for a pack that lets no other voice speak (AURA §3, OfflineVoice).
            sayOnCar?.let { return it(text) }
            if (ActiveTheme.theme.offlineFallback == ai.aura.app.assist.OfflineVoice.CLIPS_ONLY) return
            val stream = audio ?: return
            if (!stream.canSpeak) return
            scope.launch { runCatching { stream.speak(text) } }
        }

        return { command ->
            when (command) {
                is VoiceCommand.Media -> {
                    media.perform(command.action)
                    // Deliberately terse. A driver who said "pause" wants to
                    // know it landed, not to sit through a sentence.
                    say(JarvisVoiceLines.said(command.action))
                }

                is VoiceCommand.Volume -> media.adjust(command.direction, command.steps)

                is VoiceCommand.LaunchApp -> {
                    fallback(command)
                    if (command.packageName == null) say("I could not find ${command.query}")
                }

                is VoiceCommand.Navigate -> {
                    fallback(command)
                    say("Navigating to ${command.destination}")
                }

                is VoiceCommand.Unrecognised -> say(JarvisVoiceLines.UNHEARD)

                else -> fallback(command)
            }
        }
    }

    /** BLUETOOTH_CONNECT is [connect]'s to hold, as its KDoc says: a revoke throws here and it becomes a link failure. */
    @SuppressLint("MissingPermission")
    private fun resolveCar(adapter: BluetoothAdapter): BluetoothDevice {
        // Logged on every attempt, not just failures. A head unit that stops
        // advertising the service between attempts is a real and confusing
        // failure mode, and it is only visible if the record is captured each
        // time rather than once at startup.
        runCatching { BluetoothCarLink.describeBondedDevices(adapter) }
            .onSuccess { Log.i(TAG, it) }
        runCatching { BluetoothCarLink.describeProfileState(adapter) }
            .onSuccess { step(it) }

        val address = carAddress
        if (address != null) {
            return runCatching { adapter.getRemoteDevice(address) }.getOrElse {
                throw IllegalArgumentException("'$address' is not a Bluetooth address", it)
            }
        }
        return BluetoothCarLink.bondedCars(adapter).firstOrNull()
            ?: throw IllegalStateException(
                "no paired device advertises the Android Auto wireless service; " +
                    "pair the car in Settings, then pick it in ${ActiveTheme.name}"
            )
    }

    // --- notification -------------------------------------------------------

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Car link",
            // LOW: the notification exists because the platform requires one for
            // a foreground service, not because the user wants to be interrupted
            // every time the car comes back into range.
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Shows whether ${ActiveTheme.name} is connected to the car."
            setShowBadge(false)
        }
        notificationManager?.createNotificationChannel(channel)
        // A second channel, and it has to be a second one: importance is a
        // property of the channel, not of the notification, so PRIORITY_HIGH on
        // an IMPORTANCE_LOW channel does nothing at all. The ongoing service
        // notification must stay quiet -- it is there because the platform
        // demands one -- while "the car cannot show apps or play music until
        // you allow this" is worth one heads-up.
        val ask = NotificationChannel(
            ASK_CHANNEL_ID,
            "Screen sharing needed",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description =
                "Asks for the screen-sharing grant that music and app panes need."
            setShowBadge(true)
        }
        notificationManager?.createNotificationChannel(ask)
    }

    /**
     * Takes the screen-capture grant out of the start intent, if there is one.
     *
     * ## The ordering Android 14 enforces, and why it is done here
     *
     * `getMediaProjection` throws unless a foreground service with the
     * `mediaProjection` type is *already* running, and `startForeground` throws
     * if that type is claimed without a live projection token. The two
     * requirements point at each other, and the only way through is this order:
     * re-enter the foreground with the type first, then redeem the token. That
     * is why the grant is adopted in [onStartCommand] rather than lazily when
     * video starts -- by then the service has been foreground for a while under
     * the wrong type, and the transition is a `stopForeground`/`startForeground`
     * churn that drops the notification.
     *
     * A refused or absent grant is not an error. The car link is worth having on
     * its own -- it is how a first connection gets diagnosed -- so the session
     * runs without video and says so once, rather than failing.
     */
    private fun adoptProjectionGrant(intent: Intent?) {
        val data = intent?.let {
            @Suppress("DEPRECATION")
            it.getParcelableExtra<Intent>(EXTRA_PROJECTION_DATA)
        } ?: return
        val resultCode = intent.getIntExtra(EXTRA_PROJECTION_RESULT, android.app.Activity.RESULT_CANCELED)
        if (resultCode != android.app.Activity.RESULT_OK) {
            step("screen capture was not granted; this session will connect without video")
            return
        }

        // The type first. See the KDoc above: the order is not a preference.
        //
        // A ladder, and this is the bug a drive log caught. The claim used to
        // name the location type alongside `mediaProjection` in one call.
        // Android refused the location type -- as it usually does, see
        // [claimForeground] -- the whole call threw, and the grant the driver
        // had just tapped through was dropped on the floor. The car then had no
        // video and, because `AudioPlaybackCapture` is built from this same
        // projection, no music at all. Video and music do not depend on
        // location, so they no longer fall with it.
        val withProjection = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
        val text = describe(mutableLinkState.value)
        val claimed = claimForeground(
            text,
            withProjection or locationTypeIfWanted(),
            withProjection,
        )
        if (claimed == null) {
            step("could not claim the mediaProjection foreground type. " +
                "This session will connect without video")
            return
        }
        reportLostLocation(claimed)

        val manager = getSystemService(android.media.projection.MediaProjectionManager::class.java)
        projection = runCatching { manager?.getMediaProjection(resultCode, data) }
            .getOrElse {
                step("Android refused the screen capture grant: ${it.message}. " +
                    "This session will connect without video")
                null
            }
        val granted = projection ?: return
        step("screen capture granted; video will start with the session")
        watchProjection(granted)
        grantWasLost = false
        projectionNotice.granted()

        // Music too, and it is not obvious: media audio reaches the car over the
        // AAP media channel captured from the phone's own playback (ADR 0005),
        // and `AudioPlaybackCaptureConfiguration` is built from a
        // `MediaProjection`. So the screen-sharing grant is what makes the car
        // play music at all, and a session that came up without one was silent
        // until it was restarted. It is no longer: the audio stream waits for a
        // grant rather than giving up at start-up.
        runCatching { CarAudioStream.current?.adoptProjection(granted) }

        // If a session is already up -- which is now the normal case, because a
        // link that comes up on its own has no grant to travel with it -- the
        // app panes are sitting there saying screen sharing is off. Attach the
        // grant to them straight away rather than making the driver disconnect
        // and reconnect to use it.
        val live = CarVideoStream.current?.takeIf { it.surface != null } ?: return
        runCatching {
            AppPaneHost.attach(
                context = applicationContext,
                projection = granted,
                densityDpi = live.negotiated?.densityDpi ?: 160,
                onStep = ::step,
            )
        }.onSuccess { step("app panes can now show a running app") }
    }

    // Offers the one tap that turns app panes on, from the notification shade.
    //
    // A session that came up on its own has no screen-capture grant, because
    // consent needs an activity and nothing started one. The car screen says so
    // in the app pane, but the driver is holding a locked phone and the fix is
    // on it — so it is offered where their thumb already is.
    //
    // Deliberately not high priority and deliberately not repeated: the car
    // works without it, and a driver who does not use app panes should not be
    // asked twice.
    /**
     * Notices when Android takes the screen-capture grant away.
     *
     * ## The hole this fills, from a drive log
     *
     * A capture of a real drive has, at the moment the platform ended the
     * projection:
     *
     *     V MediaProjection: Dispatch stop to 0 callbacks.
     *
     * Zero. Nothing in this process was listening. `AppPaneHost` registers a
     * callback, but only when an *app pane* attaches -- and that driver's
     * layout is maps, playing, sensors and notifications, with no app pane in
     * it. So the grant died on a lock screen, and the one thing that actually
     * depended on it, music, went silent with nothing anywhere noticing. In
     * that log it stayed silent for four and a half minutes.
     *
     * The projection is used for two different things and only one of them was
     * watching it. Media capture is built from the same grant
     * (ADR 0005), needs it just as much, and exists whether or not any pane
     * shows an app -- so the service watches it too, for as long as it holds
     * one.
     *
     * `onStop` arrives on [main] because the callback was registered with that
     * handler, and everything it touches expects the main looper.
     */
    private fun watchProjection(granted: android.media.projection.MediaProjection) {
        runCatching { projectionWatch?.let { granted.unregisterCallback(it) } }
        val watch = object : android.media.projection.MediaProjection.Callback() {
            override fun onStop() {
                // Only for the grant still held. A callback from a projection
                // that has already been replaced must not clear its successor.
                if (projection !== granted) return
                projection = null
                grantLost()
                step(
                    "screen sharing ended -- Android stops it whenever the phone locks, and no " +
                        "app can opt out. Music is captured from it, so the car goes quiet until " +
                        "it is allowed again",
                )
                runCatching { CarAudioStream.current?.adoptProjection(null) }
                runCatching { AppPaneHost.onGrantLost?.invoke() }
                runCatching { offerScreenSharing() }
            }
        }
        runCatching { granted.registerCallback(watch, main) }
        projectionWatch = watch
    }

    /**
     * Says once, on the car, that the grant stopped and how to give it again (T-1082). Called
     * from every path that finds the loss out; [ProjectionLossNotice] makes the rest no-ops.
     */
    private fun grantLost() {
        grantWasLost = true
        projectionNotice.stopped()
    }

    /** The grant that ended is why the shade asks again; it changes the ask's words. */
    private var grantWasLost = false

    private val projectionNotice = ai.aura.app.car.video.ProjectionLossNotice { text ->
        sayOnCar?.invoke(text)
    }

    /** The service's own projection callback; see [watchProjection]. */
    private var projectionWatch: android.media.projection.MediaProjection.Callback? = null

    /** Where [watchProjection]'s callback is delivered. */
    private val main = Handler(Looper.getMainLooper())

    /** Whether this process has tried [ProjectionAppop.ensure]: once is enough, it is for good. */
    private var appopTried = false

    private fun offerScreenSharing() {
        // Before the ask: with PROJECT_MEDIA held the ask below completes with no dialog.
        // ponytail: a short shell call on the main thread, once per process.
        if (!appopTried) {
            appopTried = true
            step(ai.aura.app.car.video.ProjectionAppop.ensure(this))
        }
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val tap = android.app.PendingIntent.getActivity(
            this,
            PROJECTION_REQUEST_CODE,
            Intent(this, ai.aura.app.car.video.ProjectionRequestActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            android.app.PendingIntent.FLAG_IMMUTABLE or
                android.app.PendingIntent.FLAG_UPDATE_CURRENT,
        )
        // Tried directly first, because a notification in the shade is not a
        // prompt a driver reads. A session Headway started by itself -- the car
        // came into range and it connected -- reaches this point with no grant
        // and, until now, only a silent PRIORITY_DEFAULT notification to say
        // so. The driver's report was that they had to disconnect and reconnect
        // by hand to get screen sharing, which is exactly what "I never saw the
        // notification" looks like.
        //
        // Not guaranteed, and deliberately not treated as though it were:
        // Android blocks activity starts from the background, and the
        // exemptions that apply here -- the app was foreground recently, which
        // is the ordinary case of somebody getting into a car having just used
        // their phone -- are the platform's to judge, not something an app can
        // test for. A blocked start is dropped silently rather than throwing,
        // so there is nothing to catch and nothing to report. The notification
        // below is posted either way and is the answer when it does not work.
        runCatching {
            startActivity(
                Intent(this, ai.aura.app.car.video.ProjectionRequestActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            )
        }
        val notification = NotificationCompat.Builder(this, ASK_CHANNEL_ID)
            .setColor(Notify.color(Notify.Template.ASK))
            .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
            .setContentTitle(
                if (grantWasLost) "Screen sharing stopped" else "Show apps and play music on the car",
            )
            // Music is the surprising half and the half a driver notices first.
            // Media audio goes over the AAP media channel captured from the
            // phone's own playback, and Android only allows that capture through
            // a screen-sharing grant -- so without this tap the car is silent,
            // which is nothing like what "screen sharing" sounds like it means.
            .setContentText(
                if (grantWasLost) "Android ends it when the phone locks. Tap to share again."
                else "Tap to allow screen sharing — music needs it too",
            )
            .setStyle(
                NotificationCompat.BigTextStyle().bigText(
                    // The fix that survives a lock is the adb line in adb-setup.txt (the CI's
                    // artifact); the tap is the fix for now.
                    (if (grantWasLost) {
                        "Android ends screen sharing whenever the phone locks. Unlock and tap to " +
                            "share again; to keep it through a lock, run the PROJECT_MEDIA line " +
                            "in adb-setup.txt once from a computer. "
                    } else "") +
                    "${ActiveTheme.name} sends your music to the car by capturing what the phone is " +
                        "playing, and Android only allows that with a screen-sharing grant. " +
                        "Tap to allow it, for music and for showing apps on the car screen.",
                ),
            )
            // High rather than default: this is the difference between the car
            // showing apps and playing music, and not. At default it sat
            // silently in the shade behind the ongoing service notification.
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(tap)
            .build()
        runCatching { manager.notify(PROJECTION_NOTIFICATION_ID, notification) }
        step("no screen-sharing grant this session; offered it in the notification shade")
    }

    /**
     * Claims the foreground types, keeping any this service has already earned.
     *
     * `Service.startForeground` **replaces** the type set rather than adding to
     * it: `ActiveServices.setServiceForegroundInnerLocked` assigns
     * `r.foregroundServiceType`. So a later call naming only
     * `CONNECTED_DEVICE` drops `MEDIA_PROJECTION`, and on Android 14 and later
     * the platform ends a `MediaProjection` as soon as the app stops running a
     * foreground service of that type -- the grant dies, `onGrantLost` fires,
     * and every app pane says screen sharing is off for the rest of the drive.
     *
     * This is the first statement of `onStartCommand`, so *every* delivered
     * intent went through it -- including the "Show phone screen" action, which
     * is reached from the notification and from the car screen's settings sheet
     * mid-drive. Tapping "uncover my phone" would have killed the video.
     */
    private fun startForegroundNow() {
        val link = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
        val projecting = if (projection != null) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
        } else {
            0
        }
        // Everything wanted, then each rung the service can still work without.
        // The last rung is the link on its own, because that is the one type
        // this service always has a right to and the one it cannot run without.
        val claimed = claimForeground(
            notificationText,
            link or projecting or locationTypeIfWanted(),
            link or projecting,
            link,
        )
        reportLostLocation(claimed ?: return)
        if (projecting != 0 && claimed and projecting == 0) {
            // Android had already ended the projection -- on 15 and later it
            // does that unconditionally when the phone locks -- and the stale
            // token is what made the platform throw. Letting go of it here is
            // what stops the next intent throwing too, and it routes through
            // the same path a grant loss normally takes, so the audio stream
            // goes back to waiting and the shade offers sharing again.
            step("the screen-sharing grant is no longer live, so it has been let go")
            grantLost()
            runCatching { AppPaneHost.onGrantLost?.invoke() }
            projection = null
        }
    }

    /**
     * Claims the first set of foreground-service types the platform will accept.
     *
     * ## Why a ladder, and not one call
     *
     * `ActiveServices.validateForegroundServiceType` does not merely *refuse* a
     * type it will not grant -- it **throws**, and `Service.startForeground`
     * hands that back as a `SecurityException`. So a single call naming three
     * types is all-or-nothing, and a refusal of the least important one takes
     * the other two with it.
     *
     * Both halves of that cost the driver a real drive. The screen-capture
     * grant was thrown away -- no video, and no music either, since
     * `AudioPlaybackCapture` is built from the same projection -- because the
     * *location* type was refused in the same call. And a call that named
     * `mediaProjection` after Android had quietly ended the projection killed
     * the process outright, from the first statement of `onStartCommand`.
     *
     * ## Why it cannot be predicted instead
     *
     * The location policy does not ask "is the permission granted". An ordinary
     * while-using-the-app grant is `MODE_FOREGROUND`, which
     * `ForegroundServiceTypePolicy`'s location policy treats as denied unless
     * *this particular* service start was while-in-use eligible -- a property
     * of how the service was started, latched at entry, that no
     * `checkSelfPermission` can report. Trying it is the only honest test.
     *
     * Returns the set that stuck, or null if every rung was refused -- the one
     * case where this service genuinely cannot be in the foreground, and the
     * caller has to decide what to do without one.
     */
    private fun claimForeground(text: String, vararg ladder: Int): Int? {
        var lastRefusal: Throwable? = null
        // Distinct, because the rungs collapse into each other when the
        // optional types are not wanted -- with no projection and no location,
        // all three rungs are "the link on its own" and retrying a refusal
        // twice more would prove nothing.
        for (types in ladder.distinct()) {
            val outcome = runCatching {
                ServiceCompat.startForeground(
                    this,
                    NOTIFICATION_ID,
                    buildNotification(getString(R.string.app_name), text),
                    types,
                )
            }
            if (outcome.isSuccess) return types
            lastRefusal = outcome.exceptionOrNull()
            // Once per distinct type set. This runs from the first statement of
            // `onStartCommand`, so every notification button and every car-screen
            // action reaches it -- reported every time, one refused type would
            // fill a drive's log with the same paragraph.
            if (reportedRefusals.add(types)) {
                step("Android refused the foreground service type(s) " +
                    "${describeForegroundTypes(types)}: ${lastRefusal?.message}")
            }
        }
        step("Android refused every foreground service type this service can run under. " +
            "It cannot stay in the foreground, so the car link will not survive the screen " +
            "going off: ${lastRefusal?.message}")
        return null
    }

    /** Says once that hosted car apps lost location, when the type did not stick. */
    private fun reportLostLocation(claimed: Int) {
        if (locationTypeIfWanted() == 0) return
        if (claimed and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0) return
        if (!locationLossReported.compareAndSet(false, true)) return
        step("the location foreground type did not stick, so a hosted car app will get " +
            "location only while the phone is unlocked. Everything else is unaffected")
    }

    private val locationLossReported = java.util.concurrent.atomic.AtomicBoolean(false)

    /** The refusals already explained, so each is explained once per process. */
    private val reportedRefusals = java.util.Collections.newSetFromMap(
        java.util.concurrent.ConcurrentHashMap<Int, Boolean>(),
    )

    private fun describeForegroundTypes(types: Int): String {
        val names = buildList {
            if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE != 0) {
                add("connectedDevice")
            }
            if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION != 0) {
                add("mediaProjection")
            }
            if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0) add("location")
        }
        return if (names.isEmpty()) "none" else names.joinToString("+")
    }

    /**
     * The location foreground-service type, when it is worth asking for.
     *
     * ## What it buys, and for whom
     *
     * Not Headway. Headway never reads a location. It buys it for the *car
     * app* Headway is hosting: a driver reported that a navigation app's own
     * marker "doesnt update my triangle unless I am on a route and/or have my
     * phone unlocked", and that is exactly the shape of Android's while-in-use
     * rule biting a bound background service.
     *
     * A hosted car app has no activity and, outside navigation, no foreground
     * service of its own, so its process is background and its location is
     * denied. The route case works because a nav app runs its own foreground
     * service while navigating; the unlocked case works because the process is
     * briefly visible. `BIND_INCLUDE_CAPABILITIES` on the binding passes
     * Headway's own while-in-use capability down to it — which is only worth
     * anything if Headway holds one, and that is what this type is.
     *
     * ## Whenever location is granted, not only with the setting
     *
     * Headway kept this behind "Let car apps use your location", off by default.
     * Jarvis's own car screen needs it too -- its map, the road's speed limit and
     * when a turn is said all come from this fix -- and without the type a locked
     * phone got no fixes at all: the Speed panel sat on "No speed yet" (the owner,
     * 2026-09-26: "I dont think the location info is getting sent correctly"). The
     * setting now only decides whether hosted car apps are passed the location.
     */
    private fun locationTypeIfWanted(): Int {
        val granted = checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED ||
            checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
        return if (granted) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0
    }

    private fun buildNotification(title: String, text: String): Notification {
        // Resolved through the package manager rather than by naming the
        // activity class, so the service does not depend on the UI layer.
        val launch = packageManager.getLaunchIntentForPackage(packageName)
        val contentIntent = launch?.let {
            PendingIntent.getActivity(
                this,
                0,
                it,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setColor(Notify.color(Notify.Template.SERVICE))
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(ai.aura.app.ui.theme.ActiveTheme.statusIcon)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setContentIntent(contentIntent)
            .apply {
                // Only while the cover is actually up, so the notification does
                // not offer to undo something that is not happening.
                if (HeadwayAccessibilityService.instance.value?.covering == true) {
                    addAction(
                        NotificationCompat.Action(
                            0,
                            "Show phone screen",
                            PendingIntent.getService(
                                this@CarLinkService,
                                REQUEST_SHOW_PHONE,
                                Intent(this@CarLinkService, CarLinkService::class.java)
                                    .setAction(ACTION_SHOW_PHONE),
                                PendingIntent.FLAG_IMMUTABLE or
                                    PendingIntent.FLAG_UPDATE_CURRENT,
                            ),
                        ),
                    )
                }
            }
            .build()
    }

    private fun updateNotification(text: String) {
        notificationText = text
        // Posting without POST_NOTIFICATIONS is a no-op on the platform side but
        // the compat layer can throw; a failed status update must never take the
        // session down with it.
        runCatching {
            notificationManager?.notify(
                NOTIFICATION_ID,
                buildNotification(getString(R.string.app_name), text),
            )
        }
    }

    /**
     * The last status line posted, so the notification can be rebuilt without one.
     *
     * [buildNotification] reads state beyond its arguments -- whether the phone
     * screen is covered decides whether the "Show phone screen" action is there
     * -- so that state changing has to re-post, and re-posting needs the text
     * that was already on it.
     */
    private var notificationText: String = "Starting"

    /** Re-posts the ongoing notification against current state, same text. */
    private fun refreshNotification() {
        updateNotification(notificationText)
    }

    /** Notification text. Terse: this is read at a glance, in a car. */
    private fun describe(state: LinkState): String = when (state) {
        is LinkState.Idle -> "Idle"
        is LinkState.Connecting -> "Connecting to the car"
        is LinkState.Connected -> "Connected"
        is LinkState.WaitingToRetry ->
            "Reconnecting in ${state.delayMillis / 1000}s — ${state.cause}"
        is LinkState.GaveUp -> "Disconnected — ${state.cause}"
    }

    private fun publish(state: LinkState) {
        val wasConnected = mutableLinkState.value is LinkState.Connected
        mutableLinkState.value = state
        if (state is LinkState.Connected) {
            // A completed handshake outranks any earlier broadcast: the car is
            // demonstrably there.
            CarPresenceReceiver.carBluetoothGone = false
        } else if (wasConnected && CarPresenceReceiver.carBluetoothGone) {
            // The session broke and the car's Bluetooth was already gone. That
            // is a driver walking away, not a Wi-Fi flap -- and retrying it
            // twelve times with backoff is the "keeps trying to reconnect even
            // when not in range" complaint, arriving a few minutes late.
            //
            // The ACL broadcast cannot be acted on the moment it lands, because
            // during a healthy drive Bluetooth drops and returns constantly
            // while the session runs over Wi-Fi. So it is remembered, and spent
            // here: the first time the session actually fails after it.
            step("the car's Bluetooth is gone and the session has ended; stopping rather than retrying")
            // Before the return, not after it. Car apps learn that projection
            // has ended from this broadcast and cache the answer in a LiveData
            // that only refreshes when told; `onDestroy`'s `publish(Idle)`
            // cannot cover for it either, because the state above has already
            // been overwritten so its own `wasConnected` is false by then. A
            // nav app would have gone on reporting PROJECTION for the rest of
            // its process life -- and this is the one stop path a driver takes
            // every time they walk away.
            runCatching { ai.aura.app.car.carapp.CarConnectionProvider.announce(this) }
            stopSelf()
            return
        }
        // Car apps learn they are being projected by querying Headway's
        // CarConnectionProvider, and they cache the answer in a LiveData that
        // only refreshes when told. Announcing on the edge, not on every
        // publish: a reconnect countdown ticking once a second would broadcast
        // once a second to every app on the phone.
        if (wasConnected != (state is LinkState.Connected)) {
            ai.aura.app.car.carapp.CarConnectionProvider.announce(this)
        }
    }

    /**
     * Channel names taken from what this head unit advertised, not from
     * [ChannelId].
     *
     * [ChannelId] is Headway's own numbering convention, and a real car assigns
     * its own. On a 2021 Chevrolet Infotainment 3 unit the video service is the
     * id Headway's table calls `NAVIGATION_STATUS`, so an entire session's frame
     * log read as if the phone were streaming H.264 into the navigation channel.
     * That is not a cosmetic problem: the log is the only instrument that
     * reaches a real car, and a log that names things wrong sends whoever reads
     * it after the wrong bug.
     *
     * Empty until service discovery completes, which is correct — before that
     * there is no advertisement and the constant table is the best guess
     * available.
     */
    @Volatile
    private var channelNames: Map<Int, String> = emptyMap()

    /**
     * How many of the head unit's keepalives this session answered, and the
     * worst time one of those answers took to reach the wire.
     *
     * Written only by the control loop, which is a single coroutine, and read
     * only by the session summary -- so a plain volatile pair rather than
     * atomics. See the control loop for why they exist.
     */
    @Volatile
    private var pingsAnswered: Long = 0L

    @Volatile
    private var worstPingAnswerMillis: Long = 0L

    /** [worstPingAnswerMillis] split: waiting to be read, and writing the reply. */
    @Volatile
    private var worstPingQueueMillis: Long = 0L

    @Volatile
    private var worstPingSendMillis: Long = 0L

    /** The quirks in force for the current session; see where it is set. */
    @Volatile
    private var sessionQuirks: HeadUnitQuirks = HeadUnitQuirks.DEFAULT

    /**
     * Why the current session is ending, when Headway is the one ending it.
     *
     * Read only by `runChannels`'s `finally`, to hand the real cause to
     * `demux.closeAll` instead of letting it invent one.
     */
    private var sessionFailure: Throwable? = null

    /**
     * What the audio sinks, car focus, queue and Bluetooth looked like as the last session's
     * `finally` began, as a function of the supervisor's cause text: the cause is only known
     * once the supervisor reports it, after the audio object is gone (T-1217). Taken once by
     * the `WaitingToRetry` line and cleared at the start of every session, so a failure
     * before a session has audio cannot print the previous session's numbers.
     */
    @Volatile
    private var resetReport: ((String) -> String?)? = null

    /** Headset and a2dp state with the time each last changed, for [resetReport]. */
    private val profileClock = ResetReport.ProfileClock()

    private var profileReceiver: android.content.BroadcastReceiver? = null

    /** The state word for a Bluetooth profile connection state; the words [BluetoothCarLink.describeProfileState] uses. */
    private fun profileWord(state: Int?): String = when (state) {
        android.bluetooth.BluetoothProfile.STATE_CONNECTED -> "connected"
        android.bluetooth.BluetoothProfile.STATE_CONNECTING -> "connecting"
        android.bluetooth.BluetoothProfile.STATE_DISCONNECTING -> "disconnecting"
        android.bluetooth.BluetoothProfile.STATE_DISCONNECTED -> "disconnected"
        else -> "unknown"
    }

    /**
     * Starts the headset/a2dp clock: one read now, then the system's connection broadcasts.
     * Diagnostic only; nothing here connects or disconnects a profile.
     */
    @SuppressLint("MissingPermission")
    private fun watchBluetoothProfiles() {
        if (checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
        val now = SystemClock.elapsedRealtime()
        BluetoothCarLink.adapterOf(this)?.let { adapter ->
            runCatching {
                profileClock.seed("headset", profileWord(adapter.getProfileConnectionState(android.bluetooth.BluetoothProfile.HEADSET)), now)
                profileClock.seed("a2dp", profileWord(adapter.getProfileConnectionState(android.bluetooth.BluetoothProfile.A2DP)), now)
            }
        }
        val receiver = object : android.content.BroadcastReceiver() {
            override fun onReceive(context: android.content.Context, intent: Intent) {
                val profile = if (intent.action == android.bluetooth.BluetoothHeadset.ACTION_CONNECTION_STATE_CHANGED) "headset" else "a2dp"
                val state = intent.getIntExtra(android.bluetooth.BluetoothProfile.EXTRA_STATE, -1).takeIf { it >= 0 }
                profileClock.changed(profile, profileWord(state), SystemClock.elapsedRealtime())
            }
        }
        val filter = android.content.IntentFilter().apply {
            addAction(android.bluetooth.BluetoothHeadset.ACTION_CONNECTION_STATE_CHANGED)
            addAction(android.bluetooth.BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED)
        }
        runCatching {
            androidx.core.content.ContextCompat.registerReceiver(
                this, receiver, filter, androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED,
            )
            profileReceiver = receiver
        }
    }

    /** How far to walk a cause chain when naming why a session ended. */
    private val CAUSE_DEPTH: Int = 5

    /**
     * Channels logged as a byte count rather than as hex, for the same reason
     * and with the same fix.
     *
     * Keyed on the constants, this filter missed the car's real video channel
     * entirely: fifteen seconds of streaming produced four thousand lines of
     * hex, which pushed everything worth reading out of the export.
     */
    @Volatile
    private var highRateChannels: Set<Int> = DEFAULT_HIGH_RATE_CHANNELS

    /**
     * Rebuilds both from a completed service discovery.
     *
     * Naming is by content, not by id, exactly as
     * [CarVideoStream.videoServiceOf] and its siblings match: the advertisement
     * says what each channel *is*, and that is the only authority.
     */
    private fun adoptChannelNames(profile: HeadUnitProfile) {
        val names = mutableMapOf<Int, String>()
        val highRate = mutableSetOf<Int>()
        for (service in profile.services) {
            val sink = service.mediaSinkService
            when {
                service.hasMediaSinkService() &&
                    sink.availableType == MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP -> {
                    names[service.id] = "VIDEO"
                    highRate += service.id
                }

                service.hasMediaSinkService() && sink.hasAudioType() -> {
                    names[service.id] = sink.audioType.name
                        .removePrefix("AUDIO_STREAM_")
                        .let { "AUDIO_$it" }
                    highRate += service.id
                }

                service.hasMediaSourceService() -> {
                    names[service.id] = "MICROPHONE"
                    highRate += service.id
                }

                service.hasInputSourceService() -> names[service.id] = "INPUT"
                service.hasSensorSourceService() -> names[service.id] = "SENSOR"
                // Everything else keeps whatever the constant table calls it,
                // annotated with its real id so a log can be matched against the
                // advertisement without guessing.
                else -> names[service.id] =
                    "${ChannelId.describe(service.id)}?id=${service.id}"
            }
        }
        channelNames = names
        highRateChannels = highRate
        step("channels: " + names.entries.sortedBy { it.key }
            .joinToString { "${it.key}=${it.value}" })
    }

    /**
     * Puts every AAP frame in the exportable log, in hex.
     *
     * The Bluetooth handshake has had this since the beginning and it is what
     * made every RFCOMM bug findable from a log alone. The TCP side had the hook
     * and nothing wired to it, so a head unit that closed the session over one
     * wrong flag bit produced an export in which the closure was visible and the
     * cause was not.
     *
     * ## Why it is filtered rather than complete
     *
     * Video runs at 30 fps in fragments of up to `maxFragmentSize` (16 KB by
     * default). Logging those would push everything else out of a 4000-line
     * export within a couple of seconds and make the file useless for exactly
     * the failures it exists to diagnose. So the media channels get a one-line
     * summary and everything else gets bytes — bring-up is where the unknowns
     * are, and bring-up is all low-rate control traffic.
     */
    private fun logFrame(
        direction: FramedConnection.Direction,
        header: dev.headway.protocol.framing.FrameHeader,
        payload: ByteArray,
    ) {
        if (!BuildConfig.DEBUG) return
        val arrow = if (direction == FramedConnection.Direction.SENT) "tx" else "rx"
        val channel = channelNames[header.channelId] ?: ChannelId.describe(header.channelId)
        val flags = buildString {
            append(header.frameType.name)
            if (header.control) append("|CONTROL")
            if (header.encrypted) append("|ENC")
        }
        if (header.channelId in highRateChannels) {
            summariseFrame(arrow, channel, flags, payload.size)
            return
        }
        step("$arrow $channel [$flags] ${hex(payload)}")
    }

    /**
     * Collapses a media stream into a handful of lines instead of thousands.
     *
     * A real drive produced 1944 video lines in forty seconds and evicted 1827
     * older ones — the entire Bluetooth handshake, Wi-Fi join, TLS exchange and
     * channel bring-up — from a log whose only purpose is to explain why those
     * steps went wrong. Per-frame lines are worth almost nothing individually
     * and cost everything collectively.
     *
     * So the first few frames on a stream are logged (that is where a wrong
     * codec config or an absent first keyframe shows up), and after that the
     * stream is reported as a rate, once every few seconds. The counts are
     * cumulative and the byte total is real, so a stalled or starved stream is
     * still visible — as a summary line whose numbers stop moving.
     */
    private fun summariseFrame(arrow: String, channel: String, flags: String, bytes: Int) {
        val key = "$arrow $channel"
        val state = frameLogStates.getOrPut(key) { FrameLogState() }
        val now = SystemClock.elapsedRealtime()
        val seen: Long
        val total: Long
        val sinceLast: Long
        val elapsed: Long
        synchronized(state) {
            state.frames++
            state.bytes += bytes
            seen = state.frames
            total = state.bytes
            if (seen <= FRAMES_LOGGED_IN_FULL) {
                step("$arrow $channel [$flags] $bytes bytes (frame $seen)")
                if (state.lastSummaryAt == 0L) state.lastSummaryAt = now
                return
            }
            elapsed = now - state.lastSummaryAt
            if (elapsed < FRAME_SUMMARY_INTERVAL_MILLIS) return
            sinceLast = seen - state.framesAtLastSummary
            state.lastSummaryAt = now
            state.framesAtLastSummary = seen
        }
        val fps = if (elapsed > 0) sinceLast * 1000.0 / elapsed else 0.0
        step(
            "$arrow $channel [$flags] %,d frame(s) total, %,d KiB; %.1f/s over the last %.1f s"
                .format(seen, total / 1024, fps, elapsed / 1000.0)
        )
    }

    /** Per-direction, per-channel state for [summariseFrame]. */
    private class FrameLogState {
        var frames: Long = 0L
        var bytes: Long = 0L
        var framesAtLastSummary: Long = 0L
        var lastSummaryAt: Long = 0L
    }

    private val frameLogStates = ConcurrentHashMap<String, FrameLogState>()

    private fun step(message: String) {
        // Into the exportable log, not only logcat. MainActivity tells the user
        // "if the car refuses to connect, export the log and send it" -- and
        // until this went through SessionLog the export held UI and
        // accessibility lines but not one line of the handshake, join or session
        // bring-up, which is the entire reason the export exists.
        SessionLog.shared.info(TAG, message)
    }


    /**
     * Jarvis, not "Headway", to the car (the owner, 2026-09-25: "can we change the
     * android auto button on my car to jarvis?"): the name and the launcher icon at
     * the protocol's three sizes. A head unit that labels its projection button from
     * the phone shows these; one with a fixed "Android Auto" button keeps it. The button
     * reads the worn pack's name (AURA by default, T-0869); the device name stays
     * "Jarvis", because a head unit may key its pairing on it.
     */
    private fun carIdentity(): PhoneIdentity {
        fun icon(px: Int): ByteArray? = runCatching {
            // The worn pack's icon beside its name: the application icon is AURA's for every
            // persona, so a JARVIS label sat beside her galaxy (the review of T-0869).
            val res = if (ActiveTheme.theme.id == JarvisThemes.AURA.id) R.mipmap.ic_aura else R.mipmap.ic_jarvis
            val drawable = checkNotNull(applicationContext.getDrawable(res))
            val bitmap = android.graphics.Bitmap.createBitmap(px, px, android.graphics.Bitmap.Config.ARGB_8888)
            drawable.setBounds(0, 0, px, px)
            drawable.draw(android.graphics.Canvas(bitmap))
            java.io.ByteArrayOutputStream().also { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        }.getOrNull()
        return PhoneIdentity(deviceName = "${ActiveTheme.name}", labelText = ActiveTheme.theme.name, smallIcon = icon(32), mediumIcon = icon(64), largeIcon = icon(128))
    }
    companion object {
        /** After the session is up: the car's audio channels are open and its own chime is done. */
        private const val DRIVE_START_DELAY_MS = 8_000L

        // The house counts as answering for a minute after it last did: long enough to cover
        // one late line, short enough that a house that really went is announced.
        private const val HOUSE_SEEN_MS = 60_000L

        // The connect check's line is no turn's word: eight seconds for her own voice.
        private const val PATIENT_LINE_MS = 8_000L

        // The car page is asked five times, two seconds apart, before it is called down.
        private const val CONSOLE_TRIES = 5
        private const val CONSOLE_GAP_MS = 2_000L
        private const val VOICE_LINE_MAX_AGE_MS = 24 * 60 * 60 * 1000L
        /** AURA's own voice on the phone (T-0872): what a clips-only pack says a free-form line in offline. */
        private val HER_VOICE = ai.aura.app.assist.BundledVoice.Model.KOKORO

        /** How often a live session logs the car link's radio ([CarWifiNetwork.linkReport]). */
        private const val LINK_REPORT_MS = 10_000L
        /** How long an announcement may take to start before the next one stops waiting on it. */
        private const val APPROVAL_TRIES = 2
        /** Long enough for the question to be said before the microphone opens. */
        private const val APPROVAL_LISTEN_DELAY_MS = 3_500L
        private val YES = Regex("(?i)\\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|allow|approve|send it|affirmative)\\b")
        private val NO = Regex("(?i)\\b(no|nope|don'?t|do not|stop|cancel|deny|never mind|negative)\\b")

        /** A ringing call as an event: said to Sir, with the choice asked; the caller hears nothing of it. */
        internal fun callPrompt(who: String): String =
            "[event: call] $who is calling Sir's phone while he drives. Tell him in one short line and ask: " +
                "answer, decline, or text back? Then phone_action answer_call or decline_call, or send_sms " +
                "with exactly what he says. Nothing about Sir goes to the caller."

        /** What was said, as a yes, a no, or neither. A no wins a sentence with both ("no, don't"). */
        internal fun yesOrNo(heard: String): Boolean? = when {
            NO.containsMatchIn(heard) -> false
            YES.containsMatchIn(heard) -> true
            else -> null
        }

        /** The question said aloud: what Jarvis would do, in its own words. */
        internal fun approvalWords(a: ai.aura.app.assist.hermes.HudState.Approval): String {
            val why = a.reason.trim().ifBlank { a.params.trim() }.take(160)
            val tool = a.tool.replace('_', ' ')
            return if (why.isEmpty()) "${ActiveTheme.name} would like to $tool." else "${ActiveTheme.name} would like to $tool: $why."
        }

        private const val ANNOUNCE_START_MS = 5_000L
        private const val TAG = "CarLinkService"

        /**
         * The platform's name for "associated, but never got an address".
         *
         * `CarWifiNetwork.adviceFor` carries the full explanation. The short
         * version: GrapheneOS fixes this for Google's Android Auto by forcing
         * `RANDOMIZATION_PERSISTENT` and re-enabling the DHCP hostname, keyed on
         * the Gearhead package, and neither half is reachable from a
         * `WifiNetworkSpecifier` join. So Headway cannot retry its way out.
         */
        const val DHCP_FAILURE: String = "IP_PROVISIONING"
        private const val SAVED_NETWORK_WAIT_MS = 25_000L
        private const val SAVED_NETWORK_POLL_MS = 500L

        /**
         * How many addressing failures to take before giving up on the run.
         *
         * Two, not one, and the second is not hope — it is the measurement.
         *
         * The two candidate explanations for a missing lease make *different*
         * predictions about a second association: if the head unit's address
         * table is full of MACs this phone burned, a repeat may free a slot or
         * reuse one; if the unit is refusing this station outright — because it
         * sees an unfamiliar locally-administered MAC, or because the request
         * carries no DHCP hostname — the second attempt fails identically. One
         * attempt cannot tell those apart and both were live hypotheses after
         * reading GrapheneOS's own fix, so the log now records the answer.
         *
         * Three would be worse than two: past the second there is no new
         * information, only another address consumed on a unit that is not
         * releasing them.
         */
        const val MAX_ADDRESSING_ATTEMPTS: Int = 2

        /** Digs the platform's failure code out of a wrapped join failure. */
        internal fun platformReasonOf(failure: Throwable): String? {
            var current: Throwable? = failure
            // Bounded, because a cause chain can be circular.
            var hops = 0
            while (current != null && hops++ < 8) {
                (current as? CarWifiException)?.platformReason?.let { return it }
                current = current.cause
            }
            return null
        }

        /**
         * How long to wait after a failed Wi-Fi join before trying again.
         *
         * Long, and deliberately so. Releasing the network request tells
         * Settings' approval activity to abort, which leaves it showing an
         * error rather than finishing, and it is `singleTop` in its own task —
         * so a request re-issued immediately re-uses that activity instead of
         * getting a fresh prompt. The user then sees a dialog that says the
         * request was cancelled and no way to accept the new one.
         *
         * Only join failures wait this long; every other failure keeps the
         * ordinary sub-second backoff, because those are the ones the
         * 15-second reconnect requirement is about.
         */
        const val JOIN_RETRY_DELAY_MILLIS: Long = 30_000

        const val ACTION_STOP: String = "ai.aura.app.car.action.STOP"

        /**
         * Consecutive failed attempts before Headway stops trying by itself.
         *
         * Twelve, which with the backoff is a few minutes of a car that never
         * answers -- long enough to ride out a head unit rebooting or a Wi-Fi
         * flap mid-drive, short enough that a phone carried indoors stops
         * within the walk from the driveway. It resets on any session that
         * establishes, so a drive that reconnects repeatedly never approaches
         * it.
         */
        private const val MAX_CONSECUTIVE_FAILURES: Int = 12

        /** Removes the phone-screen cover. See `HeadwayAccessibilityService.showBlackout`. */
        const val ACTION_SHOW_PHONE: String = "ai.aura.app.car.action.SHOW_PHONE"

        /**
         * Uncovers the phone and keeps it uncovered until [ACTION_COVER_PHONE].
         *
         * Paired, and counted, because two things can want the phone at once
         * and a plain uncover would let either of their exits re-black the
         * screen under the other.
         */
        const val ACTION_HOLD_PHONE: String = "ai.aura.app.car.action.HOLD_PHONE"

        /** Releases one [ACTION_HOLD_PHONE], re-covering when the last one goes. */
        const val ACTION_COVER_PHONE: String = "ai.aura.app.car.action.COVER_PHONE"

        /**
         * How many things currently need the phone screen visible.
         *
         * Process-wide rather than per-service instance: the holder is an
         * activity, the cover is owned by the service, and the service can be
         * restarted between the two halves of a hold.
         */
        private val phoneHolds = java.util.concurrent.atomic.AtomicInteger(0)

        /** Distinct from the stop action's, or the two PendingIntents collide. */
        private const val REQUEST_SHOW_PHONE: Int = 1

        /** Where the learned head-unit port and the saved car link live. */
        private const val PREFS: String = "headway_link"
        private const val KEY_LEARNED_PORT: String = "learned_aap_port"
        private const val KEY_ACCEPTED_CERT: String = "accepted_certificate"
        private const val KEY_CAR_SSID: String = "car_wifi_ssid"
        private const val KEY_CAR_PASSPHRASE: String = "car_wifi_passphrase"
        private const val KEY_CAR_HIDDEN: String = "car_wifi_hidden"
        private const val KEY_CAR_BSSID: String = "car_wifi_bssid"
        private const val KEY_SUGGESTED: String = "car_wifi_suggested"

        /**
         * The fallback filter, used until service discovery says otherwise.
         *
         * Right for the emulator, which numbers its channels the way [ChannelId]
         * does, and only a guess for a car — which is why [adoptChannelNames]
         * replaces it wholesale the moment there is an advertisement to read.
         */
        private val DEFAULT_HIGH_RATE_CHANNELS: Set<Int> = setOf(
            ChannelId.MEDIA_SINK_VIDEO.id,
            ChannelId.MEDIA_SINK_MEDIA_AUDIO.id,
            ChannelId.MEDIA_SINK_GUIDANCE_AUDIO.id,
            ChannelId.MEDIA_SINK_SYSTEM_AUDIO.id,
            ChannelId.MEDIA_SOURCE_MICROPHONE.id,
        )

        /**
         * Frames logged individually before a stream collapses to summaries.
         *
         * Three, because the interesting ones are at the start: the codec
         * config, the first keyframe, and one ordinary frame to show the
         * steady-state size. Everything after that is the same line again.
         */
        private const val FRAMES_LOGGED_IN_FULL = 3

        /** How often a running stream reports itself. */
        private const val FRAME_SUMMARY_INTERVAL_MILLIS = 5_000L

        /** Same 64-byte truncation the RFCOMM logger uses, for the same reason. */
        internal fun hex(bytes: ByteArray, limit: Int = 64): String {
            val shown = bytes.take(limit).joinToString(" ") { "%02x".format(it) }
            return if (bytes.size > limit) "$shown ... (${bytes.size} bytes)" else shown
        }

        /**
         * The car's Wi-Fi credentials from the last handshake, for the UI.
         *
         * Read from the settings screen rather than from the service, because
         * the button that needs them is useful precisely when no attempt is
         * running. See `rememberCarWifi`.
         */
        fun lastCarWifi(context: android.content.Context):
            ai.aura.app.car.link.CarWifiProvisioning.CarCredentials? {
            val prefs = context.getSharedPreferences(PREFS, MODE_PRIVATE)
            val ssid = prefs.getString(KEY_CAR_SSID, null)?.takeIf { it.isNotEmpty() }
                ?: return null
            return ai.aura.app.car.link.CarWifiProvisioning.CarCredentials(
                ssid = ssid,
                passphrase = prefs.getString(KEY_CAR_PASSPHRASE, "").orEmpty(),
                hiddenSsid = prefs.getBoolean(KEY_CAR_HIDDEN, false),
            )
        }

        /**
         * The BSSID the head unit last announced, as a chooser hint.
         *
         * Deliberately separate from [lastCarWifi], which is the credential set
         * the Wi-Fi setup panel consumes. This one exists only to pre-filter the
         * companion chooser, and it is a hint precisely because the announced
         * radio has been observed not to be the one broadcasting.
         */
        fun lastCarBssid(context: android.content.Context): String? =
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_CAR_BSSID, null)
                ?.takeIf { it.isNotEmpty() }

        /** Bluetooth address of the car; omitted means "find it among the paired devices". */
        const val EXTRA_CAR_ADDRESS: String = "ai.aura.app.car.extra.CAR_ADDRESS"

        /** The `resultCode` from the screen-capture consent activity. */
        const val EXTRA_PROJECTION_RESULT: String = "ai.aura.app.car.extra.PROJECTION_RESULT"

        /** The `Intent` the consent activity returned; the projection token. */
        const val EXTRA_PROJECTION_DATA: String = "ai.aura.app.car.extra.PROJECTION_DATA"

        const val CHANNEL_ID: String = "headway.link"

        /** The channel the screen-sharing request uses; separate so it can be loud. */
        const val ASK_CHANNEL_ID: String = "headway.ask"
        private const val RENDERER_CONFLICT_NOTIFICATION_ID: Int = 0x4a43
        private const val HOME_REMEDY_NOTIFICATION_ID: Int = 0x4a44
        private const val APPROVAL_DETAIL_CHARS: Int = 160
        const val NOTIFICATION_ID: Int = 1

        /**
         * The "allow screen sharing" offer. Distinct from the service's own
         * notification and from the presence receiver's, so none of the three
         * ever replaces another -- a driver watching one vanish would read it as
         * having worked.
         */
        const val PROJECTION_NOTIFICATION_ID: Int = 3
        private const val PROJECTION_REQUEST_CODE: Int = 3

        private val mutableLinkState = MutableStateFlow<LinkState>(LinkState.Idle)

        /** Sockets the car opened to the phone, waiting for a session (CarPokeListener). */
        private val pokes = ai.aura.app.car.link.PokeSlot<android.bluetooth.BluetoothSocket>()

        /**
         * The link state, for the UI.
         *
         * Static because the state outlives any particular binding to the
         * service and the UI needs it before it has one; the service is a
         * singleton by construction, so there is exactly one writer.
         */
        val linkState: StateFlow<LinkState> = mutableLinkState.asStateFlow()

        /** Starts the service, optionally naming which paired device is the car. */
        fun start(
            context: android.content.Context,
            carAddress: String? = null,
            projectionResultCode: Int = android.app.Activity.RESULT_CANCELED,
            projectionData: Intent? = null,
        ) {
            val intent = Intent(context, CarLinkService::class.java).apply {
                if (carAddress != null) putExtra(EXTRA_CAR_ADDRESS, carAddress)
                // The projection grant travels with the start intent because it
                // must be in hand *before* the session comes up: a real head
                // unit gives the phone about fifteen seconds from channel open
                // to first frame before it hangs up, which is far too little to
                // put a consent dialog in front of a driver.
                if (projectionData != null) {
                    putExtra(EXTRA_PROJECTION_RESULT, projectionResultCode)
                    putExtra(EXTRA_PROJECTION_DATA, projectionData)
                }
            }
            context.startForegroundService(intent)
        }

        fun stop(context: android.content.Context) {
            context.startService(
                Intent(context, CarLinkService::class.java).setAction(ACTION_STOP)
            )
        }
    }
}
