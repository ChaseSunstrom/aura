package ai.aura.app.ui.theme

/**
 * @generated from design/tokens.json — DO NOT EDIT. Run `python3 design/build.py`.
 *
 * The design tokens for the phone's Views. `JarvisUi` aliases these under the
 * names its builders use; `JarvisTheme` turns them into a Compose theme;
 * `android-app/tools/design_token_test.py` reads them back against the source.
 */
object JarvisTokens {
    object Color {
        const val BG = 0xFF010409.toInt() // --jv-bg
        const val BG_RAISED = 0xFF030A12.toInt() // --jv-bg-raised
        const val PANEL = 0xFF040B13.toInt() // --jv-panel
        const val PANEL_SOLID = 0xFF040B13.toInt() // --jv-panel-solid
        const val SURFACE_2 = 0xFF081A23.toInt() // --jv-surface-2
        const val FIELD = 0xFF030A12.toInt() // --jv-field
        const val SURFACE_SUNKEN = 0xFF01040A.toInt() // --jv-surface-sunken
        const val ACCENT = 0xFF3FD0E8.toInt() // --jv-accent
        const val ACCENT_DEEP = 0xFF2C8FA3.toInt() // --jv-accent-deep
        const val ACCENT_LIFT = 0xFF9BEAF6.toInt() // --jv-accent-lift
        const val ACCENT_INK = 0xFF02050A.toInt() // --jv-accent-ink
        const val WARN = 0xFFFFB547.toInt() // --jv-warn
        const val WARMING = 0xFFFF9E2C.toInt() // --jv-warming
        const val DANGER = 0xFFFF6B8B.toInt() // --jv-danger
        const val DANGER_TEXT = 0xFFFF8FA3.toInt() // --jv-danger-text
        const val OK = 0xFF5FE3A1.toInt() // --jv-ok
        const val TEXT = 0xFFF2FBFF.toInt() // --jv-text
        const val TEXT_BRIGHT = 0xFFF2FBFF.toInt() // --jv-text-bright
        const val TEXT_DIM = 0xFFA9C8D8.toInt() // --jv-text-dim
        const val TEXT_FAINT = 0xFF8DB4CA.toInt() // --jv-text-faint
        const val TICK = 0xFF4E6177.toInt() // --jv-tick
        const val LINE = 0xFF0A2129.toInt() // --jv-line
        const val LINE_SOFT = 0xFF081920.toInt() // --jv-line-soft
        const val LINE_HAIR = 0xFF07151C.toInt() // --jv-line-hair
        const val WASH = 0x143FD0E8.toInt() // --jv-wash
        const val WASH_STRONG = 0x2E3FD0E8.toInt() // --jv-wash-strong
        const val GLOW = 0x593FD0E8.toInt() // --jv-glow
        const val FOCUS = 0xFF3FD0E8.toInt() // --jv-focus
        const val SCRIM = 0x80010409.toInt() // --jv-scrim
        const val ORB_SUBSTRATE = 0xFF060B16.toInt() // --jv-orb-substrate
        const val ORB_HOUSING = 0xFF01030A.toInt() // --jv-orb-housing
        const val ORB_HUB_METAL = 0xFFA8BDD2.toInt() // --jv-orb-hub-metal
        const val REACTOR_IDLE = 0xFF2C8FA3.toInt() // --jv-reactor-idle
        const val REACTOR_LISTENING = 0xFF3FD0E8.toInt() // --jv-reactor-listening
        const val REACTOR_THINKING = 0xFF3FD0E8.toInt() // --jv-reactor-thinking
        const val REACTOR_SPEAKING = 0xFF9BEAF6.toInt() // --jv-reactor-speaking
        const val REACTOR_TOOL = 0xFF3FD0E8.toInt() // --jv-reactor-tool
        const val REACTOR_GOAL = 0xFF2C8FA3.toInt() // --jv-reactor-goal
        const val REACTOR_ATTENTION = 0xFFFFB547.toInt() // --jv-reactor-attention
        const val REACTOR_WARMING = 0xFFFF9E2C.toInt() // --jv-reactor-warming
        const val REACTOR_ALERT = 0xFFFF6B8B.toInt() // --jv-reactor-alert
        const val REACTOR_KILL = 0xFFE0344B.toInt() // --jv-reactor-kill
        const val REACTOR_OFFLINE = 0xFFFFB547.toInt() // --jv-reactor-offline
        const val PAPER = 0xFFFFFFFF.toInt() // --jv-paper
        const val PERSONA_AURA = 0xFFB79CFF.toInt() // --jv-persona-aura
        const val PERSONA_JARVIS = 0xFF3FD0E8.toInt() // --jv-persona-jarvis
        const val CORE_GLOW = 0xFF062236.toInt() // --jv-core-glow
        const val CORE_HAZE = 0xFF021120.toInt() // --jv-core-haze
        const val CORE_SPEAK = 0xFF8CE6FC.toInt() // --jv-core-speak
        const val CORE_WORK = 0xFF5ADCF5.toInt() // --jv-core-work
        const val CORE_HOT = 0xFFF0FDFF.toInt() // --jv-core-hot
        const val CORE_WHITE = 0xFFF0FDFF.toInt() // --jv-core-white
        const val CORE_SPARK = 0xFFDCFAFF.toInt() // --jv-core-spark
        const val CORE_HALO = 0xFFB4F4FC.toInt() // --jv-core-halo
        const val CORE_FACE = 0xFF02080E.toInt() // --jv-core-face
        const val VIOLET = 0xFF9B8CFF.toInt() // --jv-violet
        const val MAP_LAND = 0xFF0A1722.toInt() // --jv-map-land
        const val MAP_WATER = 0xFF0D3357.toInt() // --jv-map-water
        const val MAP_PARK = 0xFF0F3329.toInt() // --jv-map-park
        const val MAP_BUILDING = 0xFF15273A.toInt() // --jv-map-building
        const val MAP_CASING = 0xFF040B12.toInt() // --jv-map-casing
        const val MAP_ROAD_MINOR = 0xFF223545.toInt() // --jv-map-road-minor
        const val MAP_ROAD = 0xFF34506A.toInt() // --jv-map-road
        const val MAP_ROAD_MAJOR = 0xFF5A7F9C.toInt() // --jv-map-road-major
        const val MAP_HALO = 0xFF040B12.toInt() // --jv-map-halo
        const val MAP_INK = 0xFFF2FBFF.toInt() // --jv-map-ink
        const val MAP_INK_DIM = 0xFFA9C8D8.toInt() // --jv-map-ink-dim
        const val MAP_PATH = 0xFF3A5368.toInt() // --jv-map-path
        const val MAP_CYCLEWAY = 0xFF2F7088.toInt() // --jv-map-cycleway
        const val MAP_TREE = 0xFF16473A.toInt() // --jv-map-tree
        const val MAP_SITE = 0x8CFFB547.toInt() // --jv-map-site
        const val MAP_LAND_DAY = 0xFFE3E9EE.toInt() // --jv-map-land-day
        const val MAP_WATER_DAY = 0xFFA8CBE4.toInt() // --jv-map-water-day
        const val MAP_PARK_DAY = 0xFFCFE3D2.toInt() // --jv-map-park-day
        const val MAP_BUILDING_DAY = 0xFFD0D8E0.toInt() // --jv-map-building-day
        const val MAP_CASING_DAY = 0xFF9FB0BF.toInt() // --jv-map-casing-day
        const val MAP_ROAD_MINOR_DAY = 0xFFF2F5F8.toInt() // --jv-map-road-minor-day
        const val MAP_ROAD_DAY = 0xFFFFFFFF.toInt() // --jv-map-road-day
        const val MAP_ROAD_MAJOR_DAY = 0xFFF3D58F.toInt() // --jv-map-road-major-day
        const val MAP_HALO_DAY = 0xFFF4F7F9.toInt() // --jv-map-halo-day
        const val MAP_INK_DAY = 0xFF0D2233.toInt() // --jv-map-ink-day
        const val MAP_INK_DIM_DAY = 0xFF3D566B.toInt() // --jv-map-ink-dim-day
        const val MAP_PATH_DAY = 0xFF8D9EAC.toInt() // --jv-map-path-day
        const val MAP_CYCLEWAY_DAY = 0xFF3F8FAB.toInt() // --jv-map-cycleway-day
        const val MAP_TREE_DAY = 0xFFA6CBAE.toInt() // --jv-map-tree-day
        const val MAP_GOLDEN = 0xFFFFB45C.toInt() // --jv-map-golden
        const val MAP_TWILIGHT = 0xFF2B4380.toInt() // --jv-map-twilight
        const val MAP_WARN_DAY = 0xFF8A5300.toInt() // --jv-map-warn-day
        const val WORLD_ASPHALT = 0xFF262A31.toInt() // --jv-world-asphalt
        const val WORLD_ASPHALT_MINOR = 0xFF1F2329.toInt() // --jv-world-asphalt-minor
        const val WORLD_PATH = 0xFF3A3A3C.toInt() // --jv-world-path
        const val WORLD_MARKING = 0xFFCFD6DC.toInt() // --jv-world-marking
        const val WORLD_MARKING_CENTRE = 0xFFC9A544.toInt() // --jv-world-marking-centre
        const val WORLD_GREEN = 0xFF1D3A25.toInt() // --jv-world-green
        const val WORLD_WOOD = 0xFF163020.toInt() // --jv-world-wood
        const val WORLD_WATER = 0xFF15324E.toInt() // --jv-world-water
        const val WORLD_PARKING = 0xFF1B1F25.toInt() // --jv-world-parking
        const val WORLD_CONSTRUCTION = 0xFF4B3B22.toInt() // --jv-world-construction
        const val WORLD_CONE = 0xFFFF7A1A.toInt() // --jv-world-cone
        const val WORLD_WALL = 0xFF9A9287.toInt() // --jv-world-wall
        const val WORLD_WALL_HOME = 0xFFB7A58E.toInt() // --jv-world-wall-home
        const val WORLD_WALL_TRADE = 0xFF8B97A3.toInt() // --jv-world-wall-trade
        const val WORLD_WALL_WORKS = 0xFF787D83.toInt() // --jv-world-wall-works
        const val WORLD_WALL_CIVIC = 0xFFA89C88.toInt() // --jv-world-wall-civic
        const val WORLD_ROOF = 0xFF4A4D54.toInt() // --jv-world-roof
        const val WORLD_ROOF_HOME = 0xFF5B4138.toInt() // --jv-world-roof-home
        const val WORLD_GLASS = 0xFF1A2A3B.toInt() // --jv-world-glass
        const val WORLD_TRUNK = 0xFF4A3829.toInt() // --jv-world-trunk
        const val WORLD_CANOPY = 0xFF2E6A39.toInt() // --jv-world-canopy
        const val WORLD_CANOPY_SHADE = 0xFF235230.toInt() // --jv-world-canopy-shade
        const val WORLD_POLE = 0xFF6B7178.toInt() // --jv-world-pole
        const val WORLD_LAMP = 0xFFFFD9A0.toInt() // --jv-world-lamp
        const val WORLD_SIGNAL = 0xFF25282C.toInt() // --jv-world-signal
        const val WORLD_SIGNAL_RED = 0xFFD8362C.toInt() // --jv-world-signal-red
        const val WORLD_SIGNAL_AMBER = 0xFFF0A81E.toInt() // --jv-world-signal-amber
        const val WORLD_SIGNAL_GREEN = 0xFF22A85A.toInt() // --jv-world-signal-green
        const val WORLD_GROUND = 0xFF1D2C16.toInt() // --jv-world-ground
        const val WORLD_FIELD = 0xFF2E2C18.toInt() // --jv-world-field
        const val WORLD_YARD = 0xFF1F3319.toInt() // --jv-world-yard
        const val WORLD_PAVED = 0xFF20242A.toInt() // --jv-world-paved
        const val WORLD_LAND = 0xFF101A1F.toInt() // --jv-world-land
        const val WORLD_LAND_LIT = 0xFF243432.toInt() // --jv-world-land-lit
        const val WORLD_PAVEMENT = 0xFF4A4E55.toInt() // --jv-world-pavement
        const val WORLD_KERB = 0xFF8A9099.toInt() // --jv-world-kerb
        const val WORLD_BARRIER = 0xFF9AA1A8.toInt() // --jv-world-barrier
        const val WORLD_SIGN_BLADE = 0xFF1F6B45.toInt() // --jv-world-sign-blade
        const val WORLD_SIGN_INK = 0xFF14171A.toInt() // --jv-world-sign-ink
        const val WORLD_SIGN_STOP = 0xFFC8102E.toInt() // --jv-world-sign-stop
        const val WORLD_SIGN_PLATE = 0xFFE8ECEF.toInt() // --jv-world-sign-plate
        const val WORLD_FASCIA_FOOD = 0xFF8C3B2E.toInt() // --jv-world-fascia-food
        const val WORLD_FASCIA_SHOP = 0xFF23566B.toInt() // --jv-world-fascia-shop
        const val WORLD_FASCIA_SERVICE = 0xFF3D4A63.toInt() // --jv-world-fascia-service
        const val WORLD_FASCIA_FUEL = 0xFF2F6B45.toInt() // --jv-world-fascia-fuel
        const val WORLD_SKY_DAY = 0xFF3D7CC4.toInt() // --jv-world-sky-day
        const val WORLD_SKY_DAY_HORIZON = 0xFFB4D3EA.toInt() // --jv-world-sky-day-horizon
        const val WORLD_SKY_DUSK = 0xFFC96F45.toInt() // --jv-world-sky-dusk
        const val WORLD_WINDOW_LIT = 0xFFF0C98C.toInt() // --jv-world-window-lit
        const val WORLD_SIGN_WARN = 0xFFF2C230.toInt() // --jv-world-sign-warn
        const val WORLD_CAMERA = 0xFF2B3036.toInt() // --jv-world-camera
        const val WORLD_CAMERA_SPEED = 0xFFE0A51E.toInt() // --jv-world-camera-speed
        const val WORLD_CAMERA_ALPR = 0xFF3E7FB0.toInt() // --jv-world-camera-alpr
        const val WORLD_HAZE = 0xFF0A1420.toInt() // --jv-world-haze
        const val TEXT_DIM_80 = 0xCCA9C8D8.toInt() // --jv-text-dim at 80%
        const val OVERLAY = 0xE6010409.toInt() // --jv-bg at 90%
        const val OVERLAY_HEAVY = 0xF0010409.toInt() // --jv-bg at 94%
        const val OVERLAY_APPROVAL = 0xF2010409.toInt() // --jv-bg at 95%
        const val ACCENT_13 = 0x223FD0E8.toInt() // --jv-accent at 13%
        const val ACCENT_20 = 0x333FD0E8.toInt() // --jv-accent at 20%
        const val ACCENT_27 = 0x443FD0E8.toInt() // --jv-accent at 27%
        const val ACCENT_33 = 0x553FD0E8.toInt() // --jv-accent at 33%
        const val WARN_13 = 0x22FFB547.toInt() // --jv-warn at 13%
        const val WARN_40 = 0x66FFB547.toInt() // --jv-warn at 40%
        const val WARN_53 = 0x88FFB547.toInt() // --jv-warn at 53%
        const val TRACK = 0x338DB4CA.toInt() // --jv-text-faint at 20%
        const val PANEL_94 = 0xF0040B13.toInt() // --jv-panel at 94%
    }

