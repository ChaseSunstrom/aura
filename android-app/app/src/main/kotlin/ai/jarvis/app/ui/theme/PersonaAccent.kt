package ai.jarvis.app.ui.theme

import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.reactor.personaHue
import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * The primary persona, and the accent the app wears for it (the owner,
 * 2026-09-25: "the personas don't change the colors of the UI/reactor, it still
 * is blue"). The reactor was tinted only by a persona event in a conversation, so
 * every start was Jarvis blue until one came; the controls read the fixed token.
 *
 * Remembered in [JarvisConfig.persona], loaded at start, and set wherever the
 * phone hears which persona is primary. Compose state, so a change reaches a
 * screen already up. The hues are the tokens' `persona.*` colours (personaHue);
 * the semantic colours -- a warning, an error -- never follow it. The theme pack
 * the screens wear follows it too ([ActiveTheme]); before any is heard it is the
 * default pack's, AURA (T-0869).
 */
object PersonaAccent {
    var name: String by mutableStateOf(JarvisThemes.DEFAULT_ID)
        private set

    /** The accent in ARGB: the persona's hue, or Jarvis's own accent. */
    val argb: Int get() = personaHue(name) ?: JarvisTokens.Color.ACCENT

    fun load(context: Context) {
        name = JarvisConfig(context).persona
        // An upgrade from a JARVIS-only build left .LauncherJarvis enabled: put the
        // launcher on the remembered persona's alias (a no-op when it already is).
        PersonaIcon.apply(context.applicationContext, name)
    }

    fun set(context: Context, persona: String) {
        val next = persona.trim().lowercase().ifEmpty { JarvisThemes.DEFAULT_ID }
        if (next == name) return
        name = next
        JarvisConfig(context).persona = next
        PersonaIcon.apply(context.applicationContext, next)
    }
}
