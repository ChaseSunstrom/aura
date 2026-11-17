// @generated from themes/<id>/theme.json by design/build.py — DO NOT EDIT.
package ai.jarvis.app.ui.theme

/**
 * Every theme pack as data (design/AURA/PROMPT.md §1): the persona, voice, wake phrases, core
 * renderer and its eight states, the map style. Colours are role -> "#RRGGBB" (or a gradient's
 * stops, space-separated) exactly as the pack writes them.
 */
object JarvisThemes {
    data class Theme(
        val id: String,
        val name: String,
        val spokenName: String,
        val voiceId: String,
        /** voice.offline.fallback: who speaks a line with no clip offline (OfflineVoice). */
        val offlineFallback: String,
        val wakePhrases: List<String>,
        val wakeModel: String,
        /** wake.threshold: a score at or over it, two frames running, fires a head trained for the pack. */
        val wakeThreshold: Float,
        /** wake.useSpeakerVerification: a turn its head opens needs a recognised voice (PROMPT §4). */
        val wakeVerify: Boolean,
        val renderer: String,
        val mapStyle: String,
        val marker: String,
        val toggle: String,
        val displayFamily: String,
        val monoFamily: String,
        val colors: Map<String, String>,
        val strings: Map<String, String>,
        val coreStates: Map<String, Map<String, Float>>,
        val coreTween: Float,
        /** The core's scalar params by name (budgets, wind, flareMs…); lists and objects aside. */
        val coreParams: Map<String, Float> = emptyMap(),
        /** The galaxy's aura ring radii, as fractions of its spread. */
        val coreRings: List<Float> = emptyList(),
        /** The core's palette, role -> "#RRGGBB". */
        val corePalette: Map<String, String> = emptyMap(),
        /** Each state's tint "#RRGGBB", for the states that have one. */
        val coreTints: Map<String, String> = emptyMap(),
        /** The map section flattened: dotted key -> text, lists space-joined ("sky.gradient"). */
        val map: Map<String, String> = emptyMap(),
        /** background.type: "starfield" (her space behind every panel) or "radial". */
        val ground: String = "radial",
        /** background.nebulae: the starfield's coloured washes. */
        val nebulae: List<Nebula> = emptyList(),
    )

    /** A nebula: [color] "#AARRGGBB", centred at ([x], [y]) as fractions of the ground, [w] x [h] across in px. */
    data class Nebula(val color: String, val x: Float, val y: Float, val w: Float, val h: Float)

