package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import kotlin.random.Random

/**
 * The map kept by the tile (T-1074). The owner, 2026-11-11: "how can we have that data for all
 * of those things everywhere? not just around me? and prevent it from eating up too much space?"
 */
class MapTilesTest {
    @get:Rule val tmp = TemporaryFolder()
    private var now = 1_000_000L

    private fun tiles(cap: Long = MapTiles.CAP_BYTES) = MapTiles(File(tmp.root, "tiles"), cap) { now }

    /** Text gzip cannot shrink, so a tile's size on disk is about [n]. */
    private fun noise(n: Int, seed: Int) = String(CharArray(n) { 'a' + Random(seed + it).nextInt(26) })

    @Test
    fun `a tile is read back as it was written, smaller on disk, and old after a month`() {
        val t = tiles()
        val tile = MapTiles.at(5.0015, 5.0015)
        assertEquals(MapTiles.Tile(500, 500), tile)
        assertNull(t.read(tile))
        val text = """{"buildings":[],"roads":[]}""".repeat(200)
        t.write(tile, text)
        assertTrue(t.has(tile))
        assertEquals(text, t.read(tile)!!.text)
        assertFalse(t.read(tile)!!.old)
        assertTrue(File(tmp.root, "tiles/500_500.tile").length() < text.length / 10)
        now += MapTiles.MAX_AGE_MS + 1
        assertTrue("a month on the house is asked again", t.read(tile)!!.old)
    }

    @Test
    fun `two writers in one folder keep a tile each, and a part left by a dead write goes once old`() {
        val dir = File(tmp.root, "tiles")
        val held = java.util.concurrent.CountDownLatch(1)
        val release = java.util.concurrent.CountDownLatch(1)
        // A's clock is read while its part file is open: block there until B has written.
        val a = MapTiles(dir) {
            if (Thread.currentThread().name == "writer-a") { held.countDown(); release.await() }
            now
        }
        val b = MapTiles(dir) { now }
        val ta = MapTiles.Tile(1, 1)
        val tb = MapTiles.Tile(2, 2)
        val th = Thread({ a.write(ta, "alpha") }, "writer-a").apply { start() }
        assertTrue(held.await(10, java.util.concurrent.TimeUnit.SECONDS))
        b.write(tb, "beta")
        release.countDown()
        th.join(10_000)
        assertTrue("A's tile survived B's write", File(dir, "1_1.tile").isFile)
        assertTrue(File(dir, "2_2.tile").isFile)

        val dead = File(dir, "9_9.part").apply { writeText("x"); setLastModified(0) }
        b.write(MapTiles.Tile(3, 3), "gamma")
        assertFalse("an old part is cleared", dead.exists())
    }

    @Test
    fun `past the cap the tiles used longest ago go, and one just read stays`() {
        val t = tiles(cap = 10_000)
        for (i in 0 until 4) {
            now += 1_000
            t.write(MapTiles.Tile(i, 0), noise(3_000, i * 10_000))
        }
        // Four of about 2 kB fit; reading the oldest makes it the newest used.
        now += 1_000
        assertTrue(t.read(MapTiles.Tile(0, 0)) != null)
        for (i in 4 until 6) {
            now += 1_000
            t.write(MapTiles.Tile(i, 0), noise(3_000, i * 10_000))
        }
        // Five fit and the sixth did not: down to nine tenths, the two used longest ago went.
        val dir = File(tmp.root, "tiles")
        assertTrue("the folder passed its cap: ${dir.listFiles()!!.sumOf { it.length() }}", dir.listFiles()!!.sumOf { it.length() } <= 10_000)
        assertEquals(listOf(0, 3, 4, 5), (0 until 6).filter { t.has(MapTiles.Tile(it, 0)) })
    }

    @Test
    fun `a tile cut short is no tile, and is asked for again`() {
        val t = tiles()
        val tile = MapTiles.Tile(1, 2)
        t.write(tile, "{}")
        File(tmp.root, "tiles/1_2.tile").writeBytes(byteArrayOf(1, 2, 3))
        assertNull(t.read(tile))
        assertFalse(t.has(tile))
    }

    @Test
    fun `the tiles of a box and along a route, west of Greenwich and south of the line too`() {
        assertEquals(
            listOf(MapTiles.Tile(499, 499), MapTiles.Tile(500, 499), MapTiles.Tile(499, 500), MapTiles.Tile(500, 500)),
            MapTiles.covering(doubleArrayOf(4.995, 4.995, 5.005, 5.005)),
        )
        assertEquals(MapTiles.Tile(-1, -1), MapTiles.at(-0.001, -0.001))
        val along = MapTiles.along(listOf(LatLon(5.005, 5.005), LatLon(5.005, 5.045)), 300.0, 100)
        // Driven west to east: the first tile is where it starts, the last where it ends, none skipped.
        assertEquals(MapTiles.Tile(500, 500), along.first { it.y == 500 })
        assertEquals((500..504).toList(), along.filter { it.y == 500 }.map { it.x })
        assertEquals(3, MapTiles.along(listOf(LatLon(5.005, 5.005), LatLon(5.005, 9.0)), 300.0, 3).size)
        assertTrue(MapTiles.along(emptyList(), 300.0, 10).isEmpty())
    }
}
