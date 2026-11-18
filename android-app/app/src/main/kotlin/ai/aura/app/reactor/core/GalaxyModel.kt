package ai.aura.app.reactor.core

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisThemes
import ai.aura.app.ui.theme.JarvisTokens
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/*
 * AURA's core on the phone and in the car (T-0871): the console's galaxy
 * (aura-web/src/lib/ds/core/galaxy.ts, itself ported from the pack's galaxy_engine.js)
 * as a pure model and a draw list. [GalaxyModel] advances the model; [GalaxyFrame] writes one
 * frame as a flat list of squares, soft blobs and line segments in px, in draw order, with
 * the blend each run takes; [GalaxyCanvas] draws that list.
 *
 * The per-state targets, the palette, the tint, the star budgets and the rings are the pack's
 * (`JarvisThemes.AURA`, generated from themes/aura/theme.json); the shape constants are the
 * reference's, as the console has them. The per-frame tween rates are made frame-rate
 * independent the way [CoreEngine] does it.
 *
 * Does not cover: the reference's icon mode, a persona hue over the galaxy (AURA's palette is
 * her hue), or the pixels (goldens hold those).
 */

/** How many stars: the pack's budget for a desktop, a phone, or a dock (the car's corner). */
enum class GalaxyQuality(val budget: String) {
    DESKTOP("starsDesktop"),
    PHONE("starsPhone"),
    MINI("starsMini"),
}

class GalaxyStar(
    val r: Double,
    val th: Double,
    val z: Double,
    val c: DoubleArray,
    val sz: Double,
    val b: Double,
    val hot: Boolean,
    val arm: Boolean,
    val tw: Boolean,
    val ph: Double,
    val sp: Double,
    /** Embed: how far out the star is on its fall into the core, 1 -> 0, then round again. */
    var life: Double,
)

class GalaxyBlob(val r: Double, val th: Double, val s: Double, val c: DoubleArray, val ph: Double)

class GalaxyJet(var u: Double, val sgn: Double, val jx: Double, val jy: Double, val sp: Double)

/** The model a [GalaxyFrame] draws; [GalaxyModel.step] advances it in place. */
class Galaxy(
    var mode: CoreState,
    var state: String,
    val quality: GalaxyQuality,
    val stars: List<GalaxyStar>,
    val nebulae: List<GalaxyBlob>,
    val dust: List<GalaxyBlob>,
    val knots: List<GalaxyBlob>,
    val jets: List<GalaxyJet>,
    /** The look now, part way to the state's targets, indexed as [GalaxyModel.PARAMS]. */
    val look: DoubleArray,
    val tint: DoubleArray,
    /** Reduced motion: changes snap, no flare, no ring, no beat. */
    val still: Boolean,
    /** The pack it was built from: its numbers drive [GalaxyModel.step] and [GalaxyFrame]. */
    val pack: GalaxyPack,
) {
    val mini get() = quality == GalaxyQuality.MINI
    var t = 0.0
    var rot = 0.0
    var amp = 0.0
    var kickAt = Double.NEGATIVE_INFINITY
    var lastBeat = 0.0
    var beats = 0
    var changes = 0
}

/**
 * One galaxy pack's numbers, read from its [JarvisThemes.Theme] once: what a [Galaxy] was built
 * from and is stepped and drawn with. Does not cover a pack without the galaxy renderer; a
 * galaxy is only ever made from [GalaxyModel.packOf], which falls back to the default's.
 */
class GalaxyPack internal constructor(val theme: JarvisThemes.Theme) {
    val id: String get() = theme.id
    val tween = theme.coreTween.toDouble()

    /** The arms' winding: a star at radius r sits `r × wind` radians along its arm. */
    val wind = param("wind")
    val flareMs = param("flareMs")

    internal fun param(name: String): Double = theme.coreParams.getValue(name).toDouble()

    internal val palette: Map<String, DoubleArray> = theme.corePalette.mapValues { GalaxyModel.rgb(it.value) }
    internal fun pal(role: String): DoubleArray = palette.getValue(role)

