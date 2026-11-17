package ai.jarvis.app.car.holo

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.glass.ReactorFeed
import android.graphics.Bitmap
import android.graphics.Canvas
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.time.Instant

/**
 * Draws real places for `scripts/verify/street_compare.py` (T-1180): each `NN.json` in the
 * folder `STREET_COMPARE_DIR` names (a position, a heading, the photo's time and the map
 * pack's answer round it) becomes `NN.map.png`, drawn by the drive's own painter at that
 * position and heading and lit for that time. Skipped when the variable is not set, which is
 * every ordinary test run.
 *
 * Not a check of the picture: a person looks at it beside the photo. It fails only when a
 * place cannot be drawn at all.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class StreetCompareRenderTest {

    // These pin the 3D drive, which the owner paused (T-1252): it is drawn only when asked for.
    // STREET_COMPARE_FLAT set: the flat map instead (T-1252), as `NN.flat.png`, its frame time in `NN.flat.ms`.
    @org.junit.Before fun threeD() { HoloGround.threeD = System.getenv("STREET_COMPARE_FLAT") == null }
    @org.junit.After fun flat() { HoloGround.threeD = false }

    @Test
    fun `each place in the folder is drawn where its photo was taken`() {
        val dir = System.getenv("STREET_COMPARE_DIR")?.let(::File)
        assumeTrue("STREET_COMPARE_DIR is not set", dir != null && dir.isDirectory)
        val view = DriveView(reactor = ReactorFeed.inputsFrom(Phase.IDLE))
        val places = dir!!.listFiles { f -> f.name.endsWith(".json") }!!.sortedBy { it.name }
        for (place in places) {
            val o = JSONObject(place.readText())
            val near = MapsJson.near(o.getJSONObject("near"))
            val drive = CarDrive()
            runCatching { Instant.parse(o.optString("taken")).toEpochMilli() }.getOrNull()?.let { ms -> drive.utc = { ms } }
            if (near.ground.isNotEmpty()) drive.scene.setGround(near.ground)
            drive.scene.setBuildings(near.buildings)
            drive.scene.setRoads(near.roads)
            near.details?.let { drive.scene.setDetails(it) }
            drive.resize(W, H, DENSITY, Box(0f, 0f, W.toFloat(), H.toFloat()))
            val bitmap = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888)
            drive.draw(Canvas(bitmap), view, DriveView.Pose(o.getDouble("lat"), o.getDouble("lon"), o.getDouble("heading").toFloat()), TIME_MS)
            if (!HoloGround.threeD) {
                val t0 = System.nanoTime()
                for (i in 1..20) drive.draw(Canvas(bitmap), view, DriveView.Pose(o.getDouble("lat"), o.getDouble("lon"), o.getDouble("heading").toFloat()), TIME_MS)
                File(dir, place.name.removeSuffix(".json") + ".flat.ms").writeText("${(System.nanoTime() - t0) / 20_000_000.0}")
            }
            val out = File(dir, place.name.removeSuffix(".json") + (if (HoloGround.threeD) ".map.png" else ".flat.png"))
            out.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
            assertTrue("${place.name} drew nothing", out.length() > 0)
        }
    }

    private companion object {
        const val W = 1280
        const val H = 720
        const val DENSITY = 1.5f
        const val TIME_MS = 1_250L
    }
}
