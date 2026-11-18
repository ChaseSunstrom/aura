package ai.aura.app.assist.island

import kotlin.math.abs
import kotlin.math.max

/**
 * The resting island (T-0890): while the assistant is on, the island never goes; with nothing
 * going on it rests as a small pill on the camera hole (the owner, 2026-11-09: "to indicate
 * jarvis/aura is on, can we have a persistant dynamic island type look in the phone camera
 * hole? … that should be where the dynamic island look is anyways, as it is slightly below
 * it"). It says a few small things (the persona and whether it listens) or, with the status
 * option, the phone's own status "instead of showing the default phone statuses at the top".
 * It does not hide the system's own icons: only an accessibility overlay draws over them, and it
 * would also cover the system's camera and microphone dot and the pulled-down shade.
 *
 * Pure Kotlin: where it goes, when it shows and what it says. Does not
 * cover reading the phone or drawing ([RestIsland], [RestView]).
 */
object IslandRest {
    /** A box in screen px (the camera's cutout). */
    data class Box(val left: Int, val top: Int, val right: Int, val bottom: Int)

    /** The window's offsets: [x] from the screen's centre (a centred gravity), [y] from its top, px. */
    data class At(val x: Int, val y: Int)

    /**
     * An island [height] px tall centred on [cutout] (the top one); with none, in the middle of
     * the [statusBar]. Never above the screen's edge: a hole nearer the top than half the island
     * puts the island's top at the edge.
     */
    fun at(screenWidth: Int, cutout: Box?, statusBar: Int, height: Int): At {
        if (cutout == null) return At(0, max(0, (statusBar - height) / 2))
        val cx = (cutout.left + cutout.right) / 2
        val cy = (cutout.top + cutout.bottom) / 2
        return At(cx - screenWidth / 2, max(0, cy - height / 2))
    }

    /** The conversation's island on the hole (T-0968): its window's place, the hole's width its pill makes room for, and how far down its rows start, px. */
    data class Anchor(val at: At, val notch: Int, val clear: Int)

    /**
     * The conversation's island at the top while the resting island is on: [height] px tall and
     * centred on the [hole], as the resting island is, so the turn's pill takes its place (the
     * owner, 2026-11-10: "make it work with the dynamic island"). Null with no hole, or one more
     * than [slack] px off the screen's centre: a card nearly the screen's width centred on a
     * corner hole would run off the screen, so it keeps its place under the status bar.
     */
    fun anchor(screenWidth: Int, hole: Box?, height: Int, slack: Int): Anchor? {
        if (hole == null || abs((hole.left + hole.right) / 2 - screenWidth / 2) > slack) return null
        val at = at(screenWidth, hole, 0, height)
        return Anchor(at, hole.right - hole.left, hole.bottom - at.y)
    }

    /**
     * The camera's hole: the cutout's own [outline] (the bounds of its path), or its bounding
     * [rect] when the outline is missing or empty. A Pixel's rect is the status bar's
     * approximation, from the screen's top edge to below the hole, so a pill sized on it was
     * twice the hole and started at the edge (the owner, 2026-11-10, on a Pixel 10 Pro XL). The
     * path is every side's cutouts at once, so an outline that spills past the top rect is the
     * rect's job.
     */
    fun hole(outline: Box?, rect: Box?): Box? {
        // No top rect, no hole at the top: turned sideways, the path is the hole at the side, and
        // an island centred on it sat halfway down the screen's edge (T-0968).
        if (rect == null) return null
        return outline?.takeIf { o ->
            o.right > o.left && o.bottom > o.top &&
                o.left >= rect.left && o.top >= rect.top && o.right <= rect.right && o.bottom <= rect.bottom
        } ?: rect
    }

    /**
     * How tall the island is: its resting height, or the camera's hole and [margin] round it
     * when the hole is taller (most punch holes are 30–40 dp), so the pill never looks smaller
     * than what it sits on; never taller than the [statusBar] it sits in (none, 0: uncapped).
     */
    fun height(rest: Int, cutout: Box?, margin: Int, statusBar: Int): Int {
        val tall = max(rest, cutout?.let { it.bottom - it.top + 2 * margin } ?: 0)
        return if (statusBar > 0) minOf(tall, statusBar) else tall
    }

    /**
     * Whether it shows: its setting on, its state the table's REST (the conversation's island has
     * the top while a turn is up), the status bar showing (another app full screen hides it) or
     * one of the app's [ownScreen]s in front (the home hides the bar and the island stands in for
     * it), and the screen on.
     */
    fun shown(setting: Boolean, conversation: Boolean, statusBar: Boolean, screenOn: Boolean, ownScreen: Boolean = false): Boolean =
        Island.stateFor(Island.Inputs(on = setting, turn = conversation)) == Island.State.REST && (statusBar || ownScreen) && screenOn

    /**
     * The cover's band over the status bar (T-0969): the bar's height, the screen's width less a
     * square of the bar's height at its end corner, where Android draws its camera and microphone
     * dot. Does not cover a maker that puts the dot anywhere else: there the band hides it.
     */
    fun band(screenWidth: Int, statusBar: Int, rtl: Boolean): Box =
        if (rtl) Box(statusBar, 0, screenWidth, statusBar) else Box(0, 0, screenWidth - statusBar, statusBar)

    enum class Presence(val word: String) { LISTENING("LISTENING"), MUTED("MUTED"), OFFLINE("OFFLINE") }

    /** The persona's core at rest: listening while it listens, ready while muted, offline while its link is down. */
    fun core(presence: Presence): ai.aura.app.reactor.ReactorState = when (presence) {
        Presence.LISTENING -> ai.aura.app.reactor.ReactorState.LISTENING
        Presence.MUTED -> ai.aura.app.reactor.ReactorState.IDLE
        Presence.OFFLINE -> ai.aura.app.reactor.ReactorState.OFFLINE
    }

    enum class Network { WIFI, CELL, NONE }

    /** The phone's status as read: [time] already in the phone's 12/24-hour format, [bars] 0 to 4. */
    data class Status(
        val time: String,
        val battery: Int?,
        val charging: Boolean,
        val network: Network,
        val bars: Int,
        val dnd: Boolean,
    )

    /** What the island says: [left] of the camera and [right] of it, and the status glyphs. */
    data class Face(
        val left: String,
        val right: String,
        val network: Network? = null,
        val bars: Int = 0,
        val dnd: Boolean = false,
        val charging: Boolean = false,
    )

    fun face(persona: String, presence: Presence, status: Status?): Face {
        if (status == null) return Face(persona.uppercase(), presence.word)
        return Face(
            left = status.time,
            right = status.battery?.let { "$it%" } ?: "",
            network = status.network,
            bars = status.bars.coerceIn(0, 4),
            dnd = status.dnd,
            charging = status.charging,
        )
    }

    /** Four bars from a Wi-Fi RSSI in dBm, as the status bar counts them; nothing below -88. */
    fun wifiBars(rssi: Int): Int = when {
        rssi >= -55 -> 4
        rssi >= -66 -> 3
        rssi >= -77 -> 2
        rssi >= -88 -> 1
        else -> 0
    }
}