    /** Each state's targets, indexed as [GalaxyModel.PARAMS]. */
    internal val looks: Map<String, DoubleArray> = theme.coreStates.mapValues { (_, row) ->
        DoubleArray(GalaxyModel.PARAMS.size) { (row[GalaxyModel.PARAMS[it]] ?: 0f).toDouble() }
    }
    internal val tints: Map<String, DoubleArray> = theme.coreTints.mapValues { GalaxyModel.rgb(it.value) }

    fun stars(quality: GalaxyQuality): Int = param(quality.budget).toInt()
}

object GalaxyModel {
    private const val GALAXY = "galaxy"

    /** Every pack that draws the galaxy, by id. Lazy: a [GalaxyPack] reads [PARAMS], declared below. */
    private val PACKS: Map<String, GalaxyPack> by lazy {
        JarvisThemes.ALL.filter { it.renderer == GALAXY }.associate { it.id to GalaxyPack(it) }
    }

    /** The galaxy pack for [theme]; the default galaxy pack's for a theme that draws another core. */
    fun packOf(theme: JarvisThemes.Theme): GalaxyPack =
        PACKS[theme.id] ?: PACKS.getValue(JarvisThemes.AURA.id)

    /**
     * The worn persona's galaxy pack. A composable that remembers a [Galaxy] reads this and keys
     * on it, so a change of persona builds a new model rather than stepping the old pack's.
     */
    fun worn(): GalaxyPack = packOf(ActiveTheme.theme)

    /** The longest step one frame may take: back after a minute away resumes, not leaps. */
    private const val MAX_DT = 50.0
    private const val MAX_FROZEN = 60_000.0
    /** The colour tween per 60Hz frame (the reference's). */
    private const val EASE_COLOR = 0.05
    private val BEAT = JarvisTokens.Motion.Dur.BEAT.toDouble()
    internal val SHOCK = JarvisTokens.Motion.Dur.SHOCK.toDouble()
    internal val KICK = JarvisTokens.Motion.Dur.KICK.toDouble()

    /** The tweened numbers, in the pack's names; a state that omits one wants 0. */
    val PARAMS = listOf("spin", "tilt", "radius", "wind", "glow", "comets", "jets", "accretion", "tintAmount")
    const val SPIN = 0
    const val TILT = 1
    const val RADIUS = 2
    const val WIND_NOW = 3
    const val GLOW = 4
    const val COMETS = 5
    const val JETS = 6
    const val ACCRETION = 7
    const val TINT_AMOUNT = 8

    /** How the core's nine states land on the pack's eight (galaxy.ts `GALAXY_STATE`). */
    val GALAXY_STATE: Map<CoreState, String> = mapOf(
        CoreState.IDLE to "idle",
        CoreState.LISTENING to "listen",
        CoreState.SPEAKING to "speak",
        CoreState.THINKING to "think",
        CoreState.EMBEDDING to "embed",
        // A tool call inside a turn moves like work (comets, quick spin) but without its beat.
        CoreState.ACTING to "work",
        CoreState.MUSIC to "music",
        CoreState.ALERT to "alert",
        CoreState.WORKING to "work",
    )

    /** A pack colour "#RRGGBB" as [r, g, b] 0..255. */
    fun rgb(hex: String): DoubleArray = CoreEngine.rgbOf(hex.removePrefix("#").toInt(HEX))

    private const val HEX = 16

    fun stars(quality: GalaxyQuality, pack: GalaxyPack = worn()): Int = pack.stars(quality)

    /** The reference's generator: Park–Miller, so every load (and every test) gets the same sky. */
    private class Rng(private var s: Long) {
        fun rnd(): Double {
            s = s * 16807 % 2147483647
            return s / 2147483647.0
        }

        fun gauss(): Double {
            val u = rnd().let { if (it == 0.0) 1e-9 else it }
            return sqrt(-2 * ln(u)) * cos(6.283 * rnd())
        }
    }

    private fun mix(a: DoubleArray, b: DoubleArray, f: Double) =
        DoubleArray(3) { a[it] + (b[it] - a[it]) * f }

    private fun armAngle(pack: GalaxyPack, arm: Int, r: Double) = arm * PI + r * pack.wind

