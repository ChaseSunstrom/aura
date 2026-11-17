#!/usr/bin/env bash
# scripts/install.sh — put AURA on a server or desktop with one script.
#
#   curl -fsSL <raw url>/install.sh | sh     the one-line install: it fetches the source and runs this
#   bash scripts/install.sh                  the same, from a checkout
#
# It opens a setup page in the browser (server, models, voice, wake word, devices, theme), then
# installs what the answers ask for and shows its progress on that page. Nothing is hand-edited.
#
#   --dir DIR         install directory (default $HOME/jarvis; never /opt/jarvis-host unless named)
#   --bind IP         the one address every port binds (a LAN or WireGuard IP; the page asks if omitted)
#   --no-gui          no setup page: ask in the terminal instead
#   --setup-bind IP   where the setup page listens (default 127.0.0.1: this computer only)
#   --setup-port N    the setup page's port (default 8198)
#   --build           build the images here (the default when AURA_REGISTRY names no registry)
#   --upgrade         an install is already running here: hand over to scripts/deploy-here.sh
#   --repo URL        where to clone the source when this script is run on its own
#   --yes             unattended: pick defaults, never prompt, no setup page
#   --dry-run         print every action, write and start nothing (reads only)
#
# Phases, each safe to run again: preflight, setup, secrets, n8n, images, start, verify, pair.
# Env knobs: AURA_INSTALL_MIN_GB (free disk, 30: a processor-only install stored 26), AURA_INSTALL_PORTS (ports that must be
# free), AURA_INSTALL_HEALTH_TIMEOUT (seconds per service, 180), AURA_RELEASE_URL (where the
# APK and desktop installers are published), AURA_REGISTRY (images to pull instead of building),
# AURA_SETUP_TIMEOUT (seconds the page waits for answers, 3600), AURA_SETUP_NO_BROWSER=1 (print
# the page's address, open nothing).
#
# The setup page: deploy/setup/server.py, for the length of this run, on python3 here or in a
# container when this machine has none. It is reached with a one-time token printed below; its
# answers land in DIR/setup/answers.env (0600), are checked again here character by character,
# and the password's hash and the model key are blanked there once they are in DIR/.env.
#
# Not covered: the firewall (scripts/setup-host-firewall.sh, needs sudo), TLS in front of the
# console, the model weights download (the first start of jarvis-models fetches them), and
# rollback. An existing .env is never changed: its missing keys are named, nothing more, and the
# setup page is not shown for it. A setup page bound to another address than loopback is plain
# http on that network: the token and the answers cross it unencrypted.
set -euo pipefail

DIR="${HOME:-/root}/jarvis"
BIND=""
BUILD=0 UPGRADE=0 DRY=0 YES=0 GUI=1
REPO="${AURA_REPO_URL:-}"
MIN_GB="${AURA_INSTALL_MIN_GB:-30}"
PORTS="${AURA_INSTALL_PORTS-8080 8199 8642 8643}"
HEALTH_TIMEOUT="${AURA_INSTALL_HEALTH_TIMEOUT:-180}"
SETUP_ADDR=127.0.0.1
SETUP_PORT=8198
SETUP_TIMEOUT="${AURA_SETUP_TIMEOUT:-3600}"
GPU=0

# What the setup page answered (deploy/setup/server.py writes them, load_answers reads them).
# All empty means no page was used, and every phase then does what it did before there was one.
SETUP_BIND="" SETUP_PUBLIC_URL="" SETUP_TZ="" SETUP_CONSOLE_HASH="" SETUP_MODEL="" SETUP_LLM_URL=""
SETUP_LLM_MODEL="" SETUP_LLM_KEY="" SETUP_TTS="" SETUP_STT="" SETUP_WAKE="" SETUP_DEVICES="" SETUP_THEME=""
SETUP_DIR="" SETUP_PID="" SETUP_CONTAINER="" PAGE_LIVE=0 DONE_PHASES="" CONSOLE_URL=""

# The Hermes stack's services (deploy/docker-compose.yml) and the ones whose image is this
# repository's own (deploy/docker-compose.build.yml). deploy/tests/test_install.py pins both
# against those files, so a new service cannot be left out of an install by forgetting it here.
HERMES_ALL="jarvis jarvis-adapter jarvis-web searxng jarvis-extract jarvis-cosyvoice jarvis-models jarvis-backup sandbox-certs sandbox-api sandbox-runner-1 n8n"
HERMES_BUILT="jarvis jarvis-adapter jarvis-web jarvis-extract jarvis-cosyvoice jarvis-models"
# jarvis-backup runs the gateway's image under another command: built with it, never pulled.
HERMES_SHARED="jarvis-backup"

say() { echo "[$PHASE] $*"; }
die() { echo "install.sh: $*" >&2; exit 1; }
PHASE=init
run() { # a mutating command: printed in a dry run, executed otherwise
  if [ "$DRY" = 1 ]; then say "would run: $*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:?--dir needs a path}"; shift 2 ;;
    --bind) BIND="${2:?--bind needs an address}"; shift 2 ;;
    --repo) REPO="${2:?--repo needs a URL}"; shift 2 ;;
    --setup-bind) SETUP_ADDR="${2:?--setup-bind needs an address}"; shift 2 ;;
    --setup-port) SETUP_PORT="${2:?--setup-port needs a number}"; shift 2 ;;
    --no-gui) GUI=0; shift ;;
    --build) BUILD=1; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --yes|-y) YES=1; GUI=0; shift ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
