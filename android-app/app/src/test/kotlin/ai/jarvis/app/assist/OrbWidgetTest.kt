package ai.jarvis.app.assist

import ai.jarvis.app.ListenTrampolineActivity
import ai.jarvis.app.R
import ai.jarvis.app.reactor.ReactorState
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisTokens
import android.appwidget.AppWidgetManager
import android.content.pm.PackageManager
import android.content.Intent
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * The home-screen orb (T-0590): what the widget wears for a reactor state, what the provider
 * draws, and where the tap goes. Does not cover a real launcher (unproven: Robolectric hosts the
 * RemoteViews itself) or the update the service pushes when the app is alive.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class OrbWidgetTest {

    private val app = RuntimeEnvironment.getApplication()

    @Before
    fun idleAgain() {
        OrbWidget.current = ReactorState.IDLE
    }

    private fun render(): View {
        val shadow = shadowOf(AppWidgetManager.getInstance(app))
        val id = shadow.createWidget(OrbWidgetProvider::class.java, R.layout.widget_orb)
        OrbWidgetProvider().onUpdate(app, AppWidgetManager.getInstance(app), intArrayOf(id))
        return shadow.getViewFor(id)
    }

    private fun tint(v: View): Int? = v.findViewById<ImageView>(R.id.widget_orb_ring).imageTintList?.defaultColor

    private fun label(v: View): String = v.findViewById<TextView>(R.id.widget_orb_label).text.toString()

    @Test
    fun attentionAndIdleDifferInLabelAndTint() {
        val idle = OrbWidget.look(ReactorState.IDLE)
        val attention = OrbWidget.look(ReactorState.ATTENTION)
        assertNotEquals(idle.label, attention.label)
        assertNotEquals(idle.tint, attention.tint)
        assertEquals(JarvisTokens.Color.REACTOR_ATTENTION, attention.tint)
        assertEquals(JarvisTokens.Color.REACTOR_IDLE, idle.tint)
    }

    @Test
    fun listeningDrawsAsIdle() {
        assertEquals(OrbWidget.look(ReactorState.IDLE), OrbWidget.look(ReactorState.LISTENING))
    }

    @Test
    fun everyStateHasALookWithAWord() {
        for (s in ReactorState.entries) assertTrue(s.key, OrbWidget.look(s).label.isNotBlank())
    }

    @Test
    fun aFreshProcessRedrawsAWidgetLeftOnAnOlderLook() {
        // T-1292: a killed process left the widget on "needs you"; the next process starts at idle,
        // and publish(IDLE) drew nothing because the look had not changed in this process.
        val shadow = shadowOf(AppWidgetManager.getInstance(app))
        val id = shadow.createWidget(OrbWidgetProvider::class.java, R.layout.widget_orb)
        AppWidgetManager.getInstance(app).updateAppWidget(id, OrbWidget.views(app, ReactorState.ATTENTION))
        assertEquals(OrbWidget.look(ReactorState.ATTENTION).label, label(shadow.getViewFor(id)))
        OrbWidget.redraw(app)
        assertEquals(OrbWidget.look(ReactorState.IDLE).label, label(shadow.getViewFor(id)))
    }

    @Test
    fun providerRendersIdleThenAttention() {
        val idle = render()
        assertEquals(OrbWidget.look(ReactorState.IDLE).label, label(idle))
        assertEquals(OrbWidget.look(ReactorState.IDLE).tint, tint(idle))

        OrbWidget.current = ReactorState.ATTENTION
        val attention = render()
        assertEquals(OrbWidget.look(ReactorState.ATTENTION).label, label(attention))
        assertEquals(OrbWidget.look(ReactorState.ATTENTION).tint, tint(attention))
        assertNotEquals(label(idle), label(attention))
        assertNotEquals(tint(idle), tint(attention))
    }

    @Test
    fun theWholeWidgetIsOneNamedTarget() {
        val v = render()
        assertTrue(v.isClickable || v.hasOnClickListeners())
        val said = v.contentDescription.toString()
        assertTrue(said, said.contains(ActiveTheme.spokenName))
        assertTrue(said, said.contains(OrbWidget.look(ReactorState.IDLE).label))
    }

    @Test
    fun theTapOpensTheListenTrampolineAndNothingElse() {
        val pi = OrbWidget.tap(app)
        val sp = shadowOf(pi)
        assertTrue(sp.isActivityIntent)
        val intent: Intent = sp.savedIntent
        assertEquals(ListenTrampolineActivity::class.java.name, intent.component?.className)
        assertEquals(app.packageName, intent.component?.packageName)
    }

    @Test
    fun theProviderIsDeclaredWithItsFilterAndInfo() {
        val pm = app.packageManager
        val receivers = pm.queryBroadcastReceivers(
            Intent(AppWidgetManager.ACTION_APPWIDGET_UPDATE).setPackage(app.packageName),
            PackageManager.GET_META_DATA,
        )
        val ours = receivers.firstOrNull { it.activityInfo.name == OrbWidgetProvider::class.java.name }
        assertNotNull("OrbWidgetProvider is not declared for APPWIDGET_UPDATE", ours)
        // The launcher is another app: a provider it cannot broadcast to is never updated.
        assertTrue(ours!!.activityInfo.exported)
        assertNotNull(ours.activityInfo.metaData?.get(AppWidgetManager.META_DATA_APPWIDGET_PROVIDER))
    }
}
