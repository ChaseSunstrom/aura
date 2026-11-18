package ai.aura.app.car.carapp

import ai.aura.app.ui.theme.ActiveTheme
import android.content.Context

/** The device-global permission a car-app host must define (Headway ADR 0009, docs/car/adr/H-0009). */
const val TEMPLATE_RENDERER: String = "android.car.permission.TEMPLATE_RENDERER"

/**
 * What to tell the driver when another package owns [TEMPLATE_RENDERER], or null when
 * nothing is wrong. A permission name has one definer per device, so if Headway (or
 * anything else) got there first, Jarvis installs only if that package is gone -- and
 * after an install that did succeed, a later reinstall of the other app can still take
 * the name back, which is the case this is for: the Maps tab then silently shows the
 * turn card, and nothing else would say why.
 *
 * Does not cover the connection authority, whose owner Android does not report to an
 * app; the two are declared together, so the permission is the proxy for both.
 */
fun rendererConflict(owner: String?, self: String): String? = when (owner) {
    null, self -> null
    "dev.headway.app" ->
        "Headway is still installed and holds the car-app permission, so ${ActiveTheme.name} cannot show " +
            "other apps' car screens. Uninstall Headway; ${ActiveTheme.name} does everything it did."
    else ->
        "$owner holds the car-app permission, so ${ActiveTheme.name} cannot show other apps' car screens. " +
            "Uninstall $owner to let ${ActiveTheme.name} host them."
}

/** [rendererConflict] for this device, read from the package manager. */
fun rendererConflict(context: Context): String? {
    val owner = runCatching { context.packageManager.getPermissionInfo(TEMPLATE_RENDERER, 0).packageName }.getOrNull()
    return rendererConflict(owner, context.packageName)
}