# Absolute from here on: this path goes into the env files, and compose takes a relative
# bind source for a volume's name (the backups and the voice's models went into volumes).
case "$DIR" in /*) ;; *) DIR="$PWD/$DIR" ;; esac
[[ "$SETUP_PORT" =~ ^[0-9]{2,5}$ ]] || die "--setup-port '$SETUP_PORT' is not a port number"
[[ "$SETUP_ADDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "--setup-bind '$SETUP_ADDR' is not an IPv4 address"
[ "$SETUP_ADDR" != "0.0.0.0" ] || die "refusing --setup-bind 0.0.0.0: the setup page binds one address, never all of them"
# No registry named: the images are built from this source. A registry that is not yours has
# nothing to pull, and that was the first thing a new install met.
if [ "$BUILD" = 0 ] && [ -z "${AURA_REGISTRY:-}" ]; then BUILD=1; fi

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")
SRC=""
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/../deploy/docker-compose.yml" ]; then
  SRC=$(cd "$SELF_DIR/.." && pwd)
fi

ask() { # ask PROMPT DEFAULT -> answer (the default under --yes)
  local reply
  if [ "$YES" = 1 ]; then echo "$2"; return; fi
  [ -t 0 ] || die "no terminal to ask '$1' on; pass --yes (and --bind)"
  read -r -p "$1 [$2] " reply
  echo "${reply:-$2}"
}

# This machine's private addresses, the first being the best guess for --bind.
private_addresses() {
  { hostname -I 2>/dev/null || true; ifconfig 2>/dev/null | awk '$1 == "inet" { print $2 }' || true; } \
    | tr ' ' '\n' | grep -E '^(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9.]+$' | grep -v '^172\.17\.' \
    | awk '!seen[$0]++' | head -4 || true
}

# ---- the setup page ---------------------------------------------------------------------------

# progress PHASE STATE [DETAIL]: what the page shows. Written whole and renamed, so the page
# never reads half a file; nothing is written when no page is being served.
progress() {
  [ "$PAGE_LIVE" = 1 ] || return 0
  local list="" p
  for p in $DONE_PHASES; do list="$list${list:+,}\"$p\""; done
  printf '{"phase":"%s","state":"%s","done":[%s],"detail":"%s","console":"%s"}\n' \
    "$1" "$2" "$list" "${3:-}" "$CONSOLE_URL" > "$SETUP_DIR/progress.json.tmp"
  mv "$SETUP_DIR/progress.json.tmp" "$SETUP_DIR/progress.json"
}

page_alive() {
  if [ -n "$SETUP_PID" ]; then kill -0 "$SETUP_PID" 2>/dev/null; return; fi
  [ -n "$SETUP_CONTAINER" ] && [ "$(docker inspect --format '{{.State.Running}}' "$SETUP_CONTAINER" 2>/dev/null)" = true ]
}

# The page's own word that it holds the port. An answer on the port alone could be another
# program's, and the address about to be printed and opened carries the token. A container
# cannot start on a port that is taken, so one that runs is ours.
page_bound() {
  [ -n "$SETUP_CONTAINER" ] || grep -q 'listening on' "$SETUP_DIR/server.log" 2>/dev/null
}

stop_page() {
  if [ -n "$SETUP_DIR" ]; then rm -f "$SETUP_DIR/open.html"; fi
  if [ -n "$SETUP_PID" ]; then kill "$SETUP_PID" 2>/dev/null || true; wait "$SETUP_PID" 2>/dev/null || true; fi
  if [ -n "$SETUP_CONTAINER" ]; then docker rm -f "$SETUP_CONTAINER" >/dev/null 2>&1 || true; fi
  SETUP_PID="" SETUP_CONTAINER="" PAGE_LIVE=0
}

# The end of every run, good or bad: the page is told, given one more poll to show it, and stopped.
finish() {
  local code=$?
  trap - EXIT
  if [ "$PAGE_LIVE" = 1 ]; then
    if [ "$code" -ne 0 ]; then progress "$PHASE" failed; fi
    if [ -n "$SETUP_MODEL" ]; then sleep "${AURA_SETUP_LINGER:-5}"; fi
    stop_page
  fi
  exit "$code"
}
trap finish EXIT
trap 'exit 130' INT TERM

host_timezone() {
  local tz=""
  if [ -r /etc/timezone ]; then tz=$(head -1 /etc/timezone); fi
  if [ -z "$tz" ] && [ -L /etc/localtime ]; then tz=$(readlink /etc/localtime | sed 's#.*zoneinfo/##'); fi
  [[ "$tz" =~ ^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}$ ]] || tz=Etc/UTC
  echo "$tz"
}

# What this machine has, for the page to offer only what can work here.
write_facts() {
  local list="" a stt
  for a in $BIND $(private_addresses); do
    case ",$list," in *",\"$a\","*) ;; *) list="$list${list:+,}\"$a\"" ;; esac
  done
  # The listening model is baked into the gateway image: any size when it is built here, the
  # one it was built with when it is pulled.
  if [ "$BUILD" = 1 ]; then stt='"tiny","base","small","medium","large-v3"'; else stt='"small"'; fi
  printf '{"addresses":[%s],"gpu":%s,"timezone":"%s","stt":[%s]}\n' \
    "$list" "$([ "$GPU" = 1 ] && echo true || echo false)" "$(host_timezone)" "$stt" > "$SETUP_DIR/facts.json"
}

start_page() {
  local token url waited=0
  token=$(new_secret)
  url="http://$SETUP_ADDR:$SETUP_PORT/?t=$token"
  if command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    AURA_SETUP_TOKEN="$token" python3 "$SRC/deploy/setup/server.py" --state "$SETUP_DIR" --src "$SRC" \
      --bind "$SETUP_ADDR" --port "$SETUP_PORT" > "$SETUP_DIR/server.log" 2>&1 < /dev/null &
    SETUP_PID=$!
  else
    # No python3 here: the same file in a small container, as this user so the answers it
    # writes are this user's to read. The token goes in by name, never on a command line.
    say "no python3 on this machine: serving the setup page from a container"
    SETUP_CONTAINER=aura-setup
    docker rm -f "$SETUP_CONTAINER" >/dev/null 2>&1 || true
    AURA_SETUP_TOKEN="$token" docker run -d --rm --name "$SETUP_CONTAINER" --user "$(id -u):$(id -g)" \
      -e AURA_SETUP_TOKEN -v "$SRC:/src:ro" -v "$SETUP_DIR:/state" \
      -p "$SETUP_ADDR:$SETUP_PORT:$SETUP_PORT" python:3.12-alpine \
      python /src/deploy/setup/server.py --state /state --src /src \
      --bind "$SETUP_ADDR" --listen 0.0.0.0 --port "$SETUP_PORT" > /dev/null
  fi
  PAGE_LIVE=1
  until page_bound && curl -fsS --max-time 2 -o /dev/null "http://$SETUP_ADDR:$SETUP_PORT/setup.css" 2>/dev/null; do
    waited=$((waited + 1))
    page_alive || die "the setup page did not start (see $SETUP_DIR/server.log); run again with --no-gui to answer in the terminal"
    [ "$waited" -lt 60 ] || die "the setup page did not answer on $SETUP_ADDR:$SETUP_PORT; run again with --no-gui to answer in the terminal"
    sleep 1
  done
  say "setup page: $url"
  if [ "$SETUP_ADDR" = 127.0.0.1 ]; then
    say "  from another computer: ssh -L $SETUP_PORT:127.0.0.1:$SETUP_PORT <this machine>, then open that address there"
    open_door "$url"
  else
    say "  it is plain http on that network: the token above and your answers cross it unencrypted"
  fi
}

# The browser is handed a file, never the address: a command line is readable by every user of
# the machine, and the address carries the token. The file is this user's alone and goes with
# the page. Not covered: Windows, whose opener takes no path from this shell and still gets the
# address; and a browser that may not read the install folder, where the printed address is the way.
open_door() {
  [ -z "${AURA_SETUP_NO_BROWSER:-}" ] || return 0
  local door="$SETUP_DIR/open.html"
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) open_browser "$1"; return 0 ;;
  esac
  (umask 077; printf '<!doctype html>\n<meta charset="utf-8">\n<title>AURA setup</title>\n<meta http-equiv="refresh" content="0;url=%s">\n<a href="%s">Open AURA setup</a>\n' "$1" "$1" > "$door")
  open_browser "$door"
}

open_browser() {
  [ -z "${AURA_SETUP_NO_BROWSER:-}" ] || return 0
  case "$(uname -s)" in
    Darwin) open "$1" >/dev/null 2>&1 || true ;;
    MINGW* | MSYS* | CYGWIN*) powershell.exe -NoProfile -Command "Start-Process '$1'" >/dev/null 2>&1 || true ;;
    *)
      if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null; then
        (xdg-open "$1" >/dev/null 2>&1 &)
      fi ;;
  esac
}

# The answers, one known name at a time and only in the characters the page's server writes.
# Never sourced: a file in the install directory is data, whoever wrote it.
load_answers() {
  local line key val version=""
  local shape='^(SETUP_[A-Z_]+)=([A-Za-z0-9._~+/=:@,$-]*)$'
  while IFS= read -r line; do
    [[ "$line" =~ $shape ]] || continue
    key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
    case "$key" in
      SETUP_VERSION) version="$val" ;;
      SETUP_BIND | SETUP_PUBLIC_URL | SETUP_TZ | SETUP_CONSOLE_HASH | SETUP_MODEL | SETUP_LLM_URL | SETUP_LLM_MODEL | \
        SETUP_LLM_KEY | SETUP_TTS | SETUP_STT | SETUP_WAKE | SETUP_DEVICES | SETUP_THEME) printf -v "$key" '%s' "$val" ;;
    esac
  done < "$SETUP_DIR/answers.env"
  [ "$version" = 1 ] || die "$SETUP_DIR/answers.env is not this installer's (remove it and run again)"
  case "$SETUP_MODEL" in local | endpoint) ;; *) die "$SETUP_DIR/answers.env names no model choice (remove it and run again)" ;; esac
  BIND="$SETUP_BIND"
}

# Once DIR/.env holds them, the password's hash and the model key are in that file and nowhere else.
scrub_answers() {
  [ -n "$SETUP_MODEL" ] && [ -f "$SETUP_DIR/answers.env" ] || return 0
  local tmp
  tmp=$(umask 077; mktemp "$SETUP_DIR/answers.env.XXXXXX")
  sed -E 's/^(SETUP_CONSOLE_HASH|SETUP_LLM_KEY)=.*/\1=/' "$SETUP_DIR/answers.env" > "$tmp"
  mv "$tmp" "$SETUP_DIR/answers.env"
}

