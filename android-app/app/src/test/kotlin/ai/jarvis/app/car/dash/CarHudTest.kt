package ai.jarvis.app.car.dash

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.car.dash.tiles.CarMessage
import ai.jarvis.app.car.holo.HouseRoute
import ai.jarvis.app.car.holo.LatLon
import ai.jarvis.app.car.holo.PackRouter
import ai.jarvis.app.car.phone.LiveCall
import ai.jarvis.app.car.voice.CarVoiceView
import org.json.JSONObject
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** What the phone hands the console's /car, in the shape `car.svelte.ts` `readCarState` reads. */
@RunWith(RobolectricTestRunner::class)
class CarHudTest {

    /** The console's side of this shape is `car.test.ts`; both read the one table (T-0326). */
    private val contract = JSONObject(File("../../tests/contracts/car-state.json").readText())

    @Test
    fun `the state sends exactly the contract's keys`() {
        val json = JSONObject(CarHud.state(null, null, emptyList(), null, emptyList(), CarVoiceView()))
        val want = contract.getJSONArray("keys").let { k -> (0 until k.length()).map { k.getString(it) }.toSet() }
        assertEquals(want, json.keys().asSequence().toSet())
    }

    @Test
    fun `the contract's acts are the ones the phone answers`() {
        val acts = contract.getJSONObject("acts")
        assertEquals(acts.getJSONObject("app").getString("pkg"), CarHud.appOf(acts.getJSONObject("app")))
        assertTrue(CarHud.wantsNotificationAccess(acts.getJSONObject("access")))
        // Neither reads the other's shape, and a blank package opens nothing.
        assertEquals(null, CarHud.appOf(acts.getJSONObject("access")))
        assertEquals(null, CarHud.appOf(JSONObject().put("kind", "app").put("pkg", " ")))
        assertTrue(!CarHud.wantsNotificationAccess(acts.getJSONObject("app")))
    }

    // T-0741: the car showed the page's flat OSM tiles; the phone's hologram is now its ground.
    @Test
    fun `the page's ground and route acts reach the hologram as the contract has them`() {
        val acts = contract.getJSONObject("acts")
        assertEquals(true, CarHud.groundOf(acts.getJSONObject("ground")))
        assertEquals(false, CarHud.groundOf(JSONObject().put("kind", "ground").put("on", false)))
        assertEquals(null, CarHud.groundOf(acts.getJSONObject("route")))

        val act = acts.getJSONObject("route")
        val route = CarHud.routeOf(act)!!
        val points = act.getJSONArray("points")
        assertEquals(points.length(), route.shape.size)
        assertEquals(points.getJSONArray(2).getDouble(0), route.shape[2].lat, 1e-9)
        assertEquals(points.getJSONArray(2).getDouble(1), route.shape[2].lon, 1e-9)
        assertEquals(act.getString("street"), route.street)
        val turn = act.getJSONObject("turn")
        assertEquals(turn.getDouble("lat"), route.turnAt!!.lat, 1e-9)
        assertEquals(turn.getString("street"), route.turnStreet)
        // No route, and a route the scene cannot draw, clear the one drawn.
        assertEquals(null, CarHud.routeOf(acts.getJSONObject("noRoute")))
        assertEquals(null, CarHud.routeOf(JSONObject().put("kind", "route").put("points", org.json.JSONArray().put(org.json.JSONArray().put(1.0).put(2.0)))))
    }

    // T-1161: the page hands the phone the house's `hud.car_voice`.
    @Test
    fun `the car voice the page hands over is the contract's, and an unknown one is dropped`() {
        val c = contract.getJSONObject("voice")
        assertEquals(c.getJSONObject("act").getString("mode"), CarHud.voiceOf(c.getJSONObject("act")))
        val modes = c.getJSONArray("modes")
        for (i in 0 until modes.length()) assertEquals(modes.getString(i), CarHud.voiceOf(JSONObject().put("kind", "voice").put("mode", modes.getString(i))))
        assertEquals(null, CarHud.voiceOf(JSONObject().put("kind", "voice").put("mode", "loud")))
    }

    // T-1075: the page asks the phone for a route over its packs when the house's is away.
    @Test
    fun `a pack route ask and its answer are the contract's`() {
        val c = contract.getJSONObject("packRoute")
        val ask = CarHud.packAskOf(c.getJSONObject("act"))!!
        assertEquals(7, ask.first)
        assertEquals(59.33, ask.second.lat, 1e-9)
        assertEquals(18.0638, ask.third.lon, 1e-9)
        assertEquals(null, CarHud.packAskOf(JSONObject().put("kind", "packroute").put("id", 7)))

        val want = c.getJSONObject("answer").getJSONObject("route")
        val shape = want.getJSONArray("points").let { p -> (0 until p.length()).map { LatLon(p.getJSONArray(it).getDouble(0), p.getJSONArray(it).getDouble(1)) } }
        val turns = listOf(
            HouseRoute.Turn("Turn right onto Quarry Road", "Quarry Road", shape[1], "right"),
            HouseRoute.Turn("Arrive", "", shape[2]),
        )
        val got = JSONObject(CarHud.packRouteJson(7, PackRouter.Plan(HouseRoute(shape, 320f, 40f, "", turns.first()), turns)))
        assertEquals(7, got.getInt("id"))
        val route = got.getJSONObject("route")
        assertEquals(want.getJSONArray("points").toString(), route.getJSONArray("points").toString())
        assertEquals(want.getInt("distance"), route.getInt("distance"))
        assertEquals(want.getInt("duration"), route.getInt("duration"))
        val steps = route.getJSONArray("steps")
        assertEquals(want.getJSONArray("steps").length(), steps.length())
        for (i in 0 until steps.length()) {
            val w = want.getJSONArray("steps").getJSONObject(i)
            val s = steps.getJSONObject(i)
            assertEquals(w.keys().asSequence().toSet(), s.keys().asSequence().toSet())
            for (k in listOf("type", "modifier", "name")) assertEquals(w.getString(k), s.getString(k))
            assertEquals(w.getDouble("lat"), s.getDouble("lat"), 1e-9)
            assertEquals(w.getDouble("lon"), s.getDouble("lon"), 1e-9)
        }
        assertEquals(c.getJSONObject("none").toString(), JSONObject(CarHud.packRouteJson(7, null)).toString())
    }

