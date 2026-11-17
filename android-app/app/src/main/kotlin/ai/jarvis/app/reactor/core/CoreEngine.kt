package ai.jarvis.app.reactor.core

import ai.jarvis.app.ui.theme.JarvisTokens
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * The console's core, on the phone (T-0712): `jarvis-web/src/lib/ds/core/engine.ts` and
 * `states.ts` in Kotlin, the same names and the same arithmetic, so a frame here is the frame
 * the console draws at the same moment.
 *
 * The phone used to draw `reactor/orb`, a port of the console's pre-v2 orb (deleted from the
 * web in T-0603), so the popup and the console showed two different JARVISes. The two engines
 * are bound by `tests/contracts/core_frames.json`: the web writes it from engine.ts and fails
 * when it is stale, and `CoreEngineTest` holds this file to it to 1e-4.
 *
 * Pure: no Android, no clock of its own. Time is in ms and space in core radii, as there.
 * Does not cover the drawing ([CoreCanvas]) or smoothing the inputs.
 */

/** The core's nine states (`CORE_STATES`). */
enum class CoreState(val key: String) {
    IDLE("idle"),
    LISTENING("listening"),
    SPEAKING("speaking"),
    THINKING("thinking"),
    EMBEDDING("embedding"),
    ACTING("acting"),
    MUSIC("music"),
    ALERT("alert"),
    WORKING("working");

    companion object {
        fun of(key: String): CoreState = entries.first { it.key == key }
    }
}

/**
 * Reactor state key -> core state (`REACTOR_TO_CORE`; states.ts says why each lands where it
 * does). The keys are `ReactorState.key`.
 */
val REACTOR_TO_CORE: Map<String, CoreState> = mapOf(
    "idle" to CoreState.IDLE,
    "listening" to CoreState.LISTENING,
    "thinking" to CoreState.THINKING,
    "speaking" to CoreState.SPEAKING,
    "tool" to CoreState.ACTING,
    "goal" to CoreState.WORKING,
    "attention" to CoreState.ALERT,
    "warming" to CoreState.EMBEDDING,
    "alert" to CoreState.ALERT,
    "kill" to CoreState.ALERT,
    "offline" to CoreState.ALERT,
)

/** The core state for a reactor state key; anything unknown is idle. */
fun coreStateFor(reactor: String): CoreState = REACTOR_TO_CORE[reactor] ?: CoreState.IDLE

/** What moves the core from outside, each 0..1. Null: synthesized; not finite: silence. */
data class Inputs(val level: Double? = null, val envelope: Double? = null, val music: Double? = null)

/** What a state looks like: the targets the core tweens toward (`Look`). */
class Look(
    var spin: Double,
    var gyro: Double,
    var align: Double,
    var shell: Double,
    var swirl: Double,
    var flow: Double,
    var glow: Double,
    var work: Double,
    val color: DoubleArray,
) {
    fun copy() = Look(spin, gyro, align, shell, swirl, flow, glow, work, color.copyOf())

    /** The eight tweened numbers by their engine.ts names, colour aside. */
    fun params(): List<Pair<String, Double>> = listOf(
        "spin" to spin, "gyro" to gyro, "align" to align, "shell" to shell,
        "swirl" to swirl, "flow" to flow, "glow" to glow, "work" to work,
    )
}

/** The model a renderer draws; [CoreEngine.step] advances it in place, every frame. */
class Core(var state: CoreState, var look: Look, var t: Double, val still: Boolean) {
    var spinA = 0.0
    var gyroA = 0.0
    var shellA = 0.0
    var flowA = 0.0
    var amp = 0.0
    var beat = 0.5
    var kickAt = Double.NEGATIVE_INFINITY
    var rings: MutableList<Double> = mutableListOf()
    var beats = 0
    var lastBeat = t
    var changes = 0
}

/** The particle shell: unit-sphere positions, which are hot, and each point's pace and phase. */
class Shell(val n: Int, val pos: FloatArray, val hot: BooleanArray, val speed: FloatArray, val phase: FloatArray)

