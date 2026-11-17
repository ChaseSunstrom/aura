# What the house still remembers about a turn

*Research note, 16 November 2026 (T-1489). Read on commit `980b3ca71`. Nothing
in the house or in Hermes was changed by this note; it added one test,
`aura-core/tests/test_turn_memory_shape.py`, that pins the shape the answer
rests on, and corrected the claims rows listed at the end.*

The house service (`aura-core`) once ran the assistant in-process. Its agent
kept a few facts about the turn it was in, in `aura/api/devices.py`: what the
user said, which conversation the turn belonged to and whether its reply was
spoken, who the voice gate recognised, and whether the turn had read text a
stranger wrote. Several security checks read those facts. The agent loop was
removed on 22 September 2026 (`acc05134a`) when the assistant moved to Hermes.
The question: which of those checks still have something to read, and what
does each decide when it has nothing?

## The answer

**None of them can fire on a production turn.** Two different reasons, and the
second is the one that matters:

1. Three of the four writers have no caller. Nothing records what was said,
   the turn's conversation, or the speaker.
2. The fourth, the mark for "this turn has read outside text", is written (17
   call sites). But every store is keyed by the context's id, and since the
   agent went there is no context that lasts a turn: each REST request and
   each socket message builds a fresh one, `POST /api/tools/call` passes none,
   and Hermes reaches the house one request per tool call. A mark made by one
   call is gone, as far as any reader can tell, before the next call starts.

Every reader fails open on an empty store, by design: the checks were written
as "refuse if we know better", so with nothing known they let the call through.
That was right while a writer existed. Today it means the checks are present,
tested and green, and decide nothing.

What gates a production turn instead is the Hermes guard
(`hermes/plugins/_shared/_approval_hook.py`), which keeps its own record per
session in `_taint.py`: the words that opened the turn, and which readers of
outside text have run since. Comparing the two, check by check, found two
things the house did that the guard did not. Both were then fixed, each in its own
change: T-1490 and T-1492.

## The stores, their writers and their readers

| Store | Writer | Call sites of the writer | Readers |
|---|---|---|---|
| What the user said (`TurnUtterances`) | `remember_utterance` | **0** | the lock tool's verb check (`tools.py`, `_lock_verb_refusal`); memory's "you did not ask me to remember" (`integrations/memory`, `tool_remember`); research borrowing the question (`integrations/research`); the gate's "did the turn see these words" (`tools.py`, `_composed`) |
| The turn's conversation and whether it is spoken (`TurnFacts`) | `remember_turn` | **0** | the gate, to file an approval request under a conversation (`tools.py`, `_turn_facts`); the timer tool |
| Who spoke (`TurnFacts`) | `remember_speaker` | **0** | memory, to file a fact under a person (`speaker_of`) |
| The turn has read outside text (`UntrustedTurns`) | `mark_untrusted`, `mark_untrusted_result` | 2 and 15 | the gate: hold a state-changing tool, hold a composed outbound read, mark an approval request `tainted` (`tools.py`, `requires_approval` and the request it raises); memory's `remember` and `forget`, and `undo`, which refuse (`turn_is_untrusted`) |
| What the turn was shown (`UntrustedTurns.note_seen`) | the registry, after each result | 1 | the gate's `_composed` |

