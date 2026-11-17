#!/usr/bin/env bash
# The one-line install on a clean machine, start to finish (T-0864).
#
#   scripts/verify/install-clean.sh [--rev REV] [--out DIR] [--theme aura|jarvis] [--keep] [--warm]
#   scripts/verify/install-clean.sh --clean        remove what a kept run left behind
#   scripts/verify/install-clean.sh --page-only    stop once the setup page answers: a check
#                                                  of this script's own plumbing, in minutes
#
# What it does, on this computer's Docker and nowhere else:
#
#   1. makes a repository of one commit from REV's files (what a fresh public repository holds)
#      and serves it from a git daemon, with the two entry points on a raw-file stand-in
#      (install-clean/origin.py), all on a Docker network of their own;
#   2. starts a clean machine on that network (install-clean/Dockerfile): Ubuntu with Docker
#      Engine, curl and git, its own Docker daemon and its own image store, no python3, no source;
#   3. runs the README's line in it, with the repository's address pointed at the daemon;
#   4. answers the setup page from a browser here (install-clean.mjs), presses Install, waits,
#      opens the console the page links to and signs in with the password chosen on the page;
#   5. writes clean-container.log (what the installer said, what the browser did, what was
#      running afterwards, ending "RESULT: installed" or "RESULT: failed") and the pictures:
#      into DIR when the run ends installed, replacing an earlier run's. A failed run leaves DIR
#      as it was and says where its own record and pictures are.
#
# The machine's containers run on the machine's own daemon: nothing is built, started or stopped
# on this computer's daemon but the three containers, one network and one volume named
# aura-proof*. It refuses to start while any of those exist.
#
# This computer's disk is guarded: the machine's image store is a volume on it, beside whatever
# else runs here. A run does not start with less than PROOF_MIN_FREE_GB (default 45) free where
# Docker keeps its data, and is stopped, its store removed, if less than PROOF_FLOOR_GB
# (default 10) is ever left.
#
# PROOF_CPUS=N gives the machine N processors (everything it builds and runs included), for a
# run beside other work on this computer; PROOF_DNS names the resolver its containers use
# (default: this computer's first one that is not loopback); PROOF_NET_PREFIX moves the
# network (default 10.213.0).
#
# --warm keeps an image store left by a kept run, which makes a second run minutes instead of an
# hour; the log says so, and a warm run is not the proof.
#
# Not covered: a machine with no Docker (the entry point's own Docker install needs an init
# system a container lacks), the archive fetch used where git is missing, macOS and Windows,
# a GPU, a voice turn, a phone, the model answering more than its one sentence, the public
# hosts themselves (TLS and the real addresses exist only once the repository is public).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
REV=HEAD OUT="" THEME=aura KEEP=0 WARM=0 CLEAN=0 PAGE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV="${2:?--rev needs a revision}"; shift 2 ;;
    --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
    --theme) THEME="${2:?--theme needs aura or jarvis}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --warm) WARM=1; shift ;;
    --clean) CLEAN=1; shift ;;
    --page-only) PAGE_ONLY=1; shift ;;
    -h | --help) sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install-clean.sh: unknown option $1" >&2; exit 2 ;;
  esac
done

NET=aura-proof VOLUME=aura-proof-docker IMAGE=aura-proof-machine:local
ORIGIN=aura-proof-origin GITD=aura-proof-git MACHINE=aura-proof-machine
PREFIX=${PROOF_NET_PREFIX:-10.213.0}
ORIGIN_IP=$PREFIX.2 GIT_IP=$PREFIX.3 MACHINE_IP=$PREFIX.10
WAIT_S=${PROOF_WAIT_S:-5400}
MIN_FREE_GB=${PROOF_MIN_FREE_GB:-45}
FLOOR_GB=${PROOF_FLOOR_GB:-10}
watch_pid=""
WORK=""
code=1

say() { echo "[proof] $*"; }
die() { echo "install-clean.sh: $*" >&2; exit 1; }

remove_all() {
  docker rm -f "$MACHINE" "$GITD" "$ORIGIN" >/dev/null 2>&1 || true
  if [ "${1:-}" != keep-volume ]; then docker volume rm "$VOLUME" >/dev/null 2>&1 || true; fi
  docker network rm "$NET" >/dev/null 2>&1 || true
}

if [ "$CLEAN" = 1 ]; then remove_all; say "removed"; exit 0; fi