    /** The type scale in sp, named for the job. */
    object Type {
        const val LABEL = 11f
        const val HINT = 12f
        const val MONO = 13f
        const val BODY = 14f
        const val FIELD = 15f
        const val RESPONSE = 20f
        const val TITLE = 22f
    }

    /** Font weights, as the CSS numbers. */
    object Weight {
        const val DISPLAY = 300 // --jv-weight-display
        const val BODY = 400 // --jv-weight-body
        const val LABEL = 500 // --jv-weight-label
        const val STRONG = 600 // --jv-weight-strong
    }

    /** Letter spacing in em, as `TextView.letterSpacing` takes it. */
    object Tracking {
        const val LOGO = 0.42f // --jv-track-logo
        const val SNUG = 0.04f // --jv-track-snug
        const val LABEL = 0.22f // --jv-track-label
        const val CONTROL = 0.14f // --jv-track-control
    }

    /** The spacing scale in dp, named for the job. */
    object Space {
        const val HAIRLINE = 1
        const val MICRO = 2
        const val TIGHT = 4
        const val SNUG = 6
        const val STEP = 8
        const val ROW = 10
        const val GAP = 12
        const val SECTION = 16
        const val SCREEN = 20
        const val WIDE = 24
    }

    /** Control and ornament sizes in dp, named for the job. */
    object Size {
        const val CHIP = 14
        const val INSET = 18
        const val GUTTER = 22
        const val EDGE = 26
        const val SHEET = 32
        const val WIDE_BUTTON = 34
        const val DROP = 56
        const val PANEL_MAX = 340
        const val SHEET_MIN = 200
        const val TOUCH = 48
        const val CAR_MARK = 40
        const val CAR_ART = 84
        const val CAR_ART_SMALL = 52
        const val CAR_TOUCH = 52
        const val CAR_MARGIN = 34
        const val CAR_TOP = 26
        const val CAR_BOTTOM = 28
        const val CAR_CORE = 168
        const val CAR_CORE_RIGHT = 20
        const val CAR_CORE_BOTTOM = 16
        const val CAR_CARD = 380
        const val CAR_CARD_GAP = 22
        const val CAR_SUBTITLE = 520
        const val CAR_TURN_ICON = 58
        const val CAR_TEXT_TURN = 40
        const val CAR_TEXT_ETA = 34
        const val CAR_TEXT_SPEED = 26
        const val CAR_TEXT_TITLE = 30
        const val CAR_TEXT_LINE = 24
        const val CAR_LABEL_MIN = 14
        const val CAR_LABEL_MAX = 28
    }

