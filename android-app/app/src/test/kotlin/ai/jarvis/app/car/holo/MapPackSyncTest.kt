package ai.jarvis.app.car.holo

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.work.Configuration
import androidx.work.NetworkType
import androidx.work.WorkManager
import androidx.work.impl.utils.SynchronousExecutor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * The phone copies the house's map packs (T-1017): the ready ones, through a `.part` and a
 * rename, resuming a `.part` it was stopped in, replacing a pack the house has rebuilt, dropping
 * one the house no longer lists, and leaving one that does not fit. The house is a fake here;
 * its HTTP calls are checked against `tests/contracts/map-pack.json` and a stand-in client.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class MapPackSyncTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private lateinit var dir: File
    private val gib = 1L shl 30

    private class Served(val bytes: ByteArray, val built: Long)

    private val served = mutableMapOf<String, Served>()
    private var rows: List<MapPackSync.Region>? = emptyList()
    private val asked = mutableListOf<Pair<String, Long>>()
    private var honoursRange = true
    private var failAfter = -1
    private var free = 100 * gib
    private var stop = false

    private val house = object : MapPackSync.House {
        override fun regions() = rows

        override fun pack(id: String, from: Long): MapPackSync.Download {
            asked += id to from
            val all = served.getValue(id).bytes
            val start = if (honoursRange) from.toInt() else 0
            var body: InputStream = ByteArrayInputStream(all, start, all.size - start)
            if (failAfter >= 0) {
                val head = all.copyOfRange(start, minOf(all.size, start + failAfter))
                body = object : InputStream() {
                    val src = ByteArrayInputStream(head)
                    override fun read(): Int = src.read().also { if (it < 0) throw IOException("the link dropped") }
                }
            }
            return MapPackSync.Download(honoursRange && from > 0, body)
        }
    }

    @Before
    fun fresh() {
        dir = File(context.cacheDir, "mapsync-${System.nanoTime()}")
        ShadowLog.clear()
    }

    private fun sync() = MapPackSync(dir, house, { free }, { stop }).run()

    /** A pack of one footprint, [n] ones for a bigger file, built at [built]; served as [id] and listed ready. */
    private fun offer(id: String, built: Long, n: Int = 1, state: String = "ready"): File {
        val file = File(context.cacheDir, "src-$id-$built-${System.nanoTime()}.pack")
        val features = (0 until n).map { MapPackFixture.building(5.001 + it * 1e-4, 5.001, 5.0011 + it * 1e-4, 5.0011) }
        MapPackFixture.write(file, doubleArrayOf(4.9, 4.9, 5.1, 5.1), built, features)
        val bytes = file.readBytes()
        served[id] = Served(bytes, built)
        rows = (rows ?: emptyList()).filter { it.id != id } + MapPackSync.Region(id, state, bytes.size.toLong(), built, sha256 = sha(bytes))
        return file
    }

    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun local(id: String) = File(dir, "$id.pack")
    /** The copy in progress of [id] at build [built]: named for the build, so another build's bytes are never resumed. */
    private fun part(id: String, built: Long) = File(dir, "$id.$built.pack.part")

    @Test
    fun `a ready pack is copied through a part file and renamed, and the ground can read it`() {
        offer("strelsau", 100L, n = 3)
        val r = sync()
        assertEquals(1, r.copied)
        assertArrayEquals(served["strelsau"]!!.bytes, local("strelsau").readBytes())
        assertFalse(part("strelsau", 100L).exists())
        assertEquals(listOf("strelsau" to 0L), asked)
        val packs = MapPacks(dir)
        assertEquals(3, packs.near(5.0, 5.0, 5.01, 5.01)!!.buildings.size)
        packs.close()
    }

    @Test
    fun `the pack is not there until it is whole`() {
        offer("strelsau", 100L)
        var readable = false
        val spy = object : MapPackSync.House {
            override fun regions() = house.regions()
            override fun pack(id: String, from: Long): MapPackSync.Download {
                val d = house.pack(id, from)
                return MapPackSync.Download(d.partial, object : InputStream() {
                    override fun read(): Int {
                        readable = readable || local(id).exists()
                        return d.body.read()
                    }
                })
            }
        }
        MapPackSync(dir, spy, { free }, { stop }).run()
        assertFalse("a pack under copy was readable", readable)
        assertTrue(local("strelsau").exists())
    }

    @Test
    fun `only a region the house lists as ready is copied`() {
        offer("a-ready", 1L)
        for (s in MapPackFixture.contract.getJSONArray("states").let { a -> (0 until a.length()).map(a::getString) }) {
            if (s != "ready") offer("is-$s", 1L, state = s)
        }
        val r = sync()
        assertEquals(1, r.copied)
        assertEquals(listOf("a-ready.pack"), dir.list()!!.sorted())
    }

    @Test
    fun `a part left by a stopped copy is carried on from where it ended`() {
        offer("strelsau", 100L, n = 5)
        val all = served["strelsau"]!!.bytes
        dir.mkdirs()
        part("strelsau", 100L).writeBytes(all.copyOf(1000))
        val r = sync()
        assertEquals(1, r.copied)
        assertEquals(listOf("strelsau" to 1000L), asked)
        assertArrayEquals(all, local("strelsau").readBytes())
    }

    @Test
    fun `a house that ignores the range is read from the start, not appended to the part`() {
        offer("strelsau", 100L, n = 5)
        dir.mkdirs()
        part("strelsau", 100L).writeBytes(served["strelsau"]!!.bytes.copyOf(1000))
        honoursRange = false
        assertEquals(1, sync().copied)
        assertArrayEquals(served["strelsau"]!!.bytes, local("strelsau").readBytes())
    }

    @Test
    fun `a part longer than the pack, or from another build, is started over`() {
        offer("strelsau", 100L, n = 2)
        dir.mkdirs()
        part("strelsau", 100L).writeBytes(ByteArray(served["strelsau"]!!.bytes.size + 50) { 1 })
        assertEquals(1, sync().copied)
        assertEquals(listOf("strelsau" to 0L), asked)
        assertArrayEquals(served["strelsau"]!!.bytes, local("strelsau").readBytes())

        // Resumed onto bytes of an older build the pack is whole in size and wrong in content: refused.
        val old = served["strelsau"]!!.bytes
        offer("strelsau", 200L, n = 2)
        val fresh = served["strelsau"]!!.bytes
        assertEquals(old.size, fresh.size)
        part("strelsau", 200L).writeBytes(old.copyOf(old.size - 1))
        local("strelsau").delete()
        val r = sync()
        assertEquals("the stale part spoiled the copy", 1, r.failed)
        assertFalse(local("strelsau").exists())
        assertFalse("a spoiled part is not resumed again", part("strelsau", 200L).exists())
        asked.clear()
        assertEquals(1, sync().copied)
        assertArrayEquals(fresh, local("strelsau").readBytes())
    }

    @Test
    fun `a part of another build, or from before parts were named for a build, is deleted and not resumed`() {
        offer("strelsau", 200L, n = 5)
        val all = served["strelsau"]!!.bytes
        dir.mkdirs()
        part("strelsau", 100L).writeBytes(all.copyOf(1000))
        File(dir, "strelsau.pack.part").writeBytes(all.copyOf(900))
        val r = sync()
        assertEquals(1, r.copied)
        assertEquals("nothing resumed from a part of another build", listOf("strelsau" to 0L), asked)
        assertEquals(listOf("strelsau.pack"), dir.list()!!.sorted())
    }

    @Test
    fun `a pack the house rebuilt is replaced, one it did not is left alone`() {
        offer("strelsau", 100L)
        offer("zenda", 100L)
        sync()
        val kept = local("zenda").lastModified()
        asked.clear()
        offer("strelsau", 200L, n = 2)
        val r = sync()
        assertEquals(listOf("strelsau" to 0L), asked)
        assertEquals(1, r.copied)
        assertEquals(1, r.kept)
        assertArrayEquals(served["strelsau"]!!.bytes, local("strelsau").readBytes())
        assertEquals(kept, local("zenda").lastModified())
    }

    @Test
    fun `a pack of another size than the house says is replaced even with the same stamp`() {
        offer("strelsau", 100L, n = 1)
        sync()
        asked.clear()
        // The same built stamp, more bytes: the house's row is what the phone trusts.
        val bigger = MapPackFixture.write(File(context.cacheDir, "big.pack"), doubleArrayOf(4.9, 4.9, 5.1, 5.1), 100L,
            (0 until 300).map { MapPackFixture.building(5.001 + it * 1e-4, 5.001, 5.0011 + it * 1e-4, 5.0011) }).readBytes()
        assertTrue("the fixture must differ in size", bigger.size != served["strelsau"]!!.bytes.size)
        served["strelsau"] = Served(bigger, 100L)
        rows = listOf(MapPackSync.Region("strelsau", "ready", bigger.size.toLong(), 100L, sha256 = sha(bigger)))
        assertEquals(1, sync().copied)
        assertEquals(listOf("strelsau" to 0L), asked)
    }

    @Test
    fun `a pack on the phone that cannot be opened is copied again`() {
        offer("strelsau", 100L)
        dir.mkdirs()
        local("strelsau").writeBytes(ByteArray(served["strelsau"]!!.bytes.size) { 3 })
        assertEquals(1, sync().copied)
        assertArrayEquals(served["strelsau"]!!.bytes, local("strelsau").readBytes())
    }

    @Test
    fun `a pack the house no longer lists is deleted, and nothing else in the folder is`() {
        offer("strelsau", 100L)
        offer("zenda", 100L)
        sync()
        File(dir, "notes.txt").writeText("mine")
        File(dir, "Not An Id.pack").writeText("mine")
        part("old", 7L).writeBytes(ByteArray(5))
        rows = rows!!.filter { it.id == "strelsau" }
        val r = sync()
        assertEquals(2, r.dropped)
        assertEquals(listOf("Not An Id.pack", "notes.txt", "strelsau.pack"), dir.list()!!.sorted())
    }

    @Test
    fun `a house that does not answer costs the phone nothing`() {
        offer("strelsau", 100L)
        sync()
        rows = null
        val r = sync()
        assertEquals(0, r.dropped + r.copied)
        assertTrue(local("strelsau").exists())
    }

    @Test
    fun `an empty list from the house drops nothing, a house warming up must not wipe the phone`() {
        offer("strelsau", 100L)
        sync()
        rows = emptyList()
        val r = sync()
        assertEquals(0, r.dropped)
        assertTrue(local("strelsau").exists())
    }

    @Test
    fun `a list with a row the phone cannot take as an id drops nothing`() {
        offer("strelsau", 100L)
        offer("zenda", 100L)
        sync()
        rows = rows!!.filter { it.id == "strelsau" } + MapPackSync.Region("../zenda", "ready", 10L, 1L)
        val r = sync()
        assertEquals(0, r.dropped)
        assertTrue(local("zenda").exists())
    }

    @Test
    fun `a region with a name that is no id is not touched, nor is its file written`() {
        offer("strelsau", 100L)
        val bad = listOf("../evil", "Zenda", "", "a/b", "x".repeat(70), "-lead")
        rows = rows!! + bad.map { MapPackSync.Region(it, "ready", 10L, 1L) }
        served.putAll(bad.associateWith { Served(ByteArray(10), 1L) })
        val r = sync()
        assertEquals(1, r.copied)
        assertEquals(listOf("strelsau" to 0L), asked)
        assertEquals(listOf("strelsau.pack"), dir.list()!!.sorted())
        assertFalse(File(dir.parentFile, "evil.pack").exists())
    }

    @Test
    fun `an id is what the contract says`() {
        val re = Regex(MapPackFixture.contract.getString("id"))
        for (id in listOf("strelsau", "a", "u-s-utah", "0abc")) assertTrue(id, MapPackSync.validId(id) && re.matches(id))
        for (id in listOf("", "-a", "A", "a b", "a/b", "..", "x".repeat(65))) assertFalse(id, MapPackSync.validId(id) || re.matches(id))
    }

    @Test
    fun `a pack that does not fit in the free space less a reserve is left, said in the log, and the others still copy`() {
        offer("big", 1L, n = 40)
        offer("small", 1L, n = 1)
        val big = served["big"]!!.bytes.size.toLong()
        free = MapPackSync.RESERVE_BYTES + big - 1
        val r = sync()
        assertEquals(1, r.skipped)
        assertFalse(local("big").exists())
        // The small one fits where the big one did not.
        assertTrue(local("small").exists())
        val log = ShadowLog.getLogsForTag("MapPackSync").joinToString("\n") { it.msg }
        assertTrue(log, log.contains("big") && log.contains("space"))
        free = MapPackSync.RESERVE_BYTES + big
        assertEquals(1, sync().copied)
        assertEquals(500L * 1024 * 1024, MapPackSync.RESERVE_BYTES)
    }

    @Test
    fun `a part already on the phone counts against what is still to come`() {
        offer("big", 1L, n = 40)
        val all = served["big"]!!.bytes
        dir.mkdirs()
        part("big", 1L).writeBytes(all.copyOf(all.size - 10))
        free = MapPackSync.RESERVE_BYTES + 10
        assertEquals(1, sync().copied)
    }

    @Test
    fun `a pack whose content is not the one the house listed is never installed, and the old one stays`() {
        offer("strelsau", 100L)
        sync()
        val good = local("strelsau").readBytes()
        // The house lists a rebuild (built 200) but serves a pack built 150, the same size.
        offer("strelsau", 200L)
        val wrong = File(context.cacheDir, "wrong.pack")
        MapPackFixture.write(wrong, doubleArrayOf(4.9, 4.9, 5.1, 5.1), 150L, listOf(MapPackFixture.building(5.001, 5.001, 5.0011, 5.0011)))
        served["strelsau"] = Served(wrong.readBytes(), 200L)
        rows = listOf(MapPackSync.Region("strelsau", "ready", wrong.length(), 200L, sha256 = sha(wrong.readBytes())))
        val r = sync()
        assertEquals(1, r.failed)
        assertArrayEquals("the pack in use was replaced by a wrong one", good, local("strelsau").readBytes())
        assertFalse(part("strelsau", 200L).exists())
    }

    @Test
    fun `a copy whose hash is not the one the house listed is refused, and the pack in use stays`() {
        offer("strelsau", 100L)
        sync()
        val good = local("strelsau").readBytes()
        // A rebuild (built 200) served with one block flipped past the header: same size, and
        // it still opens as a pack of that build, so only the hash can tell.
        offer("strelsau", 200L, n = 3)
        val listed = served["strelsau"]!!.bytes
        val flipped = listed.copyOf().also { it[it.size - 1] = (it[it.size - 1].toInt() xor 0xFF).toByte() }
        served["strelsau"] = Served(flipped, 200L)
        assertEquals(listed.size, flipped.size)
        val r = sync()
        assertEquals(1, r.failed)
        assertEquals(0, r.copied)
        assertArrayEquals("the pack in use was replaced by a corrupt one", good, local("strelsau").readBytes())
        assertFalse("a refused copy is not resumed", part("strelsau", 200L).exists())
        val log = ShadowLog.getLogsForTag("MapPackSync").joinToString("\n") { it.msg }
        assertTrue(log, log.contains("strelsau") && log.contains("hash"))
        // The house serves the right bytes next time: copied.
        served["strelsau"] = Served(listed, 200L)
        assertEquals(1, sync().copied)
        assertArrayEquals(listed, local("strelsau").readBytes())
    }

    @Test
    fun `a hash is checked whatever case the house writes it in, and a row with none or a malformed one is not copied`() {
        offer("strelsau", 100L)
        val row = rows!!.single()
        val shout = MapPackSync.Region(row.id, row.state, row.bytes, row.built, sha256 = row.sha256.uppercase())
        rows = listOf(shout)
        assertEquals(1, sync().copied)
        local("strelsau").delete()
        for (bad in listOf("", "abc", "z".repeat(64), row.sha256 + "0")) {
            rows = listOf(MapPackSync.Region(row.id, row.state, row.bytes, row.built, sha256 = bad))
            asked.clear()
            assertEquals(bad, 0, sync().copied)
            assertTrue("a row with no usable hash was asked for", asked.isEmpty())
            assertFalse(local("strelsau").exists())
        }
    }

    @Test
    fun `a house that sends more than it listed is cut off, nothing kept`() {
        offer("strelsau", 100L)
        val extra = served["strelsau"]!!.bytes + ByteArray(5000)
        served["strelsau"] = Served(extra, 100L)
        val r = sync()
        assertEquals(1, r.failed)
        assertFalse(local("strelsau").exists())
        assertFalse(part("strelsau", 100L).exists())
    }

    @Test
    fun `a link that drops keeps the part to resume, and the pack in use`() {
        offer("strelsau", 100L)
        sync()
        val good = local("strelsau").readBytes()
        offer("strelsau", 200L, n = 5)
        failAfter = 700
        val r = sync()
        assertEquals(1, r.failed)
        assertEquals(700L, part("strelsau", 200L).length())
        assertArrayEquals(good, local("strelsau").readBytes())
        failAfter = -1
        asked.clear()
        assertEquals(1, sync().copied)
        assertEquals(listOf("strelsau" to 700L), asked)
        assertArrayEquals(served["strelsau"]!!.bytes, local("strelsau").readBytes())
    }

    @Test
    fun `a stopped job leaves what it has, and one region failing does not stop the next`() {
        offer("a-one", 1L)
        offer("b-two", 1L)
        stop = true
        assertEquals(0, sync().copied)
        assertTrue(asked.isEmpty())
        stop = false
        served.remove("a-one") // the fake throws for it, as a dead link would
        val r = sync()
        assertEquals(1, r.failed)
        assertTrue(local("b-two").exists())
    }

    @Test
    fun `a row with no usable size or stamp is not copied`() {
        offer("strelsau", 100L)
        rows = listOf(MapPackSync.Region("strelsau", "ready", 0L, 100L), MapPackSync.Region("zenda", "ready", 10L, 0L))
        served["zenda"] = Served(ByteArray(10), 0L)
        val r = sync()
        assertEquals(0, r.copied)
        assertTrue(asked.isEmpty())
    }

    // The calls, against the contract and a stand-in client.

    private fun client(answer: (Request) -> Response) = OkHttpClient.Builder().addInterceptor { answer(it.request()) }.build()

    private fun reply(r: Request, code: Int, body: ByteArray = ByteArray(0), vararg h: Pair<String, String>) =
        Response.Builder().request(r).protocol(Protocol.HTTP_1_1).code(code).message("x")
            .apply { h.forEach { header(it.first, it.second) } }
            .body(body.toResponseBody("application/octet-stream".toMediaType())).build()

    @Test
    fun `the regions call is the contract's, bearer-authenticated, and reads each row it can`() {
        var seen: Request? = null
        // T-1024: the field the house lists a pack's sha256 under, and the form it has, are the contract's.
        val sha = MapPackFixture.contract.getJSONObject("sha256")
        val field = sha.getString("field")
        val digest = "ab".repeat(32)
        assertTrue(Regex(sha.getString("pattern")).matches(digest))
        val http = HousePacks("http://house.example:8643", "tok", client { r ->
            seen = r
            reply(r, 200, """{"regions":[
                {"id":"strelsau","name":"Strelsau","state":"ready","note":"","bytes":123456,"built":1700000000,"$field":"$digest"},
                {"id":"zenda","name":"Zenda","state":"working","note":"x"},
                {"id":"vol","state":"ready","bytes":"many","built":1}]}""".toByteArray())
        })
        val got = http.regions()!!
        val call = MapPackFixture.contract.getJSONObject("calls").getJSONArray("list")
        assertEquals(call.getString(0), seen!!.method)
        assertEquals(call.getString(1), seen!!.url.encodedPath)
        assertEquals("Bearer tok", seen!!.header("Authorization"))
        assertEquals(listOf("strelsau", "zenda", "vol"), got.map { it.id })
        assertEquals("a size that is no number is none", 0L, got[2].bytes)
        assertEquals(123456L, got[0].bytes)
        assertEquals(1700000000L, got[0].built)
        assertEquals(digest, got[0].sha256)
        assertEquals("a row with no hash has none", "", got[1].sha256)
        assertEquals("ready", got[0].state)
        assertEquals("working", got[1].state)
    }

    @Test
    fun `a list with a row that cannot be read is none, so nothing is dropped on a half-read list`() {
        for (bad in listOf("""{"state":"ready"}""", "5", """{"id":7}""", "null")) {
            val body = """{"regions":[{"id":"strelsau","state":"ready","bytes":1,"built":1},$bad]}"""
            assertNull(bad, HousePacks("http://house.example", "t", client { reply(it, 200, body.toByteArray()) }).regions())
        }
    }

    @Test
    fun `a house that does not answer or answers an error is none, not an empty list`() {
        assertNull(HousePacks("http://house.example", "t", client { reply(it, 503) }).regions())
        assertNull(HousePacks("http://house.example", "t", client { reply(it, 200, "not json".toByteArray()) }).regions())
        assertNull(HousePacks("http://house.example", "t", client { throw IOException("down") }).regions())
        assertNull(HousePacks("http://house.example", "t", client { reply(it, 200, "{}".toByteArray()) }).regions())
    }

    @Test
    fun `the pack call is the contract's, asks for the rest with a range, and says whether it was given`() {
        val seen = mutableListOf<Request>()
        val body = ByteArray(10) { it.toByte() }
        val http = HousePacks("http://house.example", "tok", client { r ->
            seen += r
            when (r.header("Range")) {
                "bytes=4-" -> reply(r, 206, body.copyOfRange(4, 10), "Content-Range" to "bytes 4-9/10")
                else -> reply(r, 200, body)
            }
        })
        val call = MapPackFixture.contract.getJSONObject("calls").getJSONArray("pack")
        val whole = http.pack("strelsau", 0)
        assertFalse(whole.partial)
        assertArrayEquals(body, whole.body.readBytes())
        assertNull(seen[0].header("Range"))
        assertEquals(call.getString(0), seen[0].method)
        assertEquals(call.getString(1).replace("{id}", "strelsau"), seen[0].url.encodedPath)
        assertEquals("Bearer tok", seen[0].header("Authorization"))

        val rest = http.pack("strelsau", 4)
        assertTrue(rest.partial)
        assertArrayEquals(body.copyOfRange(4, 10), rest.body.readBytes())
        // 200 to a range ask: the house sent it all.
        val all = HousePacks("http://house.example", "tok", client { reply(it, 200, body) }).pack("strelsau", 4)
        assertFalse(all.partial)
    }

    @Test
    fun `a range the house cannot give is asked again from the start, any other failure throws`() {
        val seen = mutableListOf<String?>()
        val http = HousePacks("http://house.example", "t", client { r ->
            seen += r.header("Range")
            if (r.header("Range") != null) reply(r, 416) else reply(r, 200, ByteArray(3))
        })
        assertFalse(http.pack("strelsau", 99).partial)
        assertEquals(listOf<String?>("bytes=99-", null), seen)
        val gone = HousePacks("http://house.example", "t", client { reply(it, 404) })
        try {
            gone.pack("strelsau", 0)
            error("a 404 gave a pack")
        } catch (e: IOException) {
            assertNotNull(e)
        }
        // A 206 for another start than asked is no use either.
        val odd = HousePacks("http://house.example", "t", client { reply(it, 206, ByteArray(3), "Content-Range" to "bytes 0-2/3") })
        try {
            odd.pack("strelsau", 5)
            error("a misplaced range gave a pack")
        } catch (e: IOException) {
            assertNotNull(e)
        }
    }

    @Test
    fun `the job waits for an unmetered network`() {
        assertEquals(NetworkType.UNMETERED, MapPackWorker.constraints().requiredNetworkType)
    }

    @Test
    fun `schedule enqueues the periodic job with those constraints, once`() {
        // constraints() alone proves nothing about what the work manager was given (T-1024).
        WorkManager.initialize(context, Configuration.Builder().setExecutor(SynchronousExecutor()).build())
        MapPackWorker.schedule(context)
        MapPackWorker.schedule(context)
        val work = WorkManager.getInstance(context).getWorkInfosForUniqueWork(MapPackWorker.NAME).get()
        assertEquals("KEEP: a second schedule adds none", 1, work.size)
        assertEquals(NetworkType.UNMETERED, work.single().constraints.requiredNetworkType)
        assertEquals(TimeUnit.HOURS.toMillis(6), work.single().periodicityInfo!!.repeatIntervalMillis)
    }

    @Test
    fun `regions the house fetched on its own are copied newest first within their budget, and the rest dropped`() {
        // T-1074: every state driven through, copied whole, is what fills a phone.
        offer("home", 100L)
        offer("strelsau", 200L)
        offer("zenda", 300L)
        sync()
        assertTrue(local("strelsau").isFile && local("zenda").isFile)
        // The house now says both were its own idea, and each is most of the budget.
        val big = MapPackSync.AUTO_BYTES * 2 / 3
        rows = rows!!.map { if (it.id == "home") it else MapPackSync.Region(it.id, it.state, big, it.built, auto = true) }
        val r = sync()
        assertTrue("home is always kept", local("home").isFile)
        assertFalse("the older self-fetched region stayed past the budget", local("strelsau").exists())
        assertEquals(1, r.dropped)
    }
}
