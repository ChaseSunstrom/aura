package ai.jarvis.app

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.compat.RuntimePermissions
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.ConsolePage
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.SystemCheckActivity
import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.update.AutoUpdate
import ai.jarvis.app.update.ReleaseFeed
import ai.jarvis.app.update.UpdateChecker
import ai.jarvis.app.update.UpdateNotification
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.activity.ComponentActivity

/**
 * The Jarvis home: the console, in a web view ([ConsolePage]), and nothing under it.
 *
 * The phone used to draw its own glass HUD here and cover it with the console once the
 * page had loaded (the owner, 2026-09-27, made the console the home; T-0639 step 3 took
 * the native HUD out). What is left is what only the phone can do: the console's page
 * and its `JarvisPhone` bridge, the permissions the console's voice and the car need,
 * the automation layer and the wake listener, the system checklist on a first run, and
 * the offer of a newer build. The phone's own options stay native ([SettingsActivity])
 * and the console links to them; the settings screen links back to a console page by
 * [EXTRA_CONSOLE_PATH].
 *
 * There is no native fallback. When the console cannot be reached the page shows the
 * console's offline asset (`console-offline.html`), with its links to the phone's
 * settings; with no server set yet it shows the same page, aimed at an address that
 * never resolves, so the link to Settings is on screen from the first launch.
 *
 * While the page is up it owns the microphone (its open mic and push-to-talk), so the
 * wake listener is paused for as long as this screen is in front and resumed when it
 * leaves: two recorders on one phone is a coin toss over which hears.
 *
 * A `ComponentActivity` rather than a plain `Activity` for the activity-result
 * registry the page's file picker uses.
 */
class MainActivity : ComponentActivity() {

    private lateinit var config: JarvisConfig

    /** Where the page is loading from, so a changed server reopens it on the next resume. */
    private var consoleShown: String? = null
    private var consolePage: ConsolePage? = null
    private lateinit var root: FrameLayout

    /** The console page the next open lands on, from [EXTRA_CONSOLE_PATH]; empty is its home. */
    private var consolePath = ""

    /** One permission prompt per launch. See [askForNotificationsOnce]. */
    private var askedForNotifications = false

    /** The "newer build" offer on screen, so resuming twice does not stack two. */
    private var updateDialog: android.app.AlertDialog? = null

