package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.ui.EmergencyCountdown
import ai.jarvis.app.ui.EmergencyCountdownActivity
import android.Manifest
import android.app.NotificationManager
import android.content.Context
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/**
 * The phone's half of `emergency_alert` (T-0947): the countdown, the cancel, who is texted with
 * what, and the number it never touches. The words, the limits and the numbers are
 * `tests/contracts/emergency.json`, which the Hermes plugin's tests read too.
 *
 * Does not cover a real SmsManager, a real dialer or the keyguard (Robolectric has none of
 * them): the send, the call, the fix and the clock are [EmergencyIo], and the device is where
 * the default one is proved.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class EmergencyAlertTest {

    @get:Rule
    val compose = createEmptyComposeRule()

    private val contract = JSONObject(File("../../tests/contracts/emergency.json").readText())
    private val limits = contract.getJSONObject("limits")
    private val app: Context get() = RuntimeEnvironment.getApplication()

    private class FakeIo : EmergencyIo {
        var cancel = false
        var notShown = false
        var hold: CompletableDeferred<Unit>? = null
        var fix: Fix? = Fix(51.5, -0.12, 60_000L)
        var failTextTo: String? = null
        var failCalls = false
        var now = 1_700_000_000_000L
        val countdowns = mutableListOf<Pair<Int, String>>()
        val whats = mutableListOf<String>()
        val texts = mutableListOf<Pair<String, String>>()
        val calls = mutableListOf<String>()

        override suspend fun countdown(ctx: Context, seconds: Int, summary: String, what: String): EmergencyCountdown.End {
            countdowns += seconds to summary
            whats += what
            hold?.await()
            return when {
                notShown -> EmergencyCountdown.End.NOT_SHOWN
                cancel -> EmergencyCountdown.End.CANCELLED
                else -> EmergencyCountdown.End.ELAPSED
            }
        }

        override suspend fun text(ctx: Context, number: String, body: String): ActionResult {
            texts += number to body
            return if (number == failTextTo) ActionResult.error("no service") else ActionResult.ok("parts" to 1)
        }

        override suspend fun call(ctx: Context, number: String): ActionResult {
            calls += number
            return if (failCalls) ActionResult.missingPermission(Manifest.permission.CALL_PHONE) else ActionResult.ok()
        }

        override suspend fun lastFix(ctx: Context) = fix

        override fun nowMs() = now

        // The test's one clock drives both: the latch runs on the monotonic read in the app.
        override fun elapsedMs() = now
    }

    private val io = FakeIo()
    private val sam = JSONObject().put("name", "Sam").put("number", "+15550100")
    private val ada = JSONObject().put("name", "Ada").put("number", "+15550111")

    @Before
    fun install() {
        EmergencyAlert.io = io
        EmergencyAlert.forgetLastAlert()
        shadowOf(app as android.app.Application).grantPermissions(Manifest.permission.SEND_SMS)
    }

    @After
    fun restore() {
        EmergencyAlert.io = DeviceEmergencyIo
        EmergencyAlert.forgetLastAlert()
    }

    private fun order(vararg contacts: JSONObject, what: String = "They asked for help.", seconds: Int? = 10) =
        JSONObject()
            .put("contacts", JSONArray().apply { contacts.forEach { put(it) } })
            .put("what", what)
            .apply { seconds?.let { put("countdown_s", it) } }

    private fun run(params: JSONObject) = runBlocking { EmergencyAlert.execute(app, params) }

    private fun clock() = SimpleDateFormat("HH:mm", Locale.US).format(Date(io.now))

    private fun said(what: String, where: String) = contract.getString("message")
        .replace("{owner}", "the owner").replace("{what}", what).replace("{where}", where).replace("{time}", clock())

    private fun assertNothingHappened(result: ActionResult) {
        assertFalse("the command was refused whole", result.ok)
        assertTrue("no countdown for a refused command", io.countdowns.isEmpty())
        assertTrue("nothing was texted", io.texts.isEmpty())
        assertTrue("nobody was called", io.calls.isEmpty())
    }

    // --- the contract -------------------------------------------------------------------

    @Test
    fun `the constants are the contract's`() {
        assertEquals(contract.getString("action"), EmergencyAlert.id)
        assertEquals(limits.getInt("contacts_max"), EmergencyContract.CONTACTS_MAX)
        assertEquals(limits.getInt("name_max"), EmergencyContract.NAME_MAX)
        assertEquals(limits.getInt("what_max"), EmergencyContract.WHAT_MAX)
        assertEquals(limits.getInt("countdown_s"), EmergencyContract.COUNTDOWN_S)
        assertEquals(limits.getInt("countdown_max_s"), EmergencyContract.COUNTDOWN_MAX_S)
        assertEquals(limits.getInt("min_digits"), EmergencyContract.MIN_DIGITS)
        assertEquals(limits.getInt("latch_s"), EmergencyContract.LATCH_S)
        assertEquals(contract.getString("message"), EmergencyContract.MESSAGE)
        assertEquals(contract.getString("where_unknown"), EmergencyContract.WHERE_UNKNOWN)
        assertEquals(contract.getString("map_link"), EmergencyContract.MAP_LINK)
        val never = contract.getJSONArray("never_numbers")
        for (i in 0 until never.length()) {
            assertTrue("${never.getString(i)} is an emergency-services number", PhoneNumbers.isEmergency(never.getString(i)))
        }
    }

    @Test
    fun `only an alert the owner let run out is remembered for the fall watch`() {
        val config = ai.jarvis.app.config.JarvisConfig(app)
        val before = config.emergencyContacts
        io.cancel = true
        run(order(sam))
        io.cancel = false
        io.notShown = true
        run(order(sam))
        assertEquals("a cancelled or unshown alert saves nobody", before, config.emergencyContacts)
        io.notShown = false
        run(order(ada))
        assertTrue(config.emergencyContacts, "+15550111" in config.emergencyContacts)
    }

    @Test
    fun `the contract's own example is accepted and answered as the contract says`() {
        val example = contract.getJSONObject("examples")
        val result = run(example.getJSONObject("command"))
        assertTrue(result.error, result.ok)
        val want = example.getJSONObject("answer_ok")
        val got = result.data!!
        for (key in want.keys()) assertEquals(key, want.get(key), got.get(key))
    }

    @Test
    fun `texting needs no tap of its own, and the action says what it is`() {
        // AUTO is safe here only because the countdown is the consent: the owner's assistant
        // raised it, the owner sees who is texted and has a cancel for the whole alert.
        assertEquals(ActionTier.AUTO, EmergencyAlert.tier)
        assertTrue(EmergencyAlert.requiredPermissions.contains(Manifest.permission.SEND_SMS))
        assertTrue(ai.jarvis.app.automation.actions.builtin.Builtins.all().any { it.id == "emergency_alert" })
    }

    // --- refused whole ------------------------------------------------------------------

    @Test
    fun `no contacts, too many contacts, or a bad what refuses the whole command`() {
        assertNothingHappened(run(order()))
        val many = (0..limits.getInt("contacts_max")).map {
            JSONObject().put("name", "P$it").put("number", "+1555020${it}0")
        }
        assertNothingHappened(run(order(*many.toTypedArray())))
        assertNothingHappened(run(order(sam, what = "  ")))
        assertNothingHappened(run(order(sam, what = "x".repeat(limits.getInt("what_max") + 1))))
        assertNothingHappened(run(order(JSONObject().put("name", "x".repeat(limits.getInt("name_max") + 1)).put("number", "+15550100"))))
    }

    @Test
    fun `a never-number among the contacts refuses everything, even beside good ones`() {
        val never = contract.getJSONArray("never_numbers")
        for (i in 0 until never.length()) {
            val bad = JSONObject().put("name", "Desk").put("number", never.getString(i))
            assertNothingHappened(run(order(sam, bad)))
        }
        val spaced = JSONObject().put("name", "Desk").put("number", "1 1 2")
        assertNothingHappened(run(order(sam, spaced)))
    }

    @Test
    fun `a number that is not a number refuses everything`() {
        assertNothingHappened(run(order(sam, JSONObject().put("name", "Odd").put("number", "call me maybe"))))
        assertNothingHappened(run(order(JSONObject().put("name", "Odd"))))
    }

    @Test
    fun `without SEND_SMS it asks for the permission and does not count down`() {
        shadowOf(app as android.app.Application).denyPermissions(Manifest.permission.SEND_SMS)
        val result = run(order(sam))
        assertEquals(ActionResult.missingPermission(Manifest.permission.SEND_SMS), result)
        assertTrue(io.countdowns.isEmpty())
        assertTrue(io.texts.isEmpty())
    }

    // --- the countdown and the cancel -----------------------------------------------------

    @Test
    fun `the countdown says who is texted and who is called, and a cancel sends nothing`() {
        io.cancel = true
        val result = run(order(sam, ada))
        assertTrue(result.ok)
        assertEquals("cancelled", result.data!!.getString("status"))
        assertEquals(listOf(10 to "Texting Sam and Ada, then calling Sam"), io.countdowns)
        assertTrue(io.texts.isEmpty())
        assertTrue(io.calls.isEmpty())
    }

    @Test
    fun `the countdown is never shorter than the contract's and never longer than thirty seconds`() {
        // each alert would otherwise be refused by the repeat latch
        run(order(sam, seconds = 1))
        EmergencyAlert.forgetLastAlert()
        run(order(sam, seconds = 600))
        EmergencyAlert.forgetLastAlert()
        run(order(sam, seconds = null))
        assertEquals(listOf(10, 30, 10), io.countdowns.map { it.first })
    }

    @Test
    fun `two alerts at once are one alert`() = runBlocking {
        io.hold = CompletableDeferred()
        val first = async(kotlinx.coroutines.Dispatchers.Default) { EmergencyAlert.execute(app, order(sam)) }
        while (io.countdowns.isEmpty()) kotlinx.coroutines.delay(10)
        val second = EmergencyAlert.execute(app, order(sam))
        assertFalse("a second command while one counts down is refused", second.ok)
        io.hold!!.complete(Unit)
        assertTrue(first.await().ok)
        assertEquals("one text, not two", 1, io.texts.size)
        // and the in-flight guard is let go once it is done (the repeat latch is separate)
        io.hold = null
        EmergencyAlert.forgetLastAlert()
        assertTrue(EmergencyAlert.execute(app, order(sam)).ok)
    }

    // --- at zero ------------------------------------------------------------------------

    @Test
    fun `at zero each contact is texted once with what where and when, then the first is called`() {
        val result = run(order(sam, ada))
        assertTrue(result.error, result.ok)
        val where = contract.getString("map_link").replace("{lat}", "51.50000").replace("{lon}", "-0.12000")
        val body = said("They asked for help.", where)
        assertEquals(listOf("+15550100" to body, "+15550111" to body), io.texts)
        assertEquals(listOf("+15550100"), io.calls)
        val data = result.data!!
        assertEquals("ok", data.getString("status"))
        assertEquals(2, data.getInt("texted"))
        assertEquals("Sam", data.getString("called"))
        assertTrue(data.getBoolean("located"))
    }

    @Test
    fun `with no fix it says unknown and located is false`() {
        io.fix = null
        val result = run(order(sam))
        assertEquals(listOf("+15550100" to said("They asked for help.", "unknown")), io.texts)
        assertFalse(result.data!!.getBoolean("located"))
    }

    @Test
    fun `a brace in what is not a placeholder`() {
        io.fix = null
        run(order(sam, what = "They said {where} and {time}."))
        assertTrue(io.texts.single().second.contains("They said {where} and {time}. Location: unknown."))
    }

    @Test
    fun `one failed text does not stop the others, and a failed call still answers ok`() {
        io.failTextTo = "+15550100"
        io.failCalls = true
        val result = run(order(sam, ada))
        assertTrue(result.error, result.ok)
        assertEquals(2, io.texts.size)
        assertEquals(1, result.data!!.getInt("texted"))
        assertTrue("called is null when the call failed", result.data!!.isNull("called"))
    }

    @Test
    fun `when no text left the phone it is an error, not an ok`() {
        io.failTextTo = "+15550100"
        val result = run(order(sam))
        assertFalse(result.ok)
        assertEquals(ActionResult.Status.ERROR, result.status)
    }

    // --- the real countdown: raised, ticking, cancelled by a tap --------------------------

    @Test
    fun `the countdown raises its screen over the lock screen and a tap on cancel ends it`() {
        var end: EmergencyCountdown.End? = null
        val worker = thread { end = runBlocking { DeviceEmergencyIo.countdown(app, 10, "Texting Sam, then calling Sam", "They asked for help.") } }
        val deadline = System.currentTimeMillis() + 5_000
        while (EmergencyCountdown.state.value == null && System.currentTimeMillis() < deadline) Thread.sleep(10)
        assertNotNull("the countdown is on", EmergencyCountdown.state.value)
        // The state is set before the screen is raised (so the screen finds it), so wait for the post.
        val shade = shadowOf(app.getSystemService(NotificationManager::class.java))
        while (shade.allNotifications.isEmpty() && System.currentTimeMillis() < deadline) Thread.sleep(10)

        val note = shade.allNotifications.single()
        assertNotNull("a full-screen intent raises it over the lock screen", note.fullScreenIntent)
        assertEquals(JarvisApp.CHANNEL_APPROVAL, note.channelId)
        // The owner sees the words that will be sent, not only the names: in the shade ...
        assertTrue(note.extras.getCharSequence(android.app.Notification.EXTRA_BIG_TEXT).toString().contains("They asked for help."))

        Robolectric.buildActivity(EmergencyCountdownActivity::class.java).setup()
        compose.waitForIdle()
        compose.onNodeWithText("Texting Sam, then calling Sam").assertExists()
        // ... and on the screen.
        compose.onNodeWithText("They asked for help.", substring = true).assertExists()
        compose.onNodeWithText("CANCEL").performClick()
        worker.join(5_000)
        assertEquals("a tap cancels", EmergencyCountdown.End.CANCELLED, end)
        assertEquals("and the screen's state is cleared", null, EmergencyCountdown.state.value)
    }

    @Test
    fun `Back does not cancel the countdown, only the control does`() {
        var end: EmergencyCountdown.End? = null
        val worker = thread { end = runBlocking { DeviceEmergencyIo.countdown(app, 10, "Texting Sam, then calling Sam", "x") } }
        val deadline = System.currentTimeMillis() + 5_000
        while (EmergencyCountdown.state.value == null && System.currentTimeMillis() < deadline) Thread.sleep(10)
        val activity = Robolectric.buildActivity(EmergencyCountdownActivity::class.java).setup().get()
        compose.waitForIdle()
        activity.onBackPressedDispatcher.onBackPressed()
        compose.waitForIdle()
        assertFalse("Back leaves the screen up", activity.isFinishing)
        assertNotNull("and the countdown running", EmergencyCountdown.state.value)
        assertEquals("and nobody has cancelled it", null, end)
        EmergencyCountdown.cancel()
        worker.join(5_000)
        assertEquals(EmergencyCountdown.End.CANCELLED, end)
    }

    // --- the countdown nobody could see: nothing is sent --------------------------------------

    @Test
    fun `when the countdown could not be shown nothing is sent and no alert is counted`() {
        io.notShown = true
        val result = run(order(sam))
        assertFalse(result.ok)
        assertEquals(ActionResult.Status.ERROR, result.status)
        assertEquals("the countdown could not be shown", result.error)
        assertTrue("nothing was texted", io.texts.isEmpty())
        assertTrue("nobody was called", io.calls.isEmpty())
        // a countdown that never showed is not a sent alert: the next one is not held back
        io.notShown = false
        assertTrue(run(order(sam)).ok)
    }

    private fun countdownEnd(notifications: Boolean, screen: Boolean): EmergencyCountdown.End? {
        shadowOf(app.getSystemService(NotificationManager::class.java)).setNotificationsEnabled(notifications)
        val launch: (Context, android.content.Intent) -> Unit = { c, i -> if (screen) c.startActivity(i) else throw SecurityException("refused") }
        var end: EmergencyCountdown.End? = null
        val worker = thread { end = runBlocking { EmergencyCountdown.run(app, 10, "Texting Sam, then calling Sam", "x", launch) } }
        val deadline = System.currentTimeMillis() + 5_000
        while (worker.isAlive && EmergencyCountdown.state.value == null && System.currentTimeMillis() < deadline) Thread.sleep(10)
        // Shown: it is still counting, so a tap is what ends it. Not shown: it has already answered.
        Thread.sleep(300)
        if (worker.isAlive) EmergencyCountdown.cancel()
        worker.join(5_000)
        return end
    }

    @Test
    fun `a refused notification and a refused screen together is not a countdown`() {
        assertEquals(EmergencyCountdown.End.NOT_SHOWN, countdownEnd(notifications = false, screen = false))
        assertEquals("and the state is cleared", null, EmergencyCountdown.state.value)
    }

    @Test
    fun `either the notification or the screen is enough to have shown it`() {
        assertEquals(EmergencyCountdown.End.CANCELLED, countdownEnd(notifications = false, screen = true))
        assertEquals(EmergencyCountdown.End.CANCELLED, countdownEnd(notifications = true, screen = false))
    }

    // --- one alert per latch -----------------------------------------------------------------

    private val latchMs get() = limits.getInt("latch_s") * 1_000L

    @Test
    fun `a second alert inside the latch is refused, and one after it is not`() {
        assertTrue(run(order(sam)).ok)
        io.now += latchMs - 1_000
        val again = run(order(sam))
        assertFalse(again.ok)
        assertEquals(ActionResult.Status.ERROR, again.status)
        assertEquals("one text only", 1, io.texts.size)
        assertEquals("one countdown only", 1, io.countdowns.size)
        io.now += 1_000
        assertTrue(run(order(sam)).ok)
        assertEquals(2, io.texts.size)
    }

    @Test
    fun `only a sent alert starts the latch, not a cancel, a refusal or a failed send`() {
        io.cancel = true
        assertTrue(run(order(sam)).ok)
        io.cancel = false
        assertNothingHappenedAfter(run(order(JSONObject().put("name", "Odd").put("number", "x"))))
        io.failTextTo = "+15550100"
        io.failCalls = true
        assertFalse("nothing left the phone", run(order(sam)).ok)
        io.failTextTo = null
        io.failCalls = false
        assertTrue("so the next one goes", run(order(sam)).ok)
        assertFalse("and now the latch holds", run(order(sam)).ok)
    }

    private fun assertNothingHappenedAfter(result: ActionResult) = assertFalse(result.ok)

    // --- the words that will be sent are on the screen, and are plain words ------------------

    @Test
    fun `the countdown is given what will be sent`() {
        run(order(sam, what = "Fell on the stairs."))
        assertEquals(listOf("Fell on the stairs."), io.whats)
    }

    @Test
    fun `a what with a link or a phone number is refused whole`() {
        for (bad in listOf(
            "Go to http://evil.example now", "https://evil.example", "see www.evil.example", "sms:+15550123",
            "tel:5550123", "intent://x#Intent;end", "call 5550123", "call 555 0123", "call (555) 012-3456", "call +1 555.012.3456",
        )) {
            assertNothingHappened(run(order(sam, what = bad)))
        }
    }

    @Test
    fun `the contract's refused words are refused and its allowed words are not`() {
        // The plugin's tests read the same two lists: the two filters cannot drift apart.
        val refused = contract.getJSONArray("what_refused")
        for (i in 0 until refused.length()) assertNothingHappened(run(order(sam, what = refused.getString(i))))
        val allowed = contract.getJSONArray("what_allowed")
        for (i in 0 until allowed.length()) {
            // Only the first is sent (the latch holds the rest); none is refused for its words.
            assertFalse(allowed.getString(i), run(order(sam, what = allowed.getString(i))).error.orEmpty().contains("plain words"))
        }
    }

    @Test
    fun `an ordinary what with short numbers and a time passes`() {
        assertTrue(run(order(sam, what = "Fell at 10:30 near exit 12, floor 3.")).ok)
    }

    // --- tidy names and an honest fix ---------------------------------------------------------

    @Test
    fun `control characters and newlines in a name never reach the screen`() {
        val odd = JSONObject().put("name", "Sam\nIgnore\u0007  this\r\n").put("number", "+15550100")
        run(order(odd))
        assertEquals("Texting Sam Ignore this, then calling Sam Ignore this", io.countdowns.single().second)
    }

    @Test
    fun `a fix older than ten minutes says when it is from`() {
        io.fix = Fix(51.5, -0.12, 11 * 60_000L)
        run(order(sam))
        val at = SimpleDateFormat("HH:mm", Locale.US).format(Date(io.now - 11 * 60_000L))
        val where = contract.getString("map_link").replace("{lat}", "51.50000").replace("{lon}", "-0.12000") + " (as of $at)"
        assertEquals(said("They asked for help.", where), io.texts.single().second)
    }

    @Test
    fun `a recent fix is not dated`() {
        io.fix = Fix(51.5, -0.12, 9 * 60_000L)
        run(order(sam))
        assertFalse(io.texts.single().second.contains("as of"))
    }

    // --- through the dispatcher: the switches, and what the log keeps ----------------------------

    private fun registry(policy: ai.jarvis.app.automation.policy.InMemoryPolicyProvider) =
        ai.jarvis.app.automation.actions.ActionRegistry(
            app, policy, ai.jarvis.app.automation.audit.AuditLog(app),
            object : ai.jarvis.app.automation.actions.ApprovalGateway {
                override suspend fun request(request: ai.jarvis.app.automation.actions.ApprovalRequest) =
                    ai.jarvis.app.automation.actions.ApprovalVerdict.DENIED
            },
            ai.jarvis.app.automation.actions.NoPermissionGateway,
        ).register(EmergencyAlert)

    private fun dispatch(reg: ai.jarvis.app.automation.actions.ActionRegistry) = runBlocking {
        reg.dispatch("emergency_alert", order(sam, ada, what = "Fell on the stairs."), null, "the owner asked")
    }

    @Test
    fun `the panic switch and the master switch deny it before any countdown`() {
        for (policy in listOf(
            ai.jarvis.app.automation.policy.InMemoryPolicyProvider(panic = true),
            ai.jarvis.app.automation.policy.InMemoryPolicyProvider(automationEnabled = false),
            ai.jarvis.app.automation.policy.InMemoryPolicyProvider(mapOf("emergency_alert" to ai.jarvis.app.automation.policy.UserPolicy.NEVER)),
        )) {
            val result = dispatch(registry(policy))
            assertEquals(ActionResult.Status.DENIED, result.status)
            assertTrue("no countdown", io.countdowns.isEmpty())
            assertTrue("no text", io.texts.isEmpty())
        }
    }

    @Test
    fun `an alert dispatched like the server does it runs with no prompt, and the log keeps no number, name or message`() {
        val result = dispatch(registry(ai.jarvis.app.automation.policy.InMemoryPolicyProvider()))
        assertTrue(result.error, result.ok)
        assertEquals(2, io.texts.size)
        val log = ai.jarvis.app.automation.audit.AuditLog(app).file().readText()
        assertTrue("the alert was logged", log.contains("emergency_alert"))
        for (secret in listOf("+15550100", "+15550111", "15550100", "Sam", "Ada", "Fell on the stairs")) {
            assertFalse("the audit log holds $secret", log.contains(secret))
        }
        assertTrue("it keeps the count", log.contains("\"contacts_count\":2"))
    }
}