# Whole GB free where this computer's Docker keeps its data.
host_free_gb() { df -Pk "$(docker info --format '{{.DockerRootDir}}')" | awk 'NR==2 { print int($4 / 1048576) }'; }

command -v docker >/dev/null || die "docker is needed"
command -v node >/dev/null || die "node is needed (the browser half is a Playwright script)"
[ -d "$ROOT/aura-web/node_modules/@playwright/test" ] || die "run 'npm ci' in aura-web first"
[ "$THEME" = aura ] || [ "$THEME" = jarvis ] || die "--theme is aura or jarvis"
sha=$(git -C "$ROOT" rev-parse --short=12 "$REV^{commit}") || die "no such revision: $REV"
[ -n "$OUT" ] || OUT="$ROOT/docs/verification/install"

left=$(docker ps -a --format '{{.Names}}' | grep -E "^($MACHINE|$GITD|$ORIGIN)$" || true)
[ -z "$left" ] || die "an earlier run is still here ($(echo "$left" | tr '\n' ' ')): --clean removes it"
if [ "$WARM" != 1 ]; then docker volume rm "$VOLUME" >/dev/null 2>&1 || true; fi
free=$(host_free_gb)
[ "$PAGE_ONLY" = 1 ] || [ "$free" -ge "$MIN_FREE_GB" ] ||
  die "$free GB free where this computer's Docker keeps its data; a run wants $MIN_FREE_GB (PROOF_MIN_FREE_GB)"

finish() {
  local code=$?
  trap - EXIT
  if [ -n "$watch_pid" ]; then kill "$watch_pid" 2>/dev/null || true; fi
  if [ "$KEEP" = 1 ]; then
    say "kept: docker exec -it $MACHINE bash; --clean removes it"
  else
    remove_all
  fi
  # A failed run's whole output stays where it is (its logs hold the page's one-time token, so
  # nothing of it is copied into the tree); a good run's is removed with the rest.
  if [ -n "$WORK" ] && [ -d "$WORK" ] && [ "$KEEP" != 1 ] && [ "$code" = 0 ]; then rm -rf "$WORK"; fi
  exit "$code"
}
trap finish EXIT
trap 'exit 130' INT TERM

WORK=$(mktemp -d "${TMPDIR:-/tmp}/aura-proof.XXXXXX")
chmod 755 "$WORK"
# The record and the pictures are made here and reach $OUT only from a run that ends installed:
# two red runs once replaced the committed proof with their own record, in a tree whose claims
# still said the install was proven (T-1497).
RECORD="$WORK/record"
mkdir -p "$WORK/tree" "$WORK/srv" "$RECORD"

# ---- 1. the repository a fresh public one would be ---------------------------------------------
git -C "$ROOT" archive "$sha" | tar -x -C "$WORK/tree"
git -C "$WORK/tree" init -q -b main
git -C "$WORK/tree" add -A -f
git -C "$WORK/tree" -c user.name=AURA -c user.email=aura@example.com -c commit.gpgsign=false \
  commit -q --no-verify -m "AURA"
git clone -q --bare "$WORK/tree" "$WORK/srv/aura.git"
files=$(git -C "$WORK/tree" ls-files | wc -l)
say "source: one commit of $files files made from $sha"

# ---- 2. the network, the stand-ins, the machine ------------------------------------------------
docker build -q -t "$IMAGE" "$ROOT/scripts/verify/install-clean" >/dev/null
docker image inspect python:3.12-alpine >/dev/null 2>&1 || docker pull -q python:3.12-alpine >/dev/null
docker network create --subnet "$PREFIX.0/24" "$NET" >/dev/null
docker run -d --name "$ORIGIN" --network "$NET" --ip "$ORIGIN_IP" \
  -v "$ROOT/scripts/verify/install-clean/origin.py:/origin.py:ro" -v "$WORK/tree:/tree:ro" \
  python:3.12-alpine python /origin.py --tree /tree --port 8000 >/dev/null
docker run -d --name "$GITD" --network "$NET" --ip "$GIT_IP" --user "$(id -u):$(id -g)" \
  -v "$WORK/srv:/srv:ro" --entrypoint git "$IMAGE" \
  daemon --export-all --base-path=/srv --reuseaddr --listen=0.0.0.0 /srv >/dev/null

# The machine's own containers need a resolver they can reach: this computer's, unless told.
dns=${PROOF_DNS:-$(awk '/^nameserver/ && $2 !~ /^127\./ { print $2; exit }' /etc/resolv.conf)}
docker run -d --privileged --name "$MACHINE" --hostname aura-clean --network "$NET" --ip "$MACHINE_IP" \
  ${PROOF_CPUS:+--cpus "$PROOF_CPUS"} -e MACHINE_DNS="$dns" -v "$VOLUME:/var/lib/docker" "$IMAGE" >/dev/null