object CoreEngine {
    /** One 60Hz frame, in ms: the reference's tween rates are per frame at this rate. */
    const val FRAME = 1000.0 / 60
    private const val MAX_DT = 100.0
    private const val EASE = 0.035
    private const val EASE_COLOR = 0.05
    private const val MAX_RINGS = 4
    private const val MAX_FROZEN = 60_000.0

    const val TILT = 0.28
    const val CAMERA = 3.3
    const val SPREAD = 0.36
    const val BOARD_WIDTH = 600.0
    const val STRUT_IN = 0.2

    private val BEAT = JarvisTokens.Motion.Dur.BEAT.toDouble()
    private val SHOCK = JarvisTokens.Motion.Dur.SHOCK.toDouble()
    private val KICK = JarvisTokens.Motion.Dur.KICK.toDouble()

    /**
     * One ring. [w] scales its segment's radial extent, [d] its depth, and [gap] is its own gap
     * (T-0748: each ring is its own ring, as engine.ts RINGS has it).
     */
    class Ring(val r: Double, val seg: Int, val a: Double, val b: Double, val dir: Double, val w: Double, val d: Double, val gap: Double) {
        val inner get() = Segment.INNER * w
        val outer get() = Segment.OUTER * w
        val depth get() = Segment.DEPTH * d
    }

    val RINGS = listOf(
        Ring(0.58, 12, 1.35, 0.0, 1.0, 1.15, 0.8, 0.08),
        Ring(0.455, 10, 0.2, 0.95, -1.0, 0.85, 1.0, 0.12),
        Ring(0.35, 8, 0.2, -0.95, 1.4, 0.6, 1.2, 0.16),
    )

    /** A segment before its ring's own scale. */
    object Segment {
        const val INNER = 0.07
        const val OUTER = 0.05
        const val DEPTH = 0.05
        const val WINDINGS = 5
    }

    object Flow {
        const val DOTS = 26
        const val TRAIL = 0.07
        const val COMET = 14
        /** Outside the ring's outer edge. */
        const val LIFT = 0.05
    }

    /** An ARGB token as [r, g, b] 0..255, as `rgbOf` reads a `#rrggbb`. */
    fun rgbOf(argb: Int): DoubleArray =
        doubleArrayOf(((argb shr 16) and 255).toDouble(), ((argb shr 8) and 255).toDouble(), (argb and 255).toDouble())

    /** The token each state's colour comes from (`LOOK_TOKEN`). */
    val LOOK_TOKEN: Map<CoreState, Int> = mapOf(
        CoreState.IDLE to JarvisTokens.Color.ACCENT,
        CoreState.LISTENING to JarvisTokens.Color.ACCENT,
        CoreState.SPEAKING to JarvisTokens.Color.CORE_SPEAK,
        CoreState.THINKING to JarvisTokens.Color.VIOLET,
        CoreState.EMBEDDING to JarvisTokens.Color.VIOLET,
        CoreState.ACTING to JarvisTokens.Color.ACCENT,
        CoreState.MUSIC to JarvisTokens.Color.ACCENT,
        CoreState.ALERT to JarvisTokens.Color.DANGER,
        CoreState.WORKING to JarvisTokens.Color.CORE_WORK,
    )

    @Suppress("LongParameterList")
    private fun look(s: CoreState, spin: Double, gyro: Double, align: Double, shell: Double, swirl: Double, flow: Double, glow: Double, work: Double) =
        s to Look(spin, gyro, align, shell, swirl, flow, glow, work, rgbOf(LOOK_TOKEN.getValue(s)))