    /** A galaxy of [pack]'s stars for [quality], in [state], its look already there. */
    fun create(
        quality: GalaxyQuality = GalaxyQuality.PHONE,
        state: CoreState = CoreState.IDLE,
        still: Boolean = false,
        pack: GalaxyPack = worn(),
    ): Galaxy {
        val rng = Rng(SEED)
        val rnd = rng::rnd
        val gauss = rng::gauss
        val mini = quality == GalaxyQuality.MINI
        fun pal(role: String) = pack.pal(role)
        fun armAngle(arm: Int, r: Double) = armAngle(pack, arm, r)
        val warm = pal("warm")
        val pink = pal("pink")
        val lav = pal("lavender")
        val blue = pal("blue")
        val gold = pal("gold")
        val ice = pal("ice")
        val budget = pack.stars(quality)
        val sky = ArrayList<GalaxyStar>(budget)
        repeat(budget) {
            val k = rnd()
            var r: Double
            val th: Double
            val z: Double
            var c: DoubleArray
            var sz: Double
            var b: Double
            var arm = false
            if (k < 0.2) {
                // The bulge: warm, thick, near the centre.
                r = min(0.32, abs(gauss()) * 0.1)
                th = rnd() * 6.283
                z = gauss() * 0.06 * (1 - r * 2.5)
                c = mix(warm, pink, r / 0.32)
                sz = 0.7 + rnd() * 0.9
                b = 0.55 + rnd() * 0.45
            } else if (k < 0.9) {
                // The two arms: pink inside, lavender, then blue at the rim.
                arm = true
                val a = floor(rnd() * 2).toInt()
                r = 0.08 + rnd().pow(0.8) * 0.95
                th = armAngle(a, r) + gauss() * (0.12 + 0.14 * r)
                r = max(0.05, r + gauss() * 0.025)
                z = gauss() * 0.022
                c = if (r < 0.35) {
                    mix(pink, lav, (r - 0.08) / 0.27)
                } else {
                    val f = min(1.0, (r - 0.35) / 0.6)
                    mix(lav, blue, f * (if (rnd() < 0.5) 1.0 else 0.4))
                }
                sz = 0.5 + rnd() * 1.1
                b = 0.35 + rnd() * 0.55
            } else {
                // The halo: faint, scattered.
                r = 0.15 + rnd() * 0.95
                th = rnd() * 6.283
                z = gauss() * 0.2
                c = mix(lav, ice, rnd())
                sz = 0.4 + rnd() * 0.6
                b = 0.12 + rnd() * 0.25
            }
            val hot = rnd() < 0.028
            if (hot) {
                c = if (rnd() < 0.5) ice else gold
                sz *= 1.9
                b = 1.0
            }
            val rounded = DoubleArray(3) { Math.round(c[it]).toDouble() }
            val tw = rnd() < 0.35
            val ph = rnd() * 6.283
            val sp = 0.6 + rnd() * 0.8
            sky += GalaxyStar(r, th, z, rounded, sz, b, hot, arm, tw, ph, sp, rnd())
        }
        val nebulae = ArrayList<GalaxyBlob>()
        repeat(pack.param(if (mini) "nebulaeMini" else "nebulae").toInt()) { i ->
            val r = 0.2 + rnd() * 0.7
            val th = armAngle(i % 2, r) + gauss() * 0.15
            val s = 0.12 + rnd() * 0.2
            nebulae += GalaxyBlob(r, th, s, listOf(pink, lav, blue)[floor(rnd() * 3).toInt()], 0.0)
        }
        val dust = ArrayList<GalaxyBlob>()
        val knots = ArrayList<GalaxyBlob>()
        if (!mini) {
            repeat(pack.param("dustLanes").toInt()) { i ->
                val r = 0.14 + rnd() * 0.72
                val th = armAngle(i % 2, r) - 0.2 - rnd() * 0.08
                dust += GalaxyBlob(r, th, 0.04 + rnd() * 0.05, pal("dust"), 0.0)
            }
            repeat(pack.param("knots").toInt()) { i ->
                val r = 0.28 + rnd() * 0.62
                val th = armAngle(i % 2, r) + gauss() * 0.07
                val ph = rnd() * 6.283
                knots += GalaxyBlob(r, th, 0.0, if (rnd() < 0.7) pal("knot") else pal("knotCool"), ph)
            }
        }
        val jets = ArrayList<GalaxyJet>()
        repeat(pack.param(if (mini) "jetsMini" else "jets").toInt()) { i ->
            val u = rnd()
            val sgn = if (i % 2 == 1) 1.0 else -1.0
            val jx = gauss()
            val jy = gauss()
            jets += GalaxyJet(u, sgn, jx, jy, 0.7 + rnd() * 0.6)
        }
        val key = GALAXY_STATE.getValue(state)
        return Galaxy(
            state, key, quality, sky, nebulae, dust, knots, jets,
            pack.looks.getValue(key).copyOf(), pack.tints[key]?.copyOf() ?: DoubleArray(3), still, pack,
        )
    }