setup() {
  PHASE=setup
  if [ "$GUI" != 1 ]; then return 0; fi
  SETUP_DIR="$DIR/setup"
  if [ "$DRY" = 1 ]; then
    say "would serve the setup page on http://$SETUP_ADDR:$SETUP_PORT and wait for the answers"
    if [ -z "$BIND" ]; then BIND=$(private_addresses | head -1); BIND="${BIND:-127.0.0.1}"; fi
    return 0
  fi
  if [ -e "$DIR/.env" ] && [ ! -s "$SETUP_DIR/answers.env" ]; then
    say "$DIR/.env exists: this install keeps its settings, and the setup page is for a new one"
    GUI=0
    # The address it already binds, read from its own file (a name and an address, no secret).
    if [ -z "$BIND" ]; then BIND=$(sed -n 's/^TAILSCALE_IP=\([0-9.]*\).*/\1/p' "$DIR/.env" | head -1); fi
    if [ -z "$BIND" ]; then BIND=$(ask "Address to bind every port to (LAN or WireGuard IP)" "127.0.0.1"); fi
    check_bind
    return 0
  fi
  (umask 077; mkdir -p "$SETUP_DIR")
  chmod 700 "$SETUP_DIR"
  rm -f "$SETUP_DIR/progress.json"
  write_facts
  if [ -s "$SETUP_DIR/answers.env" ]; then
    say "the answers are already in $SETUP_DIR/answers.env (to answer again, remove it and $DIR/.env)"
  fi
  start_page
  DONE_PHASES="preflight"
  if [ ! -s "$SETUP_DIR/answers.env" ]; then
    say "waiting for your answers in the browser (Ctrl-C stops here; nothing has been installed yet)"
    local waited=0
    while [ ! -s "$SETUP_DIR/answers.env" ]; do
      page_alive || die "the setup page stopped (see $SETUP_DIR/server.log)"
      waited=$((waited + 1))
      [ "$waited" -lt "$SETUP_TIMEOUT" ] || die "no answers after ${SETUP_TIMEOUT}s; run again when you are ready"
      sleep 1
    done
  fi
  load_answers
  say "answers received: binding $BIND, model $SETUP_MODEL, voice $SETUP_TTS, theme $SETUP_THEME"
}

