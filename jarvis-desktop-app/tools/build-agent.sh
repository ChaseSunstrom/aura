#!/usr/bin/env bash
# Freeze the Python device agent into the one binary the app carries as its
# sidecar (ADR-0051): `src-tauri/binaries/jarvis-desktop-agent-<target-triple>[.exe]`,
# the name Tauri's `bundle.externalBin` looks for.
#
#   PYTHON     the interpreter to freeze with; a command line, so a Windows build
#              under Wine is `PYTHON="wine python"`. Default python3.
#   TRIPLE     the Rust target triple the binary is for. Default: this host's.
#   OUT_DIR    where the binary goes. Default: src-tauri/binaries.
#   PIP_LIMIT_S, FREEZE_LIMIT_S   seconds each long step may take before it is
#              killed and tried once more. CI's job clock must cover both tries
#              of both (deploy/tests/test_build_agent.py holds it to that).
#   TIMES_FILE where each try's seconds and exit are also written; CI keeps it
#              as an artifact, so a slow run can be read without the job's log.
#
# The agent is installed with its `full` extras (psutil, pyperclip, pyautogui):
# a frozen binary cannot pip-install an extra later, so what it can do is what
# it was built with. Not covered: macOS (it has to be frozen on a Mac).
set -euo pipefail

app=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$app/.." && pwd)
read -r -a py <<<"${PYTHON:-python3}"
triple=${TRIPLE:-$(rustc --print host-tuple 2>/dev/null || echo x86_64-unknown-linux-gnu)}
out=${OUT_DIR:-$app/src-tauri/binaries}
work=$(mktemp -d)
trap 'rm -r -f -- "$work"' EXIT

ext=""
case "$triple" in *windows*) ext=".exe" ;; esac

# The agent's update origin is the house GitLab, which the repo does not name (T-0841): a CI
# build stamps its own server into a copy and freezes that; a local build keeps the placeholder.
src="$repo/aura-desktop"
if [ -n "${CI_SERVER_URL:-}" ]; then
  cp -r "$repo/aura-desktop" "$work/aura-desktop"
  src="$work/aura-desktop"
  # -i.bak: BSD sed (a Mac) needs the suffix; the copy is in $work and goes with it.
  sed -i.bak "s#^GITLAB_BASE = \"https://gitlab.home.example\"\$#GITLAB_BASE = \"${CI_SERVER_URL}\"#" "$src/aura_desktop/update.py"
  grep -q "^GITLAB_BASE = \"${CI_SERVER_URL}\"\$" "$src/aura_desktop/update.py"
fi

# Under Wine in CI this build ended at the runner's 30 minute clock three times in five,
# and in two to nine minutes otherwise (T-1145), with nothing saying where. Run by hand
# in the same image on a host at load 12, pip took 135 s and PyInstaller 24 s: a stall,
# not a slow build, so the limits are several times those. Each long step now has its own limit, says how long it took, and is tried once
# more when it hits the limit; a step that fails by itself is not retried. Not covered:
# time lost before this script starts (the image pull, the runner).
note() {
  echo "build-agent: $1"
  if [ -n "${TIMES_FILE:-}" ]; then mkdir -p "$(dirname "$TIMES_FILE")" && echo "$1" >>"$TIMES_FILE"; fi
}
# A Mac has no `timeout` (Homebrew's coreutils calls it gtimeout); with neither, no limit.
limiter=$(command -v timeout || command -v gtimeout || true)
[ -n "$limiter" ] || note "no timeout command here: the steps run without a limit"
step() {
  local name=$1 limit=$2 try t0 rc took
  shift 2
  for try in 1 2; do
    t0=$SECONDS
    rc=0
    if [ -n "$limiter" ]; then "$limiter" --kill-after=10 "$limit" "$@" || rc=$?; else "$@" || rc=$?; fi
    took=$((SECONDS - t0))
    note "$name, try $try: exit $rc after $took s (limit $limit s)"
    if [ "$rc" -eq 0 ]; then return 0; fi
    # 124 is timeout's own. 137 is its kill of a step that ignored the first signal, but
    # also the kernel's for a step out of memory: only one that ran to the limit is a stall.
    if [ "$rc" -ne 124 ] && { [ "$rc" -ne 137 ] || [ "$took" -lt "$limit" ]; }; then return "$rc"; fi
    # A killed Wine python leaves its server holding the prefix; the next try would wait
    # on it, so it is ended and waited for.
    if [ "${py[0]}" = wine ]; then wineserver -k 2>/dev/null || true; wineserver -w 2>/dev/null || true; fi
  done
  note "$name stalled twice at its $limit s limit"
  return 1
}

step "pip install" "${PIP_LIMIT_S:-600}" "${py[@]}" -m pip install --quiet --disable-pip-version-check "$src[full]" "pyinstaller==6.22.3"
# --clean: a try killed at its limit must not leave the next one a half-written cache.
# --collect-data: the terminal console's theme is package data read through
# importlib.resources, which a frozen import cannot find unless it was bundled.
# --exclude-module tkinter: the app answers the agent's consent prompts, and a
# standalone Python's Tk links libraries PyInstaller cannot find (it warned and
# bundled a _tkinter that could never load). Excluded, the agent's Tk consent
# backend reports itself unusable and the chain goes on to the app.
step "PyInstaller" "${FREEZE_LIMIT_S:-300}" "${py[@]}" -m PyInstaller --noconfirm --clean --onefile --name jarvis-desktop-agent \
  --collect-data aura_desktop --exclude-module tkinter \
  --distpath "$work/dist" --workpath "$work/build" --specpath "$work" \
  "$app/tools/agent_entry.py"

mkdir -p "$out"
cp "$work/dist/jarvis-desktop-agent$ext" "$out/jarvis-desktop-agent-$triple$ext"
echo "froze $out/jarvis-desktop-agent-$triple$ext"
