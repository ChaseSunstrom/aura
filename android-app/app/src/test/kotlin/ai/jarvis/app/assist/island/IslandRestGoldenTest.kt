package ai.jarvis.app.assist.island

import ai.jarvis.app.assist.island.IslandRest.Network
import ai.jarvis.app.assist.island.IslandRest.Presence
import ai.jarvis.app.assist.island.IslandRest.Status
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.JarvisThemes
import ai.jarvis.app.ui.theme.PersonaAccent
import ai.jarvis.app.ui.theme.JarvisTokens
import android.graphics.Bitmap
import androidx.compose.runtime.snapshots.Snapshot
import android.graphics.Canvas
import android.view.View
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The resting island (T-0890) as it is drawn, the persona's own core frozen in its end and the
 * worn pack's colours (AURA: her space and galaxy): on its own over a white app's status bar (the
 * worst case), and with the phone's status. A 28 dp camera hole in the middle of the top, as most punch-hole phones have.
 * Does not cover where the window lands on a real phone (IslandRestHostTest, and the phone).
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w412dp-h915dp-xhdpi")
class IslandRestGoldenTest {
    private val context = RuntimeEnvironment.getApplication()

    @After
    fun back() {
        wear(JarvisThemes.DEFAULT_ID)
    }

    // Inside a snapshot: a bare write goes to Compose's global write observer, started once per JVM
    // by an earlier class's Compose test and left with no consumer when its Robolectric run ended.
    // After that write, with IslandRestHostTest drawing between, the later Compose screenshots
    // (ApprovalScreenshotTest, CarSetupScreenshotTest) never got idle.
    private fun wear(pack: String) = Snapshot.withMutableSnapshot { PersonaAccent.set(context, pack) }

    private fun shot(
        name: String,
        face: IslandRest.Face,
        presence: Presence = Presence.LISTENING,
        pack: JarvisThemes.Theme = JarvisThemes.JARVIS,
        icons: List<Int> = emptyList(),
    ) {
        wear(pack.id)
        val screen = context.resources.displayMetrics.widthPixels
        val r = JarvisUi.dp(context, 14)
        val snug = JarvisUi.dp(context, JarvisUi.Space.SNUG)
        val tall = IslandRest.height(JarvisUi.dp(context, JarvisTokens.Material.Floating.REST_HEIGHT_DP), IslandRest.Box(0, 0, 2 * r, 2 * r), snug, statusBar = 0)
        val bar = tall + 2 * snug
        val hole = IslandRest.Box(screen / 2 - r, bar / 2 - r, screen / 2 + r, bar / 2 + r)
        val view = RestView(context).apply {
            this.face = face
            this.presence = presence
            this.icons = icons.map { context.getDrawable(it)!! }
            place(hole, tall)
        }
        view.measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED)
        view.layout(0, 0, view.measuredWidth, view.measuredHeight)
        val bitmap = Bitmap.createBitmap(screen, bar, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).apply {
            drawColor(JarvisTokens.Color.TEXT_BRIGHT)
            save()
            // The view's hole on the camera: with icons it grows to the left of it.
            translate(screen / 2f - view.holeX, (bar - view.measuredHeight) / 2f)
            view.draw(this)
            restore()
            // The camera, drawn over the picture where the phone's would be.
            drawCircle(screen / 2f, bar / 2f, r.toFloat(), android.graphics.Paint().apply { color = JarvisTokens.Color.BG })
        }
        bitmap.captureRoboImage("src/test/screenshots/island-rest-$name.png")
    }

    private val status = Status("09:41", battery = 87, charging = false, network = Network.WIFI, bars = 3, dnd = true)

    @Test
    fun `resting, JARVIS's reactor and whether it listens`() = shot("plain", face = IslandRest.face("Jarvis", Presence.LISTENING, null))

    @Test
    fun `resting under AURA, her galaxy in her space`() =
        shot("aura", face = IslandRest.face("Aura", Presence.LISTENING, null), pack = JarvisThemes.AURA)

    @Test
    fun `resting under AURA with the phone's status`() =
        shot("status", face = IslandRest.face("Aura", Presence.LISTENING, status), pack = JarvisThemes.AURA)

    // T-0969: the island as the status bar, an icon for each app with a notification showing.
    @Test
    fun `resting under AURA with the phone's status and three apps' notifications`() =
        shot(
            "icons",
            face = IslandRest.face("Aura", Presence.LISTENING, status),
            pack = JarvisThemes.AURA,
            icons = listOf(android.R.drawable.stat_notify_chat, android.R.drawable.ic_dialog_email, android.R.drawable.stat_sys_download),
        )
}