# ---- preflight --------------------------------------------------------------------------------

# True when every named container was started by compose from this install directory or from
# this source's house folder (compose records the project directory on each container). A
# container with no such label, or another directory's, is not ours.
started_here() {
  local name from here house
  here=$(cd "$DIR" 2>/dev/null && pwd -P) || return 1
  house=$(cd "${SRC:-$DIR/src}/aura-core" 2>/dev/null && pwd -P) || house=""
  for name in "$@"; do
    from=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$name" 2>/dev/null) || return 1
    case "$from" in
      "$here") ;;
      "$house") [ -n "$house" ] || return 1 ;;
      *) return 1 ;;
    esac
  done
}

preflight() {
  PHASE=preflight
  command -v docker >/dev/null || die "docker is not installed (https://docs.docker.com/engine/install/)"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is missing (the 'docker compose' plugin)"
  command -v curl >/dev/null || die "curl is not installed (the health checks use it)"

  # A running install is somebody's house: never install over it. The one exception is this
  # install itself, half done (a first run that stopped after its containers started): every
  # running container was started from this directory or this source, so the line run again
  # carries on, and compose changes only what differs.
  local running
  running=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -xE 'jarvis|jarvis-adapter|jarvis-web|jarvis-core' || true)
  if [ -n "$running" ]; then
    if [ "$UPGRADE" = 1 ]; then
      [ -n "$SRC" ] || die "--upgrade needs this script run from a checkout"
      say "an install is running; upgrading through scripts/deploy-here.sh"
      if [ "$DRY" = 1 ]; then say "would run: AURA_HOST_DIR=$DIR $SRC/scripts/deploy-here.sh"; exit 0; fi
      AURA_HOST_DIR="$DIR" exec "$SRC/scripts/deploy-here.sh"
    fi
    if started_here $running; then
      say "this install's own containers are running ($(echo "$running" | tr '\n' ' ')): carrying on with it"
    else
      echo "install.sh: an install is already running here ($(echo "$running" | tr '\n' ' '))." >&2
      die "refusing to touch it. To update it, run again with --upgrade."
    fi
  fi
  [ "$UPGRADE" = 1 ] && [ -z "$running" ] && die "--upgrade given but nothing is running; drop --upgrade for a fresh install"

  if command -v nvidia-smi >/dev/null && nvidia-smi -L 2>/dev/null | grep -q GPU; then
    if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
      GPU=1; say "NVIDIA GPU and container toolkit found: voice and models use the card"
    else
      say "NVIDIA GPU found but the container toolkit is not set up: running on the CPU (slow voice and models)"
    fi
  else
    say "no NVIDIA GPU: running on the CPU. It works, but voice and the local model are slow"
  fi

  # Two places fill. The install directory holds the source, the voices and the backups; the
  # images, which are most of it, go wherever Docker keeps its data, often another disk. A
  # short one fails the install an hour in, at a layer that will not unpack, so both are
  # looked at here. Docker Desktop keeps its data inside its own machine, where no path of
  # this one leads: that disk is then not checked.
  local probe="$DIR" free_kb store
  while [ ! -e "$probe" ]; do probe=$(dirname "$probe"); done
  free_kb=$(df -Pk "$probe" | awk 'NR==2 {print $4}')
  [ "$((free_kb / 1048576))" -ge "$MIN_GB" ] || die "under ${MIN_GB} GB free at $probe (images and models are large)"
  store=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  if [ -n "$store" ] && [ -d "$store" ]; then
    free_kb=$(df -Pk "$store" | awk 'NR==2 {print $4}')
    [ "$((free_kb / 1048576))" -ge "$MIN_GB" ] || die "under ${MIN_GB} GB free where Docker keeps its images ($store)"
  fi

  # With the setup page the address is one of its questions; without it, it is asked here.
  if [ -z "$BIND" ] && [ "$GUI" != 1 ]; then
    local guess
    guess=$(private_addresses | head -1)
    BIND=$(ask "Address to bind every port to (LAN or WireGuard IP)" "${guess:-127.0.0.1}")
  fi
  if [ -n "$BIND" ]; then check_bind; fi

  local p ports="$PORTS"
  if [ "$GUI" = 1 ] && [ "$DRY" != 1 ]; then ports="$ports $SETUP_PORT"; fi
  for p in $ports; do
    if command -v ss >/dev/null && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "(^|:)$p\$"; then
      die "port $p is already in use on this host"
    fi
  done
  say "docker ok, bind ${BIND:-asked on the setup page}, install dir $DIR"

  if [ -z "$SRC" ]; then
    [ -n "$REPO" ] || die "run this from a checkout, or pass --repo URL to clone from"
    SRC="$DIR/src"
    command -v git >/dev/null || die "git is needed to fetch the source"
    run mkdir -p "$DIR"
    if [ -d "$SRC/.git" ]; then say "source already at $SRC"; else run git clone --depth 1 "$REPO" "$SRC"; fi
  fi
}

