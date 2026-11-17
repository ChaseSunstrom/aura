package ai.jarvis.app.automation.ui

import ai.jarvis.app.automation.AutomationRuntime
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.automation.policy.PolicyStore
import ai.jarvis.app.automation.tasks.TaskAdmission
import ai.jarvis.app.automation.tasks.TaskDefinition
import ai.jarvis.app.automation.tasks.TaskRunResult
import ai.jarvis.app.ui.ListActivityScaffold
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import ai.jarvis.app.ui.v2.ActionButton
import ai.jarvis.app.ui.v2.Body
import ai.jarvis.app.ui.v2.Headline
import ai.jarvis.app.ui.v2.Label
import ai.jarvis.app.ui.v2.Projection
import ai.jarvis.app.ui.v2.Screen
import ai.jarvis.app.ui.v2.ScreenState
import ai.jarvis.app.ui.v2.Tone
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import ai.jarvis.app.ui.v2.Toggle

/**
 * The automations screen — the list of tasks this phone will run by itself.
 *
 * `AndroidManifest.xml` has declared this activity for a long time and
 * `ai.jarvis.app.ui.JarvisScreens` has launched it by name, but no class with
 * this name existed, so both entry points (the home screen's AUTOMATIONS button
 * and Settings') resolved to a toast. That is also why nothing could ever put a
 * task in the store: `TaskStore.upsert` has no other caller in shipping code.
 *
 * Read-only, deliberately, for a first cut. There is no rule editor here. What
 * it does provide is the two things the automation layer cannot work without
 * and had no route to:
 *
 *  * **Consent.** `TaskStore.setEnabledByUser` is the only path that may set a
 *    task's consent flag, and it exists solely for this screen. A pushed task
 *    containing a CONFIRM-tier action arrives switched off and stays off until
 *    a human turns it on *here*, having read the reason underneath it.
 *  * **Visibility.** Which tasks exist, which are running, and whether the
 *    master switch or the panic flag is holding everything down.
 *
 * Two levels, following `ai.jarvis.app.ui.CrashLogActivity`: a list, and one
 * task. Back steps out of the detail before it leaves the screen.
 */
