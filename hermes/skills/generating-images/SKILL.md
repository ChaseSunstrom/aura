---
name: generating-images
description: "Generate an image, make a picture, draw: image_generate or comfyui_submit on the house's own ComfyUI, submitted first and the model swapped only when the tool refuses for VRAM, shown on the HUD, and never quietly replaced by a photo from the web."
version: 0.1.0
---

# Generating an image

"Make me an image of…", "draw…", "generate a picture of…", "what would X look like" — all of
it is the house's own ComfyUI. Nothing goes to a cloud image service, ever. ComfyUI is not
running right now, so image generation is off: say so rather than reaching for another source.

## The two tools

- **`image_generate`** — the ordinary way. Text prompt in, a saved image out. It checks the
  VRAM and the installed model first and refuses early with the fix named.
- **`comfyui_submit`** — the same generator with the lid off: your own width/height/seed/steps,
  or a complete API-format workflow graph. Use it when the request needs a knob
  `image_generate` does not expose, or when you want the `prompt_id` back to poll with
  `comfyui_result`.
- **`comfyui_status`** — version, free VRAM as ComfyUI sees it, queue length, installed models.
  Read-only and free. Call it first when anything looks off.

There is no `comfyui_generate` and no `comfyui_checkpoints`. If a tool by that name seems to be
missing, it is because it never existed — use the three above.

## The VRAM sequence (this is why it fails)

Both 3090s normally hold `qwen3.8-27b` at about 84 % of each card's 24 GiB (measured
2026-09-09: ~20.1 GiB used, ~3.9 GiB free per card), and the CosyVoice TTS service now wants
roughly 3 GB of that same headroom. That leaves ComfyUI no useful room. `qwen3.6-35b` is
configured to leave room. Submit first; swap only when the tool tells you to:

1. `image_generate` (or `comfyui_submit`) with the prompt. It checks the VRAM itself and, when
   the language model holds the GPUs, refuses early with `only N GB of VRAM free` and the hint
   naming the swap. When it does not refuse, the image is on its way and there is nothing to swap.
2. Only on that refusal: `llama_swap_load` with `qwen3.6-35b`. This evicts the resident model
   for everyone and takes one to five minutes, so it needs approval and you say both the cost
   and the wait before asking. Then retry step 1.
3. Nothing to undo — the next ordinary chat turn asks llama-swap for `qwen3.8-27b` and it
   reloads by itself.

Do not swap on the assumption that the VRAM is short: a turn that opened with the swap spent
its whole budget waiting for the model and delivered no image (benchmark, 2026-09-18).
`comfyui_status` is free and shows `vram_free_gb`, but the submit is the check that counts.

A 502 from ComfyUI means the service itself is down, not a VRAM problem. Say so
plainly and name the URL you tried; do not retry it ten times and do not go and find a picture
instead.

## Show it

An image nobody can see is not a delivered image. Put it on the HUD in the same turn:

```
hud_show(kind="image", title="<what it is>", payload={"url": "<the path or url returned>"})
```

Then say one line about it. The detail lives on the screen; the spoken reply stays short.

## The gate, and the thing you must not do

Generation occupies the GPU, so it is approval-gated. **Being held at the gate is the correct
end of the turn.** Say what is waiting and why, and stop.

What is never acceptable is routing around the gate. Do not search the web for a photograph,
do not fetch something from Wikimedia, do not draw ASCII, do not shell out to some other
generator — and above all do not put any of that on the HUD and call it done. A request for a
generated image answered with a stock photograph is a wrong answer wearing the costume of a
right one, and it is worse than "this is waiting for your approval, Sir".

The same applies if the approval cannot be shown on the surface you are on. Make the call, let
it be held, and say where the approval is waiting. Never conclude that a gate is unreachable
and therefore optional.

## Details worth knowing

- The default graph is Qwen-Image (a UNet + CLIP + VAE graph, not a checkpoint graph), so the
  model names come from `diffusion_models`, not `checkpoints`. `comfyui_status` lists both.
- Sizes: square 1024×1024, landscape 1216×832, portrait 832×1216. Ask for one of those unless
  the person names a size.
- A generation takes tens of seconds to minutes. `comfyui_submit` with `wait: false` returns a
  `prompt_id` you can poll with `comfyui_result` instead of holding the turn open.
- Reading images is a different job: `vision_analyze` for "what is in this picture", which
  needs no approval and no swap.