check_bind() {
  [[ "$BIND" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "--bind '$BIND' is not an IPv4 address"
  [ "$BIND" != "0.0.0.0" ] || die "refusing 0.0.0.0: ports bind one address, never all of them"
}

# ---- secrets ----------------------------------------------------------------------------------

new_secret() { od -An -tx1 -N32 /dev/urandom | tr -d ' \n'; }

# True when the console's password is kept as a hash (a line's presence, never its value).
hashed_password() { [ -n "$SETUP_CONSOLE_HASH" ] || grep -q '^AURA_CONSOLE_PASSWORD_HASH=.' "$DIR/.env" 2>/dev/null; }

# value_for KEY -> the value install.sh sets for KEY, or return 1 to keep the template's.
# Shared secrets are the same in both stacks: the house token, the adapter key, the model key.
# ENV_OF says whose template is being filled (hermes or house): the two share some names that
# mean different things (WHISPER_MODEL, LLM_URL).
value_for() {
  case "$1" in
    TAILSCALE_IP) echo "$BIND" ;;
    AURA_CORS_ORIGINS) echo "http://$BIND:8199" ;;
    AURA_PUBLIC_URL) echo "${SETUP_PUBLIC_URL:-http://$BIND:8643}" ;;
    BACKUP_DIR) echo "$DIR/backups" ;;
    COSYVOICE_MODELS_DIR) echo "$DIR/voice/models" ;;
    # Built here: the images are this machine's own, under a name no registry answers to.
    AURA_REGISTRY) if [ "$BUILD" = 1 ]; then echo aura-local; else [ -n "${AURA_REGISTRY:-}" ] && echo "$AURA_REGISTRY"; fi ;;
    AURA_TAG) [ "$BUILD" = 1 ] && echo local ;;
    HASS_TOKEN|AURA_TOKEN) echo "$S_HOUSE" ;;
    AURA_EMERGENCY_SECRET) echo "$S_EMERGENCY" ;;  # the plugin and the house prove a contact list under it
    AURA_API_KEY|AURA_ADAPTER_KEY) echo "$S_API" ;;
    LLM_API_KEY)
      # An endpoint of the person's own: its key, or a word where it needs none (an empty key
      # renders as a YAML null). Otherwise the stack's own model server and its fresh key.
      if [ "$SETUP_MODEL" != endpoint ]; then echo "$S_LLM"
      elif [ "$ENV_OF" = hermes ]; then echo "${SETUP_LLM_KEY:-not-needed}"
      else return 1; fi ;;
    LLM_URL) [ "$ENV_OF" = hermes ] && [ "$SETUP_MODEL" = endpoint ] && echo "$SETUP_LLM_URL" ;;
    LLM_MODEL) [ "$ENV_OF" = hermes ] && [ "$SETUP_MODEL" = endpoint ] && echo "$SETUP_LLM_MODEL" ;;
    TZ) [ -n "$SETUP_TZ" ] && echo "$SETUP_TZ" ;;
    # The password chosen on the setup page is kept as the console's own hash and never as
    # itself. Single quotes: the hash is full of dollar signs, and compose reads a quoted value
    # as it stands.
    AURA_CONSOLE_PASSWORD) [ -n "$SETUP_CONSOLE_HASH" ] && echo "" ;;
    AURA_CONSOLE_PASSWORD_HASH) [ -n "$SETUP_CONSOLE_HASH" ] && printf "'%s'\n" "$SETUP_CONSOLE_HASH" ;;
    TTS_PROVIDER) case "$SETUP_TTS" in kokoro) echo openai ;; piper) echo piper ;; *) return 1 ;; esac ;;
    AURA_TTS_ENGINE) case "$SETUP_TTS" in kokoro) echo cosyvoice ;; piper) echo wyoming ;; *) return 1 ;; esac ;;
    WHISPER_MODEL) [ "$ENV_OF" = hermes ] && [ -n "$SETUP_STT" ] && echo "$SETUP_STT" ;;
    # blank in the template but this stack's own secret: fill it
    AURA_PAIRING_SECRET|SEARXNG_SECRET|BROWSER_APPROVAL_SECRET|AURA_BROWSER_TOKEN) new_secret ;;
    *) return 1 ;;
  esac
}

