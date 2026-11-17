# Does a session's reasoning level reach the model? (T-0723, 2026-11-09)

T-0683 found that a spoken turn at `VOICE_REASONING_EFFORT` = low reached its first token no
sooner than a typed one at medium (n = 12). That result had two possible causes: the level
never reaches vLLM, or a one-sentence question reasons little at either level. This page tells
them apart by counting the reasoning each turn produces.

## How it was measured

- **Through Hermes:** `scripts/hermes-probes/reasoning_effort_probe.py` against the deployed
  adapter (Hermes image of 2026-11-09).
  - Each turn opens a fresh session and sends `config.set reasoning <level>` on it. This is the
    same Hermes call the adapter makes for a spoken turn.
  - It then submits one typed question and records the reasoning the HUD streamed
    (`reasoning.delta`), the time to the first reply token, and the time to `message.complete`.
  - The levels are interleaved over three short questions. Low and medium ran 12 turns each;
    none ran 4.
- **Straight to vLLM (control):** the train question with `reasoning_effort` none, low, medium
  and unset, twice each. These calls go to the model server's `/v1/chat/completions` directly,
  so Hermes is out of the path; the usage block reports reasoning tokens.

## Results

Through Hermes. Medians, with the range of reasoning characters in brackets:

| session level | n | reasoning chars | ~tokens (chars / 4) | first token ms | complete ms |
|---|---|---|---|---|---|
| low | 12 | 222 (54-1305) | 56 | 2342 | 2721 |
| medium | 12 | 348 (62-1339) | 87 | 3169 | 3653 |
| none | 4 | 420 (54-658) | 105 | 2404 | 3237 |

Medians of reasoning characters per question, low against medium (n = 4 each):

| question | low | medium |
|---|---|---|
| kilowatt hour | 194 | 95 |
| train arrival | 134 | 511 |
| 2^10 against 10^3 | 602 | 588 |

The direct control, as reasoning tokens from the usage block:

| `reasoning_effort` sent | reasoning tokens (two runs) |
|---|---|
| none | 0, 0 (the whole completion is 12 tokens) |
| low | 73, 71 |
| medium | 70, 90 |
| unset (the template's xhigh) | 41, 49 |

## Verdict

**The session's level does not reach vLLM** (fixed by T-0814; the section after this one). Every turn runs at medium, whatever the session
asks for.

The evidence:
- Through Hermes, `none` still reasons (54-658 characters, every turn).
- Sent straight to vLLM, `none` turns thinking off: 0 reasoning tokens.
- The low-against-medium gap through Hermes goes one way on one question and the other way on
  another (194 against 95, 134 against 511). It is the model's own spread, not an effect of
  the level.

The cause is the deployed provider's static `extra_body: {reasoning_effort: "medium"}`
(`providers.qwen-vllm` in the rendered `config.yaml`). Hermes puts the session's level in the
request's top-level `reasoning_effort`. The OpenAI client then merges `extra_body` over the
top-level fields, so medium wins. This was shown with openai 2.24.0 (Hermes' own) against a mock
transport: `reasoning_effort="none"` with `extra_body={"reasoning_effort": "medium"}` sends
`"medium"`.

That provider default exists for calls that carry no level of their own. Without it, auxiliary
calls (titles, compression) would get the template's xhigh.

**Effect size once fixed.** Even when the level arrives, low and medium barely differ on a short
question (71-73 against 70-90 reasoning tokens). In this model's template, `low` is a one-line
instruction ("keep your thinking brief") and `medium` adds nothing. The large step is `none`: no
reasoning at all, and the reply starts after the first token. So VOICE_REASONING_EFFORT = low
has bought spoken turns nothing since it was introduced, and fixing the plumbing alone will
still buy little. The lever for voice latency is `none` on a spoken turn, which is a quality
call (ADR-0002 keeps thinking on), measured before it is made.

## After the fix (T-0814, 2026-11-09, image 31b1b410)

The provider default moved to `extra_body.chat_template_kwargs.reasoning_effort`; a volume
rendered before the move takes it on its next start (`config.yaml += …chat_template_kwargs`,
`-= …extra_body: reasoning_effort` in the jarvis log). The same probe, 8 turns per level,
interleaved over the three questions:

| session level | n | turns that streamed reasoning | reasoning chars, median (range) | first token ms, median | complete ms, median |
|---|---|---|---|---|---|
| none | 8 | 0 | 0 (0-0) | 1743 | 2347 |
| low | 8 | 8 | 406 (61-790) | 5020 (6 warm turns) | 5532 |
| medium | 8 | 8 | 320 (62-910) | 2580 | 3477 |

The level now arrives: `none` turns think not at all, and low and medium both stream reasoning.
Low reasons no less than medium on these questions, as the control said it would.

**A probe artefact, corrected.** A turn that does not think still gets a `reasoning.available`
event: Hermes relays the reply's own first 500 characters under that name
(`agent/turn_response_intake.py`, `_relay_thinking`). The probe counted that as reasoning, so a
`none` turn first read as reasoning exactly as long as its reply. The probe now drops it when
nothing streamed and the text is the reply's. T-0723's rows are not affected: every one of its
turns streamed `reasoning.delta`.

**Low splits the prompt cache.** The first two low turns waited 23 s for their first token; the
other six took 1-3 s. The model's chat template puts a line at the very top of the system prompt
for low ("Reasoning effort is set to low. Keep your thinking brief…") and for the unset xhigh, and
nothing for medium. Medium and low prompts therefore share no cached prefix. A control straight
to the model server, one 30k-token prompt, first token per request:

(`scripts/reasoning-prefix-probe.py`)

| request | medium | medium | low | low | none | none | medium |
|---|---|---|---|---|---|---|---|
| first token s | 21.95 (cold) | 0.64 | 22.02 | 0.61 | 0.66 | 0.55 | 0.57 |

Low pays the whole prefill again; `none` only closes the think block at the end of the prompt
and reuses medium's cache. In use the adapter switches a session to the voice level for a
spoken turn and back for a typed one. So once the level reached the model, a spoken turn at low
after typed turns would have re-read the whole conversation, and this hardware prefills about
1,400 tokens a second.

**So the voice default is now medium** (`voice.reasoning_effort`, equal to
`model.reasoning_effort`), which is what every spoken turn actually ran at before this fix.
`none` keeps the cache and starts the reply about 0.8 s sooner than medium here. It is the lever
for voice latency, and a quality call (ADR-0002 keeps thinking on) that is not made here.

Not covered: tokens as vLLM counts them on the Hermes path (the socket shows characters); a
level set by `/reasoning` or the settings tool mid-conversation pays the same one cold prefill.

    python scripts/hermes-probes/reasoning_effort_probe.py --base http://<adapter>:8643 --key $AURA_API_KEY --n 8 --levels none,low,medium
