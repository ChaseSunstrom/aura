package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The hologram drive's pipeline (T-0613, Car.dc.html `startMap`): the chase camera, the
 * near-plane clip, the fog, and what the scene puts in a frame from a real route.
 *
 * Plain JVM: the painter only turns a [HoloFrame] into canvas calls, so everything that can
 * be wrong about where a line lands, whether it is behind the camera or which street gets a
 * name is decided here.
 */
class HologramTest {

    private val cam = HoloCamera().apply { resize(1000f, 500f, 1f) }
    private val v = FloatArray(3)

    private fun sx(x: Float, y: Float, z: Float): Float {
        cam.view(x, y, z, v)
        return cam.screenX(v[0], v[2])
    }

    private fun sy(x: Float, y: Float, z: Float): Float {
        cam.view(x, y, z, v)
        return cam.screenY(v[1], v[2])
    }

    // --- the camera ------------------------------------------------------------------

    @Test
    fun `the focal length is the board's share of the width and the centre is the middle`() {
        assertEquals(420f, cam.f, 1e-3f)
        assertEquals(500f, cam.cx, 0f)
        assertEquals(250f, cam.cy, 0f)
    }

    @Test
    fun `ahead is the centre column, left is left and right is right`() {
        // Ahead of the camera is ahead of the car: it stands in the car's lane (T-1102).
        val lane = HoloCamera.CAM_X
        assertEquals(cam.cx, sx(lane, 0f, 50f), 1e-3f)
        assertTrue(sx(lane - 5f, 0f, 50f) < cam.cx)
        assertTrue(sx(lane + 5f, 0f, 50f) > cam.cx)
        // The same offset nearer is further from the middle: perspective, not a plan view.
        assertTrue(sx(5f, 0f, 10f) > sx(5f, 0f, 50f))
    }

    @Test
    fun `the ground rises to a horizon above the middle`() {
        assertTrue(cam.horizonY() < cam.cy)
        assertEquals(cam.horizonY(), sy(0f, 0f, 1e6f), 0.5f)
        assertTrue(sy(0f, 0f, 20f) > sy(0f, 0f, 100f))
    }

    @Test
    fun `a segment through the near plane is cut where it crosses, at either end`() {
        val out = FloatArray(6)
        val near = HoloCamera.NEAR
        assertTrue(HoloCamera.clipSegment(-10f, 0f, near - 1f, 10f, 0f, near + 1f, out))
        assertEquals(near, out[2], 1e-5f)
        assertEquals("interpolated, not just pushed forward", 0f, out[0], 1e-4f)
        assertEquals(near + 1f, out[5], 0f)

        assertTrue(HoloCamera.clipSegment(0f, 0f, 10f, 0f, 0f, -5f, out))
        assertEquals(10f, out[2], 0f)
        assertEquals(near, out[5], 1e-5f)
    }

    @Test
    fun `a segment wholly behind the near plane is dropped and one in front is untouched`() {
        val out = FloatArray(6)
        assertFalse(HoloCamera.clipSegment(0f, 0f, -5f, 1f, 1f, HoloCamera.NEAR / 2, out))
        assertTrue(HoloCamera.clipSegment(1f, 2f, 3f, 4f, 5f, 6f, out))
        assertEquals(listOf(1f, 2f, 3f, 4f, 5f, 6f), out.toList())
    }

    @Test
    fun `a polygon through the near plane keeps only what is in front of it`() {
        val quad = floatArrayOf(-5f, 0f, -5f, 5f, 0f, -5f, 5f, 0f, 20f, -5f, 0f, 20f)
        val out = FloatArray(3 * 5)
        val n = HoloCamera.clipPolygon(quad, 4, out)
        assertEquals(4, n)
        for (i in 0 until n) assertTrue(out[i * 3 + 2] >= HoloCamera.NEAR - 1e-5f)

        val tri = floatArrayOf(0f, 0f, -5f, 5f, 0f, 20f, -5f, 0f, 20f)
        assertEquals("one corner behind becomes two cuts", 4, HoloCamera.clipPolygon(tri, 3, out))

        val behind = floatArrayOf(0f, 0f, -5f, 5f, 0f, -6f, -5f, 0f, -7f)
        assertEquals(0, HoloCamera.clipPolygon(behind, 3, out))
    }

    @Test
    fun `the fog is clear up close, gone at the far end and never comes back`() {
        assertEquals(1f, HoloCamera.fog(0f), 0f)
        assertEquals(1f, HoloCamera.fog(HoloCamera.FOG_END_M / 11f), 1e-4f)
        assertEquals(0f, HoloCamera.fog(HoloCamera.FOG_END_M), 1e-4f)
        assertTrue("600 m is still seen", HoloCamera.fog(600f) > 0.2f)
        var last = 1f
        for (z in 0..(HoloCamera.FOG_END_M.toInt() + 50)) {
            val f = HoloCamera.fog(z.toFloat())
            assertTrue("fog rose at $z", f <= last)
            last = f
        }
    }

