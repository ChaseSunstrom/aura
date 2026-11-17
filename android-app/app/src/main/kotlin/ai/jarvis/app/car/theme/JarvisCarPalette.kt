package ai.jarvis.app.car.theme

import ai.jarvis.app.ui.theme.JarvisTokens
import dev.headway.dash.CarPalette
import dev.headway.dash.CarTheme
import dev.headway.dash.ThemeAccent
import dev.headway.dash.ThemeBase
import dev.headway.dash.ThemeChoice

/**
 * The car screen's colours (ADR-0047). The default -- dark, cyan -- is Jarvis's own
 * Reactor II palette from the design tokens, so the car looks like the console and the
 * phone. Any other base or accent the driver picks on the car's settings sheet is
 * Headway's palette for it, unchanged: those exist for daylight and for night driving,
 * and the design system has no light car theme to offer instead.
 */
fun carPalette(choice: ThemeChoice): CarPalette =
    if (choice.base == ThemeBase.DARK && choice.accent == ThemeAccent.HEADWAY) JARVIS else CarTheme.palette(choice)

private val JARVIS = CarPalette(
    ground = JarvisTokens.Color.BG,
    surface = JarvisTokens.Color.PANEL,
    surfaceRaised = JarvisTokens.Color.SURFACE_2,
    outline = JarvisTokens.Color.LINE,
    text = JarvisTokens.Color.TEXT,
    textMuted = JarvisTokens.Color.TEXT_DIM,
    accent = JarvisTokens.Color.ACCENT,
    accentDim = JarvisTokens.Color.ACCENT_DEEP,
    onAccent = JarvisTokens.Color.ACCENT_INK,
    warn = JarvisTokens.Color.WARN,
    fault = JarvisTokens.Color.DANGER,
    good = JarvisTokens.Color.OK,
)
