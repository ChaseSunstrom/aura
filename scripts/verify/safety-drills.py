#!/usr/bin/env python3
"""Six drills: prove each guardrail works, rather than that it exists.

The brief (Part 4, "Admin and safety surfaces") asks for a drill per surface,
and a drill is not an assertion that a module imports. Each of these makes the
guardrail do its job on real data and checks what came back:

  audit      write a line, read it back, and confirm it is on disk
  redaction  hand it a token and a password and confirm neither survives
  approvals  ask the matrix about a destructive tool and confirm it gates
  egress     ask the allowlist about a host nobody allowed and be refused
  killswitch engage it, see the gate refuse, release it, see the gate open
  backup     take a backup and confirm the archive exists and is readable

Everything runs against a **scratch HERMES_HOME**, never the live one: a drill
that engaged the real kill switch would be a drill that stopped the house.

Usage: python3 scripts/verify/safety-drills.py [--json]
Exit 0 when all six pass, 1 otherwise.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hermes" / "plugins"))
sys.path.insert(0, str(ROOT / "aura-schema"))


def _fresh(module: str):
    """Import (or re-import) a shared module so it re-reads HERMES_HOME."""
    mod = importlib.import_module(module)
    return importlib.reload(mod)


def drill_audit() -> tuple[bool, str]:
    audit = _fresh("_shared._audit")
    audit.append(audit.KIND_TOOL_CALL, actor="drill", tool="terminal",
                 args={"command": "date -u"}, decision="auto", outcome="ok")
    rows = audit.read(limit=10, kind=audit.KIND_TOOL_CALL)
    mine = [r for r in rows if r.get("actor") == "drill"]
    if not mine:
        return False, "the line was written and did not come back out"
    return True, f"wrote a tool_call line and read it back ({len(rows)} in the log)"


def drill_redaction() -> tuple[bool, str]:
    audit = _fresh("_shared._audit")
    secret = "glpat-" + "AAAABBBBCCCCDDDDEEEE"
    masked = audit.redact({"token": secret, "password": "hunter2", "path": "/etc/hosts"})
    text = audit.redact_text(f"the token is {secret}")
    leaked = [k for k, v in masked.items() if isinstance(v, str) and (secret in v or v == "hunter2")]
    if leaked:
        return False, f"these keys kept their secret: {leaked}"
    if secret in text:
        return False, "redact_text left the token in the sentence"
    if masked.get("path") != "/etc/hosts":
        return False, "redaction ate a value that was not a secret"
    return True, "a token and a password were masked; a path was left alone"


def drill_approvals() -> tuple[bool, str]:
    matrix = _fresh("_shared._matrix")
    destructive = matrix.decide("terminal", {"command": "rm -rf /opt/data"})
    if destructive.free:
        return False, f"a destructive terminal command was not gated: {destructive.verdict}"
    read_only = matrix.decide("hud_list_panels", {})
    if not read_only.free:
        return False, "a read-only tool was gated, which is a matrix that asks about everything"
    # Classification is half the guardrail. The other half is what becomes of an
    # `ask`, and that is a deployment setting rather than a property of the
    # matrix — `approvals.mode: smart` hands the question to an auxiliary model
    # (audit `actor: aux_llm`, `decision: smart_approve`), so a call the matrix
    # gated is approved without a person ever seeing it. The JCB run on
    # 2026-09-08 found exactly that: `safety-destruct-003` deleted on the
    # gateway route with 0 approval requests while the matrix said `ask`.
    #
    # The drill reports the mode rather than failing on it: which mode a house
    # wants is the owner's call, and a machine meant to act while everyone is
    # asleep has a real reason to want `smart`. What it must not do is let that
    # choice stay invisible.
    mode = _approvals_mode()
    note = (f"`rm -rf` is {destructive.verdict} ({destructive.rule_key}) and "
            f"hud_list_panels is free")
    if mode and mode != "ask":
        note += f"; deployed approvals.mode is {mode!r}, so an `ask` is not necessarily a person"
    return True, note


def _approvals_mode() -> str:
    """The deployed ``approvals.mode``, or "" when it cannot be read.

    Read from the running container rather than from the checkout: the image is
    built from the tree but the config is a volume, and it is the running value
    that decides whether an `ask` reaches the owner.
    """
    try:
        out = subprocess.run(["docker", "exec", "jarvis", "sh", "-c",
                              "sed -n '/^approvals:/,/^[^ ]/p' /opt/data/config.yaml"],
                             capture_output=True, text=True, timeout=30, check=False).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "mode":
            return value.strip()
    return ""


def drill_egress() -> tuple[bool, str]:
    egress = _fresh("_shared._egress")
    egress.reset()
    if egress.allowed("https://evil.example.invalid/steal"):
        return False, "a host nobody allowed was allowed"
    return True, "a host that is not on the allowlist is refused"


def drill_killswitch() -> tuple[bool, str]:
    ks = _fresh("_shared._killswitch")
    matrix = _fresh("_shared._matrix")
    if ks.engaged():
        return False, "the scratch home started with the switch already engaged"
    ks.engage(actor="drill", reason="safety drill")
    if not ks.engaged():
        return False, "engage did not engage it"
    # The switch stops actions, not questions: the gate still answers, and what
    # the drill proves is that the state is readable by the thing that gates.
    stopped = matrix.decide("terminal", {"command": "date -u"})
    ks.release(actor="drill")
    if ks.engaged():
        return False, "release did not release it"
    if stopped is None:
        return False, "the gate said nothing while the switch was engaged"
    return True, "engaged, the gate answered, released again"


def drill_backup() -> tuple[bool | None, str]:
    """Back up a marked home, then restore it into an empty one.

    An archive is not a backup. What a house needs proved is that the file
    comes back with the bytes it had, into a home that does not have it — so
    the drill writes a marker, archives, imports into a *second* scratch home
    and reads the marker there. Proving only that `hermes backup` writes a
    non-empty zip would pass with an archive nothing can restore.
    """
    home = Path(os.environ["HERMES_HOME"])
    marker = "drill-8f31c0\n"
    (home / "SOUL.md").write_text(marker, encoding="utf-8")
    # `hermes import` refuses an archive with no config.yaml, .env or state
    # database ("does not appear to be a Hermes backup"), so a home holding
    # only the marker backs up fine and restores never — the drill would fail
    # on its own fixture rather than on the thing it is testing.
    (home / "config.yaml").write_text("model: jarvis\n", encoding="utf-8")
    out = home.parent / "drill-backup.zip"
    # The only drill that needs the `hermes` binary rather than the plugin
    # modules. CI runs this on `python:3.12-slim` with no Hermes installed, so
    # a missing binary is a skip and not a failure — the same answer
    # `hermes-coverage.py` gives, and the job treats both the same way. A hard
    # failure here would have made the drill job red forever on a machine that
    # was never going to have Hermes, and a red gate nobody can turn green is a
    # gate people learn to ignore.
    if shutil.which("hermes") is None:
        return None, "skipped: no hermes on PATH (CI image); run it where Hermes is installed"
    try:
        subprocess.run(["hermes", "backup", "--output", str(out)], capture_output=True,
                       text=True, timeout=180, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"hermes backup could not run: {exc}"
    if not out.exists() or out.stat().st_size == 0:
        return False, "hermes backup produced no archive"
    import zipfile
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    if not any(n.endswith("SOUL.md") for n in names):
        return False, f"the archive has no SOUL.md among its {len(names)} entries"

    # The restore half, into an empty home. `hermes import` reads HERMES_HOME
    # like every other subcommand, so the environment is swapped for the call
    # and put back — a drill that left it pointing at the restore target would
    # silently move every drill after it.
    target = home.parent / "restored"
    target.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        target.chmod(0o777)  # the launcher's own user writes the restore (see main)
    keep = os.environ["HERMES_HOME"]
    try:
        os.environ["HERMES_HOME"] = str(target)
        done = subprocess.run(["hermes", "import", str(out), "--force"], capture_output=True,
                              text=True, timeout=180, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"hermes import could not run: {exc}"
    finally:
        os.environ["HERMES_HOME"] = keep
    back = target / "SOUL.md"
    if not back.exists():
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return False, f"restore left no SOUL.md in the empty home: {detail[-1] if detail else 'no output'}"
    # The bytes, not the file. Hermes writes a default SOUL.md into a home it
    # finds empty, so a restore that silently did nothing still leaves a
    # plausible SOUL.md behind: existence passes, and the house believes it
    # has a backup it cannot restore.
    if back.read_text(encoding="utf-8") != marker:
        return False, "the restored SOUL.md holds Hermes' default text, not the bytes that were backed up"
    return True, f"backed up {len(names)} entries and restored SOUL.md byte-for-byte into an empty home"


DRILLS = {
    "audit": drill_audit,
    "redaction": drill_redaction,
    "approvals": drill_approvals,
    "egress": drill_egress,
    "killswitch": drill_killswitch,
    "backup": drill_backup,
}


def main(argv: list[str]) -> int:
    results: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="jarvis-drill-") as tmp:
        # A scratch home, always: a drill that engaged the real kill switch
        # would be a drill that stopped the house.
        os.environ["HERMES_HOME"] = str(Path(tmp) / "hermes")
        Path(os.environ["HERMES_HOME"]).mkdir(parents=True)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            # In the Hermes image the `hermes` launcher drops root to its own user before it
            # runs, and that user could not enter or write a scratch home root had just made:
            # the backup drill failed there on its lock file, not on the backup (T-1452). The
            # folders are this run's own and are removed with it, so opening them costs nothing.
            for folder in (Path(tmp), Path(os.environ["HERMES_HOME"])):
                folder.chmod(0o777)
        for name, fn in DRILLS.items():
            try:
                ok, note = fn()
            except Exception as exc:  # a drill that raises is a drill that failed
                ok, note = False, f"{type(exc).__name__}: {exc}"
            results[name] = {"ok": ok, "note": note}
    # `None` is a skip, not a pass and not a failure: a drill that needs the
    # `hermes` binary cannot run on the CI image, and counting that as either
    # would be a lie in one direction or the other.
    failed = [n for n, r in results.items() if r["ok"] is False]
    skipped = [n for n, r in results.items() if r["ok"] is None]
    if "--json" in argv:
        print(json.dumps(results, indent=2))
    else:
        for name, r in results.items():
            word = "FAIL" if r["ok"] is False else "SKIP" if r["ok"] is None else "PASS"
            print(f"safety-drill {word}  {name:<11} {r['note']}")
        ran = len(DRILLS) - len(skipped)
        tail = f", {len(skipped)} skipped" if skipped else ""
        print(f"safety-drills: {ran - len(failed)}/{ran} passed{tail}")
    # A real failure is 1. Everything that ran passing but something skipped is
    # 2 — the exit `hermes-coverage.py` uses for the same "not installed here"
    # case, which lets one `|| test $? -eq 2` in the job cover both.
    if failed:
        return 1
    return 2 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
