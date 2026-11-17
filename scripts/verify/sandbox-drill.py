#!/usr/bin/env python3
"""Drill: can the model's shell reach the JARVIS container, the LAN or the internet?

``hermes/plugins/aura-bwrap/tests/test_bwrap.py`` proves the *argv* — that
``bwrap_argv`` puts ``--unshare-net`` and ``--clearenv`` in the command line and binds
nothing of ``/opt/data`` but the scratch. What it cannot tell you is whether the
container lets bubblewrap do any of that: the seccomp profile could be missing from
the compose file, the binary could be missing from the image, a kernel could refuse
the user namespace, and the unit test would pass through all three while every
command ran in the gateway. So this runs the sandbox, in the deployed container,
through the plugin's own ``run_sandboxed`` — the code path the selfext gate uses —
and through the terminal environment class the gateway uses.

Four passes, the first three like ``sandbox-isolation.py``:

1. **Sandboxed.** A probe tries ``open('/opt/data/config.yaml')``, a TCP connect to
   the gateway's own ports on loopback, to the Docker host, to the LAN address the
   container's ports are published on, to the model and search hosts it is configured
   with and to the internet, and counts environment names carrying
   TOKEN/KEY/PASSWORD. Every attempt must be refused, and each is printed with its
   exception. The destinations come from the container's own environment (see
   ``choose_targets``), not from fixed addresses that go stale.
2. **The positive control.** The same probe under plain ``bash -c`` in the container
   must reach at least one target per class (a file, loopback, the LAN or host, the
   configured model/search host or the internet) and see secrets in the environment —
   or the run is INCONCLUSIVE, not a pass. "Nothing was reachable" and "the probe was broken" look the same, and only
   one of them is a security property.
3. **Reset.** Task A writes a marker in its scratch (``$HOME``; the cwd is the shared work
   directory and persists on purpose); ``cleanup()`` runs; task B sees no marker and A's
   scratch is gone.
4. **execute_code.** Hermes's own ``execute_code`` runs a script that looks for the kill
   switch, the approval grants and ``config.yaml``. It must see none of them: the matrix
   reads the model's Python only as text, so the sandbox is what keeps a path built at
   run time away from the files that decide whether the gate runs (T-0852). With the
   backend set to ``local`` this pass fails, which is the truth about that setting.

Run:  python3 scripts/verify/sandbox-drill.py            (from the host)
      python3 scripts/verify/sandbox-drill.py --probe-here   (the probe alone, unsandboxed, on this host)
Needs: Docker and a running ``jarvis`` container built from an image that carries the
aura-bwrap plugin and bubblewrap. Exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE, 3 not runnable.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

#: The probe, one program so the sandbox is entered once. Written for the sandbox's
#: python (the Hermes venv, no third-party imports) and for the host's.
PROBE = r"""
import json, os, socket, sys
targets = json.loads(sys.argv[1])
out = {"file": {}, "net": {}, "env": {}}
try:
    with open("/opt/data/config.yaml", "rb") as fh:
        fh.read(64)
    out["file"]["/opt/data/config.yaml"] = "READ"
except Exception as exc:
    out["file"]["/opt/data/config.yaml"] = f"refused: {type(exc).__name__}: {exc}"
for name, host, port in targets:
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((host, port))
        out["net"][name] = "REACHED"
    except Exception as exc:
        out["net"][name] = f"refused: {type(exc).__name__}: {exc}"
    finally:
        s.close()
secrets = sorted(k for k in os.environ if any(m in k.upper() for m in ("TOKEN", "KEY", "PASSWORD")))
out["env"] = {"names_with_secret_words": len(secrets), "total": len(os.environ), "sample": secrets[:5]}
print("PROBE " + json.dumps(out))
"""

#: Runs inside the container as the gateway's user. Loads the plugin's sandbox module
#: by path (the way aura-selfext does), runs the probe through run_sandboxed, then
#: the same probe through bash, then the reset through the environment class.
DRIVER = r"""
import importlib.util, json, os, subprocess, sys
sys.path.insert(0, "/opt/hermes")  # the Hermes checkout: BaseEnvironment for pass 3
probe, targets = sys.argv[1], sys.argv[2]
source = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "plugins", "aura-bwrap", "sandbox.py")
spec = importlib.util.spec_from_file_location("jarvis_bwrap_sandbox", source)
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)
report = {"bwrap": sandbox.bwrap_path(), "uid": os.getuid()}

