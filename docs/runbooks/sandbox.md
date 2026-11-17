# Runbook: the model's shell (the bubblewrap sandbox)

Where a `terminal` command and an `execute_code` run go. Since [ADR-0045](../adr/0045-bubblewrap-sandbox.md)
that is a bubblewrap sandbox inside the `jarvis` container, one per task, provided by the
`aura-bwrap` plugin (`hermes/plugins/aura-bwrap/`). Before it, `terminal.backend: local`
ran every command in the gateway process itself, with every token of the host `.env` in
its environment and `/opt/data` one `cat` away (found 2026-09-18).

- **Backend:** `terminal.backend: aura-bwrap` in `config.yaml`, pinned on every start from
  `AURA_TERMINAL_BACKEND` in the host `.env` (default `aura-bwrap`, set in the compose file)
- **Scratch:** `terminal.sandbox_dir: /opt/data/sandboxes/<task-id>/`, the task's cwd and its
  `HOME`; its `tmp/` is the sandbox's `/tmp`
- **Needs:** `bubblewrap` in the image (`deploy/Dockerfile`) and `deploy/seccomp/jarvis.json` on the
  `jarvis` service (`security_opt`), or bwrap's `unshare` is refused and every command fails

## What is contained

Each command is `bwrap --unshare-all --unshare-net --die-with-parent --new-session --clearenv …
-- /bin/bash -c <command>` (`sandbox.bwrap_argv`, pinned by the plugin's tests):

| Inside the sandbox | What it is |
| --- | --- |
| `/usr` `/bin` `/lib` `/lib64` `/sbin` `/opt/hermes` | read-only: bash, python3 (the Hermes venv), the checkers, node |
| `/etc/alternatives` `/etc/python3*` | read-only; nothing else of `/etc` (no passwd, hosts, resolver) |
| `/proc` | the container's, read-only; a same-uid read of the gateway's `environ` or `root` is refused |
| `/dev` | a fresh, minimal one |
| `/opt/data/sandboxes/<task>/` | writable, bound at its own name; `tmp/` of it is `/tmp`; `HOME` |
| `/opt/data/work` (`terminal.cwd`) | writable, bound at its own name, the shell's cwd — shared with the gateway on purpose: the goal gates, the bench's checkers and the file-delivery skill read the agent's files there, and Hermes' command prelude `cd`s into it |
| `/opt/data/files` | writable, bound at its own name — the person's files: the browser's downloads and delivered files; no secret, no state |
| network | none: its own empty network namespace, so `127.0.0.1:9119`, `:8642`, the LAN and the internet are all unreachable |
| environment | `PATH`, `HOME`, `LANG`, `TERM` and nothing else |

Not bound: the rest of `/opt/data` (config.yaml, the memories, notes, sessions, the audit log, the
override file), `/opt/jarvis`, `/etc`, `/root`.

The self-extension gate (`selfext_verify`) runs its checkers through the same fence with
its checkout bound writable (`run_sandboxed`); git stays in the gateway because a push
needs the network.

## What is not contained

- **Same kernel, same cgroup.** A kernel bug that lets a user namespace escape escapes
  this. Memory and CPU are the container's 6 GB and cores: a fork bomb inside the sandbox
  costs the gateway, not just the task. The disposable CT of
  [ADR-0025](../adr/0025-sandbox-disposable-ct.md) is what fixes both; this is the interim.
- **Process names.** `/proc` is the container's, so `ps` inside the sandbox lists the
  gateway's processes and their command lines. Their environment, open files and root are
  refused (measured 2026-09-18).
- **Tools that are not the shell.** `write_file`, `read_file`, `web_fetch`, the browser and
  every plugin tool run in the gateway as before; the approval matrix is what governs them.
  A file the model writes in its scratch has the same path on the gateway side, so an
  artifact publish or a `read_file` of it works while the scratch exists.
- **The network from the shell is gone**, not gated: `curl`, `pip install`, `yt-dlp`,
  `git push` from `terminal` fail. The web toolset, the media tools and `selfext_open_mr`
  are the sanctioned paths.

## The reset

`container_persistent: false`: each session gets its own scratch, and Hermes retires an
environment after `terminal.lifetime_seconds` (300 s) idle or when the session ends. The
plugin's `cleanup()` deletes the scratch — that is the reset. A file the model wants to
keep has to leave through a tool (an artifact, `write_file` to `/opt/data/files`) before then.

## The drill

```bash
python3 scripts/verify/sandbox-drill.py          # from the host; PASS / FAIL / INCONCLUSIVE / not runnable
bash scripts/egress-audit.sh --live               # §7 runs it beside the coding sandbox's drill
```

Through `docker exec -u hermes jarvis`, it runs a probe under `run_sandboxed` (every reach
must be refused, each attempt printed with its exception), the same probe under plain
`bash -c` (the positive control: at least one target per class must be reached, or the run is
INCONCLUSIVE — a broken probe and a sealed sandbox look the same), and the reset (task A
writes a marker, cleanup, task B sees none). It cannot pass on an image that does not carry
the plugin: it says "not runnable" (exit 3) and the audit reports a SKIP, never a PASS.

Its destinations come from the container's environment, not from fixed addresses: the
control's service hosts are the ones in `LLM_URL` and `SEARXNG_URL` (resolved from inside
the container), and the LAN target is `TAILSCALE_IP:8643`, the address the ports are
published on. A URL that is unset or does not resolve is left out, and the INCONCLUSIVE
line names the class the control could not reach (T-1027).

## Falling back to the gateway process

```bash
# on the JARVIS host
echo 'AURA_TERMINAL_BACKEND=local' >> /opt/jarvis-host/.env
cd /opt/jarvis-host && docker compose up -d --force-recreate jarvis jarvis-adapter
```

The entrypoint re-pins `terminal.backend` from the variable on every start (it is a
`=`-leaf in `CONFIG_LEAVES`), so editing the volume's `config.yaml` does not hold across a
restart; the variable does. Remove the line and recreate to come back. Editing it from the
settings console asks (the `settings_set` row of the matrix) and applies after a restart.

## Failures

| Symptom | Cause | Fix |
| --- | --- | --- |
| every `terminal` call answers `Unknown environment type: aura-bwrap` | the plugin is not loaded (missing from `plugins.enabled`, or its `register()` raised — see `docker compose logs jarvis`) | `hermes plugins list` in the container; the gateway never falls back to `local` on its own |
| `bwrap: Can't mount proc on /newroot/proc` or `unshare: Operation not permitted` | the container runs without `deploy/seccomp/jarvis.json` (an old compose file on the host, or `security_opt` dropped) | `docker inspect jarvis --format '{{.HostConfig.SecurityOpt}}'` must show the profile; redeploy |
| the terminal toolset is missing from the model's tools | `bwrap` is not in the image: the provider reports `needs_setup` and Hermes hides the toolset rather than run commands locally | the image must be built from a Dockerfile with `bubblewrap` on the apt line |
| a file the model made "disappeared" | the environment was retired (300 s idle) and the scratch with it | expected; the model keeps results through tools, not in the scratch |
