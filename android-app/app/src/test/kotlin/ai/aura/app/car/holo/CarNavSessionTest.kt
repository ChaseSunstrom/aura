package ai.aura.app.car.holo

import ai.aura.app.assist.hermes.HudState
import ai.aura.app.car.templated.JarvisCarAppService
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.os.SystemClock
import androidx.car.app.CarAppService
import androidx.car.app.CarContext
import androidx.car.app.SurfaceCallback
import androidx.car.app.model.Action
import androidx.car.app.navigation.model.NavigationTemplate
import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ApplicationProvider
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLooper
import java.util.concurrent.TimeUnit

/**
 * Jarvis is a navigation app to the car host (T-0613): the manifest says so, the session
 * opens a NavigationTemplate screen, and that screen owns the map surface.
 *
 * Nothing in CI can drive a head unit, so these read what the host reads: the merged
 * manifest through the package manager, and the screen through the session the service
 * hands out. Getting either wrong is "Jarvis does not appear in the car" with no error.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarNavSessionTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    private fun services(category: String) = context.packageManager.queryIntentServices(
        Intent(CarAppService.SERVICE_INTERFACE).addCategory(category).setPackage(context.packageName),
        0,
    ).map { it.serviceInfo.name }

    @Test
    fun `the car service is listed under navigation and nothing else`() {
        assertEquals(listOf(JarvisCarAppService::class.java.name), services(CarAppService.CATEGORY_NAVIGATION_APP))
        // Two categories put the app in two launcher sections; IOT was the old one.
        assertEquals(emptyList<String>(), services(CarAppService.CATEGORY_IOT_APP))
    }

    @Test
    fun `the app asks for the navigation templates and the map surface`() {
        val requested = context.packageManager
            .getPackageInfo(context.packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions.orEmpty().toSet()
        // Without the first the host refuses a NavigationTemplate; without the second
        // onSurfaceAvailable never arrives and the drive is a black screen.
        assertTrue("androidx.car.app.NAVIGATION_TEMPLATES" in requested)
        assertTrue("androidx.car.app.ACCESS_SURFACE" in requested)
    }

    @Test
    fun `the minimum car api is the one the screen is written against`() {
        val meta = context.packageManager
            .getApplicationInfo(context.packageName, PackageManager.GET_META_DATA).metaData
        assertEquals(CarNavScreen.MIN_CAR_API, meta.getInt("androidx.car.app.minCarApiLevel"))
    }

    @Test
    fun `the session opens the navigation screen with an action strip`() {
        val session = JarvisCarAppService().onCreateSession()
        attach(session.carContext)

        val screen = session.onCreateScreen(Intent())

        assertTrue("got ${screen::class.java.name}", screen is CarNavScreen)
        val template = screen.onGetTemplate()
        assertTrue("got ${template::class.java.name}", template is NavigationTemplate)
        val strip = (template as NavigationTemplate).actionStrip
        assertNotNull("a NavigationTemplate needs an action strip", strip)
        // Talk is always there: it is the one way in that is not a voice.
        assertTrue(strip!!.actions.any { it.type == Action.TYPE_CUSTOM })
    }

    @Test
    fun `the screen claims the surface when created and lets it go when destroyed`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val registered = mutableListOf<SurfaceCallback?>()
        val screen = CarNavScreen(carContext, registerSurface = { registered += it })

        assertEquals("nothing is claimed before the screen exists", emptyList<SurfaceCallback?>(), registered)
        screen.dispatchLifecycleEvent(Lifecycle.Event.ON_CREATE)
        assertEquals(1, registered.size)
        assertSame(screen.surface, registered.single())

        screen.dispatchLifecycleEvent(Lifecycle.Event.ON_DESTROY)
        // A second session must not draw through a callback whose screen is gone.
        assertEquals(2, registered.size)
        assertNull(registered.last())
    }

    @Test
    fun `a card is drawn and its actions are on the strip until it goes`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val screen = CarNavScreen(carContext, registerSurface = {})
        fun strip() = (screen.onGetTemplate() as NavigationTemplate).actionStrip!!.actions
            .map { it.title?.toCharSequence().toString() }
        val talk = strip().single()
        val card = CarCard(
            CarCard.Kind.CALL, "Call · Sam", "Sam Rivera",
            actions = listOf(
                CarAction("answer", "Answer", primary = true), CarAction("decline", "Decline"),
                CarAction("driving", "I'm driving"), CarAction("extra", "Extra"),
            ),
        )

        screen.show(card)

        // The drawn buttons and the host's are the same three: a car that sends no taps
        // on the map still has every action, and the fourth is dropped from both.
        assertEquals(listOf(talk, "Answer", "Decline", "I'm driving"), strip())
        assertSame(card, screen.surface.view.card)
        screen.show(null)
        assertEquals(listOf(talk), strip())
        assertNull(screen.surface.view.card)
    }

    @Test
    fun `an approval is the card, answered by voice, and refused when nobody answers`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val screen = CarNavScreen(carContext, registerSurface = {})
        val approval = HudState.Approval("r1", "send_message", "{}", "Text Sam", listOf("once", "deny"))
        val answers = mutableListOf<String>()

        assertTrue("the car shows it, not the phone's consent door", screen.onApproval(approval) { answers += it })
        assertEquals(CarCard.Kind.APPROVAL, screen.surface.view.card?.kind)
        // Done on the car, said back as nothing, never a turn for Jarvis.
        assertEquals("", screen.onHeard("Allow it, please"))
        assertEquals(listOf("once"), answers)
        assertNull(screen.surface.view.card)

        screen.onApproval(approval.copy(requestId = "r2")) { answers += it }
        ShadowLooper.idleMainLooper(CarNavScreen.APPROVAL_MS, TimeUnit.MILLISECONDS)
        assertEquals("silence is a no", listOf("once", "deny"), answers)
        assertNull(screen.surface.view.card)
        // With the card gone "allow" is a sentence for Jarvis, and answers nothing twice.
        assertNull(screen.onHeard("allow"))
        assertEquals(2, answers.size)
    }

    @Test
    fun `the drive's own words are done on the car and the rest go to Jarvis`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val screen = CarNavScreen(carContext, registerSurface = {})
        val none = carContext.getString(ai.aura.app.R.string.car_drive_no_route_set)

        assertEquals(none, screen.onHeard("reroute"))
        assertEquals(none, screen.onHeard("What's the next turn?"))
        assertNull(screen.onHeard("reroute via the bridge"))
        assertNull(screen.onHeard("answer"))
    }

    // T-1088: the screen's route ask reaches the pack router (here the seam in its place) with the
    // destination Jarvis gave, and a reroute carries the line the drive is following (T-1047).
    @Test
    fun `with no house paired the route ask reaches the pack router with the destination and, on a reroute, the kept line`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val asked = mutableListOf<Triple<LatLon, LatLon, HouseRoute?>>()
        var answer: ((HouseRoute?) -> Unit)? = null
        val screen = CarNavScreen(carContext, registerSurface = {}, packRoute = { from, to, kept, done -> asked += Triple(from, to, kept); answer = done })
        screen.surface.position.fix(5.0, 5.0, 0f, null, timeMs = SystemClock.uptimeMillis())

        assertTrue(screen.onNavigate(JSONObject().put("lat", 5.0).put("lon", 5.01).put("label", "Rose Street")))

        assertEquals(1, asked.size)
        assertEquals(5.0, asked[0].first.lat, 1e-6)
        assertEquals(LatLon(5.0, 5.01), asked[0].second)
        assertNull("a new destination has no line to keep", asked[0].third)
        val route = HouseRoute(listOf(LatLon(5.0, 5.0), LatLon(5.0, 5.01)), 700f, 60f, "Rose Street", null)
        answer!!(route)

        screen.onHeard("reroute")

        assertEquals(2, asked.size)
        assertEquals(LatLon(5.0, 5.01), asked[1].second)
        assertSame("the line being followed", route, asked[1].third)
    }

    // T-1335: the two reasons Talk does nothing are drawn on the screen (the subtitle zone), not in
    // a pop-up a head unit may hide; neither starts a conversation, and the next talk clears them.
    @Test
    fun `talk with no house paired says so on the screen and starts nothing`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val screen = CarNavScreen(carContext, registerSurface = {})

        pressTalk(screen)

        assertEquals(carContext.getString(ai.aura.app.R.string.car_screen_not_set_up_body), screen.surface.view.said)
        assertEquals("still Talk, not Stop: no conversation began", carContext.getString(ai.aura.app.R.string.car_screen_talk), talkTitle(screen))
    }

    @Test
    fun `talk without the microphone says so on the screen, starts nothing, and the next talk clears it`() {
        val carContext = JarvisCarAppService().onCreateSession().carContext
        attach(carContext)
        val config = ai.aura.app.config.JarvisConfig(context)
        config.serverUrl = "https://house.example"
        config.token = "t"
        val screen = CarNavScreen(carContext, registerSurface = {})

        pressTalk(screen)

        assertEquals(carContext.getString(ai.aura.app.R.string.car_screen_needs_mic), screen.surface.view.said)
        assertEquals(carContext.getString(ai.aura.app.R.string.car_screen_talk), talkTitle(screen))

        // Granted on the phone: the next talk starts a conversation and the old reason is gone.
        shadowOf(context as android.app.Application).grantPermissions(android.Manifest.permission.RECORD_AUDIO)
        pressTalk(screen)
        assertEquals("", screen.surface.view.said)
        assertEquals(carContext.getString(ai.aura.app.R.string.car_screen_stop), talkTitle(screen))
        pressTalk(screen)
    }

    private fun talkTitle(screen: CarNavScreen) =
        (screen.onGetTemplate() as NavigationTemplate).actionStrip!!.actions.first().title?.toCharSequence().toString()

    private fun pressTalk(screen: CarNavScreen) {
        val talk = (screen.onGetTemplate() as NavigationTemplate).actionStrip!!.actions.first()
        talk.onClickDelegate!!.sendClick(object : androidx.car.app.OnDoneCallback {
            override fun onSuccess(response: androidx.car.app.serialization.Bundleable?) = Unit
            override fun onFailure(response: androidx.car.app.serialization.Bundleable) = Unit
        })
    }

    /**
     * A session's CarContext has no base context until the host binds; the real one is
     * attached by `CarAppService.onBind`, which needs a host. The app context stands in.
     */
    private fun attach(carContext: CarContext) {
        ContextWrapper::class.java.getDeclaredMethod("attachBaseContext", Context::class.java)
            .apply { isAccessible = true }
            .invoke(carContext, context)
    }
}
