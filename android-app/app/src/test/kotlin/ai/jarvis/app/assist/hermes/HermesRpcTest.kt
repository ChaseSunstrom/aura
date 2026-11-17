package ai.jarvis.app.assist.hermes

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HermesRpcTest {

    private val wire = ArrayList<String>()
    private var connected = true
    private val rpc = HermesRpc { text -> if (connected) { wire.add(text); true } else false }

    @Test
    fun `ids are monotonic and start at one`() {
        val a = rpc.request("session.create")
        val b = rpc.request("session.list")
        val c = rpc.request("commands.catalog")
        assertEquals(listOf(1, 2, 3), listOf(a, b, c))
        assertEquals(listOf(1, 2, 3), wire.map { JSONObject(it).getInt("id") })
    }

    @Test
    fun `a reply reaches the callback for its id and only that one`() {
        val heard = ArrayList<String>()
        rpc.request("a") { heard.add("a:$it") }
        rpc.request("b") { heard.add("b:$it") }
        rpc.receive("""{"jsonrpc":"2.0","id":2,"result":{"x":1}}""")
        assertEquals(1, heard.size)
        assertTrue(heard[0].startsWith("b:Ok"))
        assertEquals(1, rpc.inFlight)
    }

    @Test
    fun `an error reply is an Error with the server's code`() {
        var reply: HermesRpc.Reply? = null
        rpc.request("command.dispatch") { reply = it }
        rpc.receive("""{"jsonrpc":"2.0","id":1,"error":{"code":4018,"message":"unknown command"}}""")
        assertEquals(HermesRpc.Reply.Error(4018, "unknown command"), reply)
    }

    @Test
    fun `a reply is delivered exactly once`() {
        var count = 0
        rpc.request("a") { count++ }
        rpc.receive("""{"jsonrpc":"2.0","id":1,"result":{}}""")
        rpc.receive("""{"jsonrpc":"2.0","id":1,"result":{}}""")
        assertEquals(1, count)
    }

    @Test
    fun `a reply nobody waits on is dropped`() {
        val frame = rpc.receive("""{"jsonrpc":"2.0","id":99,"result":{}}""")
        assertTrue(frame is HermesFrames.Frame.Response)
        assertEquals(0, rpc.inFlight)
    }

    @Test
    fun `events pass through untouched`() {
        val frame = rpc.receive("""{"jsonrpc":"2.0","method":"event","params":{"type":"gateway.ready","payload":{}}}""")
        assertEquals("gateway.ready", (frame as HermesFrames.Frame.Event).type)
    }

    @Test
    fun `a request with no wire fails at once rather than never`() {
        connected = false
        var reply: HermesRpc.Reply? = null
        rpc.request("prompt.submit") { reply = it }
        assertEquals(HermesRpc.NOT_CONNECTED, (reply as HermesRpc.Reply.Error).code)
        assertEquals(0, rpc.inFlight)
        assertTrue(wire.isEmpty())
    }

    @Test
    fun `a closed socket fails every outstanding request once`() {
        val replies = ArrayList<HermesRpc.Reply>()
        rpc.request("a") { replies.add(it) }
        rpc.request("b") { replies.add(it) }
        rpc.failAll("closed")
        rpc.failAll("closed again")
        assertEquals(2, replies.size)
        assertTrue(replies.all { (it as HermesRpc.Reply.Error).code == HermesRpc.DISCONNECTED })
        assertEquals(0, rpc.inFlight)
    }
}
