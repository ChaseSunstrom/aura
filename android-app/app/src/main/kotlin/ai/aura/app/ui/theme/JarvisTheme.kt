package ai.aura.app.ui.theme

import ai.aura.app.R
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/**
 * @generated from design/tokens.json — DO NOT EDIT. Run `python3 design/build.py`.
 *
 * The Compose theme, built from [JarvisTokens]. The Glass HUD is one dark room,
 * so there is one colour scheme and no light variant: a Composable that reads
 * `MaterialTheme.colorScheme` gets the same palette the console and the desktop
 * draw with, and `MaterialTheme.typography` the same two faces — JetBrains Mono
 * for what a person reads, Space Grotesk Light for big numbers — bundled under
 * res/font so the phone, like the console, fetches nothing (BUILD_BRIEF §1).
 */
object JarvisColors {
    val Bg = Color(JarvisTokens.Color.BG)
    val Panel = Color(JarvisTokens.Color.PANEL)
    val Surface2 = Color(JarvisTokens.Color.SURFACE_2)
    val Accent = Color(JarvisTokens.Color.ACCENT)
    val AccentDeep = Color(JarvisTokens.Color.ACCENT_DEEP)
    val AccentInk = Color(JarvisTokens.Color.ACCENT_INK)
    val Warn = Color(JarvisTokens.Color.WARN)
    val Danger = Color(JarvisTokens.Color.DANGER)
    val Ok = Color(JarvisTokens.Color.OK)
    val Text = Color(JarvisTokens.Color.TEXT)
    val TextBright = Color(JarvisTokens.Color.TEXT_BRIGHT)
    val TextDim = Color(JarvisTokens.Color.TEXT_DIM)
    val TextFaint = Color(JarvisTokens.Color.TEXT_FAINT)
    val Line = Color(JarvisTokens.Color.LINE)
    val LineSoft = Color(JarvisTokens.Color.LINE_SOFT)
}

val JarvisColorScheme = darkColorScheme(
    primary = JarvisColors.Accent,
    onPrimary = JarvisColors.AccentInk,
    secondary = JarvisColors.AccentDeep,
    onSecondary = JarvisColors.AccentInk,
    tertiary = JarvisColors.Warn,
    onTertiary = JarvisColors.AccentInk,
    background = JarvisColors.Bg,
    onBackground = JarvisColors.Text,
    surface = JarvisColors.Panel,
    onSurface = JarvisColors.Text,
    surfaceVariant = JarvisColors.Surface2,
    onSurfaceVariant = JarvisColors.TextDim,
    outline = JarvisColors.Line,
    outlineVariant = JarvisColors.LineSoft,
    error = JarvisColors.Danger,
    onError = JarvisColors.AccentInk,
)

/** The UI face (type.family.body / chrome): labels, running text, data. */
val JetbrainsMonoFamily = FontFamily(
    Font(R.font.jetbrains_mono_400, FontWeight(400)),
    Font(R.font.jetbrains_mono_500, FontWeight(500)),
    Font(R.font.jetbrains_mono_600, FontWeight(600)),
)

/** The figure face (type.family.display): big numbers, the wordmark. */
val SpaceGroteskFamily = FontFamily(
    Font(R.font.space_grotesk_300, FontWeight(300)),
    Font(R.font.space_grotesk_400, FontWeight(400)),
)

val JarvisTypography = Typography(
    displayLarge = TextStyle(fontFamily = SpaceGroteskFamily, fontWeight = FontWeight(JarvisTokens.Weight.DISPLAY), fontSize = 40.sp),
    titleLarge = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.LABEL), fontSize = JarvisTokens.Type.TITLE.sp),
    bodyLarge = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.RESPONSE.sp),
    bodyMedium = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.BODY.sp),
    bodySmall = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.HINT.sp),
    labelLarge = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.STRONG), fontSize = JarvisTokens.Type.FIELD.sp),
    labelMedium = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.BODY), fontSize = JarvisTokens.Type.MONO.sp),
    labelSmall = TextStyle(fontFamily = JetbrainsMonoFamily, fontWeight = FontWeight(JarvisTokens.Weight.LABEL), fontSize = JarvisTokens.Type.LABEL.sp, letterSpacing = JarvisTokens.Tracking.LABEL.em),
)

val JarvisShapes = Shapes(
    small = RoundedCornerShape(JarvisTokens.Radius.SM.dp),
    medium = RoundedCornerShape(JarvisTokens.Radius.MD.dp),
    large = RoundedCornerShape(JarvisTokens.Radius.LG.dp),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = JarvisColorScheme,
        typography = JarvisTypography,
        shapes = JarvisShapes,
        content = content,
    )
}
