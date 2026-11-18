package ai.aura.app.compat

import ai.aura.app.compat.RuntimePermissions.Ask
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Brief §2.4: say why before the system dialog, and send a permission refused for good to
 * Settings, where a second dialog would never appear and the tap would do nothing.
 */
class PermissionAskTest {

    @Test fun `a held permission is not asked`() =
        assertEquals(Ask.HELD, RuntimePermissions.nextAsk(granted = true, askedBefore = true, rationale = false))

    @Test fun `the first ask explains before the dialog`() =
        assertEquals(Ask.EXPLAIN, RuntimePermissions.nextAsk(granted = false, askedBefore = false, rationale = false))

    @Test fun `a refusal the platform will ask about again explains again`() =
        assertEquals(Ask.EXPLAIN, RuntimePermissions.nextAsk(granted = false, askedBefore = true, rationale = true))

    // shouldShowRequestPermissionRationale is false both before the first ask and after
    // "don't ask again"; only the remembered ask tells them apart.
    @Test fun `a refusal for good goes to Settings`() =
        assertEquals(Ask.SETTINGS, RuntimePermissions.nextAsk(granted = false, askedBefore = true, rationale = false))

    @Test fun `several missing permissions go to Settings only when every one is refused for good`() {
        assertEquals(Ask.SETTINGS, RuntimePermissions.combine(listOf(Ask.SETTINGS, Ask.HELD, Ask.SETTINGS)))
        assertEquals(Ask.EXPLAIN, RuntimePermissions.combine(listOf(Ask.SETTINGS, Ask.EXPLAIN)))
        assertEquals(Ask.HELD, RuntimePermissions.combine(listOf(Ask.HELD, Ask.HELD)))
    }
}
