package ai.aura.app.assist

import ai.aura.app.ListenTrampolineActivity
import ai.aura.app.R
import ai.aura.app.reactor.ReactorState
import ai.aura.app.ui.theme.ActiveTheme
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.res.ColorStateList
import android.widget.RemoteViews

/**
 * The home-screen orb (T-0590): a ring tinted by the reactor's state and its word, and one tap
 * that opens [ListenTrampolineActivity]. The state is the reactor's own ([ReactorState], the
 * one the island and the tray read), so the widget cannot name a state the console would not.
 *
 * Does not cover: motion (a RemoteViews cannot run the orb), a state while the app's process is
 * dead (the widget then shows idle, which is the truth the process can no longer correct), or a
 * real launcher, which nothing here has been run on.
 */
object OrbWidget {

    /** What the widget wears for one state: the reactor's light and its tray word. */
    data class Look(val tint: Int, val label: String)

    /**
     * Listening draws as idle: the tap this widget offers is the very thing that opens the
     * microphone, so a ring that says "listening" before it is tapped would read as already done.
     */
    fun look(state: ReactorState): Look {
        val s = if (state == ReactorState.LISTENING) ReactorState.IDLE else state
        return Look(s.color, s.word)
    }

    /** The state last published in this process; a fresh process starts at idle. */
    @Volatile
    var current: ReactorState = ReactorState.IDLE

    /**
     * Tell the widget the reactor moved. Called from the main thread on every reactor input, so it
     * returns at once unless the look changed: the reactor publishes far more often than the word.
     */
    fun publish(context: Context, state: ReactorState) {
        if (look(state) == look(current)) {
            current = state
            return
        }
        current = state
        redraw(context)
    }

    /**
     * Draw [current] on every placed widget, changed or not. The app calls it once when its process
     * starts (T-1292): a process that was killed left the widget on its last look, and [publish]
     * draws nothing while the look in this process has not changed. Does not cover a process that
     * stays dead: the old look stands until something starts the app.
     */
    fun redraw(context: Context) {
        val app = context.applicationContext
        val manager = AppWidgetManager.getInstance(app) ?: return
        val ids = manager.getAppWidgetIds(ComponentName(app, OrbWidgetProvider::class.java))
        if (ids.isEmpty()) return
        val views = views(app, current)
        for (id in ids) manager.updateAppWidget(id, views)
    }

    /**
     * The whole widget as one target. [ListenTrampolineActivity.intent] with `enable`: a tap on the
     * orb is an explicit "listen", as the tile's is. Immutable, explicit, to our own activity: no
     * call, message or network happens from the provider.
     */
    fun tap(context: Context): PendingIntent = PendingIntent.getActivity(
        context,
        REQUEST_CODE,
        ListenTrampolineActivity.intent(context, enable = true),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun views(context: Context, state: ReactorState): RemoteViews {
        val look = look(state)
        return RemoteViews(context.packageName, R.layout.widget_orb).apply {
            setColorStateList(R.id.widget_orb_ring, "setImageTintList", ColorStateList.valueOf(look.tint), ColorStateList.valueOf(look.tint))
            setTextViewText(R.id.widget_orb_label, look.label)
            // The worn pack's spoken name, as the island says it (a screen reader, not the label).
            setContentDescription(
                R.id.widget_orb_root,
                "${ActiveTheme.spokenName}, ${look.label}. Tap to talk.",
            )
            setOnClickPendingIntent(R.id.widget_orb_root, tap(context))
        }
    }

    private const val REQUEST_CODE = 7
}

/**
 * The provider. A launcher asks it to draw; it draws [OrbWidget.current] and answers nothing else,
 * so nothing here can start a call, send a message or touch the network.
 */
class OrbWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        val views = OrbWidget.views(context, OrbWidget.current)
        for (id in appWidgetIds) appWidgetManager.updateAppWidget(id, views)
    }
}
