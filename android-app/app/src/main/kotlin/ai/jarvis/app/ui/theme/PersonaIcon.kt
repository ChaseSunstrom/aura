package ai.jarvis.app.ui.theme

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.util.Log

/**
 * The launcher's icon and name follow the primary persona: one of two
 * `activity-alias` entries (`.LauncherAura`, the default, and `.LauncherJarvis`)
 * is enabled, the other off. A persona with no alias (any since removed, T-0936)
 * gets the default's, so a phone never ends up with no launcher entry.
 * Called by [PersonaAccent.set] when the persona changes and by [PersonaAccent.load]
 * at start; `android-app/tools/aura_theme_test.py` pins one alias per persona.
 *
 * Does not cover a launcher that caches the old entry until it refreshes (most
 * redraw within seconds; a pinned shortcut keeps the icon it was made with), nor
 * the restart a real switch may still cost: that is a PACKAGE_CHANGED all the same
 * (T-0923; the start's "the last run ended:" line says when it happened).
 */
object PersonaIcon {
    private const val TAG = "JarvisPersonaIcon"
    private val ALIASES = mapOf(
        "aura" to "LauncherAura",
        "jarvis" to "LauncherJarvis",
    )

    fun apply(context: Context, persona: String) {
        val pm = context.packageManager
        val chosen = ALIASES[persona] ?: ALIASES.getValue(JarvisThemes.DEFAULT_ID)
        // The chosen one first: never a moment with no launcher entry at all.
        val ordered = ALIASES.values.sortedBy { if (it == chosen) 0 else 1 }
        for (alias in ordered) {
            val component = ComponentName(context.packageName, "ai.jarvis.app.$alias")
            val state = if (alias == chosen) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
            // Each write is a PACKAGE_CHANGED that can end the process (T-0923), so DEFAULT reads
            // as the manifest's own answer: only the default persona's alias is enabled there.
            val now = pm.getComponentEnabledSetting(component).takeIf { it != PackageManager.COMPONENT_ENABLED_STATE_DEFAULT }
                ?: if (alias == ALIASES.getValue(JarvisThemes.DEFAULT_ID)) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
            if (now == state) continue
            runCatching { pm.setComponentEnabledSetting(component, state, PackageManager.DONT_KILL_APP) }
                .onFailure { Log.w(TAG, "could not switch $alias", it) }
        }
    }
}