    /** The nine looks (the reference's targets). */
    val LOOKS: Map<CoreState, Look> = mapOf(
        look(CoreState.IDLE, 0.18, 0.12, 0.0, 1.0, 0.0, 0.2, 0.7, 0.0),
        look(CoreState.LISTENING, 0.35, 0.05, 1.0, 1.02, 0.0, 0.3, 0.9, 0.0),
        look(CoreState.SPEAKING, 0.45, 0.08, 0.65, 1.02, 0.0, 0.35, 1.1, 0.0),
        look(CoreState.THINKING, 1.6, 1.1, 0.0, 0.84, 1.4, 1.6, 0.85, 0.0),
        look(CoreState.EMBEDDING, 0.9, 0.5, 0.2, 0.9, 0.4, 0.9, 1.0, 0.0),
        look(CoreState.ACTING, 0.9, 0.45, 0.15, 0.94, 0.5, 0.9, 0.95, 0.0),
        look(CoreState.MUSIC, 0.55, 0.25, 0.3, 1.04, 0.2, 0.5, 1.0, 0.0),
        look(CoreState.ALERT, 0.05, 0.0, 1.0, 0.96, 0.0, 0.1, 0.8, 0.0),
        look(CoreState.WORKING, 1.1, 0.2, 0.0, 0.9, 0.25, 1.3, 1.0, 1.0),
    )

    /** The palette's fixed colours: the core's white, its spark, halo, hot points and dark faces. */
    val PALETTE: Map<String, DoubleArray> = mapOf(
        "white" to rgbOf(JarvisTokens.Color.CORE_WHITE),
        "spark" to rgbOf(JarvisTokens.Color.CORE_SPARK),
        "halo" to rgbOf(JarvisTokens.Color.CORE_HALO),
        "hot" to rgbOf(JarvisTokens.Color.CORE_HOT),
        "face" to rgbOf(JarvisTokens.Color.CORE_FACE),
    )

    fun createCore(state: CoreState = CoreState.IDLE, now: Double = 0.0, still: Boolean = false): Core =
        Core(state, LOOKS.getValue(state).copy(), now, still)

    private fun ring(core: Core, now: Double) {
        core.rings = core.rings.filter { now - it < SHOCK }.toMutableList()
        core.rings.add(now)
        if (core.rings.size > MAX_RINGS) core.rings.removeAt(0)
    }

    /**
     * Change state at [now]: a real change kicks the glow and fires a shock ring, then tweens;
     * with [snap] (reduced motion; `core.still` by default) it lands at once and fires nothing.
     */
    fun setState(core: Core, state: CoreState, now: Double, snap: Boolean = core.still): Boolean {
        if (state == core.state) return false
        core.state = state
        core.changes += 1
        core.lastBeat = now
        if (snap) {
            core.look = LOOKS.getValue(state).copy()
            return true
        }
        core.kickAt = now
        ring(core, now)
        return true
    }

    private fun unit(v: Double?, fallback: Double): Double = when {
        v == null -> fallback
        v.isFinite() -> min(1.0, max(0.0, v))
        else -> 0.0
    }

    private fun ampFor(core: Core, now: Double, inputs: Inputs): Double = when (core.state) {
        CoreState.LISTENING -> unit(inputs.level, 0.5 + 0.5 * abs(sin(now * 0.006) * sin(now * 0.0017)))
        CoreState.SPEAKING -> unit(inputs.envelope, core.beat)
        CoreState.MUSIC -> unit(inputs.music, abs(sin(now * 0.0065)).pow(3))
        else -> 0.0
    }

