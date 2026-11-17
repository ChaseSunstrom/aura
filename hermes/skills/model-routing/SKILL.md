---
name: model-routing
description: "Switch model, load the 35B, which model answers what: the routing table, why a swap is expensive, and how to check the table before llama_swap_load."
version: 0.1.0
---

# Which model answers what

The AI machine has two 3090s (48 GB, NVLink) behind llama-swap. It serves **one model at a time**:
`qwen3.8-27b` (256k context, the default) starts with 92 % of both cards, so nothing co-resides with it.
Every switch is a cold start of one to five minutes that evicts the resident model **for everyone**.

## The routing table

`llama_swap_models` returns it. `routing` maps each kind of work — chat, reasoning, coding, vision,
auxiliary, delegation, embedding — to a model id, and `needs_swap` lists the jobs whose model is not the
one loaded. On this deployment every row resolves to `qwen3.8-27b` and `needs_swap` is empty: the
default model does the reasoning, the coding, the vision and the side calls, and the delegated subagents
inherit it. That is the configured position, not an accident — a route that swaps mid-conversation makes
JARVIS slower, not smarter.

The table comes from the settings schema (`routing.*`), so if it says a job wants another model, that is
the owner's decision and you should follow it.

## Before you switch

1. `llama_swap_models` — what exists, what is resident, what the table says.
2. Say what the swap costs ("about two minutes, and it takes the model away from anything else running")
   and what you will do after.
3. `llama_swap_load` with an id the tool listed. It needs approval; that gate is not yours to waive.
4. Say how long it took when it returns.

Switch only when there is a reason the user would recognise: they asked for another model, or a job
genuinely needs one the routing table names. **Image generation is the standing example** — ComfyUI needs
VRAM the 27b is holding, so the runbook's sequence applies there.

Never call `hermes model` or change `model.default` to answer "which model is running" — that changes the
default for every future session. `llama_swap_status` answers the question.
