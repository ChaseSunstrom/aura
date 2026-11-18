package ai.aura.app.ui.theme

import ai.aura.app.R
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

/**
 * The theme pack the phone wears (T-0869): the primary persona's own pack from
 * [JarvisThemes] (each of the two has one, `tests/contracts/persona.json` `themes`), or
 * the default's for any other name, as `android-app/tools/aura_theme_test.py` pins. It reads [PersonaAccent.name], which is Compose state, so a `persona.switch`
 * repaints every screen already up: AURA's violet and Sora, then JARVIS's cyan and
 * Space Grotesk, with no restart.
 *
 * Does not cover the views drawn without Compose (the reactor, the splash, the
 * notification icon): those keep JARVIS's tokens until their own lanes move them.
 */
object ActiveTheme {
    /**
     * A persona's own pack, and the default's for a name nobody knows (a persona since
     * removed, T-0936), as the console and the adapter do (an unknown name was JARVIS
     * here, AURA there: the review of T-0869).
     */
    fun forPersona(persona: String): JarvisThemes.Theme =
        JarvisThemes.ALL.firstOrNull { it.id == persona }
            ?: JarvisThemes.ALL.first { it.id == JarvisThemes.DEFAULT_ID }

    val theme: JarvisThemes.Theme get() = forPersona(PersonaAccent.name)

    /**
     * What a screen calls the assistant (PROMPT §2, T-0873): the worn pack's written name
     * ("AURA", "JARVIS").
     */
    val name: String get() = theme.name

    /** The name as it is said aloud ("Aura", "Jarvis"): a spoken line reads this, not [name]. */
    val spokenName: String get() = theme.spokenName

    /**
     * The app's own label (`app_name`), which Android's Settings → Apps and a VPN app's exclusion
     * list show whatever is worn: the default pack's name. Does not cover the launcher, whose
     * JARVIS alias says the worn name.
     */
    val appLabel: String get() = JarvisThemes.of(JarvisThemes.DEFAULT_ID).name

    /** The wake phrase as a screen quotes it: "Hey Aura" (every pack's phrases include "hey <name>"). */
    val wakePhrase: String get() = "Hey $spokenName"

    /**
     * The worn pack's notification icon, alpha only (T-0908: the tray showed JARVIS's core with
     * AURA worn). JARVIS's for a pack without one. A notification already posted keeps its icon
     * until it is next updated.
     */
    val statusIcon: Int get() = if (theme.id == JarvisThemes.AURA.id) R.drawable.ic_aura_status else R.drawable.ic_jarvis_status

    /**
     * The first of [roles] the pack paints as a plain `#RRGGBB`, else [fallback] (a
     * [JarvisTokens] colour). A pack names only the roles it has, so JARVIS's
     * `line` is AURA's `bg.nebula`; a gradient or an rgba() is never read as a colour.
     */
    fun argb(vararg roles: String, fallback: Int): Int {
        val colors = theme.colors
        for (role in roles) parse(colors[role])?.let { return it }
        return fallback
    }

    fun color(vararg roles: String, fallback: Int): Color = Color(argb(*roles, fallback = fallback))

    /**
     * The pack's colour for [roles] at [token]'s alpha, for a View painted from a translucent
     * token: the island's fill and edge in the worn pack (T-0885). [token] itself when the pack
     * names none of [roles].
     */
    fun tint(token: Int, vararg roles: String): Int =
        androidx.core.graphics.ColorUtils.setAlphaComponent(argb(*roles, fallback = token), android.graphics.Color.alpha(token))

    /**
     * The worn pack's launch style, `Theme.Jarvis.Splash.<Id>` (values-v31/theme_splash.xml,
     * generated per pack by design/themes.py), so a new pack needs no Kotlin; 0 when the pack
     * has none. By name because the set of packs is the generator's, not this file's; the
     * prefix is the base style's own name, so no persona's name is typed here.
     */
    @android.annotation.SuppressLint("DiscouragedApi")
    fun splash(context: android.content.Context): Int = context.resources.getIdentifier(
        context.resources.getResourceEntryName(R.style.Theme_Jarvis) + ".Splash." + theme.id.replaceFirstChar { it.uppercase() },
        "style",
        context.packageName,
    )

    // "FF" + the six digits: the pack's colours are opaque. A typed 0xFF… mask would trip token-lint.
    internal fun parse(value: String?): Int? =
        value?.takeIf { it.length == 7 && it[0] == '#' }?.let { ("FF" + it.substring(1)).toLongOrNull(16)?.toInt() }

    /** `#AARRGGBB`, the shape design/themes.py writes a translucent pack colour in (a nebula); null otherwise. */
    internal fun parseArgb(value: String?): Int? =
        value?.takeIf { it.length == 9 && it[0] == '#' }?.substring(1)?.toLongOrNull(16)?.toInt()