class AutomationsActivity : ListActivityScaffold() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val main = Handler(Looper.getMainLooper())

    private var runtime: AutomationRuntime.Runtime? = null
    private var policy: PolicyStore? = null

    /** Id of the task whose detail is open, or null for the list. */
    private var showing: String? = null

    /** Set by RUN NOW / the run listener, shown under the header. */
    private var notice: String? = null

    /** Both fire from background coroutines; both re-render on the main thread. */
    private val taskListener: (List<TaskDefinition>) -> Unit = { main.post { refresh() } }
    private val runListener: (TaskRunResult) -> Unit = { result ->
        main.post {
            notice = "${result.taskName}: ${result.status.wire}" +
                (result.message?.let { " — $it" } ?: "")
            refresh()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Building the runtime is what fills AutomationBridge.dispatcher and
        // ActionEnv; it is safe to call from anywhere and idempotent. Wrapped
        // because a screen that cannot show the task list must still open and
        // say so rather than crash the app from a nav button.
        runtime = try {
            AutomationRuntime.ensure(applicationContext)
        } catch (t: Throwable) {
            Log.w(TAG, "the automation runtime could not be built", t)
            null
        }
        policy = try {
            PolicyStore(applicationContext)
        } catch (t: Throwable) {
            Log.w(TAG, "the policy store could not be opened", t)
            null
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        // ensure() constructs the TriggerManager but never starts it — triggers
        // are only live inside the running foreground service. Without this the
        // "N triggers active" line reads 0 on a phone whose automations are
        // simply not running yet, which reads as "none configured".
        runCatching { JarvisAutomationService.ensureRunning(this, "automations-screen") }
            .onFailure { Log.w(TAG, "could not start the automation service", it) }
        runtime?.let {
            it.tasks.addListener(taskListener)
            it.engine.addRunListener(runListener)
        }
        refresh()
    }

    override fun onPause() {
        runtime?.let {
            it.tasks.removeListener(taskListener)
            it.engine.removeRunListener(runListener)
        }
        super.onPause()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /**
     * Back steps out of a task before it leaves the screen.
     *
     * The classic callback rather than an `OnBackInvokedCallback`: the manifest
     * sets `enableOnBackInvokedCallback="false"` for the whole app.
     */
    @Suppress("DEPRECATION", "MissingSuperCall")
    override fun onBackPressed() {
        if (showing != null) {
            showing = null
            refresh()
            return
        }
        super.onBackPressed()
    }

    // --- rendering ----------------------------------------------------------

    private fun refresh() {
        val rt = runtime
        if (rt == null) {
            show { Unavailable() }
            return
        }
        scope.launch {
            val tasks = try {
                rt.tasks.all()
            } catch (t: Throwable) {
                Log.w(TAG, "could not read the task store", t)
                emptyList()
            }
            val admissions = HashMap<String, TaskAdmission>()
            for (task in tasks) {
                runCatching { rt.tasks.admissionFor(task.id) }.getOrNull()
                    ?.let { admissions[task.id] = it }
            }
            val open = showing?.let { id -> tasks.firstOrNull { it.id == id } }
            show {
                if (open != null) Detail(open, admissions[open.id]) else TaskList(tasks, admissions)
            }
        }
    }

    @Composable
    private fun Title() {
        Headline("Phone tasks")
        Body("The tasks this phone runs by itself, and the consent each one has.", dim = true)
    }

    @Composable
    private fun Unavailable() {
        Title()
        ScreenState(
            Screen.ERROR,
            "The automation runtime could not be started on this device. Nothing " +
                "will run automatically until it can; the crash log may say why.",
        )
    }

    @Composable
    private fun TaskList(tasks: List<TaskDefinition>, admissions: Map<String, TaskAdmission>) {
        val rt = runtime
        Title()

        val store = policy
        val live = store?.automationLive ?: false
        val state = when {
            store == null -> "policy unavailable"
            store.panic -> "PANIC — everything is stopped"
            !store.automationEnabled -> "paused"
            else -> "live"
        }
        val running = rt?.engine?.runningTaskIds?.size ?: 0
        val activeTriggers = rt?.triggers?.activeIds?.size ?: 0
        Body(
            "${tasks.size} task(s), ${tasks.count { it.isRunnable() }} runnable, " +
                "$running running now. $activeTriggers trigger(s) active — that is 0 " +
                "whenever the automation service is not running, which is not the same " +
                "as no triggers configured. Automation is $state.",
            dim = true,
            tone = if (store?.panic == true) Tone.DANGER else null,
        )

        notice?.let { Body(it) }

        if (store != null) {
            Buttons {
                if (!store.panic) {
                    ActionButton(if (live) "Pause automations" else "Resume automations", onClick = {
                        store.automationEnabled = !store.automationEnabled
                        runCatching {
                            JarvisAutomationService.ensureRunning(this@AutomationsActivity, "automations-screen")
                        }
                        refresh()
                    })
                }
                // The kill switch, which nothing could set.
                //
                // `panic` outranks the master switch and every remembered "always
                // allow": PolicyEngine returns DENY on it before it looks at anything
                // else, the boot receiver refuses to restart triggers under it, and
                // this very screen already knew how to render "PANIC — everything is
                // stopped". Four readers, no writer. The state was unreachable, and so
                // was the way out of it — which is why CLEAR is on the same button
                // rather than somewhere else. A tap, not a hold: stopping must be fast.
                ActionButton(
                    if (store.panic) "Clear panic" else "Panic",
                    onClick = { togglePanic(store) },
                    tone = Tone.DANGER,
                )
            }
        }

        if (tasks.isEmpty()) {
            ScreenState(
                Screen.EMPTY,
                "No automations yet. Tasks arrive from the house over the device channel — its " +
                    "import_tasks action, which you confirm on this phone. One containing " +
                    "an action that needs confirming arrives switched off, and this is " +
                    "where you turn it on.",
            )
        } else {
            Label("Tasks")
            Rows(tasks, { task -> showing = task.id; refresh() }) { task -> TaskRow(task, admissions[task.id]) }
        }
    }

    @Composable
    private fun TaskRow(task: TaskDefinition, admission: TaskAdmission?) {
        val isRunning = runtime?.engine?.runningTaskIds?.contains(task.id) == true
        Toggle(task.name + if (isRunning) "  · RUNNING" else "", task.enabled, { toggleEnabled(task) })
        task.description?.takeIf { it.isNotBlank() }?.let { Body(it, dim = true) }
        Body(
            "${task.source.name.lowercase()} · " +
                "on ${task.triggerTypes().joinToString(", ").ifEmpty { "nothing" }} · " +
                "does ${task.actionIds().joinToString(", ").ifEmpty { "nothing" }}",
            dim = true,
        )
        // Why it is off. Shown verbatim so the user is turning it on with
        // the reason in front of them, not despite it.
        val heldOff = admission?.takeIf { it.needsUserEnablement }?.reason.orEmpty()
        if (heldOff.isNotBlank()) Body(heldOff, tone = Tone.WARN)
    }

    @Composable
    private fun Detail(task: TaskDefinition, admission: TaskAdmission?) {
        Headline(task.name)
        val heldOff = admission?.takeIf { it.needsUserEnablement }?.reason.orEmpty()
        val isRunning = runtime?.engine?.runningTaskIds?.contains(task.id) == true
        Block(
            "State",
            buildString {
                append("enabled:       ${task.enabled}\n")
                append("by the user:   ${task.enabledByUser}\n")
                append("source:        ${task.source.name}\n")
                append("mode:          ${task.mode.name}\n")
                append("runnable:      ${task.isRunnable()}\n")
                append("running now:   $isRunning")
                if (heldOff.isNotBlank()) append("\nheld off:      $heldOff")
            },
        )
        Block(
            "Triggers",
            task.triggers.joinToString("\n") { "${it.type}  ${it.params}" }
                .ifEmpty { "(none — this task can only be run by hand)" },
        )
        if (task.conditions.isNotEmpty()) Block("Conditions", task.conditions.joinToString("\n") { it.toString() })
        Block(
            "Steps",
            task.steps.mapIndexed { i, step ->
                val what = step.action ?: step.type.name.lowercase()
                "${i + 1}. ${step.label ?: what}"
            }.joinToString("\n").ifEmpty { "(none)" },
        )
        Buttons {
            ActionButton(if (task.enabled) "Switch off" else "Switch on", onClick = { toggleEnabled(task) })
            // TaskEngine.runNow returns false without running when the task is
            // switched off — force only skips the CONDITIONS, never the enabled
            // check — so offering RUN NOW there would be a button that does
            // nothing.
            if (task.enabled) {
                ActionButton("Run now", onClick = { runNow(task) })
                ActionButton("Cancel run", onClick = {
                    val cancelled = runtime?.engine?.cancel(task.id) ?: false
                    toast(if (cancelled) "Cancelled" else "That task is not running")
                })
            }
            ActionButton("Back to list", onClick = {
                showing = null
                refresh()
            })
        }
    }

    /** A named block of fixed facts: its label, then the lines in one projection. */
    @Composable
    private fun Block(name: String, text: String) {
        Label(name)
        Projection(Modifier.fillMaxWidth()) { Body(text) }
    }

    // --- actions ------------------------------------------------------------

    private fun togglePanic(store: PolicyStore) {
        val turningOn = !store.panic
        JarvisAutomationService.panic(this, turningOn)
        notice = if (turningOn) {
            "Panic is on. Nothing runs — no command from the server, no " +
                "trigger, no task — until you clear it. Triggers are " +
                "unregistered too, so the phone is not watching either."
        } else {
            "Panic cleared."
        }
        refresh()
    }

    /**
     * The user turned a task on or off.
     *
     * `setEnabledByUser` is the only path in the whole app that may set the
     * consent flag, and nothing on the command path can reach it.
     */
    private fun toggleEnabled(task: TaskDefinition) {
        val rt = runtime ?: return
        scope.launch {
            val updated = runCatching { rt.tasks.setEnabledByUser(task.id, !task.enabled) }
                .onFailure { Log.w(TAG, "could not switch ${task.id}", it) }
                .getOrNull()
            notice = when {
                updated == null -> "Could not change ${task.name}."
                updated.enabled -> "${updated.name} is on."
                else -> "${updated.name} is off."
            }
            runCatching { rt.engine.onTasksChanged() }
            refresh()
        }
    }

    private fun runNow(task: TaskDefinition) {
        val rt = runtime ?: return
        scope.launch {
            // dataTrusted = true: this is a local tap on this phone. force is
            // never passed — a task's own conditions are part of what the user
            // approved, so "run it anyway" is a separate, deliberate thing.
            val started = runCatching { rt.engine.runNow(task.id, dataTrusted = true) }
                .onFailure { Log.w(TAG, "could not run ${task.id}", it) }
                .getOrDefault(false)
            if (!started) {
                notice = "${task.name} did not start: it is switched off, its conditions " +
                    "are not met, or automation is paused."
                refresh()
            }
        }
    }

    private companion object {
        private const val TAG = "JarvisAutomationsUi"
    }
}
