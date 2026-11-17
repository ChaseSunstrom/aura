"""``python -m bench --replay-sessions N --models a,b`` — the owner's recent prompts, two models.

Reads the last N owner prompts from a Hermes session store (sqlite, opened read-only), sends
each one to both models on the house's own model server as ONE chat-completions step with the
tool schemas offered (``--tools FILE``) and nothing executed, then prints and writes per prompt:
whether both models chose the same tool calls (names and argument keys), each one's latency, and
a 0 to 10 score from a local judge model comparing the reply with what was answered at the time.

What it does not cover: it measures tool *choice* in one step, not whether the call would have
worked; the judge is a model and only advises; the store keeps no tool schemas, so without
``--tools`` agreement is over replies with no tools offered.

Nothing leaves the host: the endpoint must resolve to loopback/private addresses only, no proxy
is used and a redirect is refused (a key and the owner's prompts ride on every request). The
results file holds his prompts verbatim: 0600, under a gitignored directory.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.runner import BenchConfig

DEFAULT_OUT = Path(__file__).parent / "results" / "replay"
MAX_PROMPTS = 200
MAX_PROMPT_CHARS = 8000
MAX_BODY = 4_000_000
# cron and subagent sessions are the house talking to itself, not the owner.
_OWNER_SOURCES = ("api_server", "tui", "cli", "oneshot")

_SQL = f"""
SELECT m.content,
  (SELECT a.content FROM messages a
    WHERE a.session_id = m.session_id AND a.role = 'assistant' AND a.id > m.id
      AND a.content IS NOT NULL AND trim(a.content) != ''
      AND a.id < COALESCE((SELECT MIN(u.id) FROM messages u
                            WHERE u.session_id = m.session_id AND u.role = 'user'
                              AND u.id > m.id), 9223372036854775807)
    ORDER BY a.id DESC LIMIT 1)
FROM messages m JOIN sessions s ON s.id = m.session_id
WHERE m.role = 'user' AND s.source IN ({",".join("?" * len(_OWNER_SOURCES))})
  AND m.content IS NOT NULL AND trim(m.content) != ''