Counts are call sites in `aura-core/aura`, the definitions and the wrappers'
own bodies left out. A sweep of the whole repository by name (Hermes plugins,
the adapter, the console, the phone's tools) finds the three unwritten writers
only in their own module and in two test files; no string dispatch names them
and no route wraps them.

```bash
git grep -n -e "remember_utterance(" -e "remember_turn(" -e "remember_speaker(" -- aura-core/aura hermes aura-adapter jarvis-web/src
git grep -n -e "mark_untrusted(" -e "mark_untrusted_result(" -- aura-core/aura | grep -vc "def "
```

The first prints only lines of `aura/api/devices.py` (the definitions and one
wrapper's body). The second prints 18: the 17 call sites and the line inside
`mark_untrusted_result` that calls `mark_untrusted`.

## What each reader decides with nothing to read

| Check | With an empty store | Reachable in production? |
|---|---|---|
| Lock verb: "the request said lock, you are calling unlock" | no refusal (`said` is `""`, the pattern does not match) | no: nothing records the utterance. The house's `lock_control` tool is reached only by the console's test-run, where a person clicked and there is no utterance by design |
| Memory: "you did not ask me to remember this" | not refused (`if said and …`) | no, for the same reason; and Hermes writes its own memory, not the house's |
| Memory and undo: refuse on a turn that read outside text | not refused | no: the mark belongs to another call's context |
| Gate: a state-changing tool on such a turn needs approval | not escalated | no, same reason. A tool that is gated for its tier or domain is still gated: that part does not read the turn |
| Gate: a composed outbound read on such a turn is held | not held | no |
| Gate: an approval request raised on such a turn is marked `tainted` | marked `false` | no |
| Research borrows the user's question | uses the model's own wording | no |
| Memory files a fact under the speaker | files it under nobody | no |
| Timer and approval requests learn the conversation | `(None, False)`: no conversation, not spoken | no |

The empty answers are pinned (`""`, `""`, `(None, False)`, `False`), and so is
the reason a full store would not help:

```bash
cd aura-core && python3 -m pytest tests/test_turn_memory_shape.py -q   # 4 tests
```

The suites that prove these checks (`test_security.py`, `test_ask_user.py`,
`test_memory.py` and others) make one `Context` and reuse it across calls. That
is the agent's shape, faithfully tested, and no production path has it.

## Where the console's test-run fits

The console can run one house tool (`jarvis/tools/call` on the house socket,
`POST /api/tools/call`). Both go through `ToolRegistry.call`, so the tier and
domain gates hold there exactly as written: a Tier-3 tool raises an approval
card. Each run is one call with its own context (or none), so the turn-based
checks are as inert there as anywhere, and correctly so: there is no turn, and
the person who clicked is the one who would be asked.

## What stands in their place

Hermes keeps the turn. `_taint.turn_start` records the words that opened it and
the surface they came from; `_taint.note_tool` marks it when a reader of outside
text runs (`READERS`: web, browser, mail, files, calendar, contacts, the screen,
past sessions and more; since T-1495 every free tool is on that list or is named
in `hermes/plugins/tests/test_readers_complete.py` as not one). The gate then:

| The house did | The Hermes guard does |
|---|---|
| Held a state-changing tool on a tainted turn | A row that asks, asks; its card says what was read and is keyed `…:after-outside`, so an "always" given in a clean turn does not cover it. A row a capability level had made free asks again (DECISIONS 34 and 45) |
| Refused `remember`/`forget`/`undo` on a tainted turn | `house_undo` always asks. A removal or rewrite of memory always asks, in both shapes the tool takes, and an addition to memory or the fact store is asked about once per call after outside text (**T-1490**, found by this comparison: additions were free, and the list shape removed entries unasked). The later queue and complaints refuse |
| Refused a lock call whose verb contradicted the request | No refusal. A lock or alarm call is a sensitive ask every time, signed on the phone, and the card shows the words that opened the turn, so the mismatch is in front of the person who answers |
| Refused a memory write the user had not asked for | Nothing. An addition in a clean turn is free whatever the user said |
| Held an outbound read the model composed on a tainted turn | A free fetch of an address the session was not given asks once per call, with the address on the card (**T-1492**, found by this comparison: there was no hold at all). Stricter than the house's rule: the address is matched whole, not word by word. A search query is not held |
| Marked a question raised on a tainted turn | Every card raised after outside text says so in its first sentence |
| Filed a fact under the recognised speaker | Hermes' memory has no per-person filing; `_speaker.py` knows who is speaking for the guest rules only |

## What this note changed, and what it leaves to decide

Corrected, because they stated the house's turn checks as proof of what
production does: the claims rows "A turn that has read external content cannot
silently act", "…and `remember`/`forget`/`undo` refuse outright", "A tainted
turn is HELD and marked, not refused" and "A question raised by a tainted turn
says where its words came from" in `docs/verification.md` now say that their
tests prove the house's gate for a turn that shares one context, that no
production path forms one, and where the live rule is. A status section at the
top of `docs/explanation/injection.md` says the same.

Left open, each as its own item:

- A shell command after a read of the owner's own files only is still free unless it
  writes an address the session was not given. After a read from beyond the house it
  asks since T-1493 (the house never held it either; T-1492 found it). Text typed into an
  open page, found the same way, asks since T-1494 unless it is the owner's own words.
- Whether the house's turn stores, the checks that read them and their tests
  are removed, or fed (a turn id passed by the Hermes plugins on each house
  call would bring every one of them back to life). Removing them deletes
  tested security code that guards nothing; feeding them duplicates what the
  guard already does, in a second place. This note recommends removal, now that
  T-1492 has given the guard the one rule it lacked, and does not do it.
- A memory addition the user never asked for, in a clean turn, is free. The
  house refused it on the turn's own words; the guard has the words
  (`_taint.opened_by`) and does not use them for this. Not obviously wanted:
  the persona is told to store what it learns unasked.

Not examined: the Hermes guard beyond the checks the house used to make; what
Hermes itself stores with no tool call; the readers list's completeness.
