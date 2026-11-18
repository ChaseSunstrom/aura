package ai.aura.app.automation.ui

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.automation.AutomationRuntime
import ai.aura.app.automation.policy.ActionTier
import ai.aura.app.automation.policy.PolicyStore
import ai.aura.app.automation.policy.UserPolicy
import ai.aura.app.config.JarvisConfig
import ai.aura.app.ui.ScreenNote
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.ui.v2.Body
import ai.aura.app.ui.v2.Hairline
import ai.aura.app.ui.v2.Headline
import ai.aura.app.ui.v2.HoldButton
import ai.aura.app.ui.v2.Label
import ai.aura.app.ui.v2.Projection
import ai.aura.app.ui.v2.RuleChip
import ai.aura.app.ui.v2.Screen
import ai.aura.app.ui.v2.ScreenState
import ai.aura.app.ui.v2.Stage
import ai.aura.app.ui.v2.Toggle
import ai.aura.app.ui.v2.Tone
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * WHAT JARVIS MAY DO WITHOUT ASKING.
 *
 * ## Why this screen exists
 *
 * [PolicyStore] has had `setPolicy`, `clearPolicy`, `all()` and
 * `clearAllPolicies()` since it was written, and their KDoc says what they are
 * for: *"Direct set from the settings screen"*, *"Every explicitly stored
 * answer, for the settings UI"*. Nothing outside the unit tests ever called
 * any of them. The store, its Tier-3 guard, its change listeners and the whole
 * `UserPolicy` vocabulary were built, documented and tested, and the user had
 * no way to write a single value into it.
 *
 * So the only way an action could ever leave [UserPolicy.ASK] was the consent
 * prompt's own remember-this control on a Tier-2 action — and the effect was
 * that Jarvis asked, every time, about everything it was allowed to ask about,
 * with no way to say "yes, always, this one is fine". Reported exactly that
 * way: *"I should be able to determine whether or not Jarvis can do certain
 * tasks or whatever without approval"*.
 *
 * ## What it can and cannot offer
 *
 * The three-way choice is the store's own vocabulary and nothing is invented
 * here:
 *
 *  * **ASK** — the default. The consent prompt decides.
 *  * **ALWAYS** — run it, no human in the loop. Offered only where the store
 *    would accept it, which is Tier 1 and Tier 2. [PolicyStore.setPolicy]
 *    refuses it for Tier 3 and [ai.aura.app.automation.policy.PolicyEngine]
 *    ignores such a value even if one were somehow stored, so this screen not
 *    offering it is the third of three independent guards, not the only one.
 *  * **NEVER** — a hard no, offered for everything. Nothing overrides it: not
 *    a server command, not a task, not a trigger.
 *
 * NEVER is available on every action deliberately. Tightening must always be
 * possible; only loosening is restricted.
 */
class ActionPolicyActivity : ComponentActivity() {

    private var store: PolicyStore? = null

    /** One action as the list draws it; null while the automation layer is not available. */
    private data class Entry(val id: String, val description: String, val tier: ActionTier, val current: UserPolicy)
    private var entries by mutableStateOf<List<Entry>?>(null)
    private var remoteApprovals by mutableStateOf(false)