    // The field log of 2026-10-09: no notification access, and the car said "Nothing playing"
    // over a song. The page has to be told it is blind, and given the driver's apps.
    @Test
    fun `what the phone can see and the driver's apps go to the page as the contract has them`() {
        val example = contract.getJSONObject("example")
        val access = example.getJSONObject("access")
        val apps = example.getJSONArray("apps").let { a ->
            (0 until a.length()).map { a.getJSONObject(it).let { app -> app.getString("pkg") to app.getString("label") } }
        }
        val json = JSONObject(
            CarHud.state(
                null, null, emptyList(), null, emptyList(), CarVoiceView(),
                notificationAccess = access.getBoolean("notifications"),
                capture = access.getBoolean("capture"),
                apps = apps,
            ),
        )
        assertEquals(contract.getJSONObject("reads").getJSONObject("access").toString(), json.getJSONObject("access").toString())
        assertEquals(contract.getJSONObject("reads").getJSONArray("apps").toString(), json.getJSONArray("apps").toString())
    }

    // T-0760: the call card's picture and running time, as the page reads them.
    @Test
    fun `a call is written as the contract has it, its photo and when it began`() {
        fun written(call: LiveCall) = JSONObject(
            CarHud.state(media = null, call = call, messages = emptyList(), place = null, navApps = emptyList(), voice = CarVoiceView()),
        ).getJSONObject("call")
        val calls = contract.getJSONObject("call")
        val active = calls.getJSONObject("active")
        val on = LiveCall("Sam Example", ringing = false, answer = null, hangUp = null, source = "dialer", photo = active.getString("photo"), since = active.getLong("since"))
        assertEquals(active.toString(), written(on).toString())
        val ringing = LiveCall("Sam Example", ringing = true, answer = null, hangUp = null, source = "dialer")
        assertEquals(calls.getJSONObject("ringing").toString(), written(ringing).toString())
    }

    @Test
    fun `the state carries media in seconds, the call, messages, place and the car's turn`() {
        val json = JSONObject(
            CarHud.state(
                media = CarHud.Playing("Midnight City", "M83", "Spotify", playing = true, positionMs = 61_500, durationMs = 243_000),
                call = LiveCall(who = "Sam", ringing = true, answer = null, hangUp = null, source = "dialer"),
                messages = listOf(CarMessage("k1", "org.example.chat", "Sam", "Running late", 1_790_000_000_000, reply = null)),
                place = CarHud.Place(41.87, -87.62, heading = null, speed = 0.0),
                navApps = listOf("organic", "google"),
                voice = CarVoiceView(phase = Phase.LISTENING, caption = "Listening"),
            ),
        )
        val media = json.getJSONObject("media")
        assertEquals(61, media.getInt("position"))
        assertEquals(243, media.getInt("duration"))
        assertTrue(media.getBoolean("playing"))
        assertEquals("ringing", json.getJSONObject("call").getString("state"))
        assertEquals("k1", json.getJSONArray("messages").getJSONObject(0).getString("id"))
        assertTrue(json.getJSONObject("place").isNull("heading"))
        assertEquals("organic", json.getJSONArray("navApps").getString(0))
        assertEquals("listening", json.getJSONObject("voice").getString("phase"))
    }

    // The Speed panel reads the car's own speedometer before the phone's GPS (2026-09-26).
    @Test
    fun `the car's speedometer goes to the page in metres per second`() {
        val json = JSONObject(CarHud.state(null, null, emptyList(), null, emptyList(), CarVoiceView(), carSpeed = 26.8))
        assertEquals(26.8, json.getDouble("carSpeed"), 0.001)
    }

    @Test
    fun `nothing playing and no call are nulls the page reads as absent`() {
        val json = JSONObject(CarHud.state(null, null, emptyList(), null, emptyList(), CarVoiceView()))
        assertTrue(json.isNull("carSpeed"))
        assertTrue(json.isNull("media"))
        assertTrue(json.isNull("call"))
        assertTrue(json.isNull("place"))
        assertEquals("idle", json.getJSONObject("voice").getString("phase"))
    }

    @Test
    fun `Google Maps gets its own turn-by-turn scheme, the others a labelled geo point`() {
        assertEquals("google.navigation:q=41.0,-87.0", CarHud.navUri("google", 41.0, -87.0, "Home").toString())
        assertEquals("geo:41.0,-87.0?q=41.0,-87.0(Blue%20Door)", CarHud.navUri("organic", 41.0, -87.0, "Blue Door").toString())
        assertEquals("https://jarvis.example.ts.net/car", CarHud.page("https://jarvis.example.ts.net/"))
    }

    @Test
    fun `a car turn says where the car is, to about ten metres, and names the tools`() {
        assertEquals(
            "(In the car, at 41.8781,-87.6298, heading 90°, 50 km/h. For places, place_search near 41.8781,-87.6298; to go, car_navigate.)",
            CarHud.whereLine(41.878113, -87.629799, 90f, 14f),
        )
        assertTrue(CarHud.whereLine(41.0, -87.0, null, 0f).contains(", parked."))
    }
}
