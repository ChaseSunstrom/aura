package ai.jarvis.app.assist.hermes

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.text.InputType
import android.util.Log
import android.view.WindowManager
import android.widget.LinearLayout

/**
 * A `clarify.request`, put to the person: the question, a button per offered
 * option, or a line to type when there are none. The answer goes back as
 * `clarify.respond`.
 *
 * A plain dialog rather than a screen of its own, because a clarifying
 * question is one beat inside a turn — the reply is still coming. Over the
 * wake overlay (a Service context) it is drawn as an application overlay
 * window, which needs the same grant the orb already has.
 */
object ClarifyPrompt {

    private const val TAG = "JarvisClarify"

    /** Returns false when no dialog could be shown here. */
    fun show(context: Context, question: String, options: List<String>, answer: (String) -> Unit): Boolean {
        var answered = false
        fun once(text: String) {
            if (answered) return
            answered = true
            answer(text)
        }
        return try {
            val builder = AlertDialog.Builder(context)
                .setTitle("${ActiveTheme.name} asks")
                .setMessage(question)
                .setCancelable(true)
            if (options.isNotEmpty()) {
                builder.setItems(options.toTypedArray()) { dialog, which ->
                    once(options[which])
                    dialog.dismiss()
                }
            } else {
                val field = JarvisUi.field(context, "Your answer", "").apply {
                    inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                }
                val pad = JarvisUi.dp(context, JarvisUi.Space.SCREEN)
                builder.setView(LinearLayout(context).apply {
                    setPadding(pad, 0, pad, 0)
                    addView(field, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
                })
                builder.setPositiveButton("Answer") { dialog, _ ->
                    once(field.text.toString().trim())
                    dialog.dismiss()
                }
            }
            builder.setNegativeButton("Later", null)
            val dialog = builder.create()
            if (context !is Activity) {
                dialog.window?.setType(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY)
            }
            dialog.show()
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not show the clarifying question", t)
            false
        }
    }
}