    private const val SEED = 7L

    /**
     * Change state. A real change flares the core and fires a shock ring, then tweens; with
     * [snap] (reduced motion by default) it lands at once and fires nothing. Whether it changed.
     */
    fun setState(g: Galaxy, mode: CoreState, snap: Boolean = g.still): Boolean {
        if (mode == g.mode) return false
        g.mode = mode
        g.state = GALAXY_STATE.getValue(mode)
        g.changes += 1
        g.lastBeat = g.t
        if (snap) {
            g.pack.looks.getValue(g.state).copyInto(g.look)
            g.pack.tints[g.state]?.copyInto(g.tint)
            return true
        }
        g.kickAt = g.t
        return true
    }

    private fun unit(v: Double?, fallback: Double): Double =
        if (v == null) fallback else if (v.isFinite()) min(1.0, max(0.0, v)) else 0.0

    /** 0..1: the mic while listening, the voice while speaking, the beat while playing. */
    private fun ampFor(g: Galaxy, inputs: Inputs): Double {
        val t = g.t
        return when (g.state) {
            "listen" -> unit(inputs.level, 0.5 + 0.5 * abs(sin(t * 0.006) * sin(t * 0.0017)))
            "speak" -> unit(inputs.envelope, 0.5 + 0.5 * sin(t * 0.008) * sin(t * 0.0031))
            "music" -> unit(inputs.music, abs(sin(t * 0.0065)).pow(3))
            else -> 0.0
        }
    }

    /** Advance [g] by [dt] ms: tween, turn, fall, beat. */
    fun step(g: Galaxy, dt: Double, inputs: Inputs = Inputs()) {
        val d = if (dt.isFinite()) min(MAX_DT, max(0.0, dt)) else 0.0
        g.t += d
        val to = g.pack.looks.getValue(g.state)
        val f = if (g.still) 1.0 else 1 - (1 - g.pack.tween).pow(d / CoreEngine.FRAME)
        val fc = if (g.still) 1.0 else 1 - (1 - EASE_COLOR).pow(d / CoreEngine.FRAME)
        for (i in to.indices) g.look[i] += (to[i] - g.look[i]) * f
        // The tint only moves toward a state that has one; leaving it, the amount fades instead.
        g.pack.tints[g.state]?.let { tint -> for (i in 0..2) g.tint[i] += (tint[i] - g.tint[i]) * fc }
        g.rot += d * 0.001 * g.look[SPIN]
        g.amp = ampFor(g, inputs)
        if (g.state == "embed") {
            for (s in g.stars) {
                s.life -= d * 0.00035 * s.sp
                if (s.life < 0) s.life += 1
            }
        }
        if (g.look[JETS] > 0.02) {
            for (j in g.jets) {
                j.u += d * 0.0005 * j.sp * (1 + g.look[SPIN] * 0.4)
                if (j.u > 1) j.u -= 1
            }
        }
        if (g.mode == CoreState.WORKING && !g.still && g.t - g.lastBeat >= BEAT) {
            // On the beat's own grid, so a late frame does not push every later beat.
            g.lastBeat = g.t - ((g.t - g.lastBeat) % BEAT)
            g.beats += 1
            g.kickAt = g.t
        }
    }

