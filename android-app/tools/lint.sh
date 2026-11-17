#!/usr/bin/env bash
# The defect-only Android lint gate (V2_PLAN §10 D29). Run from android-app/:
#
#   bash tools/lint.sh
#
# Three checks, each blocking, each at zero:
#
#   ktlint       reads android-app/.editorconfig (whole `standard` ruleset off,
#                only defect rules on).
#   detekt       reads android-app/detekt.yml (style/complexity/naming/etc off,
#                potential-bugs/coroutines/empty-blocks/non-generic-exceptions on).
#   lintDebug    Android lint; UnusedResources is promoted to an error in
#                app/build.gradle.kts, so an unused drawable/string fails here.
#
# Why a script and not the Gradle ktlint/detekt plugins: the house lint stance is
# CLI-driven and defect-only (ruff, not a build plugin). ktlint and detekt run as
# the same pinned CLIs the scout measured with, reading the same config files a
# Gradle plugin would — so adopting org.jlleitschuh.gradle.ktlint /
# io.gitlab.arturbosch.detekt later is a drop-in, and until then no unverifiable
# plugin is added to the shared build every other Android task depends on.
#
# The CLIs are located by env with dev-box fallbacks, so the same script runs on
# a laptop and on the CI runner:
#
#   KTLINT              ktlint executable          (default: `ktlint` on PATH,
#                                                    else ~/.local/bin/ktlint)
#   DETEKT_JAR          detekt fat jar             (default: ~/.local/bin/detekt-cli-all.jar)
#   LINT_SKIP_GRADLE=1  run ktlint+detekt only, skip lintDebug (for a box where
#                       Gradle must not run yet — e.g. during a latency benchmark)
set -euo pipefail

cd "$(dirname "$0")/.."   # android-app/

KTLINT_BIN="${KTLINT:-}"
if [ -z "$KTLINT_BIN" ]; then
    if command -v ktlint >/dev/null 2>&1; then
        KTLINT_BIN="$(command -v ktlint)"
    else
        KTLINT_BIN="$HOME/.local/bin/ktlint"
    fi
fi
DETEKT_JAR_PATH="${DETEKT_JAR:-$HOME/.local/bin/detekt-cli-all.jar}"

fail=0

echo "=== ktlint (android-app/.editorconfig) ==="
if [ ! -x "$KTLINT_BIN" ] && [ ! -f "$KTLINT_BIN" ]; then
    echo "ERROR: ktlint not found at '$KTLINT_BIN' (set \$KTLINT)"; exit 2
fi
# ktlint discovers .editorconfig from the working directory upward.
# The car modules (android-app/car-*) are left out on purpose: they are the
# folded-in Headway stack (ADR-0047) with its own style, their defects are
# caught by their own JVM suites in apk:build, and listing them here would
# fail the gate on style nobody means to change.
"$KTLINT_BIN" "app/src/main/**/*.kt" "app/src/test/**/*.kt" "**/*.kts" || fail=1

echo "=== detekt (android-app/detekt.yml) ==="
if [ ! -f "$DETEKT_JAR_PATH" ]; then
    echo "ERROR: detekt jar not found at '$DETEKT_JAR_PATH' (set \$DETEKT_JAR)"; exit 2
fi
# No --build-upon-default-config: detekt.yml is the whole ruleset (see its header).
java -jar "$DETEKT_JAR_PATH" --config detekt.yml \
    --input app/src/main/kotlin,app/src/test || fail=1

if [ "${LINT_SKIP_GRADLE:-0}" = "1" ]; then
    echo "=== lintDebug: skipped (LINT_SKIP_GRADLE=1) ==="
else
    echo "=== lintDebug (UnusedResources = error) ==="
    ./gradlew :app:lintDebug --no-daemon || fail=1
fi

if [ "$fail" -ne 0 ]; then
    echo "android lint gate: FAIL"
    exit 1
fi
echo "android lint gate: clean"
