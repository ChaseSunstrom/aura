package ai.jarvis.app.ui

import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.ui.v2.Hairline
import ai.jarvis.app.ui.v2.Projection
import ai.jarvis.app.ui.v2.Stage
import android.text.format.DateFormat
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp

/**
 * The shared skeleton behind the phone's read-only list screens — the audit
 * log (`ai.jarvis.app.automation.ui.AuditLogActivity`), the crash log
 * ([CrashLogActivity]) and the automations list
 * (`ai.jarvis.app.automation.ui.AutomationsActivity`), on the v2 kit (T-0639).
 *
 * All three are the same shape: one scrolling column on the v2 stage, under
 * the system bars, swapped in wholesale as the view changes, and a list/detail
 * pair the back button steps between. This base holds the parts that were
 * byte-for-byte identical across all three (jscpd flagged them as clones) so
 * they live once.
 *
 * What is deliberately NOT here:
 *
 *  * **The coroutine scope and lifecycle.** Two of the three read their source
 *    off the main thread and cancel a scope in `onDestroy`; the crash log reads
 *    synchronously and has neither. Each screen keeps its own scope and lifecycle.
 *  * **`copy(...)`.** Two screens copy a redacted record to the clipboard with
 *    `EXTRA_IS_SENSITIVE`. That stays in each screen: the clip label differs,
 *    and `boot_timeline_test.py` pins the flag to `CrashLogActivity.kt` by
 *    name — the invariant is "a copied crash report is kept out of the system
 *    clipboard preview", and it is checked where the copy happens.
 *
 * The rendering and the detail navigation stay in the subclass, because their
 * `showing` state is typed to the screen's own record.
 */
abstract class ListActivityScaffold : ComponentActivity() {

    /** Bumped by every [show], so a new page starts at its top rather than the last one's scroll. */
    private var page = 0

    /** Replace the whole screen with [content]: one scrolling column on the stage. */
    protected fun show(content: @Composable ColumnScope.() -> Unit) {
        val shown = ++page
        setContent {
            Stage {
                key(shown) {
                    Column(
                        Modifier
                            .fillMaxSize()
                            .safeDrawingPadding()
                            .verticalScroll(rememberScrollState())
                            .padding(JarvisTokens.Space.SCREEN.dp),
                        verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
                        content = content,
                    )
                }
            }
        }
    }

    /** [items] as hairline-ruled rows in one projection; tapping a row opens it. */
    @Composable
    protected fun <T> Rows(items: List<T>, open: (T) -> Unit, row: @Composable ColumnScope.(T) -> Unit) {
        Projection(Modifier.fillMaxWidth()) {
            items.forEachIndexed { index, item ->
                if (index > 0) Hairline()
                Column(
                    Modifier
                        .fillMaxWidth()
                        .heightIn(min = JarvisTokens.Size.TOUCH.dp)
                        .clickable(role = Role.Button, onClickLabel = "Open") { open(item) },
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.TIGHT.dp),
                ) { row(item) }
            }
        }
    }

    /** A row of controls that wraps rather than running off a narrow phone. */
    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    protected fun Buttons(content: @Composable () -> Unit) {
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
            verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
        ) { content() }
    }

    protected fun toast(message: String) = ScreenNote.show(this, message)

    /** A wall-clock timestamp, falling back to the raw epoch millis if the
     * platform formatter is unavailable (it is stubbed under unit tests). */
    protected fun formatTime(ts: Long): String = try {
        DateFormat.format("yyyy-MM-dd HH:mm:ss", ts).toString()
    } catch (t: Throwable) {
        ts.toString()
    }
}
