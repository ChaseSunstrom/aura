# Model time to first token (T-0637)

Read-only against production, 2026-11-08: vLLM's Prometheus `/metrics` on the upstream llama-swap
started (`vllm serve cyankiwi/Qwen3.8-27B-AWQ-INT4 --port 5803 ... --enable-prefix-caching`, found
through llama-swap `/running`), and Hermes's code read inside the `jarvis` container. Nothing was
restarted or reconfigured, and no request was sent.

## Where the time goes: the prefix is not the problem

| Measure (counters since the engine started, 154 requests) | Value |
|---|---|
| prefix caching | on (`--enable-prefix-caching`) |
| prompt tokens, total / served from the prefix cache | 3,402,805 / 3,088,960 |
| **prefix-cache hit rate** | **90.8 %** |
| prompt tokens computed per request (mean) | 2,038 |
| request prompt size | 53 requests under 500 tokens (aux calls); 86 between 20k and 50k tokens (agent turns, system prompt plus ~259 tool schemas); 15 above 50k |
| queue time (mean) | 6 ms |
| vLLM time to first token: mean / p50 / p95 | 1.61 s / under 0.25 s / under 2.5 s (histogram buckets) |
| prefill time (mean) | 1.51 s (decode mean 2.1 s, not on the first-token path) |

So vLLM's own first token is quick for most requests (93 of 154 under 0.25 s) and the cache is hit
for 9 of 10 prompt tokens. The slow tail is a request that has to compute a few thousand uncached
tokens: a new session, whose volatile tail (memory, persona and surface sections, date line) is new,
or the turn after a compaction.

## Is the Hermes prefix stable across turns?

Yes, by Hermes's own design, and our plugins keep to it (read in the container,
`agent/system_prompt.py` and `agent/turn_context.py`):

- The system prompt is built stable -> context -> volatile. The skills index, memory, our plugin
  sections (`after_memory`) and the date line are last; the date is date-only and tied to the
  session's start, so it is byte-stable through the day.
- A `pre_llm_call` hook's `{"context": ...}` is appended to the **current user message** (sidecar
  `api_content`), never to the system prompt, and replays byte-identical on later turns.
- Ours: `jarvis-personas`, `jarvis-hud` (`promises`, `learned`) return context only on the turn it
  applies; `jarvis-guard` and `later`/`habits` only record state and return nothing. The sections
  (`jarvis-persona`, `jarvis-surface`, `jarvis-playbooks`) are frozen per session by Hermes.

Pinned by `hermes/plugins/tests/test_prefix_stability.py` (every section is `after_memory`, renders
the same twice, every `pre_llm_call` hook returns only `{"context"}`, a string or nothing). No code
change was needed: nothing moved, because nothing sits early.

## What does not fit: the probe's 0.85-1.84 s against vLLM's under 0.25 s

`turn_latency_probe`'s `first_token_ms` is submit -> the first `message.delta` over the adapter's
`/ws`. vLLM's histogram is submit -> the first generated token. Thinking is on and bounded
(`LLM_REASONING_EFFORT`, `deploy/config.template.yaml:62`, `--reasoning-parser qwen3`): reasoning
tokens are generated first and the first visible delta waits for them. The likely cost is that
thinking, not prefill. Cutting it means a smaller `LLM_REASONING_EFFORT` for the voice surface, a
setting change (ADR-0002) the owner decides, not a prefix fix; captured as a follow-up.

## Before / after (probe)

The probe needs the API key from `/opt/jarvis-host/.env`, which this lane cannot read. Rows for the
main thread (no code changed, so "after" is the repeat; a changed `LLM_REASONING_EFFORT` is the
experiment worth an "after"):

    . .venv/bin/activate && python3 scripts/hermes-probes/turn_latency_probe.py --base http://<host>:8643 --key $AURA_API_KEY --n 20 --no-first-clause

| Run | prompt tokens | prefix-cache hit rate | first_token p50 | first_token p95 |
|---|---|---|---|---|
| before (2026-11-08, T-0605 probe) | 20k-50k | 90.8 % | 0.85-1.84 s | to fill |
| after, same config | to fill | to fill | to fill | to fill |
| after, lower `LLM_REASONING_EFFORT` | to fill | to fill | to fill | to fill |

To read the hit rate over the run, diff `vllm:prompt_tokens_by_source_total{source="local_cache_hit"}`
over `vllm:prompt_tokens_total` before and after.
