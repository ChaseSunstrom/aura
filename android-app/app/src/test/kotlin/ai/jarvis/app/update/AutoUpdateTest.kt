package ai.jarvis.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * When an automatic update check is due.
 *
 * The whole point of [AutoUpdate.isCheckDue] is that it is pure — a function of
 * the setting, connectivity, the last-check time and the clock — so the policy
 * is a table rather than something that only reveals itself against a live
 * ConnectivityManager and a real SharedPreferences file. This is that table,
 * and `android-app/tools/auto_update_test.py` is the same table again, checked
 * against the Kotlin source in the container that cannot compile it.
 */
class AutoUpdateTest {

    private val now = 1_000_000_000_000L
    private val day = AutoUpdate.CHECK_INTERVAL_MS

    private fun due(
        enabled: Boolean = true,
        online: Boolean = true,
        lastCheckMs: Long = 0L,
        nowMs: Long = now,
    ) = AutoUpdate.isCheckDue(enabled, online, lastCheckMs, nowMs)

    @Test
    fun theSettingIsTheFirstWord() {
        // Off beats everything, including a first run on a live network.
        assertFalse(due(enabled = false, lastCheckMs = 0L))
        assertFalse(due(enabled = false, lastCheckMs = now - day * 10))
    }

    @Test
    fun offlineIsNeverDue() {
        // A check offline can only fail, and failing spends the daily slot.
        assertFalse(due(online = false, lastCheckMs = 0L))
        assertFalse(due(online = false, lastCheckMs = now - day * 10))
    }

    @Test
    fun theFirstCheckIsDue() {
        // Nothing recorded yet: a fresh install should not wait a day to learn
        // there is already a newer build.
        assertTrue(due(lastCheckMs = 0L))
        // A negative sentinel is "never checked" too.
        assertTrue(due(lastCheckMs = -1L))
    }

    @Test
    fun withinTheDayItIsNotDue() {
        assertFalse(due(lastCheckMs = now - (day - 1)))
        assertFalse(due(lastCheckMs = now)) // just checked
        assertFalse(due(lastCheckMs = now - day / 2))
    }

    @Test
    fun aDayLaterItIsDueAgain() {
        assertTrue(due(lastCheckMs = now - day))
        assertTrue(due(lastCheckMs = now - (day + 1)))
        assertTrue(due(lastCheckMs = now - day * 10))
    }

    @Test
    fun aFutureStampDoesNotLockTheCheckOut() {
        // The clock was set forward once and corrected, or a backup carried a
        // stamp from another device. Trusting it would refuse checks until real
        // time caught up; a negative elapsed is treated as due and self-heals.
        assertTrue(due(lastCheckMs = now + day))
        assertTrue(due(lastCheckMs = now + day * 365))
    }

    @Test
    fun theIntervalIsAnHour() {
        // A day until ADR-0050: every green build of the branch production runs is
        // published now, and the owner wants the phone on the latest one.
        assertEquals(60L * 60L * 1000L, AutoUpdate.CHECK_INTERVAL_MS)
    }

    /** The four cases android-app/tools/auto_update_test.py UNATTENDED_CASES asserts. */
    @Test
    fun onlyAProductionBuildInstallsItselfAndOnlyWithTheSettingOn() {
        fun update(prerelease: Boolean) = ReleaseFeed.Update(
            versionCode = 2000, versionName = "1.0.2000", tag = "v1.0.2000+2000",
            downloadUrl = "https://gitlab.home.example/x.apk", sizeBytes = 1, notes = "", prerelease = prerelease,
        )
        assertTrue(AutoUpdate.shouldInstallUnattended(autoInstall = true, update(prerelease = false)))
        assertFalse(AutoUpdate.shouldInstallUnattended(autoInstall = true, update(prerelease = true)))
        assertFalse(AutoUpdate.shouldInstallUnattended(autoInstall = false, update(prerelease = false)))
        assertFalse(AutoUpdate.shouldInstallUnattended(autoInstall = false, update(prerelease = true)))
    }
}
