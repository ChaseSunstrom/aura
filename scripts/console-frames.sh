#!/usr/bin/env bash
# Capture one text frame of the glass console per layout mode into
# docs/design/screens/console-<mode>.txt — the brief's evidence for the
# terminal surface (GLASS_HUD §9.7), drawn from the fixed demo tape with no
# socket and no escapes. aura-desktop/tests/test_console_app.py fails when a
# file here is not what the code draws, so a change to the renderer is a
# re-run of this script in the same commit.
set -euo pipefail
cd "$(dirname "$0")/.."
for mode in overview task focus ambient; do
  (cd aura-desktop && python3 -m aura_desktop console --demo "$mode" --cols 100 --rows 30 --plain) \
    > "docs/design/screens/console-$mode.txt"
  echo "wrote docs/design/screens/console-$mode.txt"
done
