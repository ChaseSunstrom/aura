# ADR-0045: The model's shell runs under bubblewrap inside the container, as a plugin terminal backend

- **Status:** Accepted (2026-09-18), interim until [ADR-0025](0025-sandbox-disposable-ct.md)'s CT exists
- **Comes from:** the completion brief row 1.5 (the sandbox, proven), ledger B8, research of 2026-09-18
- **Builds on:** [ADR-0025](0025-sandbox-disposable-ct.md) (the disposable CT, owner-blocked), [ADR-0024](0024-governed-self-extension.md) (the self-extension loop), [ADR-0026](0026-approval-matrix-audit-log.md) (the matrix decides whether a command runs; this decides where)

## Context

`terminal.backend: local` was the deployed setting. Hermes runs that backend's commands as
children of the gateway process: the model's `terminal` tool and every `execute_code` run
inherited the container's environment — `HERMES_API_KEY`, `HASS_TOKEN`, the GitLab token, the
DAV password, everything the host `.env` hands the container — and saw the whole of
`/opt/data`: `config.yaml`, the memories, the notes, the audit log, the sessions. The
approval matrix gates *which* commands run; it never claimed to contain one that runs.

The CT of ADR-0025 needs the owner (Proxmox credentials, a CT). What Hermes v2026.8.31 offers
instead, measured rather than read from its docs, which have no `sandbox:` key: the switch is
`terminal.backend`, with built-in values `local`, `docker`, `singularity`, `modal`,
`managed_modal`, `daytona`, `vercel_sandbox`, `ssh`, and a plugin extension point —
`PluginContext.register_terminal_environment_provider` with the `TerminalEnvironmentProvider`
ABC (`agent/terminal_env_provider.py`) and a duck-typed environment (`execute()`, `cleanup()`;
`BaseEnvironment` in `tools/environments/base.py` supplies the session snapshot, cwd tracking,
timeouts and interrupts around one `_run_bash` spawn point). `execute_code` uses the same
backend through file-based RPC under `env.get_temp_dir()`.

Probed the same day, in a throwaway `debian:12-slim` / `python:3.12-slim-bookworm` on this
host's Docker 29.8:

- bubblewrap 0.8.0 runs inside a Docker container once the container's seccomp profile allows
  `unshare` and the mount calls; Docker's default refuses `unshare` to a container without
  `CAP_SYS_ADMIN` (`unshare -Ur true` → "Operation not permitted").
- `--proc /proc` (a fresh procfs) is refused inside Docker (its masked `/proc` paths make procfs
  "not fully visible"); binding the container's `/proc` read-only works, and from the sandbox's
  user namespace a same-uid read of the gateway's `/proc/<pid>/environ` or `root` is
  `Permission denied`; `cmdline` is readable, as it is for any uid.
- python starts with only `/usr` bound (no `/etc` at all); `/etc/alternatives` is needed for
  Debian's `awk` and `pager`.
- a `--tmpfs /tmp` breaks `execute_code`, whose RPC files must outlive one `execute()`.

## Decision

**A plugin terminal backend, `aura-bwrap`, wraps every command in bubblewrap inside the
`jarvis` container.** `bwrap --unshare-all --unshare-net --die-with-parent --new-session
--clearenv`, with `/usr` and the merged-usr names, `/opt/hermes`, `/etc/alternatives` and
`/etc/python3*` read-only, the container's `/proc` read-only, a fresh `/dev`, and one writable
path: the task's scratch under `terminal.sandbox_dir` (`/opt/data/sandboxes/<task>/`), bound at
its own name, its `tmp/` as the sandbox's `/tmp`. The environment is `PATH`, `HOME`, `LANG`,
`TERM`. Nothing else of `/opt/data` is bound. `bwrap_argv` is a pure function and the plugin's
tests pin every fence; `run_sandboxed` is the one-shot form other plugins use — the selfext
gate's checkers run through it with the checkout bound writable, git stays in the gateway.

**The provider says what it is:** `is_container` (host cwds are sanitised, file tools resolve
through the environment), not `is_remote` (same kernel, same machine), `skip_container_guards`
(nothing of the host is writable but the scratch), `session_isolated_when_nonpersistent` with
`container_persistent: false` (each session its own scratch). `cleanup()` deletes the scratch:
that is the reset, run by Hermes after `lifetime_seconds` idle or at the session's end.

