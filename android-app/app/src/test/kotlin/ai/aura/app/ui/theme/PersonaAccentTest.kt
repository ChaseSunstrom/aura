package ai.aura.app.ui.theme

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertSame
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** The owner, 2026-09-25: "the personas don't change the colors ... can we change the app icon/name". */
@RunWith(RobolectricTestRunner::class)
class PersonaAccentTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private val pm get() = context.packageManager

    private fun state(alias: String) = pm.getComponentEnabledSetting(ComponentName(context, "ai.jarvis.app.$alias"))

    // Back to the default, not to JARVIS: PersonaAccent is a singleton that outlives the
    // test, and a later class's goldens are drawn in whatever it holds.
    @After
    fun back() = PersonaAccent.set(context, JarvisThemes.DEFAULT_ID)

    // T-0923: a changed component state is a PACKAGE_CHANGED, which can end the process
    // (REASON_PACKAGE_STATE_CHANGE) despite DONT_KILL_APP. A fresh install's aliases read
    // DEFAULT, and the start wrote ENABLED/DISABLED over the manifest's own same answer.
    @Test
    fun `the start changes no launcher entry the manifest already has right`() {
        for (alias in listOf("LauncherAura", "LauncherJarvis")) {
            pm.setComponentEnabledSetting(ComponentName(context, "ai.jarvis.app.$alias"), PackageManager.COMPONENT_ENABLED_STATE_DEFAULT, PackageManager.DONT_KILL_APP)
        }
        PersonaIcon.apply(context, "aura")
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DEFAULT, state("LauncherAura"))
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DEFAULT, state("LauncherJarvis"))
        PersonaIcon.apply(context, "jarvis")
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, state("LauncherJarvis"))
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DISABLED, state("LauncherAura"))
    }

    @Test
    fun `the accent and the launcher entry follow the persona, and survive a restart`() {
        PersonaAccent.set(context, "JARVIS")
        assertEquals(JarvisTokens.Color.ACCENT, PersonaAccent.argb)
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, state("LauncherJarvis"))
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DISABLED, state("LauncherAura"))
        PersonaAccent.load(context)
        assertEquals("jarvis", PersonaAccent.name)
        PersonaAccent.set(context, "aura")
        assertEquals(JarvisTokens.Color.PERSONA_AURA, PersonaAccent.argb)
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, state("LauncherAura"))
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DISABLED, state("LauncherJarvis"))
    }

    @Test
    fun `a fresh phone wears AURA - its name, accent, theme and launcher entry`() {
        PersonaAccent.load(context)
        assertEquals("aura", PersonaAccent.name)
        assertEquals(JarvisTokens.Color.PERSONA_AURA, PersonaAccent.argb)
        assertSame(JarvisThemes.AURA, ActiveTheme.theme)
        // On by the manifest (DEFAULT) or by a write; never off (T-0923 writes only a change).
        assertNotEquals(PackageManager.COMPONENT_ENABLED_STATE_DISABLED, state("LauncherAura"))
        assertNotEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, state("LauncherJarvis"))
        // An empty name from the wire is the default, not JARVIS.
        PersonaAccent.set(context, "jarvis")
        PersonaAccent.set(context, "  ")
        assertEquals("aura", PersonaAccent.name)
    }

    @Test
    fun `a switch to JARVIS repaints the theme and moves the launcher, and back`() {
        val auraBg = ActiveTheme.argb("bg.void", fallback = JarvisTokens.Color.BG)
        PersonaAccent.set(context, "jarvis")
        assertSame(JarvisThemes.JARVIS, ActiveTheme.theme)
        assertEquals(JarvisTokens.Color.BG, ActiveTheme.argb("bg.void", fallback = 0))
        assertEquals(JarvisTokens.Color.ACCENT, ActiveTheme.argb("accent", fallback = 0))
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, state("LauncherJarvis"))
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DISABLED, state("LauncherAura"))
        assertEquals(false, ActiveTheme.pill)
        PersonaAccent.set(context, "aura")
        assertEquals(auraBg, ActiveTheme.argb("bg.void", fallback = 0))
        assertEquals(JarvisTokens.Color.PERSONA_AURA, ActiveTheme.argb("accent", fallback = 0))
        assertEquals(true, ActiveTheme.pill)
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, state("LauncherAura"))
    }

    @Test
    fun `a persona since removed wears the default's pack, and a role the pack lacks falls back`() {
        // T-0936: a phone that kept "ada" from before the update wears AURA, never a blank.
        assertSame(JarvisThemes.AURA, ActiveTheme.forPersona("ada")) // T-0936
        assertSame(JarvisThemes.AURA, ActiveTheme.forPersona("nobody"))
        assertSame(JarvisThemes.AURA, ActiveTheme.forPersona("aura"))
        // AURA names no `line`; a gradient or rgba() is never read as a colour.
        assertEquals(42, ActiveTheme.argb("line", "panel.edge", "accent.gradient", fallback = 42))
    }

    @Test
    fun `screens call the assistant by the worn pack's name`() {
        PersonaAccent.set(context, "aura")
        assertEquals(listOf("AURA", "Aura", "Hey Aura"), listOf(ActiveTheme.name, ActiveTheme.spokenName, ActiveTheme.wakePhrase))
        PersonaAccent.set(context, "jarvis")
        assertEquals(listOf("JARVIS", "Jarvis", "Hey Jarvis"), listOf(ActiveTheme.name, ActiveTheme.spokenName, ActiveTheme.wakePhrase))
        PersonaAccent.set(context, "ada") // T-0936: removed, so the default's pack and name
        assertEquals(listOf("AURA", "Aura", "Hey Aura"), listOf(ActiveTheme.name, ActiveTheme.spokenName, ActiveTheme.wakePhrase))
    }

    // Found by name, as a new pack needs no Kotlin (T-0885): the generated style, never 0.
    @Test
    fun `the launch splash is the worn pack's generated style`() {
        PersonaAccent.set(context, "aura")
        assertEquals(ai.aura.app.R.style.Theme_Jarvis_Splash_Aura, ActiveTheme.splash(context))
        PersonaAccent.set(context, "jarvis")
        assertEquals(ai.aura.app.R.style.Theme_Jarvis_Splash_Jarvis, ActiveTheme.splash(context))
        PersonaAccent.set(context, "ada") // T-0936: removed, so the default's splash
        assertEquals(ai.aura.app.R.style.Theme_Jarvis_Splash_Aura, ActiveTheme.splash(context))
    }
}
