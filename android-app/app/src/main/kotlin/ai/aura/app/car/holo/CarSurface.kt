package ai.aura.app.car.holo

import android.graphics.Rect
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.DisplayMetrics
import android.util.Log
import android.view.Surface
import androidx.car.app.SurfaceCallback
import androidx.car.app.SurfaceContainer

/**
 * The host's map surface with the [CarDrive] on it, drawn on a thread of its own so a
 * frame never waits on the main thread and the main thread never waits on a frame.
 *
 * The screen sets [view] and [level] and feeds [position] from the main thread; each frame
 * reads whatever is there and asks [position] where the car is at that instant, so the car
 * moves every frame rather than once a fix. Every touch of the surface and of the drive
 * (the screen's [scene] edits included) happens under one lock,
 * so [onSurfaceDestroyed] returns only once no frame is being drawn into a surface the
 * host is about to take back (drawing into it afterwards is an IllegalStateException on
 * the render thread, which would take the app down).
 *
 * Does not cover pacing to the display's vsync: a frame every [FRAME_MS] is what a head
 * unit's encoder takes anyway, and a frame that is late is simply drawn late.
 */
class CarSurface(
    /** A tap on a drawn card button, on the main thread. */
    private val onAction: (CarAction) -> Unit,
) : SurfaceCallback {
    @Volatile var view = DriveView()
    @Volatile var level = 0f

    /** Where the car is; thread-safe on its own. */
    val position = CarPosition()

    private val lock = Any()
    private val drive = CarDrive()
    private var surface: Surface? = null
    private var width = 0
    private var height = 0
    private var density = 1f
    private var visible: Box? = null
    private var stable: Box? = null
    private var stale = true

    /** The drive's clock starts with the screen, so a float of it stays exact for hours. */
    private val startMs = SystemClock.uptimeMillis()
    private var thread: HandlerThread? = null

    override fun onSurfaceAvailable(container: SurfaceContainer) {
        synchronized(lock) {
            surface = container.surface
            width = container.width
            height = container.height
            density = container.dpi.toFloat() / DisplayMetrics.DENSITY_DEFAULT
            stale = true
        }
        if (thread != null) return
        val t = HandlerThread("car-drive").apply { start() }
        thread = t
        Handler(t.looper).post(Frames(Handler(t.looper)))
    }

    override fun onVisibleAreaChanged(visibleArea: Rect) = synchronized(lock) {
        visible = visibleArea.toBox()
        stale = true
    }

    override fun onStableAreaChanged(stableArea: Rect) = synchronized(lock) {
        stable = stableArea.toBox()
        stale = true
    }

    override fun onSurfaceDestroyed(container: SurfaceContainer) = release()

    /** Hosts on car API 5 and up send taps on the map; older ones only have the strip. */
    override fun onClick(x: Float, y: Float) {
        val action = synchronized(lock) { drive.actionAt(x, y) } ?: return
        onAction(action)
    }

    /** The route, the street and the buildings, changed or read between frames, never during one. */
    fun <T> scene(block: (HoloScene) -> T): T = synchronized(lock) { block(drive.scene) }

    /** Stop drawing and let the thread go; the next [onSurfaceAvailable] starts again. */
    fun release() {
        synchronized(lock) { surface = null }
        thread?.quitSafely()
        thread = null
    }

    /** One frame, then the next one queued; stops by itself once the surface is gone. */
    private inner class Frames(private val handler: Handler) : Runnable {
        override fun run() {
            val next = SystemClock.uptimeMillis() + FRAME_MS
            if (frame()) handler.postAtTime(this, next)
        }
    }

    private fun frame(): Boolean = synchronized(lock) {
        val s = surface ?: return false
        if (stale) {
            drive.resize(width, height, density, visible ?: Box(0f, 0f, 0f, 0f), stable)
            stale = false
        }
        try {
            val canvas = s.lockHardwareCanvas()
            try {
                val now = SystemClock.uptimeMillis()
                drive.draw(canvas, view, position.at(now), now - startMs, level)
            } finally {
                s.unlockCanvasAndPost(canvas)
            }
            true
        } catch (e: IllegalStateException) {
            // The host took the surface back between frames without telling us yet; the
            // next onSurfaceAvailable starts drawing again.
            Log.w(TAG, "surface gone mid-frame", e)
            false
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "surface gone mid-frame", e)
            false
        }
    }

    private fun Rect.toBox() = Box(left.toFloat(), top.toFloat(), right.toFloat(), bottom.toFloat())

    companion object {
        /** About 30 frames a second. */
        const val FRAME_MS = 33L

        private const val TAG = "CarSurface"
    }
}