    /** Corner radii in dp. */
    object Radius {
        const val SM = 2
        const val MD = 3
        const val LG = 4
        const val PILL = 999
    }

    /** Durations in milliseconds, and the curves, for animators. */
    object Motion {
        object Dur {
            const val INSTANT = 90
            const val FAST = 160
            const val BASE = 260
            const val SLOW = 320
            const val PULSE = 620
            const val ENTER = 900
            const val SWEEP = 1400
            const val BLINK = 2400
            const val PANEL_SUMMON = 200
            const val PANEL_DISMISS = 160
            const val PANEL_REORDER = 240
            const val PANEL_REDUCED = 80
            const val FLOAT_IN = 420
            const val FLOAT_OUT = 220
            const val GLIDE = 360
            const val SCAN = 550
            const val UNFOLD = 700
            const val BOOT = 1100
            const val TOAST = 8000
            const val APPEAR = 500
            const val EXIT = 450
            const val BAR = 400
            const val ROW_IN = 350
            const val CONNECTOR = 500
            const val BEAT = 1200
            const val SHOCK = 900
            const val KICK = 500
            const val FADE = 350
            const val UNROLL = 700
            const val HOLD = 2400
            const val UNDO = 5000
        }

        /** Cubic-bezier control points, as (x1, y1, x2, y2). */
        object Ease {
            val OUT = floatArrayOf(0.2f, 0.8f, 0.2f, 1f)
            val IN_OUT = floatArrayOf(0.65f, 0f, 0.35f, 1f)
            val OVERSHOOT = floatArrayOf(0.34f, 1.56f, 0.64f, 1f)
            val STANDARD = floatArrayOf(0.2f, 0.8f, 0.2f, 1f)
            val DECELERATE = floatArrayOf(0f, 0f, 0.2f, 1f)
            val ACCELERATE = floatArrayOf(0.4f, 0f, 1f, 1f)
            val SPRING = floatArrayOf(0.34f, 1.56f, 0.64f, 1f)
            val SCAN = floatArrayOf(0.3f, 0.7f, 0.2f, 1f)
            val APPEAR = floatArrayOf(0.5f, 0f, 0.2f, 1f)
            val EXIT = floatArrayOf(0.6f, 0f, 0.8f, 0.2f)
            val QUICK = floatArrayOf(0.2f, 0.9f, 0.25f, 1f)
            val UNROLL = floatArrayOf(0.16f, 0.9f, 0.2f, 1f)
        }

