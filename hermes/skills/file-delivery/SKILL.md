---
name: file-delivery
description: Use when saving a file to a user-specified path.
---

# File delivery — write to a requested path

When the user asks "save X to /path" (a number from a script, a report, an export), the acceptance criteria are three: the file exists at the requested path, its content matches what was asked, and the result is stated in the reply. A miss on any one must be reported, not hidden.

## Procedure

1. **Check before writing.** `ls -ld <target_dir>` and `id` in one call. write_file stages a `.hermes-tmp.<n>` file in the target directory and renames it, so *directory* writability (not file writability) gates the write. A root-owned 755 dir with a non-root agent will fail — discover that by checking, not by failing.
2. **Write and verify.** Use write_file (confirm `verified:true`) or terminal for raw bytes. For user-visible paths, read the exact target back once.
3. **Compute from execution.** For "compute and save" tasks, run the script and take the value from real output — never from head-math.
4. **If the path is not writable:**
   - Probe for root escalation *once* (sudo present? `id -u`?). On the jarvis host there is none (see reference); a second probe is wasted.
   - Write the artifact to the closest writable location (jarvis host: `/opt/data/work`), keeping the user's exact filename. Verify it.
   - Report: requested path, actual path, reason (owner/mode), the one-line fix the user can apply (`chown hermes:hermes <dir>`), and the actual result (the number, whatever was asked).
5. **Never** claim the file is at the requested path when it isn't, and never fabricate file contents to close the gap.

## Pitfalls

- "Permission denied" on `.hermes-tmp.<pid>` from write_file = target dir not writable; don't retry the same write.
- A successful fallback write is not task-complete; the reply must carry both requested and actual paths.
- A value that is right in the code but never run is not a verified result.

## Environment

- jarvis host identity and ownership patterns: `references/jarvis-host-permissions.md`.