waited=0
until docker exec "$MACHINE" docker info >/dev/null 2>&1; do
  waited=$((waited + 1))
  if [ "$waited" -ge 90 ]; then
    docker exec "$MACHINE" tail -5 /var/log/dockerd.log >&2 || true
    die "the machine's Docker daemon did not start"
  fi
  sleep 1
done

# shellcheck disable=SC2016  # the script below runs in the machine, not here
facts=$(docker exec "$MACHINE" sh -c '
  . /etc/os-release
  echo "$PRETTY_NAME; $(docker --version | cut -d, -f1); $(docker compose version | head -1)"
  for tool in curl git bash python3 node; do
    if command -v "$tool" >/dev/null 2>&1; then echo "$tool: yes"; else echo "$tool: not installed"; fi
  done | tr "\n" " "
  echo
  echo "processors: $(nproc); images in its Docker before the line: $(docker images -q | wc -l)"')
# nproc counts this computer's processors whatever share of them the machine was given: the
# first records said ten for runs that were held to four.
if [ -n "${PROOF_CPUS:-}" ]; then
  facts="${facts/; images in its Docker/, held to $PROOF_CPUS by this run; images in its Docker}"
fi

# The guard on this computer's disk, and the measure of what an install stores: the most the
# machine's image store ever held, looked at twice a minute.
(
  most=0
  while sleep 30; do
    if [ "$(host_free_gb)" -lt "$FLOOR_GB" ]; then
      echo "under $FLOOR_GB GB was left on this computer's disk" > "$WORK/stopped"
      docker rm -f "$MACHINE" >/dev/null 2>&1 || true
      docker volume rm "$VOLUME" >/dev/null 2>&1 || true
      break
    fi
    now=$(docker exec "$MACHINE" du -sx --block-size=1M /var/lib/docker 2>/dev/null | cut -f1 || true)
    if [ -n "$now" ] && [ "$now" -gt "$most" ]; then most=$now; echo "$most" > "$WORK/most-mb"; fi
  done
) &
watch_pid=$!

# ---- 3. the line --------------------------------------------------------------------------------
line="curl -fsSL http://$ORIGIN_IP:8000/aura/aura/main/install.sh | AURA_REPO_URL=git://$GIT_IP/aura.git sh -s -- --setup-bind $MACHINE_IP"
say "the line: $line"
started=$(date +%s)
docker exec "$MACHINE" sh -c "$line" > "$WORK/install.log" 2>&1 &
install_pid=$!

url="" waited=0
while [ -z "$url" ]; do
  url=$(sed -n 's/^\[setup\] setup page: \(http[^ ]*\)$/\1/p' "$WORK/install.log" | head -1)
  [ -n "$url" ] && break
  kill -0 "$install_pid" 2>/dev/null || break
  waited=$((waited + 1))
  [ "$waited" -lt 900 ] || break
  sleep 1
done

if [ "$PAGE_ONLY" = 1 ]; then
  kill "$install_pid" 2>/dev/null || true
  [ -n "$url" ] || { sed -E 's/\?t=[0-9a-f]+/?t=<token>/g' "$WORK/install.log" | tail -20 >&2; die "the installer never printed the setup page's address"; }
  echo "$facts" | sed 's/^/[proof] machine: /'
  grep -E '^\[(aura|preflight|setup)\] ' "$WORK/install.log" | sed -E 's/\?t=[0-9a-f]+/?t=<token>/g'
  curl -fsS --max-time 5 -o /dev/null "$url" || die "the setup page does not answer from here"
  say "the setup page answers from here; stopping (--page-only)"
  code=0
  exit 0
fi

# ---- 4. the browser ------------------------------------------------------------------------------
page=1
if [ -n "$url" ]; then
  say "the setup page is up; answering it from a browser here"
  password="proof-$(od -An -tx1 -N9 /dev/urandom | tr -d ' \n')"
  if SETUP_URL="$url" SETUP_BIND="$MACHINE_IP" SETUP_PASSWORD="$password" \
    SETUP_LLM_URL="http://$ORIGIN_IP:8000/v1" SETUP_THEME="$THEME" SETUP_OUT="$RECORD" SETUP_WAIT_S="$WAIT_S" \
    node "$ROOT/scripts/verify/install-clean.mjs" > "$WORK/page.log" 2>&1; then
    page=0
  fi
  if [ "$page" = 0 ]; then
    if wait "$install_pid"; then code=0; else code=$?; fi
  else
    kill "$install_pid" 2>/dev/null || true
  fi
else
  : > "$WORK/page.log"
  say "the installer never printed the setup page's address"
  if kill -0 "$install_pid" 2>/dev/null; then kill "$install_pid" 2>/dev/null || true; else wait "$install_pid" || code=$?; fi
fi
took=$(($(date +%s) - started))

# ---- 5. the record -------------------------------------------------------------------------------
answers() { if curl -fsS --max-time 5 -o /dev/null "$1" 2>/dev/null; then echo "answers"; else echo "no answer"; fi; }
console=$(answers "http://$MACHINE_IP:8199/healthz")
adapter=$(answers "http://$MACHINE_IP:8643/health")
result="failed"
if [ "$code" = 0 ] && [ "$page" = 0 ] && [ "$console" = answers ] && [ "$adapter" = answers ]; then result="installed"; fi

{
  echo "The one-line install on a clean machine (scripts/verify/install-clean.sh)"
  echo
  echo "date:    $(date -u +%Y-%m-%d)"
  echo "source:  one commit of $files files made from $sha, cloned by the line from a git daemon"
  echo "machine: a container with its own Docker daemon"
  echo "$facts" | sed 's/^/         /'
  if [ "$WARM" = 1 ]; then echo "         WARM RUN: the image store was kept from an earlier run; this is not the proof"; fi
  echo "line:    $line"
  echo "         (from the public repository the line is the README's: the address is the raw-file"
  echo "         host's and AURA_REPO_URL is not set; --setup-bind is here because the browser was"
  echo "         on another machine than the install)"
  echo
  echo "--- what the installer said (its own lines; build and pull output left out) ---"
  grep -E '^\[(aura|preflight|setup|secrets|n8n|images|start|verify|pair)\] |^install\.sh: ' "$WORK/install.log" \
    | sed -E 's/\?t=[0-9a-f]+/?t=<token>/g' || true
  echo
  echo "--- what the browser did ---"
  grep -E '^\[page\] |^RESULT: ' "$WORK/page.log" | sed -E 's/\?t=[0-9a-f]+/?t=<token>/g' || true
  echo
  echo "--- afterwards, on the machine ---"
  docker exec "$MACHINE" docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null | sort | sed 's/^/running: /' || true
  docker exec "$MACHINE" sh -c 'docker system df --format "{{.Type}}: {{.TotalCount}}, {{.Size}}" | head -2' 2>/dev/null | sed 's/^/stored:  /' || true
  if [ -s "$WORK/most-mb" ]; then
    echo "disk:    the machine's image store held $(($(cat "$WORK/most-mb") / 1024)) GB at its fullest (looked at twice a minute)"
  fi
  if [ -s "$WORK/stopped" ]; then echo "STOPPED BY THE PROOF: $(cat "$WORK/stopped")"; fi
  echo "console http://$MACHINE_IP:8199/healthz: $console"
  echo "adapter http://$MACHINE_IP:8643/health: $adapter"
  echo "installer exit: $code; browser: $([ "$page" = 0 ] && echo "finished and signed in" || echo "did not finish")"
  if [ "$result" = installed ]; then
    echo "took: $((took / 60)) min $((took % 60)) s from the line to the signed-in console"
  else
    echo "took: $((took / 60)) min $((took % 60)) s from the line to where it stopped"
  fi
  echo
  echo "RESULT: $result"
} > "$RECORD/clean-container.log"

if [ "$result" != installed ]; then
  docker exec "$MACHINE" sh -c 'docker ps -a --format "{{.Names}}: {{.Status}}"' > "$WORK/containers.log" 2>&1 || true
  say "RESULT: failed (the whole output is in $WORK: install.log, page.log, containers.log; its record and pictures are in $RECORD, and $OUT is as it was)"
  exit 1
fi
# An earlier run's record and pictures are not this run's: a failed.png beside RESULT: installed
# was the first thing the second run left.
mkdir -p "$OUT"
rm -f "$OUT/clean-container.log" "$OUT"/setup-*.png "$OUT"/console-*.png "$OUT/failed.png"
cp "$RECORD"/* "$OUT"/
say "RESULT: installed in $((took / 60)) min; the record is $OUT/clean-container.log"