    val AURA = Theme(
        id = "aura",
        name = "AURA",
        spokenName = "Aura",
        voiceId = "af_heart",
        offlineFallback = "prerendered-clips",
        wakePhrases = listOf("aura", "hey aura"),
        wakeModel = "assets/wake/aura.onnx",
        wakeThreshold = 0.95f,
        wakeVerify = true,
        renderer = "galaxy",
        mapStyle = "space-drive",
        marker = "star",
        toggle = "pill",
        displayFamily = "Sora",
        monoFamily = "DM Mono",
        colors = mapOf(
            "bg.void" to "#05030D",
            "bg.nebula" to "#2A1250",
            "bg.deep" to "#120828",
            "accent" to "#B79CFF",
            "accent.soft" to "#E4D7FF",
            "accent.gradient" to "#D9C7FF #B79CFF #F2A4E2",
            "memory" to "#F472D0",
            "cool" to "#7CC7FF",
            "attention" to "#FFC36B",
            "attention.soft" to "#FFD89A",
            "ok" to "#6EE7B7",
            "danger" to "#FF6B8B",
            "text" to "#F7F2FF",
            "text.secondary" to "#C8BCE3",
            "text.label" to "#AFA1D6",
            "off" to "#5E5288",
            "on.accent" to "#170A2E",
            "on.attention" to "#2A1A05",
            "panel.fill" to "rgba(183,156,255,.08) rgba(10,5,24,0)",
            "panel.edge" to "rgba(236,220,255,.14)",
            "glow.text" to "rgba(183,156,255,.45)",
        ),
        strings = mapOf(
            "ready" to "Ready when you are.",
            "listening" to "Listening…",
            "thinking" to "Thinking…",
            "working" to "On it.",
            "offline" to "I can’t reach home right now — I’ll keep what I can on this device.",
            "approvalAsk" to "Needs your OK",
            "takeOver" to "Esc to take over",
        ),
        coreStates = mapOf(
            "idle" to mapOf("spin" to 0.2f, "tilt" to 1.05f, "radius" to 1.0f, "wind" to 0.0f, "glow" to 0.85f, "comets" to 0.0f, "jets" to 0.0f, "accretion" to 0.5f),
            "listen" to mapOf("spin" to 0.28f, "tilt" to 0.42f, "radius" to 1.02f, "wind" to 0.0f, "glow" to 1.0f, "comets" to 0.0f, "jets" to 0.0f, "accretion" to 0.7f),
            "speak" to mapOf("spin" to 0.32f, "tilt" to 0.62f, "radius" to 1.02f, "wind" to 0.0f, "glow" to 1.15f, "comets" to 0.0f, "jets" to 0.0f, "accretion" to 0.8f),
            "think" to mapOf("spin" to 1.3f, "tilt" to 1.12f, "radius" to 0.86f, "wind" to 1.3f, "glow" to 0.9f, "comets" to 0.3f, "jets" to 1.0f, "accretion" to 1.0f, "tintAmount" to 0.35f),
            "embed" to mapOf("spin" to 0.7f, "tilt" to 0.75f, "radius" to 0.95f, "wind" to 0.4f, "glow" to 1.05f, "comets" to 0.0f, "jets" to 0.25f, "accretion" to 1.3f, "tintAmount" to 0.25f),
            "music" to mapOf("spin" to 0.42f, "tilt" to 0.55f, "radius" to 1.03f, "wind" to 0.0f, "glow" to 1.05f, "comets" to 0.0f, "jets" to 0.0f, "accretion" to 0.8f),
            "alert" to mapOf("spin" to 0.06f, "tilt" to 0.3f, "radius" to 0.9f, "wind" to 0.0f, "glow" to 0.9f, "comets" to 0.0f, "jets" to 0.0f, "accretion" to 0.6f, "tintAmount" to 0.6f),
            "work" to mapOf("spin" to 1.05f, "tilt" to 0.82f, "radius" to 0.93f, "wind" to 0.2f, "glow" to 1.0f, "comets" to 1.0f, "jets" to 0.3f, "accretion" to 0.9f),
        ),
        coreTween = 0.035f,
        coreParams = mapOf("arms" to 2.0f, "wind" to 4.6f, "starsDesktop" to 2100.0f, "starsPhone" to 1200.0f, "starsMini" to 360.0f, "nebulae" to 18.0f, "nebulaeMini" to 6.0f, "jets" to 90.0f, "jetsMini" to 30.0f, "dustLanes" to 26.0f, "knots" to 14.0f, "flareMs" to 650.0f),
        coreRings = listOf(1.0f, 1.1f),
        corePalette = mapOf("warm" to "#FFEEDC", "pink" to "#F48CD6", "lavender" to "#BEA0FF", "blue" to "#82BEFF", "gold" to "#FFCD82", "ice" to "#E4EEFF", "dust" to "#060310", "knot" to "#FFAAE6", "knotCool" to "#BED2FF", "spark" to "#FFFAFF", "listenRing" to "#D7C3FF", "alertRing" to "#FF7896", "jet" to "#EBD7FF", "jetSpark" to "#F0E1FF", "auraRing" to "#C8AFFF", "sweep" to "#FAF0FF", "comet" to "#E6D7FF", "cometWarm" to "#FFD296", "glow" to "#FFE1F5", "bulge" to "#FFFDFF", "spike" to "#FFF5FF", "accretion" to "#FFD7F5", "accretionHot" to "#FFF5F5", "shock" to "#E1CDFF"),
        coreTints = mapOf("think" to "#CD96FF", "embed" to "#F48CD6", "alert" to "#FF6E8C"),
        map = mapOf("style" to "space-drive", "sky.gradient" to "#05030D #120828 #3A1A63", "sky.band" to "milky-way", "sky.stars" to "320", "ground.gradient" to "#1C0B3A #0B0519 #05030D", "ground.texture" to "star-dust", "road.edges" to "dotted-light", "road.surface" to "star-stream", "road.lanes" to "#F2EAFF@35%", "buildings.style" to "crystal", "buildings.near" to "#B79CFF", "buildings.far" to "#F48CD6", "buildings.windows" to "true", "buildings.beacons" to "#FF7896", "route.style" to "comet", "route.color" to "#FFC36B", "turnMarker" to "planet", "otherVehicles" to "orb:#FF7896", "ego" to "starlight-wireframe+aura+wake", "labels.font" to "display", "labels.case" to "upper", "labels.backing" to "pill", "coreDock" to "bottom-right"),
        ground = "starfield",
        nebulae = listOf(Nebula("#1CF472D0", 0.24f, 0.74f, 520.0f, 340.0f), Nebula("#147CC7FF", 0.8f, 0.2f, 640.0f, 400.0f)),
    )

