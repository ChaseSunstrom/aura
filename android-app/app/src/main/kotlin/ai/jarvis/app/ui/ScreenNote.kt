package ai.jarvis.app.ui

import ai.jarvis.app.ui.theme.JarvisTokens
import android.app.Activity
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import androidx.appcompat.widget.AppCompatTextView

/**
 * The screen's word on what just happened, in its own window: the v2 replacement for the
 * system toast (UI_INVENTORY §3 flagged every toast as legacy). A hairline note at the foot
 * of the activity's content, over whatever the screen draws, in the screen's own type and
 * colours rather than the platform's grey pill; TalkBack hears it as a polite live region.
 *
 * One note per screen: a second message replaces the first and restarts its
 * [JarvisTokens.Motion.Dur.TOAST] dwell. A tap dismisses it.
 *
 * Does not reach beyond the activity: a message said as the screen finishes, or from a
 * service with no screen, is not shown. Those are notifications' job.
 */
object ScreenNote {
    fun show(activity: Activity, message: String) {
        if (activity.isFinishing || activity.isDestroyed) return
        val content = activity.findViewById<FrameLayout>(android.R.id.content) ?: return
        val note = content.findViewWithTag<Note>(Note.TAG) ?: Note(activity).also { note ->
            val margin = JarvisUi.dp(activity, JarvisTokens.Space.WIDE)
            content.addView(
                note,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    Gravity.BOTTOM,
                ).apply { setMargins(margin, margin, margin, margin) },
            )
        }
        note.say(message)
    }

    private class Note(activity: Activity) : AppCompatTextView(activity) {
        private val hide = Runnable { visibility = View.GONE }

        init {
            tag = TAG
            setTextColor(JarvisTokens.Color.TEXT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.HINT)
            val pad = JarvisUi.dp(activity, JarvisTokens.Space.GAP)
            setPadding(pad, pad, pad, pad)
            background = GradientDrawable().apply {
                setColor(JarvisTokens.Color.BG_RAISED)
                setStroke(JarvisUi.dp(activity, JarvisTokens.Space.HAIRLINE), JarvisTokens.Color.LINE)
            }
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
            setOnClickListener { visibility = View.GONE }
        }

        fun say(message: String) {
            text = message
            visibility = View.VISIBLE
            removeCallbacks(hide)
            postDelayed(hide, JarvisTokens.Motion.Dur.TOAST.toLong())
        }

        companion object {
            const val TAG = "jarvis.screen-note"
        }
    }
}