# 1. sandboxed
try:
    done = sandbox.run_sandboxed(["python3", "-c", probe, targets], cwd="/", timeout=60)
    report["sandboxed"] = {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr[-800:]}
    report["argv"] = sandbox.describe(sandbox.bwrap_argv("/", "<scratch>", command=["python3", "-c", "<probe>"]))
except Exception as exc:
    report["sandboxed"] = {"error": f"{type(exc).__name__}: {exc}"}

# 2. the control: the same probe, in the gateway's own world
done = subprocess.run(["bash", "-c", 'python3 -c "$0" "$1"', probe, targets], capture_output=True, text=True, timeout=60)
report["control"] = {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr[-800:]}

# 3. the reset, through the environment class the gateway uses
try:
    Env = sandbox.environment_class()
    a = Env(cwd="/opt/data/work", timeout=30, task_id="drill-a")
    # The marker goes into HOME, which is the scratch: the cwd is the shared work
    # directory (bound on purpose, see the runbook), and a file there is *meant*
    # to outlive the task.
    a.execute("echo marker > \"$HOME/marker.txt\" && ls -la \"$HOME\"")
    scratch_a = str(a.scratch)
    marker_seen_by_gateway = os.path.exists(os.path.join(scratch_a, "marker.txt"))
    a.cleanup()
    b = Env(cwd="/opt/data/work", timeout=30, task_id="drill-b")
    listing = b.execute("ls -A \"$HOME\"; test -e \"$HOME/marker.txt\" && echo MARKER-PRESENT || echo no-marker")
    b.cleanup()
    report["reset"] = {"scratch_a": scratch_a, "marker_written": marker_seen_by_gateway,
                       "scratch_a_gone": not os.path.exists(scratch_a),
                       "task_b_output": listing.get("output", "")[-400:]}
except Exception as exc:
    report["reset"] = {"error": f"{type(exc).__name__}: {exc}"}

# 4. execute_code, through Hermes's own tool: which world does the model's Python run in?
SEALED = ["/opt/data/state/kill-switch.json", "/opt/data/state/approval-grants.json",
          "/opt/data/state", "/opt/data/config.yaml"]
try:
    os.environ.setdefault("TERMINAL_ENV", os.environ.get("AURA_TERMINAL_BACKEND")
                          or os.environ.get("JARVIS_TERMINAL_BACKEND") or "aura-bwrap")
    try:
        import hermes_cli.plugins as hermes_plugins
        hermes_plugins.discover_plugins()
    except Exception as exc:  # the provider may already be registered; the run below says
        report["plugins"] = f"{type(exc).__name__}: {exc}"[:200]
    from tools import code_execution_tool
    code = ("import json, os\n"
            f"print('CODE ' + json.dumps({{p: os.path.exists(p) for p in {SEALED!r}}}))\n")
    answer = json.loads(code_execution_tool.execute_code(code, task_id="drill-code"))
    seen = [l for l in str(answer.get("output", "")).splitlines() if l.startswith("CODE ")]
    report["code"] = {"backend": os.environ["TERMINAL_ENV"], "status": answer.get("status"),
                      "seen": json.loads(seen[-1][5:]) if seen else None,
                      "error": str(answer.get("error", ""))[-300:]}
except Exception as exc:
    report["code"] = {"error": f"{type(exc).__name__}: {exc}"}