        /** Stagger between one row and the next, in milliseconds. */
        object Stagger {
            const val STEP = 26
            const val CAP = 320
            const val ROW = 70
            const val ROW_CAP = 560
            const val PROJECT = 35
        }

        /** The reactor's own clock — rotation periods and the breath — in milliseconds. */
        object Reactor {
            const val BLADES = 90000
            const val COIL = 60000
            const val IRIS_A = 20000
            const val IRIS_B = 32000
            const val BREATHE = 5000
            const val GLINT = 14000
            const val LEVEL = 3400
            const val THINK = 9000
            const val SPEAK = 600
            const val COMET = 1600
            const val HEARTBEAT = 1100
            const val CHASE = 1200
            const val RIPPLE = 1800
            const val BEACON = 1400
            const val STROBE = 900
            const val DRIFT = 7000
            const val ORBIT = 2800
            const val SWEEP = 2200
            const val TUMBLE = 24000
        }

        /** Ambient drift: rotations slow enough to read as alive rather than as motion. */
        object Ambient {
            const val BLINK_SLOW = 2600
            const val DRIFT = 18000
            const val DRIFT_SLOW = 40000
        }

        /** The boot: a panel condenses from SCALE and BLUR_DP to its own grade and scale 1. */
        object Boot {
            const val SCALE = 1.04f // --jv-boot-scale
        }
    }