# make_env TEMPLATE DEST: a new private .env, or a report of what an existing one lacks.
make_env() {
  local tpl="$1" dest="$2" line key val n=0 tmp
  if [ -e "$dest" ]; then
    local missing=""
    while IFS= read -r line; do
      [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]] || continue
      key="${BASH_REMATCH[1]}"
      grep -qE "^$key=" "$dest" || missing="$missing $key"
    done < "$tpl"
    say "$dest exists: left unchanged"
    [ -z "$missing" ] || say "  keys in the template but not in it:$missing"
    return
  fi
  if [ "$DRY" = 1 ]; then say "would create $dest (0600) from $tpl with fresh secrets"; return; fi
  mkdir -p "$(dirname "$dest")"
  tmp=$(umask 077; mktemp "$dest.XXXXXX")
  while IFS= read -r line; do
    if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
      if v=$(value_for "$key"); then
        line="$key=$v"; n=$((n + 1))
      elif [[ "$val" == change-me* ]]; then
        line="$key=$(new_secret)"; n=$((n + 1))
      fi
    fi
    printf '%s\n' "$line" >> "$tmp"
  done < "$tpl"
  chmod 600 "$tmp"; mv "$tmp" "$dest"
  say "created $dest (0600), $n values set; secrets are in that file and nowhere else"
}

secrets() {
  PHASE=secrets
  S_HOUSE=$(new_secret); S_API=$(new_secret); S_LLM=$(new_secret); S_EMERGENCY=$(new_secret)
  # The same layout production has (scripts/deploy.sh): deploy/ synced into DIR, so compose's
  # project directory is DIR and the services' env_file, n8n.env and ./ mounts resolve there,
  # not in the checkout. The install's own .env and n8n.env are never copied over.
  if [ "$DRY" = 1 ]; then say "would copy $SRC/deploy/ into $DIR (not .env, n8n.env, tests)"; else
    mkdir -p "$DIR"
    tar -C "$SRC/deploy" --exclude=./.env --exclude=./n8n.env --exclude=./tests --exclude=./__pycache__ \
      --exclude=./backups --exclude=./.previous_tag --exclude=./seed-repo.tar --exclude=./setup -cf - . | tar -C "$DIR" -xf -
  fi
  ENV_OF=hermes
  make_env "$SRC/deploy/.env.example" "$DIR/.env"
  # The house stack runs from its checkout, .env beside its compose file (its own layout).
  ENV_OF=house
  make_env "$SRC/aura-core/.env.example" "$SRC/aura-core/.env"
  if [ "$DRY" != 1 ]; then mkdir -p "$DIR/backups" "$DIR/voice/models"; scrub_answers; fi
  if hashed_password; then
    say "the console password is the one you chose; only its hash is in $DIR/.env"
  else
    say "the console password is AURA_CONSOLE_PASSWORD in $DIR/.env"
  fi
}

n8n() {
  PHASE=n8n
  if [ -f "$SRC/deploy/n8n-env.sh" ]; then
    run bash "$SRC/deploy/n8n-env.sh" "$DIR/n8n.env"
  else
    say "deploy/n8n-env.sh not in this checkout: n8n is skipped"
  fi
}

