package ai.jarvis.app.assist

import ai.jarvis.app.JarvisAssistActivity
import android.app.assist.AssistStructure
import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.service.voice.VoiceInteractionSession
import android.util.Log

/**
 * Trampoline session: shows no UI of its own; on assist invocation it launches
 * [JarvisAssistActivity]. This is the standard pattern for apps whose assist
 * experience is an activity rather than a system overlay panel.
 *
 * When the system says it will hand over the screen the owner called JARVIS up over
 * (SHOW_WITH_ASSIST: the owner's "use text from screen" switch is on), the session
 * stays for it, puts its visible text in [ScreenContext] and then finishes (T-0824).
 * Not covered: an app that hands no structure (a secure window, a game) gives nothing,
 * and a structure that comes later than [ASSIST_WAIT_MS] is not waited for.
 */
class JarvisVoiceInteractionSession(context: Context) : VoiceInteractionSession(context) {

    override fun onCreate() {
        super.onCreate()
        // We never render session UI.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            setUiEnabled(false)
        }
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        val intent = JarvisAssistActivity.newIntent(context)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // Assistant-privileged start: allowed from the background and
                // over the keyguard.
                startAssistantActivity(intent)
            } else {
                context.startActivity(intent)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch JarvisAssistActivity from session", e)
        }
        // A new call never carries the last call's screen.
        ScreenContext.open()
        // The activity owns the experience; the session stays only for the screen's text.
        main.removeCallbacks(done)
        if (showFlags and SHOW_WITH_ASSIST == 0) finish() else main.postDelayed(done, ASSIST_WAIT_MS)
    }

    override fun onHandleAssist(state: AssistState) {
        // One call per activity on the screen (split screen): the focused one is the one meant.
        val locked = context.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true
        if (state.isFocused) runCatching { ScreenContext.offer(seen(state), locked = locked) }.onFailure { Log.w(TAG, "screen not read", it) }
        if (state.index >= state.count - 1) {
            main.removeCallbacks(done)
            finish()
        }
    }

    override fun onHide() {
        ScreenContext.close()
        main.removeCallbacks(done)
        super.onHide()
    }

    override fun onDestroy() {
        main.removeCallbacks(done)
        super.onDestroy()
    }

    private val main = Handler(Looper.getMainLooper())
    private val done = Runnable { finish() }

    private fun seen(state: AssistState): ScreenContext.Seen? {
        val structure = state.assistStructure ?: return null
        val windows = (0 until structure.windowNodeCount).map { ViewNodeOf(structure.getWindowNodeAt(it).rootViewNode) }
        val root = object : ScreenContext.Node {
            override val text: CharSequence? = null
            override val visible = true
            override val secret = false
            override val children = windows
        }
        val pkg = structure.activityComponent?.packageName.orEmpty()
        val app = runCatching {
            context.packageManager.run { getApplicationLabel(getApplicationInfo(pkg, 0)).toString() }
        }.getOrDefault(pkg)
        return ScreenContext.Seen(app, ScreenContext.address(state.assistContent?.webUri?.toString()), ScreenContext.read(root))
    }

    /** Android's view node as [ScreenContext] reads one. */
    private class ViewNodeOf(private val node: AssistStructure.ViewNode) : ScreenContext.Node {
        override val text: CharSequence? get() = node.text
        override val visible: Boolean get() = node.visibility == View.VISIBLE
        override val secret: Boolean get() = ScreenContext.secret(node.inputType, node.autofillHints.orEmpty().toList())
        override val children: List<ScreenContext.Node> get() = (0 until node.childCount).map { ViewNodeOf(node.getChildAt(it)) }
    }

    companion object {
        private const val TAG = "JarvisVISession"
        /** The structure arrives within a few hundred ms of the show; past this it is not coming. */
        private const val ASSIST_WAIT_MS = 2_000L
    }
}