    /** The three surfaces: the solid each is filled with, its hairline edge and its radius */
    object Material {

        /** Large background surfaces: the Focus rail, the Ambient strip, the phone's stack ground, a dialog's scrim. */
        object Pane {
            const val EDGE = 0x1A3FD0E8.toInt() // --jv-material-pane-edge
            const val RADIUS_DP = 3 // --jv-material-pane-radius
            const val SOLID = 0xD9030A12.toInt() // --jv-material-pane-solid
        }

        /** Every content panel. */
        object Panel {
            const val EDGE = 0x243FD0E8.toInt() // --jv-material-panel-edge
            const val EDGE_HOVER = 0x593FD0E8.toInt() // --jv-material-panel-edge-hover
            const val REFRACT_DEG = 12.0f // --jv-material-panel-refract
            const val RADIUS_DP = 3 // --jv-material-panel-radius
            const val SOLID = 0xC7040B13.toInt() // --jv-material-panel-solid
        }

        /** Glass on glass: buttons, inputs, selects, chips, toggles, the drag and resize handles, dialogs, the command palette. */
        object Control {
            const val EDGE = 0x593FD0E8.toInt() // --jv-material-control-edge
            const val RADIUS_DP = 2 // --jv-material-control-radius
            const val SOLID = 0x99040B13.toInt() // --jv-material-control-solid
        }