# ---- images, start ----------------------------------------------------------------------------

# DIR holds the copied compose file and .env; the project name matches production's container names.
# AURA_SRC is where the build file's contexts point: the source, not the install directory.
compose_hermes() {
  AURA_SRC="$SRC" COMPOSE_PROJECT_NAME=jarvis docker compose --project-directory "$DIR" -f "$DIR/docker-compose.yml" \
    ${GPU_FILE:+-f "$GPU_FILE"} ${BUILD_FILE:+-f "$BUILD_FILE"} "$@"
}
# The house stack: its compose file and .env in the checkout, which is also its build context.
compose_core() { docker compose --project-directory "$SRC/aura-core" -f "$SRC/aura-core/docker-compose.yml" "$@"; }

# The Hermes services this install runs: what the answers ask for, or all of them when there
# were no answers. One name per word, into the array WANT.
choose_services() {
  local s
  WANT=()
  if [ -z "$SETUP_MODEL" ]; then
    for s in $HERMES_ALL; do WANT+=("$s"); done
    return 0
  fi
  WANT=(jarvis jarvis-adapter jarvis-web searxng jarvis-extract jarvis-backup)
  if [ "$SETUP_TTS" = kokoro ]; then WANT+=(jarvis-cosyvoice); fi
  if [ "$SETUP_MODEL" = local ]; then WANT+=(jarvis-models); fi
  if [ -f "$DIR/n8n.env" ] || [ "$DRY" = 1 ]; then WANT+=(sandbox-certs sandbox-api sandbox-runner-1 n8n); fi
}

# only LIST: the chosen services that are in LIST, into the array PICK.
only() {
  local s
  PICK=()
  for s in "${WANT[@]}"; do
    case " $1 " in *" $s "*) PICK+=("$s") ;; esac
  done
}

# The source the gateway image carries for its own self-extension (deploy/Dockerfile's ADD):
# what git would archive, or the tree itself when the source came down as an archive. The
# house runs from this same tree and keeps its state in it (its .env, config/.storage), so a
# tree without git is copied once, on the first run, before the house has ever started, and
# without the .env this installer has just written; a later run reuses that first copy.
seed_repo() {
  local out="$SRC/deploy/seed-repo.tar" stage
  if [ "$DRY" = 1 ]; then say "would write $out"; return; fi
  if [ -d "$SRC/.git" ] && command -v git >/dev/null; then
    git -C "$SRC" archive --format=tar --prefix=repo/ -o "$out" HEAD
  elif [ -s "$out" ]; then
    say "the source copy for the gateway image is already there"
  else
    stage=$(mktemp -d "${TMPDIR:-/tmp}/aura-seed.XXXXXX")
    mkdir "$stage/repo"
    tar -C "$SRC" --exclude=./deploy/seed-repo.tar --exclude=./aura-core/.env --exclude=./.git -cf - . | tar -C "$stage/repo" -xf -
    tar -C "$stage" -cf "$out" repo
    rm -rf "$stage"
  fi
}

images() {
  PHASE=images
  if [ "$GPU" = 1 ]; then GPU_FILE="$DIR/docker-compose.gpu.yml"; fi
  choose_services
  if [ "$BUILD" = 1 ]; then
    BUILD_FILE="$DIR/docker-compose.build.yml"
    say "building the images from $SRC (the first time takes a while; a second run reuses what is built)"
    seed_repo
    only "$HERMES_BUILT"
    progress images running "building ${#PICK[@]} images from the source"
    if [ "${#PICK[@]}" -gt 0 ]; then run compose_hermes build "${PICK[@]}"; fi
    # What is left comes from its publisher: everything chosen that is neither built here
    # nor the gateway's image under another name.
    local s rest=()
    for s in "${WANT[@]}"; do
      case " $HERMES_BUILT $HERMES_SHARED " in *" $s "*) ;; *) rest+=("$s") ;; esac
    done
    progress images running "fetching the other images"
    if [ "${#rest[@]}" -gt 0 ]; then run compose_hermes pull "${rest[@]}"; fi
    run compose_core build
    run compose_core pull --ignore-buildable
  else
    say "pulling from ${AURA_REGISTRY:-the registry in $DIR/.env} (an image missing there fails here: use --build)"
    progress images running "fetching the images"
    run compose_hermes pull "${WANT[@]}"
    run compose_core pull --ignore-buildable
  fi
}

# The theme chosen on the setup page is the primary persona (tests/contracts/persona.json), put
# in Hermes' volume before the first start so the first hello is already in that voice. AURA is
# what an absent file means, so only the other one is written; a file already there is left.
seed_persona() {
  [ "$SETUP_THEME" = jarvis ] || return 0
  local since
  since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # shellcheck disable=SC2016  # the $-less script below runs in the container, not here
  run compose_hermes run --rm --no-deps -T --entrypoint sh jarvis -c \
    'mkdir -p /opt/data/state && cd /opt/data/state && { [ -e persona.json ] || printf %s "$0" > persona.json; } && chown -R hermes:hermes /opt/data/state' \
    "{\"schema\": \"jarvis.persona.v1\", \"active\": \"jarvis\", \"since\": \"$since\", \"previous\": \"aura\"}"
}

