package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * The chase (T-1061, the owner, 2026-11-11: "more smooth instead of a hard follow, with
 * wheels turning and a line to show the turn when making it"): what the scene carries from
 * one frame to the next. Plain JVM, stepped on the frame clock the way the car draws it.
 */
class HoloChaseTest {
    private val cam = HoloCamera().apply { resize(1280f, 720f, 1.5f) }
    private val plane = GeoPlane(59.33, 18.06)
    private val scene = HoloScene()
    private val frame = HoloFrame()
    private var east = 0f
    private var north = 0f
    private var t = 0L

    /** [ms] of driving at [mps], the heading given by the clock, a frame every 33 ms. */
    private fun drive(ms: Long, mps: Float, heading: (Long) -> Float) {
        val end = t + ms
        while (t < end) {
            t += 33
            val h = heading(t) * PI.toFloat() / 180f
            east += mps * 0.033f * sin(h)
            north += mps * 0.033f * cos(h)
            scene.build(frame, cam, plane.lat(north), plane.lon(east), heading(t), t)
        }
    }

    @Test
    fun `the camera is straight behind the car, so the drawn car is in the middle of the screen`() {
        drive(1_000, 12f) { 0f }
        // The tail's bar, the one line drawn in the danger ink: its middle is the car's centre line.
        var seen = 0
        for (i in 0 until frame.lineCount) {
            if (frame.lineInk[i] != Ink.DANGER) continue
            val o = i * HoloFrame.LINE_STRIDE
            assertEquals(cam.cx, (frame.lines[o] + frame.lines[o + 2]) / 2f, 0.5f)
            seen++
        }
        assertTrue("the tail bar is drawn", seen > 0)
    }

    @Test
    fun `the camera turns after the car, not with it, and gets there`() {
        drive(1_000, 12f) { 0f }
        assertEquals(0f, scene.camHeadingDeg, 0.01f)
        // The car is now heading 30 degrees right; the camera has only started to follow.
        drive(200, 12f) { 30f }
        assertTrue("${scene.camHeadingDeg}", scene.camHeadingDeg in 3f..20f)
        assertTrue("the car is not seen turned", scene.bodyYawDeg > 10f)
        drive(2_000, 12f) { 30f }
        assertEquals(30f, scene.camHeadingDeg, 1f)
        assertEquals(0f, scene.bodyYawDeg, 1f)
    }

    @Test
    fun `the short way round north, never the long one`() {
        drive(500, 12f) { 350f }
        drive(300, 12f) { 10f }
        val h = scene.camHeadingDeg
        assertTrue("$h", h > 350f || h < 10f)
    }

    @Test
    fun `a first frame, or one after a gap, is where the car is`() {
        scene.build(frame, cam, plane.lat0, plane.lon0, 90f, 5_000)
        assertEquals(90f, scene.camHeadingDeg, 0.01f)
        scene.build(frame, cam, plane.lat0, plane.lon0, 200f, 9_000)
        assertEquals(200f, scene.camHeadingDeg, 0.01f)
        assertEquals(0f, scene.steerDeg, 0.01f)
    }

    @Test
    fun `the front wheels steer into a turn and straighten after it, and the wheels roll`() {
        drive(1_000, 10f) { 0f }
        assertEquals(0f, scene.steerDeg, 0.5f)
        val rolled = scene.rollRad
        drive(330, 10f) { 0f }
        assertTrue("the wheels did not roll", scene.rollRad != rolled)
        // 20 degrees a second to the right at 10 m/s.
        val from = t
        drive(1_500, 10f) { (it - from) * 0.02f }
        assertTrue("${scene.steerDeg}", scene.steerDeg > 2f)
        assertTrue("${scene.steerDeg}", scene.steerDeg <= HoloScene.MAX_STEER_DEG)
        drive(2_000, 10f) { 30f }
        assertEquals(0f, scene.steerDeg, 0.5f)
        // Standing still, a phone's heading wanders: nothing steers.
        drive(1_000, 0f) { 30f + (it % 100) * 0.1f }
        assertEquals(0f, scene.steerDeg, 0.01f)
    }

    @Test
    fun `a line shows the turn while it is made, and not on a straight road`() {
        drive(1_500, 10f) { 0f }
        assertEquals(0f, scene.turnLine, 0.01f)
        val straight = frame.lineCount
        val from = t
        drive(1_200, 10f) { (it - from) * 0.02f }
        assertTrue("${scene.turnLine}", scene.turnLine > 0.5f)
        assertTrue("no line was drawn", frame.lineCount > straight)
        // To the left it bends left: the line's far end is left of the car on screen.
        val left = t
        drive(2_500, 10f) { 24f - (it - left) * 0.02f }
        assertTrue("${scene.turnLine}", scene.turnLine > 0.5f)
        assertTrue(scene.steerDeg < -2f)
    }

    @Test
    fun `the brake lights brighten when the car slows, and not when it holds its speed`() {
        var v = 15f
        var n = 0f
        var clock = 0L
        fun step() {
            clock += 33
            n += v * 0.033f
            scene.build(frame, cam, plane.lat(n), plane.lon0, 0f, clock)
        }
        repeat(60) { step() }
        assertEquals(0f, scene.brake, 0.05f)
        // 5 m/s2: a firm stop.
        repeat(45) { v = (v - 5f * 0.033f).coerceAtLeast(0f); step() }
        assertTrue("${scene.brake}", scene.brake > 0.5f)
        assertTrue(scene.brake <= 1f)
    }

    // T-0999: what is behind the car says how fast it goes.
    @Test
    fun `a standing car leaves no light behind it, a faster one a longer trail, never past the cap`() {
        drive(1_500, 0f) { 0f }
        assertEquals(0f, scene.trailM, 0.001f)
        val standing = frame.lineCount
        drive(2_000, 10f) { 0f }
        val slow = scene.trailM
        assertTrue("$slow", slow > 1f)
        assertTrue("no trail was drawn", frame.lineCount > standing)
        drive(2_000, 30f) { 0f }
        assertTrue("${scene.trailM} after $slow", scene.trailM > slow * 2f)
        drive(2_000, 90f) { 0f }
        assertEquals(HoloScene.TRAIL_MAX_M, scene.trailM, 0.001f)
    }
}
