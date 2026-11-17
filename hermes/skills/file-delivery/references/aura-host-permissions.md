# jarvis host — file paths and permissions

Observed 2026-09-02 while delivering a file into a root-owned directory.

## Identity

- Agent runs as `hermes` (uid 10000, gid 10000).
- No `sudo` binary; `su root` prompts for a password and fails; `CapEff=0` (no capability escalation; `setpriv` exists but is useless without caps).
- Practical conclusion: root-owned paths are unreachable from the agent; one probe is enough, do not loop escalation attempts.

## Paths

- Writable: `/opt/data/work` (hermes:hermes, 755) — the fallback landing zone.
- Observed not writable: `/opt/data/work/code-write-001` (root:root, 755) — created outside the agent; treat as user/automation-owned.

## write_file mechanics

- Stages a `.hermes-tmp.<n>` file in the target directory, then renames; directory write permission is the gate, and permission errors name the tmp file, not the final path.

## Fix (user side)

- `chown hermes:hermes <dir>` or `chmod 775 <dir>` from a root shell on the host makes the requested path writable.