    val JARVIS = Theme(
        id = "jarvis",
        name = "JARVIS",
        spokenName = "Jarvis",
        voiceId = "bm_george",
        offlineFallback = "bundled-voice",
        wakePhrases = listOf("jarvis", "hey jarvis"),
        wakeModel = "builtin:hey_jarvis_v0.1",
        wakeThreshold = 0.6f,
        wakeVerify = true,
        renderer = "reactor",
        mapStyle = "hologram",
        marker = "bracket",
        toggle = "square",
        displayFamily = "Space Grotesk",
        monoFamily = "JetBrains Mono",
        colors = mapOf(
            "bg.void" to "#010409",
            "bg.nebula" to "#062236",
            "bg.deep" to "#030A12",
            "accent" to "#3FD0E8",
            "accent.soft" to "#9BEAF6",
            "accent.gradient" to "#9BEAF6 #3FD0E8 #3FD0E8",
            "memory" to "#9B8CFF",
            "cool" to "#3FD0E8",
            "attention" to "#FFB547",
            "attention.soft" to "#FFC97A",
            "ok" to "#5FE3A1",
            "danger" to "#FF6B8B",
            "text" to "#F2FBFF",
            "text.secondary" to "#A9C8D8",
            "text.label" to "#8DB4CA",
            "off" to "#4E6177",
            "on.accent" to "#02050A",
            "on.attention" to "#1A1206",
            "panel.fill" to "rgba(1,8,14,.75) rgba(1,8,14,0)",
            "panel.edge" to "rgba(63,208,232,.26)",
            "glow.text" to "rgba(63,208,232,.45)",
            "bg.raised" to "#030A12",
            "bg.panel" to "#040B13",
            "surface.2" to "#081A23",
            "surface.sunken" to "#01040A",
            "accent.deep" to "#2C8FA3",
            "line" to "#0A2129",
            "line.soft" to "#081920",
            "line.hair" to "#07151C",
        ),
        strings = mapOf(
            "ready" to "Ready when you are.",
            "listening" to "Listening…",
            "thinking" to "Thinking…",
            "working" to "On it.",
            "offline" to "I can’t reach the server — working locally.",
            "approvalAsk" to "Needs your OK",
            "takeOver" to "Esc to take over",
        ),
        coreStates = mapOf(
            "idle" to mapOf(),
            "listen" to mapOf(),
            "speak" to mapOf(),
            "think" to mapOf(),
            "embed" to mapOf(),
            "music" to mapOf(),
            "alert" to mapOf(),
            "work" to mapOf(),
        ),
        coreTween = 0.035f,
        coreParams = mapOf("shellPoints" to 1500.0f),
        coreRings = listOf(),
        corePalette = mapOf(),
        coreTints = mapOf(),
        map = mapOf("style" to "hologram", "buildings" to "wireframe", "route" to "ribbon", "turnMarker" to "chevron", "otherVehicles" to "box:#FFB547", "ego" to "wireframe", "coreDock" to "bottom-right"),
        ground = "radial",
        nebulae = listOf(),
    )

    val ALL: List<Theme> = listOf(AURA, JARVIS)
    const val DEFAULT_ID = "aura"

    /** The pack for an id, or the default for anything unknown (a persona without a pack). */
    fun of(id: String?): Theme = ALL.firstOrNull { it.id == id } ?: ALL.first { it.id == DEFAULT_ID }
}
