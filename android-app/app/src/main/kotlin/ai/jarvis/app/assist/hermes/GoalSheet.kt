package ai.jarvis.app.assist.hermes

import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.app.AlertDialog
import android.text.InputType
import android.view.ViewGroup
import android.widget.LinearLayout

/**
 * "Set goal": one line of text and the four things it can become
 * (`docs/hermes-hud-protocol.md` § Goal commands):
 *
 *  * SET — `goal <text>`: the standing goal, first turn starts.
 *  * DRAFT — `jgoal draft <text>`: the jarvis-goals plugin expands a
 *    one-liner into a completion contract, then sets it.
 *  * SUBGOAL — `jgoal subgoal <text>`: an extra acceptance criterion
 *    mid-loop (`/subgoal` is a reserved built-in name, so it lives under `jgoal`).
 *  * GATE — `jgoal gate add <shell command>`: must exit 0 before "done".
 *
 * The `jgoal` ones are plugin commands: the client prefixes their arg with
 * `@<session_id> ` on the wire ([HermesDispatch.argFor]) and they fail with
 * 4018 on a backend without the plugin, which the dispatcher turns into a
 * sentence on the reply line.
 */
object GoalSheet {

    fun show(activity: Activity, dispatch: (String, String) -> Unit) {
        val field = JarvisUi.field(activity, "keep working on… / a command for a gate", "").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            isSingleLine = false
            minLines = 2
        }
        val pad = JarvisUi.dp(activity, JarvisUi.Space.SCREEN)
        val body = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, 0, pad, 0)
            addView(field, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            addView(JarvisUi.hint(activity, "Set makes it the goal. Draft asks the judge to write the contract first. Subgoal adds a criterion to the running goal. Gate adds a shell command that must pass."))
        }
        val actions = listOf(
            "Set" to "goal",
            "Draft" to "jgoal draft",
            "Subgoal" to "jgoal subgoal",
            "Gate" to "jgoal gate add",
        )
        AlertDialog.Builder(activity)
            .setTitle("Set a goal")
            .setView(body)
            .setItems(actions.map { it.first }.toTypedArray()) { dialog, which ->
                val text = field.text.toString().trim()
                if (text.isNotEmpty()) {
                    val (name, prefix) = actions[which].second.split(" ", limit = 2).let { parts ->
                        parts[0] to parts.getOrElse(1) { "" }
                    }
                    dispatch(name, if (prefix.isEmpty()) text else "$prefix $text")
                }
                dialog.dismiss()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