        /** The floating assistant, the island (T-0695): the desktop popup and the phone's overlay, over other apps, a pill at the top that grows into a card and back (tests/contracts/island.json). */
        object Floating {
            const val FILL = 0xF0040B13.toInt() // --jv-material-floating-fill
            const val EDGE = 0x383FD0E8.toInt() // --jv-material-floating-edge
            const val RADIUS_DP = 22 // --jv-material-floating-radius
            const val PILL_HEIGHT_DP = 44 // --jv-material-floating-pill-height
            const val PILL_WIDTH_DP = 212 // --jv-material-floating-pill-width
            const val REST_WIDTH_DP = 220 // --jv-material-floating-rest-width
            const val REST_HEIGHT_DP = 32 // --jv-material-floating-rest-height
            const val CORE_REST_DP = 24 // --jv-material-floating-core-rest
            const val ACTIVITY_WIDTH_DP = 300 // --jv-material-floating-activity-width
            const val CARD_WIDTH_DP = 400 // --jv-material-floating-card-width
            const val CORE_PILL_DP = 28 // --jv-material-floating-core-pill
            const val CORE_CARD_DP = 64 // --jv-material-floating-core-card
            const val DIM = 0x5C010409.toInt() // --jv-material-floating-dim
            const val BLUR_DP = 28 // --jv-material-floating-blur
            const val HALO_DP = 28 // --jv-material-floating-halo
            const val GLOW_DP = 200 // --jv-material-floating-glow
            const val ORB_DP = 120 // --jv-material-floating-orb
            const val ORB_LIGHT = 0x8CF2FBFF.toInt() // --jv-material-floating-orb-light
            const val ORB_RIM = 0x993FD0E8.toInt() // --jv-material-floating-orb-rim
            const val ORB_SHADE = 0xE6010409.toInt() // --jv-material-floating-orb-shade
        }
    }