    /** Advance the model to [now]: tween, turn, read the inputs, beat. */
    fun step(core: Core, now: Double, inputs: Inputs = Inputs()) {
        val dt = min(MAX_DT, max(0.0, now - core.t))
        core.t = now
        val target = LOOKS.getValue(core.state)
        val l = core.look
        // k per 60Hz frame is 1-(1-k)^(dt/frame) per dt: 120Hz and 60Hz land in the same place.
        val f = 1 - (1 - EASE).pow(dt / FRAME)
        val fc = 1 - (1 - EASE_COLOR).pow(dt / FRAME)
        l.spin += (target.spin - l.spin) * f
        l.gyro += (target.gyro - l.gyro) * f
        l.align += (target.align - l.align) * f
        l.shell += (target.shell - l.shell) * f
        l.swirl += (target.swirl - l.swirl) * f
        l.flow += (target.flow - l.flow) * f
        l.glow += (target.glow - l.glow) * f
        l.work += (target.work - l.work) * f
        for (i in 0 until 3) l.color[i] += (target.color[i] - l.color[i]) * fc

        core.spinA += dt * 0.001 * l.spin
        core.gyroA += dt * 0.001 * l.gyro
        core.shellA += dt * 0.001 * (0.12 + l.flow * 0.25)
        core.flowA += dt * 0.001 * (0.6 + l.flow * 1.6)
        core.beat = 0.5 + 0.5 * sin(now * 0.008) * sin(now * 0.0031)
        core.amp = ampFor(core, now, inputs)

        if (core.state == CoreState.WORKING && !core.still && now - core.lastBeat >= BEAT) {
            // On the beat's own grid, so a late frame does not push every later beat.
            core.lastBeat = now - ((now - core.lastBeat) % BEAT)
            core.beats += 1
            core.kickAt = now
            ring(core, now)
        }
    }

    /** The glow kick after a change or a beat: 1 at once, 0 after the kick time. */
    fun kick(core: Core, now: Double): Double = min(1.0, max(0.0, 1 - (now - core.kickAt) / KICK))

    /** Each live shock ring's progress, 0 (just fired) to 1 (gone). */
    fun shocks(core: Core, now: Double): DoubleArray =
        core.rings.map { (now - it) / SHOCK }.filter { it >= 0 && it < 1 }.toDoubleArray()

    class ShockRing(val radius: Double, val width: Double, val alpha: Double)

    /** A shock ring at progress f: radius (core radii), width (board px) and alpha. */
    fun shockRing(f: Double) = ShockRing(0.2 + 1.1 * f, 6 - 5 * f, 0.8 * (1 - f))

    class Glow(val glow: Double, val radius: Double)

    /** The core's glow (brightness) and radius now. */
    fun coreGlow(core: Core): Glow {
        val flick = if (core.state == CoreState.ALERT) 0.8 + 0.2 * sin(core.t * 0.03) else 1.0
        return Glow(
            glow = core.look.glow * flick * (1 + 0.25 * core.amp) + 0.6 * kick(core, core.t),
            radius = 0.15 * (1 + 0.12 * core.amp),
        )
    }

    /** A repeatable 0..1 for point i: the shell is the same on every load and in every test. */
    private fun hash(i: Int, salt: Int): Double {
        val x = sin(i * 12.9898 + salt * 78.233) * 43758.5453
        return x - floor(x)
    }

    /** [n] points spread evenly over the unit sphere (the golden-angle spiral); about 3% hot. */
    fun fibonacci(n: Int): Shell {
        val count = max(0, n)
        val pos = FloatArray(count * 3)
        val hot = BooleanArray(count)
        val speed = FloatArray(count)
        val phase = FloatArray(count)
        val ga = PI * (3 - sqrt(5.0))
        for (i in 0 until count) {
            val y = 1 - ((i + 0.5) / count) * 2
            val r = sqrt(1 - y * y)
            pos[i * 3] = (cos(i * ga) * r).toFloat()
            pos[i * 3 + 1] = y.toFloat()
            pos[i * 3 + 2] = (sin(i * ga) * r).toFloat()
            hot[i] = hash(i, 1) < 0.03
            speed[i] = (0.8 + 0.4 * hash(i, 2)).toFloat()
            phase[i] = hash(i, 3).toFloat()
        }
        return Shell(count, pos, hot, speed, phase)
    }