    /** The console page's file input waiting on the system picker (the chat's Attach, T-0497). */
    private var pickedFile: ((Array<android.net.Uri>?) -> Unit)? = null
    private val filePicker = registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult()) { result ->
        pickedFile?.invoke(android.webkit.WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
        pickedFile = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisUi.immersive(this)
        // The page's ground goes under the camera's cutout too, and keeps its content clear of
        // it itself (ConsolePage.fit, T-0974): before Android 15 a window with its bars hidden
        // was kept out of the cutout's band, which showed black.
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode = android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            }
        }
        config = JarvisConfig(this)
        // The colour behind the page, which is transparent until it paints.
        root = FrameLayout(this).apply { setBackgroundColor(JarvisTokens.Color.BG) }
        setContentView(root)
        consolePath = consolePathOf(intent)
        openConsoleHome()
        onBackPressedDispatcher.addCallback(this, object : androidx.activity.OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                val view = consolePage?.view
                if (view != null && view.canGoBack()) view.goBack() else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })
    }

    // --- the console as the home screen -----------------------------------------------

    /**
     * A settings link to a console page (T-0689) while the home is already up: the page is
     * rebuilt at that path when the screen resumes. An intent without the extra (the
     * launcher's) leaves the page where it was.
     */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val path = consolePathOf(intent)
        if (path.isEmpty()) return
        consolePath = path
        closeConsoleHome()
    }

    /**
     * The console's page, loading from the server the phone is set to. The page is shown
     * once it has painted and hidden when a load fails and there is no offline asset: a
     * transparent page took every touch, so an unreachable console left a screen that
     * looked alive and did nothing.
     */
    private fun openConsoleHome() {
        val console = ai.jarvis.app.car.dash.CarHud.consoleUrl(this)?.trimEnd('/')?.plus("/")
            ?: ConsolePage.OFFLINE_BASE
        if (consolePage != null && console == consoleShown) return
        closeConsoleHome()
        // Offline there is one page, and it is not the console's: the path waits for none.
        val url = if (console == ConsolePage.OFFLINE_BASE) console else console + consolePath.removePrefix("/")
        consolePath = ""
        val page = ConsolePage(
            this,
            url,
            " JarvisPhone",
            onLoaded = { consolePage?.view?.visibility = View.VISIBLE },
            onFailed = { consolePage?.view?.visibility = View.INVISIBLE },
            onRendererGone = {
                closeConsoleHome()
                openConsoleHome()
            },
            // Unreachable, the console's own offline page.
            offlinePage = true,
            // Signs the page in to the console, which has no password to type here (T-0387).
            key = config.hermesKey,
            // An input left unanswered never opens again, so every path answers: an
            // earlier pick still waiting gets nothing, and so does a phone with no picker.
            chooseFile = { intent, answer ->
                pickedFile?.invoke(null)
                pickedFile = answer
                try {
                    filePicker.launch(intent)
                } catch (e: android.content.ActivityNotFoundException) {
                    pickedFile = null
                    answer(null)
                }
            },
        )
        page.view.visibility = View.INVISIBLE
        root.addView(page.view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        ConsolePage.fit(page.view)
        consolePage = page
        consoleShown = console
        page.bridgePhone()
        page.start()
    }

    private fun closeConsoleHome() {
        val page = consolePage ?: return
        consolePage = null
        consoleShown = null
        (page.view.parent as? ViewGroup)?.removeView(page.view)
        page.stop()
    }

    override fun onResume() {
        super.onResume()
        // Settings may have set or changed the server behind our back.
        openConsoleHome()
        askForNotificationsOnce()
        startAutomationLayer()
        openSystemCheckOnceIfSetupIsIncomplete()
        maybeOfferUpdate()
        // A drive's log that could not get home from the car goes now (T-0742).
        runCatching { ai.jarvis.app.car.log.CarLogUpload.sendPending(this) }
        // The page has the microphone while it is in front.
        consolePage?.resume()
        runCatching { WakeWordService.pause(this) }
    }

    /**
     * Leaving the screen gives the microphone back, every time: the moment another window
     * is in front of this one, the page's open mic is behind somebody else's UI.
     */
    override fun onPause() {
        consolePage?.pause()
        updateDialog?.dismiss()
        updateDialog = null
        // Only if it is supposed to be running at all; resuming a listener the user has
        // switched off would turn leaving this screen into a way to switch it back on.
        if (config.wakeWordEnabled) runCatching { WakeWordService.resume(this) }
        // The platform keeps this for the next cold start: the app opens on the worn pack's
        // ground and mark, not the manifest's JARVIS reactor (T-0885, the owner saw AURA worn
        // and JARVIS launching). Set on leaving, as a persona can change while the app is open.
        ai.jarvis.app.ui.theme.ActiveTheme.splash(this).takeIf { it != 0 }?.let { splashScreen.setSplashScreenTheme(it) }
        super.onPause()
    }

    override fun onDestroy() {
        closeConsoleHome()
        super.onDestroy()
    }

    /**
     * Take the user to the checklist the first time something essential is off.
     *
     * A banner was not enough, and the field report says why: "the overlay isn't popping
     * up still, only the notification". Every one of those symptoms is a special access
     * that has to be granted on a Settings screen the user has never heard of, and an app
     * that waits to be asked about it is an app that stays broken. The checklist names
     * each one, says what breaks without it and opens the exact page.
     *
     * Once per install, and only when something ESSENTIAL is missing, so this is a setup
     * step rather than a nag.
     */
    private fun openSystemCheckOnceIfSetupIsIncomplete() {
        if (config.setupChecklistShown) return
        if (GrapheneCompat.missingEssentials(this).isEmpty()) {
            // Nothing to walk them through -- and remember that, so a permission revoked
            // in six months does not trigger a first-run flow.
            config.setupChecklistShown = true
            return
        }
        config.setupChecklistShown = true
        runCatching { openSystemCheck() }
    }

    /**
     * Ask for the runtime permissions the phone's side of Jarvis needs, once per launch.
     *
     * POST_NOTIFICATIONS is declared in the manifest, which on Android 12 and below is the
     * whole story; on 13+ a permission nobody requests is one you do not have, and an
     * approval that cannot be delivered times out and is denied. Location rides the same
     * round trip (one request at a time is all Android runs): the car screen's map, speed
     * limit and turn timing read the phone's fix. The microphone rides it too: the
     * console's own voice is granted to the page only while the app holds it, and the
     * native mute and listen taps that used to ask are gone with the native HUD.
     *
     * Asked from the home screen rather than at the first use, because this is an ordinary
     * Activity that can hold the round trip, and at most once per launch: a dialog that
     * reappears on every resume is how people learn to hit Deny.
     */
    private fun askForNotificationsOnce() {
        if (askedForNotifications) return
        val missing = listOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
        ).any { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED } ||
            (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
        if (!missing) return
        askedForNotifications = true
        // A permission already held is answered at once without a dialog, so they go
        // together; below 13 POST_NOTIFICATIONS is simply not a runtime one.
        askFor(
            arrayOf(
                POST_NOTIFICATIONS,
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION,
            ),
            REQ_NOTIFICATIONS,
            fromTap = false,
        )
    }

    /**
     * Every ask from here says why before the system dialog (brief §2.4), from the same why
     * lines as SYSTEM CHECK. A permission refused for good opens Settings when the owner tapped
     * for it ([fromTap]); at launch that stays quiet, as does a refusal the dialog could ask
     * about again, so a "no" is not asked again on every start.
     */
    private fun askFor(permissions: Array<String>, requestCode: Int, fromTap: Boolean) {
        val ask = { runCatching { requestPermissions(permissions, requestCode) } }
        val why = RuntimePermissions.whyOf(this, permissions.toList())
        when (RuntimePermissions.nextAsk(this, permissions.toList())) {
            RuntimePermissions.Ask.HELD -> ask()
            RuntimePermissions.Ask.EXPLAIN -> {
                if (!fromTap && permissions.any { shouldShowRequestPermissionRationale(it) }) return
                android.app.AlertDialog.Builder(this)
                    .setTitle("${ActiveTheme.name} needs your permission")
                    .setMessage(why)
                    .setPositiveButton("Continue") { _, _ -> ask() }
                    .setNegativeButton("Not now", null)
                    .show()
            }
            RuntimePermissions.Ask.SETTINGS -> if (fromTap) {
                android.app.AlertDialog.Builder(this)
                    .setTitle("Turned off in Android settings")
                    .setMessage("$why\n\nAndroid will not ask again. Turn it on under Permissions.")
                    .setPositiveButton("Open settings") { _, _ ->
                        runCatching {
                            startActivity(
                                Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                                    .setData(android.net.Uri.fromParts("package", packageName, null)),
                            )
                        }
                    }
                    .setNegativeButton("Not now", null)
                    .show()
            }
        }
    }

    /**
     * Bring the automation layer up from a foreground path.
     *
     * `JarvisAutomationService` is otherwise started only by `BootReceiver` on
     * BOOT_COMPLETED / MY_PACKAGE_REPLACED, so a fresh install had no `AutomationRuntime`
     * -- no dispatcher, no `ActionEnv` -- and every command from the server was answered
     * "unsupported" until the phone was next rebooted.
     *
     * Starting a foreground service from a resumed Activity is always permitted; the guard
     * is here because a refused start must not take the home screen down with it.
     */
    private fun startAutomationLayer() {
        runCatching { JarvisAutomationService.ensureRunning(this, "home") }
        // The one route Android never refuses for a microphone-typed service, which is why
        // the wake listener is nudged here rather than from a receiver, and why this is the
        // one caller that may claim `fromForeground`.
        runCatching { WakeWordService.ensureRunning(this, fromForeground = true) }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        RuntimePermissions.markRefused(this, permissions, grantResults)
        // "All the time" is its own request, and only once while-in-use is held: the car
        // link starts from the car's Bluetooth with the phone locked, and a foreground
        // service started from the background gets no fix without it.
        if (requestCode == REQ_NOTIFICATIONS &&
            checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED &&
            checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) != PackageManager.PERMISSION_GRANTED
        ) runCatching { requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), REQ_BACKGROUND_LOCATION) }
    }

    private fun openSystemCheck() =
        startActivity(Intent(this, SystemCheckActivity::class.java))

    // --- automatic updates --------------------------------------------------

    /**
     * Offer any newer build, and -- at most once an hour -- look for one.
     *
     * Two halves, because they run at different speeds. A build a previous check already
     * found is offered straight away from cache ([AutoUpdate.pendingOffer] is a
     * SharedPreferences read and nothing more). The network check is gated by
     * [AutoUpdate.shouldCheckNow] and runs on its own thread; only if it turns up
     * something new does an offer or a notification appear.
     *
     * A production build installs itself when "Install updates automatically" is on
     * ([AutoUpdate.act], ADR-0050); anything else is surfaced, and INSTALL, then the
     * system's PackageInstaller prompt, are the two confirmations -- see [UpdateChecker].
     */
    private fun maybeOfferUpdate() {
        if (!config.isConfigured) return
        val installed = appVersionCode()
        AutoUpdate.pendingOffer(this, installed)?.let { showUpdateOffer(it) }

        val allowPrerelease = config.allowPrereleaseUpdates
        Thread {
            if (!AutoUpdate.shouldCheckNow(this, config)) return@Thread
            val result = UpdateChecker(applicationContext).check(installed, allowPrerelease)
            if (result !is UpdateChecker.Result.Offered) return@Thread
            // Installs a production build unattended when the setting allows (ADR-0050);
            // otherwise remembers and posts it. The notification is for the backgrounded
            // case; the offer is for the open one.
            AutoUpdate.act(applicationContext, config, result.update)
            if (AutoUpdate.shouldInstallUnattended(config.autoInstallUpdates, result.update)) return@Thread
            runOnUiThread { if (!isFinishing && !isDestroyed && hasWindowFocus()) showUpdateOffer(result.update) }
        }.start()
    }

    /**
     * Ask whether to install [update], unless the user has said LATER to this build.
     * Idempotent: an offer already up is left alone, so resuming twice does not stack two.
     */
    private fun showUpdateOffer(update: ReleaseFeed.Update) {
        if (updateDialog?.isShowing == true || isFinishing || isDestroyed) return
        if (AutoUpdate.isDismissed(this, update.versionCode)) return
        updateDialog = android.app.AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage("Version ${update.versionName} is ready to install.")
            .setPositiveButton("Install") { _, _ -> installUpdate(update) }
            .setNegativeButton("Later") { _, _ -> dismissUpdateOffer(update) }
            .show()
    }

    /**
     * Download [update] and hand it to the installer, on a background thread --
     * [UpdateChecker.install] blocks on a sixty-megabyte download. The system's
     * prompt is what actually installs it; a failure (no install permission,
     * a signature mismatch) comes back as a sentence to show.
     */
    private fun installUpdate(update: ReleaseFeed.Update) {
        ai.jarvis.app.ui.ScreenNote.show(this, "Downloading ${update.versionName}…")
        Thread {
            val result = UpdateChecker(applicationContext).install(update)
            val message = when (result) {
                is UpdateChecker.Result.Handed ->
                    "Downloaded ${update.versionName}. Confirm the install prompt — " +
                        "if it does not appear, look in your notifications."
                is UpdateChecker.Result.Failed -> result.message
                // check() is never called here, so UpToDate/Offered cannot occur;
                // say nothing rather than invent a message for an impossible state.
                else -> ""
            }
            if (message.isNotEmpty()) {
                runOnUiThread { ai.jarvis.app.ui.ScreenNote.show(this, message) }
            }
        }.start()
    }

    /**
     * LATER: remember this build as unwanted and take the notification down too. The
     * next, higher-numbered build is above the mark and offers itself again -- see
     * [AutoUpdate.isDismissed].
     */
    private fun dismissUpdateOffer(update: ReleaseFeed.Update) {
        AutoUpdate.dismiss(this, update.versionCode)
        UpdateNotification.cancel(this)
    }

    /** The installed build number -- the only thing Android compares on install. */
    private fun appVersionCode(): Long =
        runCatching { packageManager.getPackageInfo(packageName, 0).longVersionCode }
            .getOrDefault(0L)

    companion object {
        /** A console page to open at, such as `/devices`: the settings screen's links (T-0689). */
        const val EXTRA_CONSOLE_PATH = "ai.jarvis.app.CONSOLE_PATH"

        /**
         * MainActivity is the launcher, so any app can send the extra: only a plain path
         * survives. The console URL ends in `/`, so even a stray one stays on its origin; this
         * keeps a query or a fragment from riding along.
         */
        private val CONSOLE_PATH = Regex("/[a-z0-9/_-]*")

        internal fun consolePathOf(intent: Intent?): String =
            intent?.getStringExtra(EXTRA_CONSOLE_PATH)?.takeIf { CONSOLE_PATH.matches(it) }.orEmpty()

        private const val REQ_NOTIFICATIONS = 4713
        private const val REQ_BACKGROUND_LOCATION = 4719

        /**
         * Spelled out rather than `Manifest.permission.POST_NOTIFICATIONS`,
         * which is API 33+. The string is stable, and writing it means no
         * version guard at the constant.
         */
        private const val POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"
    }
}