    /** The reactor's own core brightness per state */
    object Light {

        /** The ambient halo: a radial gradient centred on the reactor, rgba(C, k) to transparent over `extent`. */
        object Halo {
            // EXTENT: 60vmin has no dp reading // --jv-light-halo-extent
            const val IDLE = 0.1f // --jv-light-halo-idle
            const val LISTENING = 0.18f // --jv-light-halo-listening
            const val THINKING = 0.18f // --jv-light-halo-thinking
            const val SPEAKING = 0.12f // --jv-light-halo-speaking
            const val SPEAKING_PEAK = 0.24f // --jv-light-halo-speaking-peak
            const val TOOL = 0.18f // --jv-light-halo-tool
            const val ATTENTION = 0.22f // --jv-light-halo-attention
            const val WARMING = 0.08f // --jv-light-halo-warming
            const val WARMING_PEAK = 0.2f // --jv-light-halo-warming-peak
            const val ALERT = 0.24f // --jv-light-halo-alert
            const val KILL = 0 // --jv-light-halo-kill
            const val OFFLINE = 0 // --jv-light-halo-offline
        }
    }

    /** The three springs, as spring(stiffness = STIFFNESS, dampingRatio = RATIO); SETTLE is the longest they may take, in ms */
    object Spring {

        /** A panel opening, moving, resizing, changing mode; layout choreography; a timeline row drawing in. */
        object Glass {
            const val STIFFNESS = 210.0f // --jv-spring-glass-stiffness
            const val DAMPING = 24.0f // --jv-spring-glass-damping
            const val MASS = 1.0f // --jv-spring-glass-mass
            const val SETTLE = 380 // --jv-spring-glass-settle
            const val RATIO = 0.83f // --jv-spring-glass-ratio
        }

        /** A dragged panel docking into a tile slot or a snap line; drag release. */
        object Snap {
            const val STIFFNESS = 380.0f // --jv-spring-snap-stiffness
            const val DAMPING = 30.0f // --jv-spring-snap-damping
            const val MASS = 1.0f // --jv-spring-snap-mass
            const val SETTLE = 260 // --jv-spring-snap-settle
            const val RATIO = 0.77f // --jv-spring-snap-ratio
        }

        /** Hover lift, a toggle, a chip, a handle appearing. */
        object Control {
            const val STIFFNESS = 520.0f // --jv-spring-control-stiffness
            const val DAMPING = 34.0f // --jv-spring-control-damping
            const val MASS = 1.0f // --jv-spring-control-mass
            const val SETTLE = 180 // --jv-spring-control-settle
            const val RATIO = 0.75f // --jv-spring-control-ratio
        }

        /** Every camera move on the map: flying to a panel, framing everything, returning to the reactor. */
        object Camera {
            const val STIFFNESS = 120.0f // --jv-spring-camera-stiffness
            const val DAMPING = 22.0f // --jv-spring-camera-damping
            const val MASS = 1.0f // --jv-spring-camera-mass
            const val SETTLE = 640 // --jv-spring-camera-settle
            const val RATIO = 1.0f // --jv-spring-camera-ratio
        }
    }
}