    /**
     * The shell now, into [out] (4 floats a point: x, y, z before the tilt, and an alpha). The
     * radius breathes by state; while embedding each point falls in and fades, and starts again.
     */
    fun shellInto(core: Core, shell: Shell, out: FloatArray) {
        val l = core.look
        val t = core.t
        val amp = core.amp
        for (i in 0 until shell.n) {
            val px = shell.pos[i * 3].toDouble()
            val py = shell.pos[i * 3 + 1].toDouble()
            val pz = shell.pos[i * 3 + 2].toDouble()
            val sp = shell.speed[i].toDouble()
            var k = l.shell
            var alpha = 1.0
            when (core.state) {
                CoreState.LISTENING -> k *= 1 + 0.07 * amp * sin(py * 9 - t * 0.012)
                CoreState.SPEAKING -> k *= 1 + 0.07 * amp
                CoreState.MUSIC -> k *= 1 + 0.09 * amp * (0.5 + 0.5 * sin(py * 5 + px * 3))
                CoreState.ALERT -> k *= 1 + 0.012 * sin(t * 0.05 + px * 40)
                CoreState.EMBEDDING -> {
                    val run = t * 0.00045 * sp + shell.phase[i]
                    val life = 1 - (run - floor(run))
                    k *= 0.25 + 0.75 * life
                    alpha = 0.25 + 0.75 * life
                }
                else -> k *= 1 + 0.018 * sin(t * 0.002 + py * 3)
            }
            // One speed for the whole shell, as engine.ts: a speed a point clumped the spiral (T-0749).
            val a = core.shellA * (1 + l.swirl * py * py) + l.swirl * py * 0.8
            val c = cos(a)
            val s = sin(a)
            out[i * 4] = ((px * c + pz * s) * k).toFloat()
            out[i * 4 + 1] = (py * k).toFloat()
            out[i * 4 + 2] = ((pz * c - px * s) * k).toFloat()
            out[i * 4 + 3] = alpha.toFloat()
        }
    }

    class Pose(val a: Double, val b: Double, val pulse: Double)

    /** Ring [ri] now: turned a about X, then b about Y, scaled by the pulse with the voice or the beat. */
    fun ringPose(core: Core, ri: Int): Pose {
        val r = RINGS[ri]
        val al = core.look.align
        val wk = core.look.work
        val t = core.t
        val a = r.a * (1 - al) + sin(t * 0.0003 + ri) * 0.12 * (1 - al) + wk * sin(t * 0.0016 + ri * 2.1) * 0.7
        val b = (r.b + core.gyroA * r.dir) * (1 - al) + (ri - 1) * 0.08 * al + wk * cos(t * 0.0012 + ri * 2.1) * 0.7
        val pulse = if (core.state == CoreState.SPEAKING || core.state == CoreState.MUSIC) 1 + 0.05 * core.amp else 1.0
        return Pose(a, b, pulse)
    }

    /** How lit segment k is: a running wave while thinking or playing, steady otherwise. */
    fun segmentLit(core: Core, k: Int): Double = when (core.state) {
        CoreState.MUSIC -> 0.6 + 0.8 * max(0.0, sin(core.t * 0.013 + k * 1.3))
        CoreState.THINKING -> 0.7 + 0.6 * max(0.0, sin(core.t * 0.02 - k))
        else -> 1.0
    }

    /** The angle of flowing dot i on ring ri now. */
    fun flowAngle(core: Core, ri: Int, i: Int): Double =
        core.flowA * RINGS[ri].dir * 0.8 + (i.toDouble() / Flow.DOTS) * PI * 2 + ri

    /** The comet's head on ring ri now: it runs the other way round, faster. */
    fun cometAngle(core: Core, ri: Int): Double = -core.flowA * RINGS[ri].dir * 1.3 + ri * 2

    /** The frame [at] ms after a change from [from] to [state], stepped at 60Hz (`frozenAt`). */
    fun frozenAt(state: CoreState, from: CoreState = CoreState.IDLE, at: Double = 0.0, inputs: Inputs = Inputs()): Core {
        val end = if (at.isFinite()) min(MAX_FROZEN, max(0.0, at)) else 0.0
        val core = createCore(from, 0.0)
        setState(core, state, 0.0)
        val n = floor(end / FRAME).toInt()
        for (i in 1..n) step(core, i * FRAME, inputs)
        if (core.t != end) step(core, end, inputs)
        return core
    }
}
