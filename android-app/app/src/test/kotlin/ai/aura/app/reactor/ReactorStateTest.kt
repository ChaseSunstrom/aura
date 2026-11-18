package ai.aura.app.reactor

import ai.aura.app.ui.theme.JarvisTokens
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The phone's half of the reactor contract.
 *
 * `tests/contracts/reactor.json` carries the state machine — eleven inputs, ten
 * rules and a truth table — and the browser's `reactorState.ts` and the console
 * agent's `reactor.py` run the same cases against their own copies. This runs
 * them against [derive], so an approval waiting on the phone cannot show as
 * "thinking" while the console shows "needs you". That is not hypothetical: the
 * old five-state orb mapped events to states in a different file on every
 * surface, and the notification orb and the wake-word orb disagreed.
 *
 * Pure JVM on purpose — no Robolectric, no framework. [derive] has no
 * `android.*` import, which is what lets the table be tested in milliseconds and
 * read by the Python mirror as text.
 *
 * Float comparisons carry an exact delta of zero where the value is copied
 * rather than computed: a geometry read out of the contract has to *be* the
 * contract's, not be near it.
 */
class ReactorStateTest {

    private val contract: JSONObject by lazy {
        // A unit test runs with its working directory at `android-app/app`,
        // which is what `PanelTypesContractTest` relies on for its contract too.
        val file = File("../../tests/contracts/reactor.json")
        assertTrue("contract not found at ${file.absolutePath}", file.isFile)
        JSONObject(file.readText())
    }

    private fun inputsFrom(json: JSONObject): ReactorInputs {
        var i = ReactorInputs()
        for (key in json.keys()) {
            i = when (key) {
                "kill" -> i.copy(kill = json.getBoolean(key))
                "linkUp" -> i.copy(linkUp = json.getBoolean(key))
                "adapterError" -> i.copy(adapterError = json.getBoolean(key))
                "turnRunning" -> i.copy(turnRunning = json.getBoolean(key))
                "errorEvent" -> i.copy(errorEvent = json.getBoolean(key))
                "attention" -> i.copy(attention = json.getBoolean(key))
                "warming" -> i.copy(warming = json.getBoolean(key))
                "speaking" -> i.copy(speaking = json.getBoolean(key))
                "listening" -> i.copy(listening = json.getBoolean(key))
                "toolsRunning" -> i.copy(toolsRunning = json.getInt(key))
                "goalActive" -> i.copy(goalActive = json.getBoolean(key))
                else -> error("the contract names an input this file does not have: $key")
            }
        }
        return i
    }

    @Test
    fun `every case of the contract's truth table`() {
        val cases = contract.getJSONObject("derivation").getJSONArray("cases")
        assertTrue("the contract's truth table has shrunk", cases.length() >= 20)
        for (n in 0 until cases.length()) {
            val case = cases.getJSONObject(n)
            val name = case.getString("name")
            val got = derive(inputsFrom(case.getJSONObject("inputs")))
            assertEquals(name, case.getString("state"), got.state.key)
            assertEquals(name, case.getBoolean("goal"), got.goal)
        }
    }

    @Test
    fun `the resting inputs are the contract's`() {
        val rest = contract.getJSONObject("derivation").getJSONObject("rest")
        val i = ReactorInputs()
        assertEquals(rest.getBoolean("kill"), i.kill)
        assertEquals(rest.getBoolean("linkUp"), i.linkUp)
        assertEquals(rest.getBoolean("adapterError"), i.adapterError)
        assertEquals(rest.getBoolean("turnRunning"), i.turnRunning)
        assertEquals(rest.getBoolean("errorEvent"), i.errorEvent)
        assertEquals(rest.getBoolean("attention"), i.attention)
        assertEquals(rest.getBoolean("warming"), i.warming)
        assertEquals(rest.getBoolean("speaking"), i.speaking)
        assertEquals(rest.getBoolean("listening"), i.listening)
        assertEquals(rest.getInt("toolsRunning").toLong(), i.toolsRunning.toLong())
        assertEquals(rest.getBoolean("goalActive"), i.goalActive)
    }

    @Test
    fun `the contract names exactly the inputs this file has`() {
        // A new input on one surface and not the others is how a precedence
        // quietly stops being shared.
        val named = contract.getJSONObject("derivation").getJSONObject("inputs").keys().asSequence().toSet()
        val here = setOf(
            "kill", "linkUp", "adapterError", "turnRunning", "errorEvent",
            "attention", "warming", "speaking", "listening", "toolsRunning", "goalActive",
        )
        assertEquals(named.sorted(), here.sorted())
    }

    @Test
    fun `the precedence list is the contract's, and goal is not in it`() {
        val precedence = contract.getJSONArray("precedence")
        assertEquals(precedence.length().toLong(), ReactorState.PRECEDENCE.size.toLong())
        for (n in 0 until precedence.length()) {
            assertEquals(precedence.getString(n), ReactorState.PRECEDENCE[n].key)
        }
        // The overlay cannot win a comparison it is never entered into.
        assertTrue("goal is in the precedence list", ReactorState.GOAL !in ReactorState.PRECEDENCE)
    }