ORDER BY m.timestamp DESC, m.id DESC LIMIT ?
"""


class Refused(Exception):
    """A precondition failed before any prompt was sent (exit 2)."""


@dataclass
class Prompt:
    prompt: str
    reference: str | None


def read_prompts(store: Path, n: int) -> list[Prompt]:
    uri = f"file:{urllib.parse.quote(str(Path(store).resolve()))}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise Refused(f"cannot open the session store read-only: {exc}") from exc
    try:
        db.execute("PRAGMA query_only = 1")
        rows = db.execute(_SQL, (*_OWNER_SOURCES, n)).fetchall()
    except sqlite3.Error as exc:
        raise Refused(f"the session store has an unexpected shape: {exc}") from exc
    finally:
        db.close()
    return [Prompt(p[:MAX_PROMPT_CHARS], a[:MAX_PROMPT_CHARS] if a else None) for p, a in rows]


def is_private_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    # ::ffff:8.8.8.8 reads as private to is_private before Python 3.13: judge the v4 inside.
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_loopback or ip.is_link_local or (ip.is_private and not ip.is_unspecified)


def check_endpoint(base: str) -> None:
    parts = urllib.parse.urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused(f"not an http(s) endpoint: {base!r}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 80, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise Refused(f"cannot resolve {parts.hostname}: {exc}") from exc
    bad = sorted({str(i[4][0]) for i in infos if not is_private_address(str(i[4][0]))})
    if bad or not infos:
        raise Refused(f"{parts.hostname} is not a private address ({', '.join(bad) or 'none'}): "
                      "nothing is sent off the host")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - returning None raises HTTPError
        return None


# ponytail: the name is resolved once in check_endpoint and again by urllib (DNS rebinding in
# between); pin the address if the model server ever sits behind a name outside our control.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _call(base: str, key: str, path: str, body: dict | None, timeout: float) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path, method="GET" if body is None else "POST",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read(MAX_BODY))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise Refused(f"{path} answered with a redirect: refused") from exc
        raise


def parse_score(text: str) -> int | None:
    found = re.findall(r"SCORE:\s*(\d+)", text or "")
    return int(found[-1]) if found and int(found[-1]) <= 10 else None


def _tool_signature(message: dict) -> list[list]:
    sig = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
            keys = sorted(args) if isinstance(args, dict) else ["<not an object>"]
        except ValueError:
            keys = ["<unparsable>"]
        sig.append([str(fn.get("name")), keys])
    return sorted(sig)


def _ask(cfg: dict, model: str, messages: list[dict], *, tools: list | None,
         max_tokens: int) -> tuple[dict, float]:
    body = {"model": model, "messages": messages, "temperature": 0, "stream": False,
            "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    t0 = time.monotonic()
    doc = _call(cfg["base"], cfg["key"], "/v1/chat/completions", body, cfg["timeout"])
    return doc["choices"][0]["message"], time.monotonic() - t0


def _judge(cfg: dict, prompt: Prompt, reply: str) -> int | None:
    if prompt.reference is None:
        return None
    ask = (f"Owner request:\n<<<\n{prompt.prompt}\n>>>\nWhat was answered at the time:\n<<<\n"
           f"{prompt.reference}\n>>>\nCandidate reply:\n<<<\n{reply}\n>>>\n"
           "Score how well the candidate serves the request compared with the earlier answer, "
           "0 (useless) to 10 (as good or better). End with a line `SCORE: <n>`.")
    msg, _ = _ask(cfg, cfg["judge"], [{"role": "system", "content": "You grade assistant replies."},
                                       {"role": "user", "content": ask}],
                  tools=None, max_tokens=512)
    return parse_score(msg.get("content") or "")


def run_replay(prompts: list[Prompt], models: list[str], judge: str, tools: list | None,
               cfg: dict) -> list[dict]:
    cfg = {**cfg, "judge": judge}
    rows: list[dict[str, Any]] = [
        {"prompt": p.prompt, "reference": p.reference, "models": {}} for p in prompts]
    # One model at a time: llama-swap loads a model on first request (about 7 minutes cold).
    for model in models:
        try:  # warm-up, so the first prompt's latency is not the load time
            _ask(cfg, model, [{"role": "user", "content": "ping"}], tools=None, max_tokens=1)
        except (OSError, ValueError, KeyError):
            pass
        for p, row in zip(prompts, rows):
            res: dict[str, Any] = {"latency_s": None, "tools": [], "reply": "", "score": None, "error": None}
            row["models"][model] = res
            try:
                msg, res["latency_s"] = _ask(cfg, model, [{"role": "user", "content": p.prompt}],
                                             tools=tools, max_tokens=1024)
                res["tools"] = _tool_signature(msg)
                res["reply"] = msg.get("content") or ""
            except (OSError, ValueError, KeyError, IndexError) as exc:
                res["error"] = f"{type(exc).__name__}: {exc}"[:200]
    for model in models:
        for p, row in zip(prompts, rows):
            res = row["models"][model]
            if res["error"] is None:
                text = res["reply"] or "(tool calls: " + ", ".join(t[0] for t in res["tools"]) + ")"
                try:
                    res["score"] = _judge(cfg, p, text)
                except (OSError, ValueError, KeyError, IndexError):
                    res["score"] = None
    for row in rows:
        both = list(row["models"].values())
        row["agree"] = None if any(r["error"] for r in both) else both[0]["tools"] == both[1]["tools"]
    return rows


def judge_line(judge: str, models: list[str]) -> str:
    return f"judged by {judge}" + (", one of the two under test" if judge in models else "")


def render_table(rows: list[dict], models: list[str], tools_offered: bool, judge: str) -> str:
    out = [judge_line(judge, models)]
    if not tools_offered:
        out.append("no tools offered (no --tools file): agreement is over replies with no tools "
                   "offered, so it is only as informative as that.")
    head = f"{'#':>3}  {'prompt':40}  {'agree':5}  " + "  ".join(
        f"{m[:14]:>14} s/score" for m in models)
    out += [head, "-" * len(head)]
    for i, r in enumerate(rows, 1):
        agree = {True: "yes", False: "NO", None: "err"}[r["agree"]]
        cells = []
        for m in models:
            x = r["models"][m]
            lat = "err" if x["error"] else f"{x['latency_s']:.1f}"
            cells.append(f"{lat:>9}/{'-' if x['score'] is None else x['score']:<4}")
        prompt = " ".join(r["prompt"].split())[:40]
        out.append(f"{i:>3}  {prompt:40}  {agree:5}  " + "  ".join(cells))
    agreed = sum(1 for r in rows if r["agree"])
    out.append(f"\nagreement {agreed}/{len(rows)}")
    for m in models:
        lats = [r["models"][m]["latency_s"] for r in rows if r["models"][m]["latency_s"] is not None]
        scores = [r["models"][m]["score"] for r in rows if r["models"][m]["score"] is not None]
        out.append(f"{m}: median latency {statistics.median(lats):.1f}s, " if lats else f"{m}: ")
        out[-1] += f"mean score {statistics.mean(scores):.1f}" if scores else "no score"
    return "\n".join(out)


def write_results(doc: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = out / f"replay-{stamp}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, indent=2)
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description=__doc__.split("\n\n")[0])
    p.add_argument("--replay-sessions", type=int, required=True, metavar="N",
                   help=f"how many recent owner prompts (1 to {MAX_PROMPTS})")
    p.add_argument("--models", required=True, help="exactly two model names, comma separated")
    p.add_argument("--store", required=True, help="path to the session store (state.db)")
    p.add_argument("--judge", help="judge model (default BENCH_JUDGE_MODEL, else the second model: still loaded)")
    p.add_argument("--tools", help="JSON file: tool schemas to offer (OpenAI tools format)")
    p.add_argument("--base", help="model server base URL (BENCH_MODELS_BASE, else BENCH_API_BASE)")
    p.add_argument("--key", help="model server key (BENCH_MODELS_KEY, else BENCH_API_KEY)")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds per request (cold start)")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p


def _replay(args: argparse.Namespace) -> int:
    env = BenchConfig.from_env()
    base = args.base or os.environ.get("BENCH_MODELS_BASE") or env.api_base
    key = args.key or os.environ.get("BENCH_MODELS_KEY") or env.api_key
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) != 2 or models[0] == models[1]:
        raise Refused("--models takes exactly two different model names")
    if not 1 <= args.replay_sessions <= MAX_PROMPTS:
        raise Refused(f"--replay-sessions must be 1 to {MAX_PROMPTS}")
    judge = args.judge or os.environ.get("BENCH_JUDGE_MODEL") or models[1]
    tools = None
    if args.tools:
        try:
            tools = json.loads(Path(args.tools).read_text())
        except (OSError, ValueError) as exc:
            raise Refused(f"cannot read --tools: {exc}") from exc
        if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
            raise Refused("--tools must be a JSON list of tool schemas")
    check_endpoint(base)
    cfg = {"base": base, "key": key, "timeout": args.timeout}
    try:
        served = {m.get("id") for m in _call(base, key, "/v1/models", None, 30).get("data", [])}
    except (OSError, ValueError, AttributeError) as exc:
        raise Refused(f"cannot list models at {base}: {exc}") from exc
    missing = [m for m in (*models, judge) if m not in served]
    if missing:
        raise Refused("the server does not list: " + ", ".join(missing))
    prompts = read_prompts(Path(args.store), args.replay_sessions)
    if not prompts:
        raise Refused("no owner prompts in the store")
    print(f"replaying {len(prompts)} prompts on {models[0]} and {models[1]} (judge {judge}): "
          "each model loads on first use (about 7 minutes cold), nothing is executed.", flush=True)
    rows = run_replay(prompts, models, judge, tools, cfg)
    doc = {"created": datetime.now(timezone.utc).isoformat(), "base": base, "models": models,
           "judge": judge, "judge_note": judge_line(judge, models), "tools_offered": len(tools) if tools else 0, "rows": rows}
    path = write_results(doc, Path(args.out))
    print(render_table(rows, models, bool(tools), judge))
    print(f"\nwrote {path} (0600: it holds the prompts verbatim)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _replay(args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