    private val RGBA = Regex("""rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d*\.?\d+)\s*\)""")

    /**
     * Every `rgba(r, g, b, a)` in [value], in order: the shape a pack writes its panel glass in
     * (`panel.fill` is two, top and bottom; `panel.edge` one). Empty for anything else, and a
     * colour with a channel past 255 is skipped, never clamped into some other colour.
     */
    internal fun rgbas(value: String?): List<Int> = RGBA.findAll(value.orEmpty()).mapNotNull { m ->
        val (r, g, b) = m.groupValues.subList(1, 4).map { it.toInt() }
        val a = m.groupValues[4].toFloatOrNull() ?: return@mapNotNull null
        val alpha = Math.round(a.coerceIn(0f, 1f) * CHANNEL_MAX)
        if (maxOf(r, g, b) > CHANNEL_MAX) null else android.graphics.Color.argb(alpha, r, g, b)
    }.toList()

    private const val CHANNEL_MAX = 255

    private val FAMILIES = mapOf(
        "Sora" to SoraFamily,
        "DM Mono" to DmMonoFamily,
        "Space Grotesk" to SpaceGroteskFamily,
        "JetBrains Mono" to JetbrainsMonoFamily,
    )

    /** The pack's display face (big words, the wordmark); JARVIS's for a face the phone lacks. */
    val display: FontFamily get() = FAMILIES[theme.displayFamily] ?: SpaceGroteskFamily

    /** The pack's reading face (labels, running text, data). */
    val mono: FontFamily get() = FAMILIES[theme.monoFamily] ?: JetbrainsMonoFamily

    /** AURA's controls are pills, JARVIS's square-cornered (the pack's `toggle`). */
    val pill: Boolean get() = theme.toggle == "pill"
}

/** AURA's display face (themes/aura `displayFamily`), bundled like the rest under res/font. */
val SoraFamily = FontFamily(
    Font(R.font.sora_200, FontWeight.ExtraLight),
    Font(R.font.sora_300, FontWeight.Light),
    Font(R.font.sora_400, FontWeight.Normal),
    Font(R.font.sora_500, FontWeight.Medium),
)

/** AURA's reading face (themes/aura `monoFamily`). */
val DmMonoFamily = FontFamily(
    Font(R.font.dm_mono_300, FontWeight.Light),
    Font(R.font.dm_mono_400, FontWeight.Normal),
    Font(R.font.dm_mono_500, FontWeight.Medium),
)

private fun TextStyle.faced(): TextStyle =
    copy(fontFamily = if (fontFamily == SpaceGroteskFamily) ActiveTheme.display else ActiveTheme.mono)

/**
 * [JarvisTheme] in the active pack's colours and faces: what Material reads
 * (`colorScheme`, `typography`) follows the persona; the sizes, weights and shapes
 * stay the tokens'.
 */
@Composable
fun AppTheme(content: @Composable () -> Unit) {
    val t = JarvisTokens.Color
    val scheme = JarvisColorScheme.copy(
        primary = ActiveTheme.color("accent", fallback = t.ACCENT),
        onPrimary = ActiveTheme.color("on.accent", fallback = t.ACCENT_INK),
        tertiary = ActiveTheme.color("attention", fallback = t.WARN),
        background = ActiveTheme.color("bg.void", fallback = t.BG),
        onBackground = ActiveTheme.color("text", fallback = t.TEXT),
        surface = ActiveTheme.color("bg.panel", "bg.deep", fallback = t.PANEL),
        onSurface = ActiveTheme.color("text", fallback = t.TEXT),
        surfaceVariant = ActiveTheme.color("surface.2", "bg.nebula", fallback = t.SURFACE_2),
        onSurfaceVariant = ActiveTheme.color("text.secondary", fallback = t.TEXT_DIM),
        outline = ActiveTheme.color("line", "bg.nebula", fallback = t.LINE),
        error = ActiveTheme.color("danger", fallback = t.DANGER),
    )
    val ty = JarvisTypography
    val typography = Typography(
        displayLarge = ty.displayLarge.faced(),
        titleLarge = ty.titleLarge.faced(),
        bodyLarge = ty.bodyLarge.faced(),
        bodyMedium = ty.bodyMedium.faced(),
        bodySmall = ty.bodySmall.faced(),
        labelLarge = ty.labelLarge.faced(),
        labelMedium = ty.labelMedium.faced(),
        labelSmall = ty.labelSmall.faced(),
    )
    MaterialTheme(colorScheme = scheme, typography = typography, shapes = JarvisShapes, content = content)
}