    /** Inputs that alone produce each state, for the pairwise ordering below. */
    private val alone: Map<ReactorState, ReactorInputs> = mapOf(
        ReactorState.KILL to ReactorInputs(kill = true),
        ReactorState.OFFLINE to ReactorInputs(linkUp = false),
        ReactorState.ALERT to ReactorInputs(errorEvent = true),
        ReactorState.ATTENTION to ReactorInputs(attention = true),
        ReactorState.WARMING to ReactorInputs(warming = true),
        ReactorState.SPEAKING to ReactorInputs(speaking = true),
        ReactorState.LISTENING to ReactorInputs(listening = true),
        ReactorState.TOOL to ReactorInputs(toolsRunning = 1),
        ReactorState.THINKING to ReactorInputs(turnRunning = true),
        ReactorState.IDLE to ReactorInputs(),
    )

    private fun merge(a: ReactorInputs, b: ReactorInputs) = ReactorInputs(
        kill = a.kill || b.kill,
        linkUp = a.linkUp && b.linkUp,
        adapterError = a.adapterError || b.adapterError,
        turnRunning = a.turnRunning || b.turnRunning,
        errorEvent = a.errorEvent || b.errorEvent,
        attention = a.attention || b.attention,
        warming = a.warming || b.warming,
        speaking = a.speaking || b.speaking,
        listening = a.listening || b.listening,
        toolsRunning = maxOf(a.toolsRunning, b.toolsRunning),
        goalActive = a.goalActive || b.goalActive,
    )

    @Test
    fun `each state has a trigger of its own`() {
        for ((state, inputs) in alone) {
            assertEquals(state.name, state, derive(inputs).state)
        }
    }

    @Test
    fun `of any two states the higher one wins, in both orders`() {
        // Every pair, not only the neighbours: the ordering is a total order or
        // it is nothing, and a hand-written `when` chain can be neither.
        for ((first, a) in alone) {
            for ((second, b) in alone) {
                if (first == second) continue
                val rankA = ReactorState.PRECEDENCE.indexOf(first)
                val rankB = ReactorState.PRECEDENCE.indexOf(second)
                val expected = if (rankA < rankB) first else second
                val got = derive(merge(a, b)).state
                assertEquals("$first vs $second", expected, got)
            }
        }
    }

    @Test
    fun `the goal rides every state but the two dark ones`() {
        for ((state, inputs) in alone) {
            val got = derive(inputs.copy(goalActive = true))
            assertEquals(state.name, !state.still, got.goal)
            assertNotEquals("goal is an overlay, never a state", ReactorState.GOAL, got.state)
        }
    }

    @Test
    fun `an adapter error is an alert mid-turn and offline at rest`() {
        // The judgement §10 asks for: mid-turn something was lost and the person
        // needs to know; at rest nothing was, and the honest reading is the link.
        val midTurn = derive(ReactorInputs(adapterError = true, turnRunning = true)).state
        assertEquals(ReactorState.ALERT, midTurn)
        assertEquals(ReactorState.OFFLINE, derive(ReactorInputs(adapterError = true)).state)
    }

    @Test
    fun `every state carries the contract's word, and no two share one`() {
        val states = contract.getJSONObject("states")
        for (state in ReactorState.entries) {
            assertEquals(state.key, states.getJSONObject(state.key).getString("word"), state.word)
        }
        // The word is what the tray, the notification and the console strip all
        // show, so two states sharing one would be indistinguishable everywhere
        // but the screen.
        val words = ReactorState.entries.map { it.word }.toSet()
        assertEquals(ReactorState.entries.size.toLong(), words.size.toLong())
    }

    @Test
    fun `the ignition lasts motion dur enter`() {
        // The contract names a token rather than a number, and the token is 900 ms.
        assertEquals("motion.dur.enter", contract.getJSONObject("geometry").getString("ignite_ms"))
        assertEquals(900L, JarvisTokens.Motion.Dur.ENTER.toLong())
    }
}

/**
 * The two moments the reactor is felt as well as seen. Every other transition is
 * silent, because a phone that buzzes on "thinking" is a phone that gets muted,
 * and then the approval buzz is lost with the rest.
 */
class ReactorHapticsTest {

    private class Recorder : ReactorHaptics.Buzzer {
        val buzzes = mutableListOf<Int>()
        override fun buzz(effect: Int) {
            buzzes += effect
        }
    }

    @Test
    fun `only attention, alert and the kill switch are felt`() {
        for (state in ReactorState.entries) {
            val recorder = Recorder()
            ReactorHaptics.onTransition(ReactorState.IDLE, state, recorder)
            val expected = when (state) {
                ReactorState.ATTENTION, ReactorState.ALERT -> 1L
                // Twice, so it cannot be mistaken for an alert.
                ReactorState.KILL -> 2L
                else -> 0L
            }
            assertEquals(state.name, expected, recorder.buzzes.size.toLong())
        }
    }

    // T-0853: TalkBack cannot see the goal's satellite, so the core's description says it.
    @Test
    fun `a screen reader hears the goal after the state's word, once`() {
        assertEquals("Thinking, on a goal", Derived(ReactorState.THINKING, goal = true).spoken)
        assertEquals("Thinking", Derived(ReactorState.THINKING, goal = false).spoken)
        assertEquals("On a goal", Derived(ReactorState.GOAL, goal = true).spoken)
    }

    @Test
    fun `staying in a state does not buzz again`() {
        val recorder = Recorder()
        ReactorHaptics.onTransition(ReactorState.ALERT, ReactorState.ALERT, recorder)
        assertTrue("a re-announced state buzzed", recorder.buzzes.isEmpty())
    }

    @Test
    fun `no vibrator is a no-op rather than a crash`() {
        // Haptics are never load-bearing: a car, a tablet or a muted phone has to
        // get the state and only miss the buzz.
        ReactorHaptics.onTransition(ReactorState.IDLE, ReactorState.ALERT, null)
    }
}
