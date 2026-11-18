package ai.aura.app.car.video

import ai.aura.app.ui.theme.ActiveTheme
import android.app.AppOpsManager
import android.content.Context
import android.os.Process
import ai.aura.app.automation.actions.builtin.Shizuku

/**
 * Screen sharing that starts with the car and needs no tap. Android asks for consent
 * on every share unless the app holds the PROJECT_MEDIA app-op, which only the shell
 * can set; with Shizuku running and Jarvis allowed in it, the app sets it for itself
 * once, and the share the car asks for at connect completes with no dialog (the owner,
 * 2026-10-08: "it did auto end it ... but didn't auto start it").
 *
 * Not covered: a phone without Shizuku running, or with Jarvis not allowed in it --
 * [ensure] says which, and the one-time `adb shell appops set <pkg> PROJECT_MEDIA
 * allow` (printed by the CI's adb-setup) does the same from a computer.
 */
object ProjectionAppop {
    private const val OP = "android:project_media"

    fun held(ctx: Context): Boolean = runCatching {
        ctx.getSystemService(AppOpsManager::class.java)
            .unsafeCheckOpNoThrow(OP, Process.myUid(), ctx.packageName) == AppOpsManager.MODE_ALLOWED
    }.getOrDefault(false)

    /** Sets the app-op through Shizuku when it can; the line says what happened. */
    fun ensure(ctx: Context): String {
        if (held(ctx)) return "screen sharing needs no tap (PROJECT_MEDIA allowed)"
        if (!Shizuku.pingBinder()) return "screen sharing will ask each drive: Shizuku is not running to allow it once"
        if (!Shizuku.hasPermission()) return "screen sharing will ask each drive: allow ${ActiveTheme.name} in Shizuku and it stops asking"
        val done = runCatching {
            Shizuku.newProcess(arrayOf("appops", "set", ctx.packageName, "PROJECT_MEDIA", "allow"))?.waitFor()
        }.getOrNull()
        return if (held(ctx)) "screen sharing allowed for good through Shizuku; no more taps"
        else "Shizuku could not allow screen sharing (exit $done); it will ask each drive"
    }
}
