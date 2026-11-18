package ai.aura.app.glass

import ai.aura.app.assist.Phase
import ai.aura.app.reactor.ReactorInputs
import ai.aura.app.reactor.core.V2Core
import ai.aura.app.ui.theme.JarvisTheme
import android.content.Context
import android.view.View
import android.widget.FrameLayout
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner

/**
 * The console's core as a View, for the surfaces still built from Views: the assist popup,
 * the floating overlay, the enrolment screen, the companion's question card and voice
 * identity. Each drew the phone's port of the console's pre-v2 orb (`reactor/orb`), so the popup
 * and the console showed two different JARVISes (the owner, 2026-11-08: "the popup is still
 * the old one"); each now draws [V2Core], the v2 core ported from `$lib/ds/core` (T-0712),
 * fed by a [ReactorModel] the host writes to from its Ui callbacks. `ReactorState.derive`
 * still picks the state; the console's `coreStateFor` maps it.
 *
 * The core's semantics carry the state word, so TalkBack reads "Listening" from this view.
 * The persona's hue is not worn: the console's core has no tint.
 *
 * ### The overlay window
 *
 * A `ComposeView` needs a lifecycle and a saved-state owner in its view
 * tree. An Activity provides them (`ComponentActivity.setContentView`); a
 * window a Service adds through `WindowManager` provides nothing, and a
 * ComposeView attached to one throws on its first frame. [attachOwners]
 * gives such a window its own owners, started when the view is attached
 * and destroyed when it is detached, so the overlay's reactor runs its
 * clock while the window is up and stops when it goes.
 */
class ReactorView(context: Context) : FrameLayout(context) {

    val model = ReactorModel()

    /** Whether the reactor's felt transitions buzz from this view; state, so a change reaches the composition. */
    var haptics: Boolean by mutableStateOf(true)

    // `ComposeView` is final, so the reactor is a child rather than a parent:
    // the host lays this out as any View and the composition fills it.
    private val compose = ComposeView(context).apply {
        setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnDetachedFromWindow)
        setContent {
            JarvisTheme {
                V2Core(
                    model.derived.state,
                    Modifier.fillMaxSize(),
                    level = model.level,
                    haptics = haptics,
                    testTimeMs = if (canvasOnly) STILL_AT_SECONDS * 1000.0 else null,
                    label = model.derived.spoken,
                )
            }
        }
    }

    init {
        addView(compose, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    }

    // --- what the old orb's callers still say --------------------------------

    fun setInputs(inputs: ReactorInputs) {
        model.inputs = inputs
    }

    fun setPhase(phase: Phase, linkUp: Boolean = true, warming: Boolean = false) = model.setPhase(phase, linkUp, warming)

    fun setAmplitude(level: Float) = model.setAmplitude(level)

    fun setCaption(caption: String) {
        model.caption = caption
    }

    companion object {
        /**
         * Draw one frozen frame on a stopped clock, everywhere a core is drawn through
         * the car's reactor or a ReactorView ([V2Core]). Robolectric runs frames
         * synchronously, so a core that animates never lets its looper go idle, and it
         * cannot compile AGSL. On by default there and nowhere else: when each golden class
         * set and reset it, one that forgot left it on for the classes after it, so the same
         * golden drew a live core alone and a frozen one in the full suite (T-0736), and a
         * Compose test of a live core never went idle.
         */
        @androidx.annotation.VisibleForTesting
        @JvmStatic
        var canvasOnly: Boolean = android.os.Build.FINGERPRINT == "robolectric"

        /** The instant a still reactor is drawn at: past the ignition, mid-breath. */
        internal const val STILL_AT_SECONDS = 2.4f

        /**
         * Give [root] the view-tree owners a Service-owned window lacks. Call
         * before the window is added; the owners follow the view's attach and
         * detach, which is the window's add and remove.
         */
        fun attachOwners(root: View) {
            val owners = WindowOwners()
            root.setViewTreeLifecycleOwner(owners)
            root.setViewTreeSavedStateRegistryOwner(owners)
            root.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
                override fun onViewAttachedToWindow(v: View) = owners.start()
                override fun onViewDetachedFromWindow(v: View) = owners.stop()
            })
        }
    }
}

/**
 * A lifecycle and a saved-state registry for a window that has neither.
 * Resumed while attached, so `Reactor`'s clock runs; destroyed on detach,
 * so it stops. Saved state is never restored: an overlay has none worth
 * keeping across a window that is rebuilt on every wake word.
 */
private class WindowOwners : LifecycleOwner, SavedStateRegistryOwner {
    private val registry = LifecycleRegistry(this)
    private val savedState = SavedStateRegistryController.create(this)

    init {
        savedState.performAttach()
        savedState.performRestore(null)
        registry.currentState = Lifecycle.State.CREATED
    }

    override val lifecycle: Lifecycle get() = registry
    override val savedStateRegistry: SavedStateRegistry get() = savedState.savedStateRegistry

    fun start() {
        if (registry.currentState == Lifecycle.State.DESTROYED) return
        registry.currentState = Lifecycle.State.RESUMED
    }

    fun stop() {
        if (registry.currentState == Lifecycle.State.DESTROYED) return
        registry.currentState = Lifecycle.State.DESTROYED
    }
}
