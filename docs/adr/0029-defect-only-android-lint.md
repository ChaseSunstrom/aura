# ADR-0029: The Android lint gate is defect-only

- **Status:** Accepted (Phase 2)
- **Reference:** the v2 plan §10 (D29)

## Context

Python is linted by ruff under a deliberately defect-only ruleset (`ruff.toml`: F, E9, B006/B008/B023) so that a finding is a defect, never a style opinion. The Android tree had no equivalent gate. ktlint's full `standard` ruleset and detekt's default config are mostly style, complexity and naming rules; run as-is on this corpus they produce hundreds of findings with no defect behind them, and a gate whose findings are noise is a gate people learn to ignore. One rule in particular, detekt's `SwallowedException`, fires 194 times on the Android code's pervasive and intentional fallback-return pattern (`catch (e: Exception) { return Outcome.X }`), where discarding the exception and returning a typed outcome *is* the handling.

## Decision

The Android lint gate is defect-only, the same call `ruff.toml` makes for Python.

- ktlint runs under `android-app/.editorconfig` with the whole `standard` ruleset disabled and only the defect-shaped rules on: `no-unused-imports`, `no-wildcard-imports`, `no-empty-file`, `no-empty-class-body`. The `filename` rule stays off: renaming a file whose name describes its role is churn, not a fix.
- detekt runs under `android-app/detekt.yml` with style, complexity, naming, comments, formatting and performance off, and potential-bugs, coroutines, empty-blocks and exceptions on. `SwallowedException` is off; a genuinely error-dropping empty `catch {}` is still caught by `EmptyCatchBlock`.
- Android `lintDebug` keeps `abortOnError`, with `UnusedResources` promoted to an error: unused resources are deleted, not suppressed.
- Both configs are read verbatim by the standalone CLIs and by `gradle :app:ktlintCheck` / `gradle :app:detekt`, so the local run and the CI run cannot disagree.

The gate is `.gitlab/ci/quality-android.yml`'s `kotlin-lint` job, mirrored by `make lint-android` and a `.pre-commit-config.yaml` block.

## Consequences

A red `kotlin-lint` is a defect to fix, not a formatting quarrel. Style drift in Kotlin is not caught by CI, on purpose, as it is not for Python. Turning a rule on later is a change to this decision and must be driven to zero on the whole corpus first, so the gate never goes red on a rule nobody chose. The OWASP dependency scan in the same file skips itself visibly without an `NVD_API_KEY`; it is present, not blocking.