    // --- the world -------------------------------------------------------------------

    @Test
    fun `the local plane is metres east and north of its origin`() {
        val p = GeoPlane(60.0, 18.0)
        assertEquals(111.2f, p.north(60.001), 0.5f)
        // At 60 degrees a degree of longitude is half as long.
        assertEquals(111.2f, p.east(18.002), 0.5f)
        assertEquals(60.0015, p.lat(p.north(60.0015)), 1e-7)
        assertEquals(18.0015, p.lon(p.east(18.0015)), 1e-7)
    }

    @Test
    fun `the car frame puts the heading straight ahead and east on the right going north`() {
        // Heading north: east is to the right, nothing ahead.
        assertEquals(10f, HoloScene.carX(10f, 0f, 0f, 1f), 1e-5f)
        assertEquals(0f, HoloScene.carZ(10f, 0f, 0f, 1f), 1e-5f)
        // Heading east: east is ahead and north is on the left.
        assertEquals(10f, HoloScene.carZ(10f, 0f, 1f, 0f), 1e-5f)
        assertEquals(-10f, HoloScene.carX(0f, 10f, 1f, 0f), 1e-5f)
    }

    // --- the scene -------------------------------------------------------------------

    private val plane = GeoPlane(59.33, 18.06)
    private fun at(east: Float, north: Float) = LatLon(plane.lat(north), plane.lon(east))
    private val wide = HoloCamera().apply { resize(1280f, 720f, 1.5f) }

    /** North for [turnAt] metres, then 200 m to the right (east) or the left. */
    private fun route(turnAt: Float?, right: Boolean = true): HoloRoute {
        if (turnAt == null) return HoloRoute(listOf(at(0f, 0f), at(0f, 600f)), "Larch Way")
        val side = if (right) 200f else -200f
        return HoloRoute(
            listOf(at(0f, 0f), at(0f, turnAt), at(side, turnAt)),
            "Larch Way",
            turnAt = at(0f, turnAt),
            turnStreet = "Quarry Road",
        )
    }

    private fun frame(scene: HoloScene, timeMs: Long = 0L, camera: HoloCamera = wide) =
        HoloFrame().also { scene.build(it, camera, plane.lat0, plane.lon0, 0f, timeMs) }

    private fun labels(f: HoloFrame) = (0 until f.labelCount).map { f.labelText[it] }

    @Test
    fun `only the current street and the next turn are named`() {
        val scene = HoloScene().apply { setRoute(route(60f)) }
        val f = frame(scene)
        assertEquals(listOf("Larch Way", "Quarry Road"), labels(f))
        assertEquals(Ink.BRIGHT, f.labelInk[0])
        assertEquals("the turn is the warning colour", Ink.WARN, f.labelInk[1])
        for (i in 0 until f.labelCount) {
            assertTrue(f.labelPx[i] >= wide.labelMinPx && f.labelPx[i] <= wide.labelMaxPx)
        }
    }

    @Test
    fun `no turn, a turn in the fog or one under the bonnet gets no name`() {
        assertEquals(listOf("Larch Way"), labels(frame(HoloScene().apply { setRoute(route(null)) })))
        assertEquals(listOf("Larch Way"), labels(frame(HoloScene().apply { setRoute(route(HoloCamera.FOG_END_M + 50f)) })))
        assertEquals(listOf("Larch Way"), labels(frame(HoloScene().apply { setRoute(route(4f)) })))
    }

    @Test
    fun `the chevrons point the way the road turns`() {
        val chevronXs = HashMap<Boolean, List<Float>>()
        for (right in listOf(true, false)) {
            val scene = HoloScene().apply { setRoute(route(60f, right)) }
            assertEquals(if (right) 1 else -1, scene.turnSign)
            val f = frame(scene)
            var chevrons = 0
            for (i in 0 until f.lineCount) {
                if (f.lineInk[i] != Ink.WARN) continue
                chevrons++
                val o = i * HoloFrame.LINE_STRIDE
                chevronXs[right] = chevronXs[right].orEmpty() + listOf(f.lines[o], f.lines[o + 2])
            }
            assertEquals("three chevrons of two strokes", 6, chevrons)
        }
        // Against each other, not the screen's middle: the camera is in the lane, not on the road's line.
        assertTrue("$chevronXs", chevronXs.getValue(true).min() > chevronXs.getValue(false).max())
    }

