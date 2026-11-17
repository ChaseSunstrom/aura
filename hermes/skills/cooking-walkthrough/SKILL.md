---
name: cooking-walkthrough
description: "Cook a recipe, walk me through it step by step: one step at a time, each timed step a house timer."
version: 0.1.0
---

# Walking Sir through a recipe (or any step-by-step job)

"Walk me through the carbonara", "let's cook this", "next", "what's next?", "say that again",
"how long left on the pasta?". His hands are busy and he is listening, not reading: one step
at a time, said in a sentence or two.

## Start

1. **The recipe.** Use the one he gave you (pasted, a link: `web_extract`, a file: read it). If he
   named a dish, find one (`web_search`) and say in one line what you picked and how long it
   takes. Strip it to ingredients and numbered steps; keep every quantity and temperature exact.
2. **Ingredients first**, said once, short: "You need: 200 g spaghetti, 3 eggs, ...". Ask if he
   has everything only when one is unusual.
3. **Put the step on screen**: `hud_show {kind: text, id: "walkthrough", title: "<dish> — step 1 of N",
   payload: {markdown: "<the step, then the ingredients it uses>"}}`. Every later step calls
   `hud_show` with the same id, so the panel is replaced, never stacked.

## Each step

- Say the step. Nothing about the step after it unless the two overlap ("while that boils, ...").
- **A step with a time** ("simmer 10 minutes", "rest 5 minutes"): start it in the house, named for
  what it is: `house_timer {action: start, duration: "10m", label: "sauce"}`. Say "Timer set for the
  sauce, ten minutes." The house rings him wherever he is and the timer survives a restart. Two
  steps running at once get two labels. "Pause the sauce": `house_timer {action: pause, label}`.
- **"How long left?"**: `house_timer {action: list}` and read `remaining_spoken` for the one he
  means. Never work it out from when you set it.
- **"Next"**: the next step, panel updated. **"Back" / "say that again"**: the same step again, word
  for word; a timer already running for it keeps running, never start it twice. **"Skip"**: say
  what the skipped step was for, in one line, and move on; its timer stops only if he says so.
- A step that needs judgement ("until golden") gets the sign to look for, not a time.

## End

"That's it, enjoy." Then `hud_dismiss {id: "walkthrough"}`. Cancel a timer of this recipe still
running (`house_timer {action: cancel, label}`) only if he says he is done with it. He stops
halfway ("forget it"): dismiss the panel, leave the timers alone unless he says to cancel them.

The same flow serves any walkthrough with steps: a repair, a set-up, a workout. Only the timer
labels change.