    /** The galaxy [at] ms after a change from [from] to [state], stepped at 60Hz: the same picture every run. */
    fun at(
        state: CoreState,
        from: CoreState = CoreState.IDLE,
        at: Double = 0.0,
        quality: GalaxyQuality = GalaxyQuality.PHONE,
        inputs: Inputs = Inputs(),
        pack: GalaxyPack = worn(),
    ): Galaxy {
        val end = if (at.isFinite()) min(MAX_FROZEN, max(0.0, at)) else 0.0
        val g = create(quality, from, pack = pack)
        setState(g, state)
        val n = floor(end / CoreEngine.FRAME).toInt()
        repeat(n) { step(g, CoreEngine.FRAME, inputs) }
        step(g, end - n * CoreEngine.FRAME, inputs)
        return g
    }
}

/**
 * One frame as instances: x0, y0, x1, y1, width, shape, r, g, b (0..1), alpha at start, alpha
 * at end ([STRIDE] floats each), in runs of one blend ([batchOver]: source-over for the dust
 * lanes, which darken; every other run adds light). Grows by doubling, never shrinks.
 */
class GalaxyDrawList(capacity: Int = 4096) {
    var data = FloatArray(capacity * STRIDE)
        private set
    var n = 0
        private set
    var batches = 0
        private set
    val batchStart = IntArray(MAX_BATCHES)
    val batchCount = IntArray(MAX_BATCHES)
    val batchOver = BooleanArray(MAX_BATCHES)
    var width = 0f
    var height = 0f

    fun clear() {
        n = 0
        batches = 0
    }

    fun blend(over: Boolean) {
        if (batches > 0 && batchOver[batches - 1] == over) return
        if (batches > 0 && batchCount[batches - 1] == 0) {
            batchOver[batches - 1] = over
            return
        }
        batchStart[batches] = n
        batchCount[batches] = 0
        batchOver[batches] = over
        batches += 1
    }

    fun put(shape: Int, x0: Double, y0: Double, x1: Double, y1: Double, w: Double, c: DoubleArray, a0: Double, a1: Double = a0) {
        if (!(a0 > MIN_ALPHA || a1 > MIN_ALPHA) || !(w > 0)) return
        if ((n + 1) * STRIDE > data.size) data = data.copyOf(data.size * 2)
        val o = n * STRIDE
        data[o] = x0.toFloat()
        data[o + 1] = y0.toFloat()
        data[o + 2] = x1.toFloat()
        data[o + 3] = y1.toFloat()
        data[o + 4] = w.toFloat()
        data[o + 5] = shape.toFloat()
        data[o + 6] = (c[0] / 255).toFloat()
        data[o + 7] = (c[1] / 255).toFloat()
        data[o + 8] = (c[2] / 255).toFloat()
        data[o + 9] = min(1.0, max(0.0, a0)).toFloat()
        data[o + 10] = min(1.0, max(0.0, a1)).toFloat()
        n += 1
        batchCount[batches - 1] += 1
    }

    companion object {
        const val STRIDE = 11
        /** A square of half-side `width`; a soft disc of radius `width`; a line `width` wide. */
        const val SQUARE = 0
        const val BLOB = 1
        const val LINE = 2
        private const val MAX_BATCHES = 8
        private const val MIN_ALPHA = 0.002
    }
}

/**
 * Writes one frame of a [Galaxy] into a [GalaxyDrawList] (galaxy.ts `galaxyInto`). Holds the
 * projection's scratch, so a frame allocates nothing; one per drawing thread.
 */
class GalaxyFrame {
    private var qx = 0.0
    private var qy = 0.0
    private var qz = 0.0
    private var ct = 1.0
    private var st = 0.0
    private var cp = 1.0
    private var sp = 0.0
    private var cx = 0.0
    private var cy = 0.0
    private var spread = 0.0
    private var sc = 0.0
    private val col = DoubleArray(3)

    private fun p(x: Double, y: Double, z: Double) {
        val y1 = y * ct - z * st
        val z1 = y * st + z * ct
        val s = CAMERA / (CAMERA - z1)
        qx = cx + (x * cp - y1 * sp) * spread * s
        qy = cy + (x * sp + y1 * cp) * spread * s
        qz = z1
    }