**Missing bwrap is a refusal, never a fallback.** The provider's `is_available` is false without
the binary, so Hermes hides the terminal; `create_environment` raises; `run_sandboxed` raises
`BwrapMissing`. A Hermes without the extension point leaves `terminal.backend: aura-bwrap`
naming nothing, and Hermes answers "Unknown environment type" rather than running locally.

**The container gets `deploy/seccomp/jarvis.json`**: Docker's default profile (fetched from
`github.com/moby/profiles`, `seccomp/default.json`) with `clone`, `clone3`, `unshare`,
`setns`, `mount`, `umount2` and `pivot_root` allowed unconditionally and removed from the
`CAP_SYS_ADMIN` group. The kernel still gates each against the caller's own user namespace:
`sethostname`, `mount` as the container's root, `syslog` and `reboot` stay refused (probed).
Never `seccomp=unconfined`. `scripts/deploy.sh` copies `deploy/` whole, so the relative path
holds on the host.

**The switch is env-owned and pinned.** `terminal.backend` is a schema leaf
(`AURA_TERMINAL_BACKEND`, default `aura-bwrap` in the compose file, `advanced`), rendered
quoted into the template and listed as `=terminal.backend` in `CONFIG_LEAVES`, because every
deployed volume already carries `backend: local` and an unpinned leaf never replaces a present
one. `AURA_TERMINAL_BACKEND=local` in the host `.env` is the way back;
`terminal.container_persistent` and `terminal.sandbox_dir` are plain new leaves.

**The drill is the proof.** `scripts/verify/sandbox-drill.py` (§7 of
`scripts/egress-audit.sh --live`) runs the probe through the plugin's own `run_sandboxed` in the
deployed container, the same probe under plain `bash -c` as the positive control, and the reset
through the environment class; INCONCLUSIVE when the control reaches nothing, "not runnable"
on an image without the plugin. Its fourth pass runs Hermes's own `execute_code` and fails if
the script can see the kill switch, the approval grants or `config.yaml` (T-0852): the approval
matrix reads the model's Python only as text, so the sandbox is what keeps a path built at run
time from the files that decide whether the gate runs. That holds only while the backend is
`aura-bwrap`; on `local` the pass fails, and only the text check and the ask are left.

## Alternatives, ranked

1. **The disposable CT over `ssh` (ADR-0025).** Another kernel, a snapshot to reset to,
   resources of its own. Owner-blocked; this decision is the interim until it lands, and
   stands down when it does.
2. **This: bubblewrap as a plugin backend.** Same kernel and cgroup, but no network, no
   environment, no home, a real reset, no daemon, no socket, no new image, and a pure argv
   to pin. Costs one seccomp profile and one apt package.
3. **The `docker` backend.** A real container per task, but it needs a Docker CLI and the
   daemon socket mounted into the gateway — the image deletes the CLI and mounts no socket on
   purpose, because a socket is root on the host. Re-adding it to gain a sandbox would give the
   model the host.
4. **The `singularity` backend.** No socket, but a second container runtime and an image to
   maintain inside the image, for the same kernel as bubblewrap.
5. **Modal, Daytona, Vercel.** Cloud sandboxes: a credential, an egress, and the model's
   code on someone else's machine. Against the local-first line of this project.

## Consequences

- **Good.** The model's shell holds no credential and sees nothing of the home; the selfext
  gate no longer runs model-written tests in the gateway process (ADR-0024's open end).
- **Good.** The switch, the profile, the leaves and the drill are all pinned by tests that need
  no Docker, and the drill needs nothing but the deployed container.
- **Bad.** Same kernel, same cgroup: a namespace escape or a fork bomb reaches the gateway.
  The CT fixes both; nothing here pretends to.
- **Bad.** The network is gone from the shell, not gated: `curl`, `pip`, `yt-dlp` and `git
  push` from `terminal` fail. The web toolset, the media tools and `selfext_open_mr` are the
  paths; anything that only ever worked through the shell's network has to be re-homed.
- **Shared on purpose.** `terminal.cwd` (`/opt/data/work`) is bound writable at its own path and is
  the shell's cwd: the goal gates, the bench's checkers and the file-delivery skill read the agent's
  files there from the gateway side, and Hermes' command prelude `cd`s into it — the first cut bound
  only the scratch and every command failed on that `cd` (pipeline 350). It holds no secret and no
  state; the rest of the home stays out.
- **Not covered.** Every tool that is not the shell — `write_file`, the browser, the plugins —
  runs in the gateway as before, under the matrix. Process names and command lines of the
  container are visible from inside. The profile is derived from the moby default of
  2026-09-18; a Docker that ships a stricter builtin later would not be reflected until the
  file is re-derived.