start() {
  PHASE=start
  # The page's Install button was the yes; in the terminal it is asked.
  if [ "$DRY" != 1 ] && [ "$YES" != 1 ] && [ -z "$SETUP_MODEL" ]; then
    [ "$(ask "Start it now? (y/n)" y)" = y ] || die "stopped before starting; run again to continue"
  fi
  seed_persona
  progress start running "starting the house"
  if [ "$SETUP_WAKE" = off ]; then
    # No wake word: its service is not started, and the house hears only what is sent to it.
    run compose_core up -d --scale wyoming-openwakeword=0
  else
    run compose_core up -d
  fi
  progress start running "starting the assistant"
  if [ -n "$SETUP_MODEL" ]; then run compose_hermes up -d "${WANT[@]}"; else run compose_hermes up -d; fi
}

# ---- verify, pair -----------------------------------------------------------------------------

wait_for() { # wait_for NAME URL
  local waited=0
  progress verify running "waiting for $1"
  while ! curl -fsS --max-time 3 -o /dev/null "$2" 2>/dev/null; do
    waited=$((waited + 3))
    [ "$waited" -lt "$HEALTH_TIMEOUT" ] || { say "$1: no answer from $2 after ${HEALTH_TIMEOUT}s"; return 1; }
    sleep 3
  done
  say "$1 healthy"
}

wait_healthy() { # wait_healthy NAME CONTAINER: Docker's own health check, for what has no port to ask
  local waited=0
  progress verify running "waiting for $1"
  until [ "$(docker inspect --format '{{.State.Health.Status}}' "$2" 2>/dev/null)" = healthy ]; do
    waited=$((waited + 3))
    [ "$waited" -lt "$HEALTH_TIMEOUT" ] || { say "$1: not healthy after ${HEALTH_TIMEOUT}s"; return 1; }
    sleep 3
  done
  say "$1 healthy"
}

verify() {
  PHASE=verify
  if [ "$DRY" = 1 ]; then say "would wait up to ${HEALTH_TIMEOUT}s each for :8080/healthz, :8643/health, the assistant healthy, :8199/healthz, sandbox-api healthy, n8n :5678"; return; fi
  local bad=0
  wait_for "jarvis-core" "http://127.0.0.1:8080/healthz" || bad=1
  wait_for "adapter" "http://$BIND:8643/health" || bad=1
  # The adapter answers before the assistant it relays to does. A console opened in between
  # reads "link closed" and the first question goes unanswered (the clean-machine run).
  wait_healthy "assistant" jarvis || bad=1
  wait_for "console" "http://$BIND:8199/healthz" || bad=1
  if [ -f "$DIR/n8n.env" ]; then
    wait_healthy "sandbox-api" jarvis-sandbox-api || bad=1
    wait_for "n8n" "http://127.0.0.1:5678/healthz" || bad=1
  fi
  [ "$bad" = 0 ] || die "a service did not come up; see '$(basename "$SRC")' compose logs (docker compose logs)"
}

pair() {
  PHASE=pair
  # The devices named on the setup page, none included; with no page, the two most people have.
  local url="http://$BIND:8199" devices=",phone,desktop,"
  if [ -n "$SETUP_MODEL" ]; then devices=",$SETUP_DEVICES,"; fi
  # The name is the theme's: the persona this install starts as.
  local name=AURA
  if [ "$SETUP_THEME" = jarvis ]; then name=JARVIS; fi
  if hashed_password; then
    say "$name is up. Console: $url   (sign in with the password you chose)"
  else
    say "$name is up. Console: $url   (password: AURA_CONSOLE_PASSWORD in $DIR/.env)"
  fi
  if command -v qrencode >/dev/null; then qrencode -t ANSIUTF8 "$url"; fi
  local rel="${AURA_RELEASE_URL:-the Apps page in the console settings}"
  case "$devices" in *,phone,* | *,car,*)
    say "Phone: install the Android app from $rel, then Settings > Pairing in the console shows the QR to scan" ;;
  esac
  case "$devices" in *,car,*)
    say "Car: pair the phone first; the car screen appears in Android Auto once the phone app is signed in" ;;
  esac
  case "$devices" in *,desktop,*)
    say "Computer: the desktop app (AppImage or Windows installer) from $rel; the console offers Open in the desktop app to pair it" ;;
  esac
  case "$devices" in ,,)
    say "No device was chosen: the console is in any browser, and Settings > Pairing adds a phone or a computer later" ;;
  esac
  say "Not yet done: the firewall (scripts/setup-host-firewall.sh) and HTTPS in front of the console (docs/getting-started/install.md)"
  CONSOLE_URL="$url"
}

# Each phase, told to the page as it starts and when it is behind us.
for step in preflight setup secrets n8n images start verify pair; do
  progress "$step" running
  "$step"
  if [ "$step" = setup ] && [ -n "$SETUP_MODEL" ]; then check_bind; fi
  case " $DONE_PHASES " in *" $step "*) ;; *) DONE_PHASES="$DONE_PHASES $step" ;; esac
done
if [ "$DRY" != 1 ]; then progress "done" "done"; fi
