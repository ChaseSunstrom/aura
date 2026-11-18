package ai.aura.app.car.holo

import ai.aura.app.config.JarvisConfig
import android.content.Context
import android.graphics.Canvas
import android.os.SystemClock
import android.util.Log
import android.view.View

/**
 * The hologram alone: the scene from a pose over the sky, then the edge shading. What
 * [CarDrive] draws under its zones and [GroundView] draws under the car page. Allocates
 * nothing per frame; not thread-safe.
 */
class HoloGround {
    val scene = HoloScene()
    private val cam = HoloCamera()
    private val frame = HoloFrame()
    private var painter = HoloPainter(MapLook.of())
    private val flat = FlatMap()
    /** This map's own wall clock (UTC ms; T-1107); null is the app's, [HoloLight.clock]. */
    var utc: (() -> Long)? = null
    private var width = 0f
    private var height = 0f

    /** A [width] by [height] px surface at [density] px per dp, the drive framed in [area]. */
    fun resize(width: Float, height: Float, density: Float, area: Box = Box(0f, 0f, width, height)) {
        cam.resize(area.width, area.height, density, (area.left + area.right) / 2, (area.top + area.bottom) / 2)
        this.width = width
        this.height = height
        painter.resize(width, height, cam.horizonY(), cam.unit)
        flat.resize(width, height, density, area)
    }

    /** The scene from [pose] at [timeMs] on the drive's clock; the sky alone before the first fix. */
    fun draw(canvas: Canvas, pose: DriveView.Pose?, timeMs: Long) {
        // The owner paused the 3D drive (T-1252): the flat map is what is drawn unless [threeD].
        if (!threeD) {
            flat.draw(canvas, scene, pose, timeMs)
            painter.vignette(canvas)
            return
        }
        // The persona can change mid-drive: its pack's map style takes the next frame (T-0871).
        val look = MapLook.of()
        if (look !== painter.look) {
            painter = HoloPainter(look)
            painter.resize(width, height, cam.horizonY(), cam.unit)
        }
        scene.utcMs = (utc ?: HoloLight.clock)?.invoke() ?: 0L
        if (pose != null) {
            scene.build(frame, cam, pose.lat, pose.lon, pose.headingDeg, timeMs)
            painter.light(scene.daylight)
            painter.paint(canvas, frame, timeMs)
        } else {
            painter.background(canvas, cam.horizonY(), timeMs)
        }
        painter.vignette(canvas)
    }

    companion object {
        /**
         * The 3D drive instead of the flat map, for every ground in the app: the way back to
         * it while it is paused. Off unless the phone's setting says so ([JarvisConfig.carMap3d]).
         */
        @Volatile var threeD = false
    }
}

/**
 * The hologram drive as the ground of the wireless car's screen (T-0741): the console's
 * car page is drawn over it, transparent, once it asks (`{kind: 'ground'}`, which it sends
 * only when the phone's state says `access.ground`, tests/contracts/car-state.json). The
 * route is the page's (`{kind: 'route'}`), the buildings and the road the car is on the
 * house's ([DriveFeed]).
 *
 * Redrawn on every vsync while shown, from [CarPosition.at] at that instant, so between GPS
 * fixes a second apart the car moves at the display's rate rather than once a fix (T-0747).
 * Hidden, it draws nothing and listens to nothing.
 *
 * Does not cover the turn card, the core or the panels: the page draws those over it.
 */
class GroundView(
    context: Context,
    /** The frame clock; a seam so a test can step it. */
    private val clock: () -> Long = SystemClock::uptimeMillis,
) : View(context) {
    private val ground = HoloGround()
    val position = CarPosition()
    val feed = DriveFeed(context, position, { edit -> scene(edit) }, clock = clock)
    private val config = JarvisConfig(context)

    /** The drive's clock starts with the view, so a float of it stays exact for hours. */
    private val startMs = clock()
    private val redraw = Runnable { invalidate() }
    private var posed = false

    var shown = false
        private set

    init {
        visibility = GONE
    }

    /** Shown and fed (the page asked for it), or hidden and stopped. */
    fun show(on: Boolean) {
        if (on == shown) return
        shown = on
        Log.i(TAG, if (on) "ground shown" else "ground hidden")
        visibility = if (on) VISIBLE else GONE
        if (on) {
            feed.start(HouseMaps.of(config))
            postInvalidateOnAnimation()
        } else {
            removeCallbacks(redraw)
            feed.stop()
        }
    }

    /** The route, the street and the buildings, changed between frames (as [CarSurface.scene]). */
    fun <T> scene(block: (HoloScene) -> T): T = block(ground.scene).also { invalidate() }

    /** The page's route, or null once it has none (arrived, cancelled). */
    fun route(route: HoloRoute?) = scene { it.setRoute(route) }.also { feed.ahead(route?.shape) }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        ground.resize(w.toFloat(), h.toFloat(), resources.displayMetrics.density)
    }

    override fun onDraw(canvas: Canvas) {
        val now = clock()
        val pose = position.at(now)
        // T-0986: the ground draws the car only with a fix, and a drive with no car (the owner,
        // 2026-11-10) could not say whether it had one. Each change is one line, never the place.
        if ((pose != null) != posed) {
            posed = pose != null
            Log.i(TAG, if (posed) "ground: a fix, drawing the car" else "ground: no fix, the sky alone")
        }
        ground.draw(canvas, pose, now - startMs)
        // The car's encoder takes 30 frames a second (ScreenEncoder.MAX_ENCODED_FPS): a
        // draw each 60 Hz vsync would spend half the main thread's painting on frames the
        // car never gets. This lands on every second vsync at 60 Hz and every one at 30.
        if (shown) postOnAnimationDelayed(redraw, PACE_MS)
    }

    override fun onDetachedFromWindow() {
        show(false)
        super.onDetachedFromWindow()
    }

    private companion object {
        const val TAG = "HoloGround"

        /** Half a 60 Hz vsync under the encoder's 33 ms, so the vsync after it is the next one the car takes. */
        const val PACE_MS = 25L
    }
}
