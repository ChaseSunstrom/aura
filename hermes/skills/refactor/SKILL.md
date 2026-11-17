---
name: refactor
description: "Use when refactoring code that keeps behaviour identical."
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [refactor, behavior-preservation, differential-test, python, code-quality]
    related_skills: [test-driven-development, systematic-debugging]
---

# Behavior-Preserving Refactor

Refactor existing code (extract duplicated logic, rename, restructure) with a *proof* that behaviour is unchanged — a differential test against the pre-refactor code — not a claim.

## When to Use

- "Refactor X to remove duplicated logic, keeping behaviour identical"
- Any change where the user names preserved behaviour as the acceptance criterion
- Don't use for: bug fixes (behaviour changes by definition), greenfield code, or when the target file does not actually contain the code described (that is a setup problem — see Pitfalls)

## Procedure

1. **Verify the target before promising.** `read_file` the exact path; confirm it is non-empty and contains the code the task describes. Probe writability of the directory with `touch <dir>/.wtest && rm <dir>/.wtest` before attempting any write. If the file is empty, missing, or unwritable: stop, report exactly what was found, and do NOT proceed to step 2 on invented code.
2. **Save the oracle.** Copy the pre-refactor file verbatim into a staging dir (e.g. `/opt/data/work/<task>-stage/original.py`). It is the ground truth for the differential test.
3. **Extract.** Move the duplicated logic into one private helper (e.g. `_validate`); every call site delegates to it. Public signatures, return types, exception types, and exception messages stay exactly as they were.
4. **Differential test.** Run `scripts/diff_behavior.py original.py refactored.py <func> [<func> ...]` (add `--cases` for domain-specific inputs beyond the default probes). Required: exit 0 and "N comparisons, 0 mismatches".
5. **Report.** State the comparison count and what was extracted. If the real target was unfixed, say where the verified artifacts landed and what the user must fix (permissions or missing source).

## Pitfalls

- **Fabrication trap.** If the target is empty or unreadable, inventing an "original" and presenting the result as a completed refactor is the failure mode. Report the empty file; if asked to keep working anyway, build the described pattern in a writable staging dir and label it as staged, not as the real file.
- **Exception messages are behaviour.** Compare the exception type AND `str(exc)`, not just the class — two functions that both raise ValueError with different text are not identical.
- **Don't silently normalise.** `int(" 80 ")`, `int("080")`, `int("+5")` all parse — a refactor must not "fix" inputs the original accepted.
- **Permission surprises.** Task dirs under `/opt/data/work` can be root:root with no sudo for the agent user; `write_file` fails mid-write. The 5-second `touch` probe in step 1 avoids the wasted round-trip.
- **Keep the public API identical.** Only the private helper and internal calls change; public function names and signatures must match between original and refactored.

## Verification

- `scripts/diff_behavior.py` exits 0 with "0 mismatches" across all functions and cases
- Public function names and signatures unchanged between original and refactored
- If the real target file could not be fixed, the staging dir path and the user fix required are stated in the reply