print("DRIVER " + json.dumps(report))
"""

#: Where each destination comes from. Fixed addresses went stale (a router, a tailnet
#: host from before Tailscale went, a public resolver the container cannot reach), and a
#: control that reaches nothing makes every run INCONCLUSIVE (T-1027); so the control's
#: service hosts are the ones the container is configured to use, and the LAN target is
#: the address its ports are published on (``TAILSCALE_IP``: the name is kept).
SERVICE_URLS = (("model host", "LLM_URL"), ("search host", "SEARXNG_URL"))
_ENV_NAMES = ("TAILSCALE_IP",) + tuple(v for _, v in SERVICE_URLS)
PUBLIC = ("public internet 1.1.1.1:53", "1.1.1.1", 53)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def choose_targets(env: dict[str, str], gateway_ip: str, resolve) -> tuple[list[tuple[str, str, int]],
                                                                        dict[str, tuple[str, ...]]]:
    """Pure: the destinations and, per class, the names the control must reach one of.

    ``resolve(host)`` returns an IP or None; a service URL that does not parse or resolve
    is left out (the class then rests on the public control alone, and the verdict says
    so), and a URL's credentials or path are never kept. Does not check any of them is up.
    """
    targets = [
        ("hermes serve 127.0.0.1:9119", "127.0.0.1", 9119),
        ("api server 127.0.0.1:8642", "127.0.0.1", 8642),
        ("docker host :8080", gateway_ip, 8080),
    ]
    lan = ["docker host :8080"]
    if env.get("TAILSCALE_IP"):
        targets.append(("LAN bind address :8643", env["TAILSCALE_IP"], 8643))
        lan.append("LAN bind address :8643")
    service = []
    for name, var in SERVICE_URLS:
        try:
            parts = urlsplit(env.get(var, ""))
            host, port = parts.hostname, parts.port or _DEFAULT_PORTS.get(parts.scheme)
        except ValueError:
            continue
        ip = host and (host if _is_ip(host) else resolve(host))
        if ip and port:
            targets.append((name, ip, port))
            service.append(name)
    targets.append(PUBLIC)
    return targets, {
        "loopback": ("hermes serve 127.0.0.1:9119", "api server 127.0.0.1:8642"),
        "lan/host": tuple(lan),
        "internet/service": (*service, PUBLIC[0]),
    }


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _targets(container: str) -> tuple[list[tuple[str, str, int]], dict[str, tuple[str, ...]]]:
    """Resolved on the host side: the sandbox has no resolver, and a probe that failed
    at the name lookup would read as "unreachable" without opening a socket."""
    def resolve(host: str) -> str | None:
        found = (_run(["docker", "exec", container, "getent", "hosts", host]) or "").split()
        return found[0] if found else None

    # Only the names wanted are kept; nothing else in the environment is read on or printed.
    raw = _run(["docker", "exec", container, "env"]) or ""
    env = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    return choose_targets({k: v for k, v in env.items() if k in _ENV_NAMES},
                          resolve("host.docker.internal") or "172.17.0.1", resolve)


def _run(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _last_json(text: str, tag: str) -> dict | None:
    for line in reversed(text.strip().splitlines()):
        if line.startswith(tag + " "):
            return json.loads(line[len(tag) + 1:])
    return None


def _show(title: str, probe: dict) -> None:
    print(f"\n{title}")
    for path, result in probe["file"].items():
        print(f"    {path:38} {result}")
    for name, result in probe["net"].items():
        print(f"    {name:38} {result}")
    env = probe["env"]
    print(f"    {'environment':38} {env['names_with_secret_words']} secret-looking names of "
          f"{env['total']} (e.g. {env['sample']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--container", default="jarvis", help="the gateway container (default: jarvis)")
    parser.add_argument("--probe-here", action="store_true",
                        help="run the probe on this host, unsandboxed, to show what it reports")
    args = parser.parse_args()

    if args.probe_here:
        targets = json.dumps(_targets(args.container)[0]) if shutil.which("docker") else json.dumps([PUBLIC])
        done = subprocess.run([sys.executable, "-c", PROBE, targets], capture_output=True, text=True, timeout=60)
        probe = _last_json(done.stdout, "PROBE")
        if probe is None:
            print(f"the probe did not report: {done.stderr[-400:]}")
            return 3
        _show("probe on this host (no sandbox — a demonstration of the probe, not a verdict)", probe)
        return 0

    if not shutil.which("docker"):
        print("NOT RUNNABLE: no docker on this machine; the argv pins in "
              "hermes/plugins/aura-bwrap/tests still ran")
        return 3
    if _run(["docker", "inspect", args.container]) is None:
        print(f"NOT RUNNABLE: no container named {args.container}")
        return 3

    targets, classes = _targets(args.container)
    print(f"Destinations ({len(targets)}), resolved on the host:")
    for name, host, port in targets:
        print(f"  - {name:38} -> {host}:{port}")

    done = subprocess.run(
        ["docker", "exec", "-i", "-u", "hermes", args.container, "/opt/hermes/.venv/bin/python",
         "-", PROBE, json.dumps(targets)],
        input=DRIVER, capture_output=True, text=True, timeout=300,
    )
    report = _last_json(done.stdout, "DRIVER")
    if report is None:
        print(f"NOT RUNNABLE: the in-container driver did not report (exit {done.returncode}):\n"
              f"{done.stderr[-1200:]}\n{done.stdout[-400:]}")
        print("Is the deployed image built from a tree that carries hermes/plugins/aura-bwrap "
              "and bubblewrap (deploy/Dockerfile)?")
        return 3

    print(f"\nbwrap in the container: {report['bwrap'] or 'MISSING'}; driver uid {report['uid']}")
    if report.get("argv"):
        print(f"argv: {report['argv']}")

    sandboxed = report["sandboxed"]
    if "error" in sandboxed:
        print(f"\nFAIL: the sandboxed pass could not run: {sandboxed['error']}")
        return 1
    sealed = _last_json(sandboxed["stdout"], "PROBE")
    if sealed is None:
        print(f"\nFAIL: the sandboxed probe did not report (exit {sandboxed['exit']}): "
              f"{sandboxed['stderr']}")
        return 1
    _show("[1/4] sandboxed (run_sandboxed) — nothing may be reached", sealed)

    control = _last_json(report["control"]["stdout"], "PROBE")
    if control is None:
        print(f"\nINCONCLUSIVE: the control probe did not report: {report['control']['stderr']}")
        return 2
    _show("[2/4] the control (bash -c in the gateway's world) — the probe must work here", control)

    reset = report["reset"]
    print("\n[3/4] reset between tasks")
    if "error" in reset:
        print(f"    FAIL: {reset['error']}")
        return 1
    print(f"    task A scratch: {reset['scratch_a']}; marker written: {reset['marker_written']}; "
          f"gone after cleanup: {reset['scratch_a_gone']}")
    print(f"    task B sees: {reset['task_b_output'].strip()!r}")

    # The kill switch, the grants and config.yaml decide whether the approval gate runs at
    # all. The matrix can only read execute_code's Python as text, so a path built at run
    # time gets past it (T-0686): what keeps the model's code from those files is that
    # Hermes runs execute_code inside this sandbox whenever the backend is not `local`.
    # That is Hermes's dispatch, not ours, and a release could move it (T-0852).
    code = report.get("code") or {}
    print("\n[4/4] execute_code, through Hermes's own tool")
    print(f"    backend: {code.get('backend')}; status: {code.get('status')}")
    for path, there in (code.get("seen") or {}).items():
        print(f"    {path:38} {'VISIBLE' if there else 'not there'}")
    if code.get("seen") is None:
        print(f"\nINCONCLUSIVE: execute_code did not report ({code.get('error')}); the sandbox "
              "passes above say nothing about where the model's Python runs.")
        return 2
    if any(code["seen"].values()):
        print("\nFAIL: execute_code can see " + ", ".join(p for p, t in code["seen"].items() if t)
              + (": the backend is `local`, so the model's Python is the gateway (ADR-0045's "
                 "way back; only the matrix's text check and the ask stand there)."
                 if code.get("backend") == "local" else
                 ": Hermes ran it outside the sandbox although the backend is not `local`."))
        return 1

    print()
    leaks = [n for n, r in sealed["net"].items() if r == "REACHED"]
    leaks += [p for p, r in sealed["file"].items() if r == "READ"]
    if sealed["env"]["names_with_secret_words"]:
        leaks.append(f"{sealed['env']['names_with_secret_words']} secret-looking env names")
    if leaks:
        print("FAIL: the sandbox reached " + ", ".join(leaks))
        return 1
    if not reset["marker_written"] or not reset["scratch_a_gone"] or "MARKER-PRESENT" in reset["task_b_output"]:
        print("FAIL: the reset did not hold (see [3/4])")
        return 1

    missing_classes = []
    if control["file"]["/opt/data/config.yaml"] != "READ":
        missing_classes.append("file")
    for cls, names in classes.items():
        if not any(control["net"].get(n) == "REACHED" for n in names):
            missing_classes.append(cls)
    if not control["env"]["names_with_secret_words"]:
        missing_classes.append("environment")
    if missing_classes:
        print("INCONCLUSIVE: the control reached nothing in " + ", ".join(missing_classes)
              + ", so this run proves the probe was refused, not that the sandbox refused it. "
              "Check that those destinations are up.")
        return 2
    print(f"PASS: {len(sealed['net'])} destinations, the config file and the environment were "
          f"refused in the sandbox; the control reached every class; the reset held; "
          f"execute_code ran behind the same fence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