    @Test
    fun `nothing is drawn as another vehicle, because nothing reports one`() {
        val f = frame(HoloScene().apply { setRoute(route(60f)) })
        for (i in 0 until f.polyCount) {
            assertTrue(f.polyFillInk[i] != Ink.WARN && f.polyStrokeInk[i] != Ink.WARN)
        }
    }

    // T-0980, the owner: "the car was drawing with the flat 2d plane under it ... but no
    // buildings/roads". The house's streets and paths are drawn on the ground: a street as its
    // two edges, a driveway or a path as one fainter line, each piece of it as the map has it.
    private fun roadLines(vararg roads: HoloRoad): Int =
        // The wireframe city (the Space Drive look): the solid one is HoloWorldTest's.
        frame(HoloScene().apply { real = false; setRoads(roads.toList()) }, timeMs = 1000L).lineCount -
            frame(HoloScene().apply { real = false }, timeMs = 1000L).lineCount

    @Test
    fun `a street ahead is its two edges, a driveway or a path one line, and a far one nothing`() {
        val across = listOf(at(-20f, 20f), at(20f, 20f))
        assertEquals(2, roadLines(HoloRoad(across, "residential")))
        assertEquals(1, roadLines(HoloRoad(across, "service")))
        assertEquals(1, roadLines(HoloRoad(across, "footway")))
        assertEquals("past the select radius", 0, roadLines(HoloRoad(listOf(at(-20f, 400f), at(20f, 400f)), "residential")))
        assertEquals("one point is no road", 0, roadLines(HoloRoad(across.take(1), "residential")))
    }

    @Test
    fun `a road follows its shape piece by piece, and a long straight one passing close is drawn`() {
        val bend = HoloRoad(listOf(at(-30f, 30f), at(-10f, 40f), at(10f, 40f), at(30f, 30f)), "residential")
        assertEquals("three pieces, two edges each", 6, roadLines(bend))
        // A country road whose nodes are 2 km apart, 30 m ahead of the car.
        assertEquals(2, roadLines(HoloRoad(listOf(at(-2000f, 30f), at(2000f, 30f)), "secondary")))
    }

    private fun square(e0: Float, n0: Float, size: Float, clockwise: Boolean = false): List<LatLon> {
        val ccw = listOf(at(e0, n0), at(e0 + size, n0), at(e0 + size, n0 + size), at(e0, n0 + size), at(e0, n0))
        return if (clockwise) ccw.reversed() else ccw
    }

    @Test
    fun `a building ahead shows the wall facing the car and its roof, either winding`() {
        val base = frame(HoloScene(), timeMs = 1000L).polyCount
        for (cw in listOf(false, true)) {
            val scene = HoloScene().apply { real = false; setBuildings(listOf(HoloBuilding(square(-5f, 40f, 10f, cw), 9f))) }
            assertEquals("clockwise=$cw", base + 2, frame(scene, timeMs = 1000L).polyCount)
        }
    }

    @Test
    fun `a building behind the camera or past the fog adds nothing`() {
        val base = frame(HoloScene(), timeMs = 1000L).polyCount
        for (n0 in listOf(-60f, HoloCamera.FOG_END_M + 30f)) {
            val scene = HoloScene().apply { setBuildings(listOf(HoloBuilding(square(-5f, n0, 10f), 9f))) }
            assertEquals("at $n0 m", base, frame(scene, timeMs = 1000L).polyCount)
        }
    }

    @Test
    fun `a building with no height is drawn at the default height`() {
        val known = frame(HoloScene().apply {
            setBuildings(listOf(HoloBuilding(square(-5f, 40f, 10f), HoloScene.DEFAULT_HEIGHT_M)))
        }, timeMs = 1000L)
        val unknown = frame(HoloScene().apply { setBuildings(listOf(HoloBuilding(square(-5f, 40f, 10f), null))) }, timeMs = 1000L)
        assertEquals(known.ptCount, unknown.ptCount)
        assertEquals(known.pts.copyOf(known.ptCount).toList(), unknown.pts.copyOf(unknown.ptCount).toList())
    }

    @Test
    fun `a second frame of the same drive reuses the first frame's storage`() {
        val scene = HoloScene().apply {
            setRoute(route(60f))
            setBuildings(listOf(HoloBuilding(square(-20f, 30f, 10f), 20f)))
        }
        val f = frame(scene)
        val lines = f.lines
        val pts = f.pts
        val count = f.lineCount
        scene.build(f, wide, plane.lat0, plane.lon0, 0f, 0L)
        assertSame(lines, f.lines)
        assertSame(pts, f.pts)
        assertEquals(count, f.lineCount)
    }
}
