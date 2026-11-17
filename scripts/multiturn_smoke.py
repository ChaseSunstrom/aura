#!/usr/bin/env python3
"""Does a five-turn conversation keep its thread on the real route? (T-0730)

    AURA_BASE=http://<host>:8643 AURA_API_KEY=… python scripts/multiturn_smoke.py \
        [--runs 3] [--resume-between-turns] [--browse] [--timeout 240]

The owner, 2026-11-08: "it sucks at multi turn stuff". Each run is the owner's shape of a
conversation -- ask, clarify, answer, act, follow up -- where every turn after the first only
makes sense with the turn before it ("the second one", "go ahead", "the first one instead").
The first reply's numbered list is the thread: turns three to five must name the option the
owner pointed at. Typed turns over the adapter's /ws, on one session; with
--resume-between-turns every turn opens a new socket and resumes the session by its stored
id, the way the phone does after a reconnect. An approval is denied (no turn here needs a tool).

Prints one line per turn (the reply's first 160 characters) and a verdict per run; exit 1
when any run loses the thread. Does not cover voice turns (the speaker ticket) or the car
page; and the model varies, so a single failure is a lead, not a proof.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _probe import Rpc, adapter_ws_url, connect  # noqa: E402

TURNS = [
    "I want to pick a fruit for a dessert tonight. Ask me which one: give me exactly three "
    "options as a numbered list, one fruit name per line, nothing else on those lines.",
    "Before I choose, say the three options again in one line.",
    "The second one.",
    "Go ahead: one short dessert idea with it, two sentences at most.",
    "Actually, the first one instead. Same thing, one short idea.",
]
# --browse: the owner's own shape ("use a website for me, ask me which one"), where the list
# comes from a page the browser read, so the thread runs through tool results too.
BROWSE = ("Use a website for me: open https://en.wikipedia.org/wiki/Dessert in the browser, "
          "then give me exactly three kinds of dessert named on that page as a numbered list, one "
          "name per line, nothing else on those lines, and ask me which one.")
_ITEM = re.compile(r"^\s*(?:\*\*)?([1-3])[.)]\s*(?:\*\*)?\s*([^\n*—:(-]+)", re.M)


def options(reply: str) -> list[str]:
    """The numbered list's names, in order; [] when the reply has no 1-3 list."""
    found = {int(n): name.strip().rstrip(".").lower() for n, name in _ITEM.findall(reply)}
    return [found[i] for i in (1, 2, 3)] if all(found.get(i) for i in (1, 2, 3)) else []


def names(reply: str, option: str) -> bool:
    """The option is in the reply as words, plural too (fig is in "figs", not in "configure"):
    its whole name or, for "Blood orange", its last word."""
    return any(re.search(rf"\b{re.escape(o)}(?:e?s)?\b", reply.lower()) for o in (option, option.split()[-1]))


def picks(reply: str, option: str, listed: list[str]) -> bool:
    """The reply goes on with ``option``; one that lists every option again has lost the thread."""
    return names(reply, option) and not all(names(reply, o) for o in listed)


async def ask(rpc: Rpc, sid: str, text: str, timeout: float,
              asked: list[tuple[str, list[str]]]) -> str:
    """One turn's reply. A question Jarvis asks with its clarify tool ends the reply here, and
    the next turn's words answer it, the way the owner answered on the console (2026-11-08);
    ``asked`` carries that open request, and its batch's question ids, from one call to the
    next: Hermes asks as a batch even for one question, and a batch answered without its ids
    resolves empty ("the line went quiet")."""
    if asked:
        rid, qids = asked.pop()
        for qid in qids or [""]:
            await rpc.call("clarify.respond", {"session_id": sid, "request_id": rid, "answer": text,
                                               **({"question_id": qid} if qid else {})})
    else:
        await rpc.call("prompt.submit", {"session_id": sid, "text": text, "surface": {"kind": "console"}})
    reply = ""
    async for p in rpc.events(timeout):
        t, pl = p.get("type"), p.get("payload") or {}
        if t == "clarify.request":
            batch = pl.get("questions") or [pl]
            asked.append((str(pl.get("request_id") or ""), [str(b["qid"]) for b in batch if b.get("qid")]))
            q = batch[0]
            choices = q.get("choices") or q.get("options") or []
            listed = "\n".join(f"{i}. {c}" for i, c in enumerate(choices, 1))
            return "\n".join(s for s in (reply, str(q.get("question") or ""), listed) if s).strip()
        if t == "approval.request":
            await rpc.call("approval.respond", {"session_id": sid, "request_id": pl.get("request_id"), "choice": "deny"})
        elif t == "message.delta":
            reply += pl.get("text") or ""
        elif t == "message.complete":
            return (pl.get("text") or reply).strip()
        elif t == "error":
            return f"<error: {pl.get('message') or pl}>"
    return f"<no reply in {timeout:.0f} s>"


async def run(url: str, resume: bool, timeout: float, turns: list[str]) -> bool:
    replies: list[str] = []
    asked: list[tuple[str, list[str]]] = []
    if resume:
        async with connect(url) as rpc:
            created = (await rpc.call("session.create", {}))["result"]
        stored = created.get("stored_session_id") or created["session_id"]
        for text in turns:
            async with connect(url) as rpc:
                answer = await rpc.call("session.resume", {"session_id": stored})
                if "result" not in answer:
                    print(f"  resume failed: {answer.get('error')}")
                    return False
                replies.append(await ask(rpc, answer["result"]["session_id"], text, timeout, asked))
            print(f"  {len(replies)}. {replies[-1][:160]!r}")
    else:
        async with connect(url) as rpc:
            sid = await rpc.create_session()
            for text in turns:
                replies.append(await ask(rpc, sid, text, timeout, asked))
                print(f"  {len(replies)}. {replies[-1][:160]!r}")
    listed = options(replies[0])
    if not listed:
        print("  LOST: the first reply has no 1-3 list to follow")
        return False
    first, second = listed[0], listed[1]
    checks = [
        ("clarify names all three", all(names(replies[1], o) for o in listed)),
        ("'the second one' is the second", picks(replies[2], second, listed)),
        ("'go ahead' acts on it", picks(replies[3], second, listed)),
        ("'the first one instead' switches", picks(replies[4], first, listed)),
    ]
    for what, ok in checks:
        print(f"  {'ok  ' if ok else 'LOST'} {what} ({listed})")
    return all(ok for _, ok in checks)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=os.environ.get("AURA_BASE", "http://127.0.0.1:8643"))
    ap.add_argument("--key", default=os.environ.get("AURA_API_KEY", ""))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--resume-between-turns", action="store_true")
    ap.add_argument("--browse", action="store_true", help="the first turn reads a web page")
    ap.add_argument("--timeout", type=float, default=240.0)
    a = ap.parse_args()
    turns = [BROWSE, *TURNS[1:]] if a.browse else TURNS
    if not a.key:
        print("set AURA_API_KEY (the apps' key)")
        return 2
    url = adapter_ws_url(a.base.rstrip("/"), a.key)
    held = 0
    for i in range(a.runs):
        print(f"run {i + 1}{' (resumed between turns)' if a.resume_between_turns else ''}:")
        held += await run(url, a.resume_between_turns, a.timeout, turns)
    print(f"{held}/{a.runs} runs kept the thread")
    return 0 if held == a.runs else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