    /** Redraws when something else writes the store — the consent prompt does. */
    private val onStoreChanged = { runOnUiThread { refresh() } }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        remoteApprovals = JarvisConfig(this).remoteApprovals
        store = try {
            // The same table the dispatcher uses, so this screen can never
            // offer ALWAYS for something the engine classifies differently.
            PolicyStore(applicationContext) { id -> tierOf(id) }
        } catch (t: Throwable) {
            Log.w(TAG, "the policy store could not be opened", t)
            null
        }
        // Drawn once and re-read from state: a tap on a chip must not throw the
        // list back to its top, which a fresh setContent per refresh would.
        setContent { Page() }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        store?.addChangeListener(onStoreChanged)
        refresh()
    }

    override fun onPause() {
        store?.removeChangeListener(onStoreChanged)
        super.onPause()
    }

    @Composable
    private fun Page() {
        Stage {
            Column(
                Modifier
                    .fillMaxSize()
                    .safeDrawingPadding()
                    .verticalScroll(rememberScrollState())
                    .padding(JarvisTokens.Space.SCREEN.dp),
                verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
            ) {
                Headline("What ${ActiveTheme.name} may do")
                Body("Which actions run without asking, which ask every time, and which are refused.", dim = true)
                Body(
                    "Everything starts at ASK, which means the tier decides: ${ActiveTheme.name} just does " +
                        "the harmless things, tells you about the middling ones, and stops for " +
                        "your approval before anything it cannot take back. Change one to ALWAYS " +
                        "and it stops asking. Change one to NEVER and nothing can run it — not a " +
                        "command from the server, not a task, not a trigger. The most dangerous " +
                        "actions cannot be set to ALWAYS at all; they are marked below.",
                    dim = true,
                )
                Toggle("Answer these prompts from the console too", remoteApprovals, { on ->
                    remoteApprovals = on
                    JarvisConfig(this@ActionPolicyActivity).remoteApprovals = on
                })
                Body(
                    "With this on, a prompt waiting on this phone also shows in the console's " +
                        "approvals, so you can answer it there while the phone is locked.",
                    dim = true,
                )

                val list = entries
                when {
                    list == null -> ScreenState(Screen.ERROR, "The automation layer is not available on this phone.")
                    list.isEmpty() -> ScreenState(Screen.EMPTY, "No actions are registered on this phone.")
                    else -> {
                        Label("Actions")
                        // One projection, a hairline between actions — not a panel each.
                        Projection(Modifier.fillMaxWidth()) {
                            list.forEachIndexed { index, entry ->
                                if (index > 0) Hairline()
                                Row(entry)
                            }
                        }
                    }
                }

                // Every remembered answer goes, so held rather than tapped.
                HoldButton("Back to ask for everything", onConfirm = {
                    store?.clearAllPolicies()
                    ScreenNote.show(this@ActionPolicyActivity, "Every action is back to ASK.")
                    refresh()
                })
            }
        }
    }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Row(entry: Entry) {
        Label(entry.id)
        Body(entry.description.ifBlank { "No description." }, dim = true)
        Body(tierBlurb(entry.tier), dim = true, tone = if (entry.tier == ActionTier.CONFIRM) Tone.WARN else null)

        // ALWAYS is dropped from the list rather than shown-and-refused: an
        // option that is offered and then silently ignored is worse than one
        // that was never there, and the sentence above says why it is missing.
        val allowAlways = entry.tier != ActionTier.CONFIRM
        val choices = buildList {
            add(UserPolicy.ASK)
            if (allowAlways) add(UserPolicy.ALLOW_ALWAYS)
            add(UserPolicy.NEVER)
        }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
            verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
        ) {
            for (choice in choices) {
                RuleChip(
                    wordsOf(choice),
                    selected = choice == entry.current,
                    onClick = { applyPolicy(entry.id, choice, entry.tier) },
                    kind = kindOf(choice),
                    tone = if (choice == UserPolicy.NEVER) Tone.DANGER else Tone.ACCENT,
                )
            }
        }
    }

    // --- the list -----------------------------------------------------------

    private fun tierOf(actionId: String): ActionTier? =
        runCatching { AutomationRuntime.ensure(applicationContext).registry[actionId]?.tier }
            .getOrNull()

    private fun refresh() {
        val registry = runCatching { AutomationRuntime.ensure(applicationContext).registry }
            .onFailure { Log.w(TAG, "the action table could not be built", it) }
            .getOrNull()
        val live = store
        if (registry == null || live == null) {
            entries = null
            return
        }
        val stored = live.all()
        entries = registry.ids().sorted().mapNotNull { id ->
            registry[id]?.let { Entry(id, it.description, it.tier, stored[id] ?: UserPolicy.ASK) }
        }
    }

    private fun applyPolicy(actionId: String, picked: UserPolicy, tier: ActionTier) {
        val live = store ?: return
        if (picked == UserPolicy.ASK) {
            live.clearPolicy(actionId)
            refresh()
            return
        }
        // The store's own answer, not this screen's guess at it. If it refuses,
        // say so — the alternative is a control that appears to have taken.
        if (!live.setPolicy(actionId, picked, tier)) {
            ScreenNote.show(this, "“$actionId” always needs your approval; that cannot be turned off.")
        }
        refresh()
    }

    private fun kindOf(policy: UserPolicy): String = when (policy) {
        UserPolicy.ASK -> "Ask"
        UserPolicy.ALLOW_ALWAYS -> "Always"
        UserPolicy.NEVER -> "Never"
    }

    private fun wordsOf(policy: UserPolicy): String = when (policy) {
        UserPolicy.ASK -> "the tier decides"
        UserPolicy.ALLOW_ALWAYS -> "without asking"
        UserPolicy.NEVER -> "refuse it"
    }

    private fun tierBlurb(tier: ActionTier): String = when (tier) {
        ActionTier.AUTO -> "Tier 1 · harmless; runs silently unless you say otherwise"
        ActionTier.NOTIFY -> "Tier 2 · runs and tells you afterwards"
        ActionTier.CONFIRM -> "Tier 3 · always stops for your approval, and cannot be set to ALWAYS"
    }

    private companion object {
        const val TAG = "JarvisActionPolicy"
    }
}