    private fun d(r: Double, a: Double, z: Double = 0.0) = p(r * cos(a), r * sin(a), z)

    /** A closed ring of [n] segments at radius [radius] in the disc; [depth] scales alpha by nearness. */
    private fun ring(list: GalaxyDrawList, radius: Double, n: Int, w: Double, c: DoubleArray, alpha: Double, depth: Boolean = false, dash: Boolean = false) {
        d(radius, 0.0)
        var px = qx
        var py = qy
        for (i in 1..n) {
            d(radius, i.toDouble() / n * 6.283)
            val a = if (depth) alpha * (0.35 + 0.65 * (qz + 1) / 2) else alpha
            if (!(dash && i % 2 == 1)) list.put(GalaxyDrawList.LINE, px, py, qx, qy, w * sc, c, a)
            px = qx
            py = qy
        }
    }

    /** One frame of [g] on a [width] x [height] px surface, centred at ([x], [y]) as fractions, at [scale]. */
    fun build(g: Galaxy, list: GalaxyDrawList, width: Float, height: Float, x: Float = 0.5f, y: Float = 0.5f, scale: Float = 1f) {
        val M = GalaxyModel
        val LINE = GalaxyDrawList.LINE
        val BLOB = GalaxyDrawList.BLOB
        val SQUARE = GalaxyDrawList.SQUARE
        list.clear()
        list.width = width
        list.height = height
        val side = min(width, height).toDouble()
        spread = side * SPREAD * scale
        sc = side * scale / BOARD
        cx = width.toDouble() * x
        cy = height.toDouble() * y
        val t = g.t
        val l = g.look
        val mini = g.mini
        val state = g.state
        val amp = g.amp
        val since = t - g.kickAt
        val flareMs = g.pack.flareMs
        val flare = if (since < flareMs) 0.55 * (1 - since / flareMs) else 0.0
        val radK = l[M.RADIUS] * (1 + 0.012 * sin(t * 0.0015))
        ct = cos(l[M.TILT])
        st = sin(l[M.TILT])
        val pa = -0.32 + 0.04 * sin(t * 0.00011)
        cp = cos(pa)
        sp = sin(pa)
        val S = spread

        list.blend(false)
        for (n in g.nebulae) {
            d(n.r * radK, n.th + g.rot)
            list.put(BLOB, qx, qy, qx, qy, n.s * S * (1 + 0.1 * sin(t * 0.0006 + n.r * 9)), n.c, if (mini) 0.1 else 0.13)
        }
        // Dark dust lanes trailing each arm.
        list.blend(true)
        for (dd in g.dust) {
            d(dd.r * radK, dd.th + g.rot + l[M.WIND_NOW] * dd.r * 1.6)
            list.put(BLOB, qx, qy, qx, qy, dd.s * S, dd.c, 0.55)
        }
        list.blend(false)
        val tinted = l[M.TINT_AMOUNT] > 0.01
        for (s in g.stars) {
            var r = s.r * radK
            var a = s.th + g.rot + l[M.WIND_NOW] * s.r * 1.6 + 0.04 * sin(g.rot * 0.6 + s.r * 7)
            var bb = s.b * (1 + flare)
            if (s.tw) bb *= 0.75 + 0.25 * sin(t * 0.004 * s.sp + s.ph)
            when (state) {
                "listen" -> {
                    val wave = sin(s.r * 22 - t * 0.012)
                    bb *= 1 + 1.6 * amp * max(0.0, wave).pow(4)
                    r *= 1 + 0.03 * amp * wave
                }
                "speak" -> {
                    r *= 1 + 0.03 * amp
                    bb *= 1 + amp * 0.9 * max(0.0, 1 - s.r * 2.5)
                    if (s.arm) bb *= 1 + 0.9 * amp * max(0.0, sin(s.r * 9 - t * 0.007)).pow(3)
                }
                "music" -> {
                    val wave = max(0.0, sin(s.r * 13 - t * 0.009))
                    bb *= 1 + 1.3 * amp * wave
                    r *= 1 + 0.025 * amp * wave
                }
                "embed" -> {
                    r *= 0.08 + 0.92 * s.life
                    a += (1 - s.life) * 2.4
                    bb *= 0.6 + 0.7 * (1 - s.life)
                }
                "alert" -> bb *= 0.85 + 0.15 * sin(t * 0.03 + s.th * 5)
            }
            d(r, a, s.z)
            val near = (qz + 1) / 2
            val al = min(1.0, bb * (0.55 + 0.6 * near))
            val c = if (tinted) mixInto(col, s.c, g.tint, l[M.TINT_AMOUNT]) else s.c
            val z = s.sz * sc * (0.8 + 0.5 * near)
            list.put(SQUARE, qx, qy, qx, qy, z / 2, c, al)
            if (s.hot && !mini) {
                // The hot stars' diffraction cross.
                list.put(LINE, qx - z * 2.2, qy, qx + z * 2.2, qy, 0.7 * sc, c, 0.35 * al)
                list.put(LINE, qx, qy - z * 2.2, qx, qy + z * 2.2, 0.7 * sc, c, 0.35 * al)
            }
        }
        // Bright star-forming knots along the arms.
        val spark = g.pack.pal("spark")
        for (k in g.knots) {
            d(k.r * radK, k.th + g.rot + l[M.WIND_NOW] * k.r * 1.6)
            val f = 0.7 + 0.3 * sin(t * 0.002 + k.ph)
            list.put(BLOB, qx, qy, qx, qy, 7 * sc * f, k.c, 0.55 * f)
            list.put(SQUARE, qx, qy, qx, qy, sc, spark, 0.8 * f)
        }
        // Listening: rings of light roll outward; alert: rose rings close inward.
        if (state == "listen" || state == "alert") {
            val alert = state == "alert"
            for (i in 0 until 3) {
                var f = (t * 0.00045 + i / 3.0) % 1
                if (alert) f = 1 - f
                val alpha = (if (alert) 0.5 else 0.35) * sin(f * PI)
                ring(list, 0.12 + f, 72, 1.2, g.pack.pal(if (alert) "alertRing" else "listenRing"), alpha)
            }
        }
        // Polar jets while thinking.
        if (l[M.JETS] > 0.02) {
            for (sgn in JET_SIDES) {
                p(0.0, 0.0, 0.04 * sgn)
                val x0 = qx
                val y0 = qy
                p(0.0, 0.0, (if (mini) 0.7 else 0.95) * sgn)
                list.put(LINE, x0, y0, qx, qy, 3 * sc, g.pack.pal("jet"), 0.6 * l[M.JETS], 0.0)
            }
            val jetSpark = g.pack.pal("jetSpark")
            for (j in g.jets) {
                val sprd = 0.015 + j.u * 0.07
                p(j.jx * sprd, j.jy * sprd, j.sgn * (0.05 + j.u * (if (mini) 0.65 else 0.9)))
                list.put(SQUARE, qx, qy, qx, qy, (1.6 - j.u) * sc / 2, jetSpark, (1 - j.u) * l[M.JETS])
            }
        }
        // Aura rings in the disc plane, brighter on the near side, and the sweep round the outer one.
        val auraRing = g.pack.pal("auraRing")
        ring(list, OUTER, 140, 1.0, auraRing, 0.35, depth = true)
        if (!mini) ring(list, INNER, 120, 1.0, auraRing, 0.28, depth = true, dash = true)
        run {
            val sw = t * 0.0009 * (1 + l[M.SPIN])
            val sweep = g.pack.pal("sweep")
            d(OUTER, sw)
            var px = qx
            var py = qy
            for (i in 1..18) {
                d(OUTER, sw + i * 0.035)
                list.put(LINE, px, py, qx, qy, 2.4 * sc, sweep, i / 18.0)
                px = qx
                py = qy
            }
        }
        // Comets on three orbits while working.
        if (l[M.COMETS] > 0.02) {
            for (i in ORBITS.indices) {
                if (mini && i == 2) continue
                val radius = ORBITS[i][0]
                val speed = ORBITS[i][1]
                val ph = t * 0.001 * speed * (1 + l[M.SPIN] * 0.5) + i * 2.1
                val c = g.pack.pal(if (i == 1) "cometWarm" else "comet")
                fun at(j: Int) {
                    val an = ph - j * 0.035
                    p(cos(an) * radius, sin(an) * radius, 0.08 * sin(an * 2 + i))
                }
                at(0)
                val hx = qx
                val hy = qy
                var px = qx
                var py = qy
                for (j in 1..18) {
                    at(j)
                    list.put(LINE, px, py, qx, qy, 2.2 * (1 - j / 22.0) * sc, c, (1 - j / 18.0) * 0.9 * l[M.COMETS])
                    px = qx
                    py = qy
                }
                list.put(BLOB, hx, hy, hx, hy, 9 * sc, spark, l[M.COMETS])
            }
        }
        // The core: bulge, accretion rings, diffraction spikes, and the shock ring after a change.
        p(0.0, 0.0, 0.0)
        val ox = qx
        val oy = qy
        val r0 = S * 0.16 * (1 + 0.15 * amp + (if (since < M.KICK) 0.3 * (1 - since / M.KICK) else 0.0))
        val gl = l[M.GLOW] * (1 + flare * 0.6) * (if (state == "alert") 0.8 + 0.2 * sin(t * 0.03) else 1.0)
        list.put(BLOB, ox, oy, ox, oy, r0 * 3.2, g.pack.pal("glow"), 0.5 * gl)
        list.put(BLOB, ox, oy, ox, oy, r0, g.pack.pal("bulge"), gl)
        val accretion = g.pack.pal("accretion")
        val accretionHot = g.pack.pal("accretionHot")
        for (i in 0 until 2) {
            val radius = 0.055 + i * 0.03
            d(radius, 0.0)
            var px = qx
            var py = qy
            for (j in 1..64) {
                val an = j / 64.0 * 6.283
                d(radius, an)
                val dop = 0.5 + 0.5 * cos(an - g.rot * 6 - 1)
                val c = mixInto(col, accretion, accretionHot, dop)
                val w = (if (i == 1) 1.0 else 1.8) * sc
                list.put(LINE, px, py, qx, qy, w, c, l[M.ACCRETION] * (0.25 + 0.6 * dop) * (if (i == 1) 0.55 else 1.0))
                px = qx
                py = qy
            }
        }
        val spike = g.pack.pal("spike")
        for (k in 0 until 2) {
            val an = pa + k * PI / 2
            val len = r0 * (3.4 + amp * 1.2)
            val dx = cos(an) * len
            val dy = sin(an) * len
            list.put(LINE, ox, oy, ox + dx, oy + dy, 1.4 * sc, spike, 0.75 * gl, 0.0)
            list.put(LINE, ox, oy, ox - dx, oy - dy, 1.4 * sc, spike, 0.75 * gl, 0.0)
        }
        if (since < M.SHOCK) {
            val f = since / M.SHOCK
            ring(list, 0.15 + f, 64, 2.2 - 1.6 * f, g.pack.pal("shock"), 0.7 * (1 - f))
        }
    }

    private fun mixInto(out: DoubleArray, a: DoubleArray, b: DoubleArray, f: Double): DoubleArray {
        for (i in 0..2) out[i] = a[i] + (b[i] - a[i]) * f
        return out
    }

    private companion object {
        /** The camera distance in the perspective divide `s = CAMERA / (CAMERA - z)`. */
        const val CAMERA = 3.4
        /** The galaxy's radius as a share of the surface's shorter side (the reference's .36 × 1.12). */
        const val SPREAD = 0.36 * 1.12
        /** The board the reference's pixel sizes were drawn on: a size `w` there is `w × side/600` here. */
        const val BOARD = 600.0
        val JET_SIDES = doubleArrayOf(1.0, -1.0)
        /** The comets' orbits: radius, speed. */
        val ORBITS = arrayOf(doubleArrayOf(0.62, 1.6), doubleArrayOf(0.85, 1.2), doubleArrayOf(1.02, 0.9))
        /** The pack's aura rings, outer then inner. */
        val RINGS = JarvisThemes.AURA.coreRings.map { it.toDouble() }.sortedDescending()
        val OUTER = RINGS.first()
        val INNER = RINGS.getOrElse(1) { OUTER }
    }
}
