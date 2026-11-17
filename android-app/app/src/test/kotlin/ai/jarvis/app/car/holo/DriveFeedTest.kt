package ai.jarvis.app.car.holo

import android.Manifest
import android.app.Application
import android.content.Context
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * The ground's footprints with the house away (T-1017): when the buildings call fails the drive
 * feed asks the map packs the phone copied, off the main thread, and the scene gets what the
 * house would have sent. Made-up places (Strelsau, Ruritania), near 5, 5.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class DriveFeedTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private lateinit var dir: File
    private var scenes = 0
    private var now = 1_000L

    // Nothing listens on port 1: the house is away, and says so at once.
    private val away = HouseMaps("http://127.0.0.1:1", "t")

    @Before
    fun fresh() {
        shadowOf(context as Application).grantPermissions(Manifest.permission.ACCESS_FINE_LOCATION)
        dir = File(context.cacheDir, "feed-${System.nanoTime()}")
        ShadowLog.clear()
    }

    private fun pack(vararg f: MapPackFixture.Feature) =
        MapPackFixture.write(File(dir, "strelsau.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 1L, f.toList())

    private fun feed(tiles: MapTiles = MapTiles(File(dir, "tiles"))) = DriveFeed(
        context, CarPosition(), { edit -> scenes++; edit(HoloScene()) }, clock = { now }, packs = MapPacks(dir), tiles = tiles,
    )

    private fun log() = ShadowLog.getLogsForTag("DriveFeed").joinToString("\n") { it.msg }

    /** Runs the main looper and waits for the pack's thread, up to a few seconds. */
    private fun until(what: String, ok: () -> Boolean) {
        val end = System.nanoTime() + 5_000_000_000L
        while (!ok() && System.nanoTime() < end) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        assertTrue(what, ok())
    }

    /** The tiles drawn round where [drive] puts the car: each is asked of the house once. */
    private val round = MapTiles.covering(DriveFeed.nearBox(LatLon(5.0015, 5.0015))).size

    @Test
    fun `the fog's end and a refetch's room each way is sixteen tiles here (nine until T-1185), and the car's own is asked for first`() {
        assertEquals(16, round)
        val at = LatLon(5.0015, 5.0015)
        val order = DriveFeed.nearestFirst(MapTiles.covering(DriveFeed.nearBox(at)), at)
        assertEquals(MapTiles.at(at.lon, at.lat), order.first())
    }

    private fun DriveFeed.drive() {
        assertTrue(fix(5.0015, 5.0015, 10f, 0f, 5f, now) != null)
    }

    @Test
    fun `the house away, the footprints and roads come from the pack the phone holds`() {
        pack(
            MapPackFixture.building(5.001, 5.001, 5.002, 5.002, 12.0),
            MapPackFixture.road("Rose Street", 5.0 to 5.0012, 5.003 to 5.0012),
        )
        val f = feed()
        f.start(away)
        f.drive()
        until("the pack did not feed the scene") { scenes > 0 }
        assertTrue(log(), log().contains("1 buildings") && log().contains("1 roads"))
        assertFalse("no coordinates in the log: ${log()}", log().contains("5.00"))
        f.stop()
    }

    @Test
    fun `the house away and no pack covering the place, the scene is left as it was`() {
        pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002))
        val f = feed()
        f.start(away)
        assertTrue(f.fix(7.0, 7.0, 10f, 0f, 5f, now) != null)
        Thread.sleep(300)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0, scenes)
        f.stop()
    }

    @Test
    fun `no pack folder at all is no footprints, not a crash`() {
        val f = feed()
        f.start(away)
        f.drive()
        Thread.sleep(300)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0, scenes)
        f.stop()
    }

    @Test
    fun `a pack answer that comes after the drive stopped is not delivered`() {
        pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002))
        val f = feed()
        f.start(away)
        f.drive()
        f.stop()
        Thread.sleep(300)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0, scenes)
    }

    @Test
    fun `a pack read that finishes after stop is dropped by the ask guard, not by luck`() {
        // The spy answers whatever the state of the packs, so only the feed's own guard can
        // keep its answer out of the scene.
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        val ring = listOf(LatLon(5.001, 5.001), LatLon(5.001, 5.002), LatLon(5.002, 5.002))
        val spy = object : MapPacks(dir) {
            override fun near(west: Double, south: Double, east: Double, north: Double): NearMap? {
                entered.countDown()
                // stop() interrupts the reader; this read goes on regardless, as a real one in SQLite does.
                while (true) {
                    try {
                        release.await()
                        break
                    } catch (e: InterruptedException) {
                        // keep waiting
                    }
                }
                return NearMap(listOf(HoloBuilding(ring, null)), emptyList()).also { finished.countDown() }
            }
        }
        val f = DriveFeed(context, CarPosition(), { edit -> scenes++; edit(HoloScene()) }, clock = { now }, packs = spy)
        f.start(away)
        f.drive()
        // The house's failure comes back through the main looper, which has to run for the ask to go out.
        until("the pack was never asked") { entered.count == 0L }
        f.stop()
        release.countDown()
        assertTrue("the read did not finish", finished.await(5, TimeUnit.SECONDS))
        Thread.sleep(300)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(0, scenes)
    }

    @Test
    fun `where a pack covers the place the house is not asked for it, and where none does it is`() {
        // The owner's drive, 2026-11-11: a kilometre of buildings asked from the house every
        // 250 m over mobile data, though the pack had drawn the same box, while map matches
        // timed out, her speech ran dry and the socket dropped. A house as a socket that counts.
        val server = ServerSocket(0, 5, InetAddress.getLoopbackAddress())
        val calls = AtomicInteger()
        val body = """{"buildings":[],"roads":[]}"""
        val serving = Thread {
            while (!server.isClosed) {
                try {
                    server.accept().use { s ->
                        val input = s.getInputStream().bufferedReader()
                        // The live zones (T-1108) are a small ask of their own, not one of the tiles counted here.
                        if (!input.readLine().orEmpty().contains("/api/maps/live")) calls.incrementAndGet()
                        while (input.readLine().orEmpty().isNotEmpty()) { /* the request's headers */ }
                        s.getOutputStream().write(
                            ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n" +
                                "Connection: close\r\n\r\n$body").toByteArray(),
                        )
                    }
                } catch (e: java.io.IOException) {
                    // closed by the test, or a dropped call
                }
            }
        }.apply { isDaemon = true; start() }
        try {
            // No pack yet: the house is all there is.
            val bare = DriveFeed(context, CarPosition(), { edit -> scenes++; edit(HoloScene()) }, clock = { now }, packs = MapPacks(dir), tiles = MapTiles(File(dir, "tiles")))
            bare.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            bare.drive()
            // T-1074: by the tile, the ones round the car, each asked for once.
            until("the house was not asked where no pack covers") { calls.get() == round && log().contains("0 buildings") }
            bare.stop()

            pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002))
            ShadowLog.clear()
            val f = DriveFeed(context, CarPosition(), { edit -> scenes++; edit(HoloScene()) }, clock = { now }, packs = MapPacks(dir), tiles = MapTiles(File(dir, "tiles")))
            f.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            f.drive()
            until("the pack did not draw the ground") { log().contains("1 buildings") }
            Thread.sleep(300)
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals("the house was asked for a box the pack holds", round, calls.get())
            f.stop()
        } finally {
            server.close()
            serving.join(2_000)
        }
    }

    @Test
    fun `a house that has not answered yet does not keep the packs off the ground`() {
        // A house that takes the call and says nothing, as a slow link does.
        val server = ServerSocket(0, 5, InetAddress.getLoopbackAddress())
        try {
            pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002))
            val f = DriveFeed(context, CarPosition(), { edit -> scenes++; edit(HoloScene()) }, clock = { now }, packs = MapPacks(dir), tiles = MapTiles(File(dir, "tiles")))
            f.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            f.drive()
            until("the ground waited on the house") { log().contains("1 buildings") }
            f.stop()
        } finally {
            server.close()
        }
    }

    @Test
    fun `no house paired, the packs still feed the scene`() {
        pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002, 12.0))
        val f = feed()
        f.start(null)
        f.drive()
        until("the pack did not feed the scene") { scenes > 0 }
        assertTrue(log(), log().contains("1 buildings"))
        f.stop()
    }

    @Test
    fun `a stopped feed starts again with the packs usable`() {
        pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002))
        val f = feed()
        f.start(away)
        f.drive()
        until("first drive") { scenes > 0 }
        f.stop()
        scenes = 0
        // stop() closed that HouseMaps; the screen makes a new one per drive.
        f.start(HouseMaps("http://127.0.0.1:1", "t"))
        f.drive()
        until("second drive") { scenes > 0 }
        f.stop()
    }

    @Test
    fun `the packs are read off the main thread`() {
        pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002))
        var thread: Thread? = null
        val spy = object : MapPacks(dir) {
            override fun near(west: Double, south: Double, east: Double, north: Double): NearMap? {
                thread = Thread.currentThread()
                return super.near(west, south, east, north)
            }
        }
        val f = DriveFeed(context, CarPosition(), { edit -> scenes++; edit(HoloScene()) }, clock = { now }, packs = spy)
        f.start(away)
        f.drive()
        until("pack asked") { scenes > 0 }
        assertFalse(thread === Looper.getMainLooper().thread)
        f.stop()
    }

    private fun d(m: Double) = m / GeoPlane.M_PER_DEG

    @Test
    fun `a route over the packs is searched off the main thread and answered on it`() {
        pack(MapPackFixture.road("Rose Street", 5.0 to 5.0, 5.0 + d(800.0) to 5.0))
        var searched: Thread? = null
        val spy = object : MapPacks(dir) {
            override fun roads(west: Double, south: Double, east: Double, north: Double, limit: Int): List<PackRoad> {
                searched = Thread.currentThread()
                return super.roads(west, south, east, north, limit)
            }
        }
        val f = DriveFeed(context, CarPosition(), { edit -> edit(HoloScene()) }, clock = { now }, packs = spy)
        f.start(away)
        var got: HouseRoute? = null
        var answered = 0
        var onMain = false
        f.routeFromPacks(LatLon(5.0, 5.0), LatLon(5.0, 5.0 + d(800.0))) { got = it; answered++; onMain = Looper.myLooper() === Looper.getMainLooper() }
        until("no route came") { answered > 0 }
        assertTrue(onMain)
        assertFalse(searched === Looper.getMainLooper().thread)
        assertEquals("Rose Street", got!!.street)
        assertEquals(800f, got!!.distanceM, 10f)
        f.stop()
    }

    @Test
    fun `outside every pack the answer is none`() {
        pack(MapPackFixture.road("Rose Street", 5.0 to 5.0, 5.0 + d(800.0) to 5.0))
        val f = feed()
        f.start(away)
        val answers = mutableListOf<HouseRoute?>()
        f.routeFromPacks(LatLon(6.0, 6.0), LatLon(6.0, 6.004)) { answers += it }
        until("no answer") { answers.size == 1 }
        assertEquals(listOf<HouseRoute?>(null), answers)
        f.stop()
    }

    @Test
    fun `a route in flight when the drive stops is dropped, and none is started after`() {
        pack(MapPackFixture.road("Rose Street", 5.0 to 5.0, 5.0 + d(800.0) to 5.0))
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val reads = AtomicInteger()
        val spy = object : MapPacks(dir) {
            override fun roads(west: Double, south: Double, east: Double, north: Double, limit: Int): List<PackRoad> {
                reads.incrementAndGet()
                entered.countDown()
                try { release.await(5, TimeUnit.SECONDS) } catch (_: InterruptedException) { /* stop() ends the worker */ }
                return emptyList()
            }
        }
        val f = DriveFeed(context, CarPosition(), { edit -> edit(HoloScene()) }, clock = { now }, packs = spy)
        f.start(away)
        val answers = mutableListOf<HouseRoute?>()
        f.routeFromPacks(LatLon(5.0, 5.0), LatLon(5.0, 5.0 + d(800.0))) { answers += it }
        assertTrue("the worker never started", entered.await(5, TimeUnit.SECONDS))
        f.stop()
        release.countDown()
        f.routeFromPacks(LatLon(5.0, 5.0), LatLon(5.0, 5.0 + d(800.0))) { answers += it }
        Thread.sleep(300)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals("nothing after stop", emptyList<HouseRoute?>(), answers)
        assertEquals("and no second search", 1, reads.get())
        // A drive started again routes again.
        f.start(away)
        f.routeFromPacks(LatLon(6.0, 6.0), LatLon(6.0, 6.004)) { answers += it }
        until("a new drive was not served") { answers.size == 1 }
        f.stop()
    }

    @Test
    fun `the house's live zones are asked for with the first fix and drawn, then not again until a while or a way on`() {
        // T-1108 (the owner, 2026-11-12: "traffic data, construction, road barriers").
        liveBody = """{"source":"on","as_of":1,"zones":[{"kind":"closure","line":[[5.0015,5.0016],[5.0015,5.0019]]}]}"""
        val server = house({ town }, AtomicInteger())
        val scene = HoloScene()
        try {
            val f = DriveFeed(context, CarPosition(), { edit -> edit(scene) }, clock = { now }, packs = MapPacks(dir), tiles = MapTiles(File(dir, "tiles")))
            f.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            f.drive()
            val cam = HoloCamera().apply { resize(1280f, 720f, 1.5f) }
            until("the closure was not drawn") {
                scene.build(HoloFrame(), cam, 5.0015, 5.0015, 0f, 1_000L)
                scene.world.counts[15] >= 1
            }
            now += 5_000
            f.drive()
            Thread.sleep(200)
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals("asked again five seconds on, parked", 1, liveCalls.get())
            // What the house no longer lists is taken off the road at the next ask.
            liveBody = """{"zones":[],"source":"on","as_of":2}"""
            now += 180_000
            f.drive()
            until("the ended closure stayed") {
                scene.build(HoloFrame(), cam, 5.0015, 5.0015, 0f, 1_000L)
                liveCalls.get() == 2 && scene.world.counts[15] == 0
            }
            f.stop()
        } finally {
            server.close()
        }
        assertTrue(DriveFeed.asksLive(null, 0L))
        assertTrue("half a box on, a quarter minute after", DriveFeed.asksLive(600f, 15_000L))
        assertFalse("a motorway is not an ask a second", DriveFeed.asksLive(600f, 5_000L))
        assertFalse(DriveFeed.asksLive(100f, 60_000L))
        assertTrue(DriveFeed.asksLive(0f, 180_000L))
    }

    @Test
    fun `three asks for the live zones with no answer clear them, and not before`() {
        val f = feed()
        f.start(away)
        repeat(2) {
            assertTrue(f.fix(7.0, 7.0, 10f, 0f, 5f, now) != null)
            Thread.sleep(300)
            shadowOf(Looper.getMainLooper()).idle()
            now += 180_000
        }
        assertEquals("two failures already cleared the road", 0, scenes)
        assertTrue(f.fix(7.0, 7.0, 10f, 0f, 5f, now) != null)
        until("the zones were never cleared") { scenes == 1 }
        f.stop()
    }

    private val liveCalls = AtomicInteger()

    /** A house as a socket that answers every call with [body] and counts them (the live ask apart). */
    @Volatile private var liveBody = """{"zones":[],"source":"off","as_of":null}"""

    private fun house(body: () -> String, calls: AtomicInteger): ServerSocket {
        val server = ServerSocket(0, 5, InetAddress.getLoopbackAddress())
        Thread {
            while (!server.isClosed) {
                try {
                    server.accept().use { s ->
                        val input = s.getInputStream().bufferedReader()
                        // T-1108: the live zones are their own ask, counted apart from the tiles.
                        val live = input.readLine().orEmpty().contains("/api/maps/live")
                        if (live) liveCalls.incrementAndGet() else calls.incrementAndGet()
                        while (input.readLine().orEmpty().isNotEmpty()) { /* the request's headers */ }
                        val b = if (live) liveBody else body()
                        s.getOutputStream().write(
                            ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${b.length}\r\n" +
                                "Connection: close\r\n\r\n$b").toByteArray(),
                        )
                    }
                } catch (e: java.io.IOException) {
                    // closed by the test, or a dropped call
                }
            }
        }.apply { isDaemon = true; start() }
        return server
    }

    private val town = """{"buildings":[{"ring":[[5.001,5.001],[5.002,5.001],[5.002,5.002],[5.001,5.001]],"height_m":9}],"roads":[],"details":[]}"""

    @Test
    fun `a place driven before is drawn from its tiles with the house away, and the house is not asked for it twice`() {
        // T-1074: the owner, 2026-11-11: "how can we have that data ... everywhere? not just
        // around me? and prevent it from eating up too much space?" No pack here: tiles.
        val calls = AtomicInteger()
        val server = house({ town }, calls)
        try {
            val first = feed()
            first.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            first.drive()
            // The same footprint in each of the answers is one building.
            until("the tiles did not draw the ground") { calls.get() == round && log().contains("1 buildings") }
            // Kept before the stop: the last answer may not have been delivered yet, and a stop
            // drops it (the stop's own case is the test below); with sixteen tiles that is likelier.
            until("the tiles were not kept") { File(dir, "tiles").list().orEmpty().count { it.endsWith(".tile") } == round }
            first.stop()

            ShadowLog.clear()
            val again = feed()
            again.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            again.drive()
            until("the kept tiles did not draw the ground") { log().contains("1 buildings") }
            Thread.sleep(300)
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals("a kept tile was asked for again", round, calls.get())
            again.stop()

            ShadowLog.clear()
            val offline = feed()
            offline.start(away)
            offline.drive()
            until("the house away, the kept tiles did not draw the ground") { log().contains("1 buildings") }
            offline.stop()
        } finally {
            server.close()
        }
    }

    @Test
    fun `a stop right after the last answer still keeps every answered tile`() {
        // CI, 2026-11-12 (pipeline 835): "the tiles were not kept". Writes queue on the
        // reader's thread and stop() threw the queued ones away. Held here at the first
        // write's clock read, so every other write is still queued when stop() runs.
        val hold = java.util.concurrent.CountDownLatch(1)
        val slow = MapTiles(File(dir, "tiles"), now = {
            if (Thread.currentThread().name == "jarvis-map-pack") hold.await(5, java.util.concurrent.TimeUnit.SECONDS)
            System.currentTimeMillis()
        })
        val calls = AtomicInteger()
        val server = house({ town }, calls)
        try {
            val f = feed(slow)
            f.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            f.drive()
            // Each answer reaches the feed on the main thread, which queues its write; the
            // house's client logs a line when one has arrived, not when it was asked for.
            until("the house did not answer every tile") {
                ShadowLog.getLogs().count { it.msg?.contains("/api/maps/buildings") == true } >= round
            }
            // The line is logged just before the answer is posted to the main thread.
            repeat(5) { shadowOf(Looper.getMainLooper()).idle(); Thread.sleep(10) }
            f.stop()
            hold.countDown()
            until("a tile answered before the stop was not kept") {
                File(dir, "tiles").list().orEmpty().count { it.endsWith(".tile") } == round
            }
        } finally {
            hold.countDown()
            server.close()
        }
    }

    @Test
    fun `an answer the house marks uncovered is drawn and never kept, so the place is asked about again`() {
        val calls = AtomicInteger()
        val server = house({ """{"buildings":[],"roads":[],"details":[],"covered":false}""" }, calls)
        try {
            val f = feed()
            f.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            f.drive()
            until("the empty place was not drawn") { calls.get() == round && log().contains("0 buildings") }
            f.stop()
            Thread.sleep(200)
            assertEquals(0, File(dir, "tiles").list().orEmpty().size)
            val again = feed()
            again.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            again.drive()
            until("the place was not asked about again") { calls.get() == 2 * round }
            again.stop()
        } finally {
            server.close()
        }
    }

    @Test
    fun `a planned route's tiles are fetched in the order it is driven, but not the ones a pack or the cache holds`() {
        val calls = AtomicInteger()
        val server = house({ town }, calls)
        try {
            pack(MapPackFixture.building(5.001, 5.001, 5.002, 5.002)) // covers 4.9 to 5.1
            val f = feed()
            f.start(HouseMaps("http://127.0.0.1:${server.localPort}", "t"))
            // From inside the pack, east along a parallel to 5.13: the tiles past 5.1 are the house's.
            f.ahead(listOf(LatLon(5.005, 5.05), LatLon(5.005, 5.13)))
            val kept = { File(dir, "tiles").list().orEmpty().filter { it.endsWith(".tile") }.toSet() }
            // Three rows of tiles beside the road; the last in driving order is the far end's northern one.
            until("the route's tiles were not fetched") { kept().containsAll(setOf("511_500.tile", "513_500.tile", "513_501.tile")) }
            // Settled: more rows of tiles beside the road since T-1185, and a late one is no failure.
            // The looper is run in between: an answer delivered by it asks for the next tile.
            var before = -1
            var quiet = 0
            while (quiet < 10) {
                shadowOf(Looper.getMainLooper()).idle()
                Thread.sleep(50)
                if (calls.get() == before) quiet++ else { before = calls.get(); quiet = 0 }
            }
            val asked = calls.get()
            assertTrue("a tile the pack covers was fetched: ${kept()}", kept().none { it.startsWith("509_") || it.startsWith("505_") })
            // The same route again: everything is held.
            f.ahead(listOf(LatLon(5.005, 5.05), LatLon(5.005, 5.13)))
            Thread.sleep(300)
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals(asked, calls.get())
            f.stop()
        } finally {
            server.close()
        }
    }
}
