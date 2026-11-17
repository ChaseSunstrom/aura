#!/bin/sh
# install.sh: the one-line install for Linux and macOS.
#
#   curl -fsSL <raw url>/install.sh | sh
#   curl -fsSL <raw url>/install.sh | sh -s -- --yes --bind 192.0.2.10
#
# A thin entry point: it checks the system and what the install needs, fetches the source into
# $AURA_HOME/src and hands over to scripts/install.sh, which holds every install phase. Options
# after `--` go to that installer untouched (--dir first, so one you pass wins). On Windows use
# install.ps1 instead.
#
# Settings, all optional, from the environment:
#   AURA_REPO_URL         where the source is fetched from (the release sets the default below)
#   AURA_REF              branch or tag (default main)
#   AURA_HOME             where to install (default $HOME/aura); the source goes to $AURA_HOME/src
#   AURA_TARBALL_URL      the archive used when git is missing (default: <repo>/archive/refs/heads/<ref>.tar.gz)
#   AURA_INSTALL_DOCKER=1 install Docker Engine without asking (Linux only)
#
# Not covered: a mirror whose archive URLs differ from GitHub's (set AURA_TARBALL_URL), checking
# a signature or checksum of the source (it is fetched over https and nothing more), a ref
# change on a checkout that already exists (a re-run pulls the branch that is checked out), a
# file removed upstream from a copy that came as an archive (a re-run unpacks over the copy and
# never empties it, because the house keeps its state there), and Docker Desktop on macOS
# (named, never installed).
set -eu

# The release sets this to the public repository; a fork or a mirror sets AURA_REPO_URL instead.
AURA_REPO_DEFAULT="https://github.com/ChaseSunstrom/aura"

# The one temporary file at a time, removed on every way out (an exec skips the trap, so the
# hand-over removes it first).
TMPFILE=""

usage() {
  echo "usage: curl -fsSL <raw url>/install.sh | sh -s -- [installer options]"
  echo
  echo "Checks Docker and the tools it needs, fetches the source and runs scripts/install.sh."
  echo "Options are passed on to that installer; run it with --help after this once fetched."
  echo
  echo "environment (all optional):"
  echo "  AURA_REPO_URL          where the source comes from (default $AURA_REPO_DEFAULT)"
  echo "  AURA_REF               branch or tag (default main)"
  echo "  AURA_HOME              install directory (default \$HOME/aura; source in \$AURA_HOME/src)"
  echo "  AURA_TARBALL_URL       archive used when git is missing"
  echo "  AURA_INSTALL_DOCKER=1  install Docker Engine without asking (Linux only)"
  echo
  echo "On Windows, run in PowerShell instead: irm <raw url>/install.ps1 | iex"
}

say() { printf '[aura] %s\n' "$*"; }

fail() {
  printf '[aura] %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [ -n "$TMPFILE" ]; then rm -f "$TMPFILE"; TMPFILE=""; fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# True when a terminal can be opened. Testing -r on /dev/tty is not enough: the node is there
# with no terminal behind it (cron, a container, CI), and opening it fails.
tty_ok() { (exec </dev/tty) 2>/dev/null; }

load_settings() {
  REPO_URL=${AURA_REPO_URL:-$AURA_REPO_DEFAULT}
  REF=${AURA_REF:-main}
  if [ -z "${AURA_HOME:-}" ] && [ -z "${HOME:-}" ]; then
    fail "HOME is not set: set AURA_HOME to the directory to install into"
  fi
  AHOME=${AURA_HOME:-$HOME/aura}
  # A trailing slash is dropped, and a path that is nothing but slashes is refused: the source
  # folder is emptied and rebuilt, and "/src" must never be that folder.
  while [ "${AHOME%/}" != "$AHOME" ]; do AHOME=${AHOME%/}; done
  [ -n "$AHOME" ] || fail "AURA_HOME must not be the root directory"
  # Absolute from here on: the installer writes this path into env files compose reads, and
  # compose takes a relative bind source for a volume's name.
  case $AHOME in /*) ;; *) AHOME=$(pwd)/$AHOME ;; esac
  BASE=${REPO_URL%/}
  BASE=${BASE%.git}
  TARBALL=${AURA_TARBALL_URL:-$BASE/archive/refs/heads/$REF.tar.gz}
  SRC=$AHOME/src
}

check_system() {
  OS=$(uname -s)
  case $OS in
    Linux | Darwin) ;;
    *)
      fail "this installer runs on Linux and macOS, not $OS. On Windows, run in PowerShell: irm $BASE/raw/$REF/install.ps1 | iex"
      ;;
  esac
}

# A missing prerequisite is said at once and counted; check_prereqs stops after naming them all.
problem() {
  PROBLEMS=$((PROBLEMS + 1))
  printf '[aura] missing: %s\n' "$1" >&2
}

download() { # download URL FILE
  if [ "$DOWNLOADER" = curl ]; then
    curl -fsSL "$1" -o "$2"
  else
    wget -qO "$2" "$1"
  fi
}

# Docker's own convenience script, run as root on a person's say-so: either the environment
# said it (AURA_INSTALL_DOCKER=1) or a terminal is there and the answer is y. A pipe is not a
# terminal, so `curl | sh` asks on /dev/tty, never on the script's own stdin.
want_docker() {
  [ "${AURA_INSTALL_DOCKER:-}" = 1 ] && return 0
  tty_ok || return 1
  printf '[aura] Docker is not installed. Install it with its own script from get.docker.com (runs as root)? [y/N] ' >/dev/tty
  ANSWER=""
  read -r ANSWER </dev/tty || ANSWER=""
  case $ANSWER in
    y | Y | yes | YES | Yes) return 0 ;;
  esac
  return 1
}

install_docker() {
  TMPFILE=$(mktemp "${TMPDIR:-/tmp}/aura-docker.XXXXXX")
  say "fetching Docker's installer from https://get.docker.com"
  if ! download https://get.docker.com "$TMPFILE"; then
    problem "Docker's installer could not be downloaded from https://get.docker.com"
    cleanup
    return 0
  fi
  if [ "$(id -u)" = 0 ]; then
    say "running Docker's installer"
    sh "$TMPFILE" || problem "Docker's installer failed (see its output above)"
  elif have sudo; then
    say "running Docker's installer through sudo"
    sudo sh "$TMPFILE" || problem "Docker's installer failed (see its output above)"
  else
    problem "Docker's installer needs root and there is no sudo: run this as root, or install Docker Engine yourself (https://docs.docker.com/engine/install/)"
  fi
  cleanup
}

check_prereqs() {
  PROBLEMS=0
  GIT=0
  DOWNLOADER=""
  have bash || problem "bash is missing (install it with your package manager; the installer runs under it)"
  if have git; then GIT=1; fi
  if have curl; then
    DOWNLOADER=curl
  elif have wget; then
    DOWNLOADER=wget
  fi
  if [ "$GIT" = 0 ] && { [ -z "$DOWNLOADER" ] || ! have tar; }; then
    problem "no way to fetch the source: install git, or curl (or wget) together with tar"
  fi

  if ! have docker; then
    if [ "$OS" = Darwin ]; then
      problem "docker is missing: install Docker Desktop (https://docs.docker.com/desktop/) and start it"
    elif [ "$PROBLEMS" -gt 0 ]; then
      # Nothing is installed as root while something else is still missing.
      problem "docker is missing (https://docs.docker.com/engine/install/)"
    elif [ -z "$DOWNLOADER" ]; then
      problem "docker is missing and there is no curl or wget to fetch Docker's installer: install Docker Engine (https://docs.docker.com/engine/install/)"
    elif want_docker; then
      install_docker
      have docker || problem "docker is still missing after its installer (https://docs.docker.com/engine/install/)"
    else
      problem "docker is missing: install Docker Engine (https://docs.docker.com/engine/install/), or run this again with AURA_INSTALL_DOCKER=1 to have it installed with Docker's own script"
    fi
  fi
  # stdin is the script itself when it came down a pipe: no child may read it.
  if have docker; then
    docker compose version >/dev/null 2>&1 </dev/null ||
      problem "the Docker Compose v2 plugin is missing (docker compose version fails; https://docs.docker.com/compose/install/)"
    docker info >/dev/null 2>&1 </dev/null ||
      problem "the Docker daemon is not reachable (docker info fails): start Docker, and on Linux make sure your user may use it (the docker group, or run this through sudo)"
  fi

  if [ "$PROBLEMS" -gt 0 ]; then
    fail "cannot go on until the above is fixed; nothing was fetched"
  fi
}

# The folder is ours to take only when it is empty. One with other files in it is somebody's
# (AURA_HOME may point at a folder that already has a src), and one with an earlier copy of the
# source is never removed either: a running house keeps its settings and what it has learned
# inside that copy (aura-core/.env, aura-core/config), and emptying it to fetch a fresh one
# would take them with it.
clear_source() {
  [ -e "$SRC" ] || return 0
  if [ -d "$SRC" ] && rmdir "$SRC" 2>/dev/null; then return 0; fi
  fail "$SRC exists and is not a copy of the source: move it away or set AURA_HOME"
}

fetch_with_git() {
  if [ -d "$SRC/.git" ]; then
    say "updating $SRC"
    # A re-run must not fail because the network is down: the source already there is used.
    if ! git -C "$SRC" pull --ff-only </dev/null; then
      say "pull failed; using the source already in $SRC"
    fi
  else
    clear_source
    say "cloning $REPO_URL ($REF) into $SRC"
    git clone --depth 1 --branch "$REF" "$REPO_URL" "$SRC" </dev/null ||
      fail "could not clone $REPO_URL at $REF"
  fi
}

fetch_tarball() {
  TMPFILE=$(mktemp "${TMPDIR:-/tmp}/aura-src.XXXXXX")
  if [ "$GIT" = 1 ]; then
    say "the source in $SRC came as an archive; downloading $TARBALL"
  else
    say "git is not installed; downloading $TARBALL"
  fi
  if ! download "$TARBALL" "$TMPFILE"; then
    if [ -f "$SRC/scripts/install.sh" ]; then
      say "download failed; using the source already in $SRC"
      cleanup
      return 0
    fi
    fail "could not download $TARBALL"
  fi
  # Download first: a failed download keeps the copy that works. An earlier copy is unpacked
  # over, never emptied (see clear_source), so a file removed upstream lingers in it.
  if [ ! -f "$SRC/scripts/install.sh" ]; then clear_source; fi
  mkdir -p "$SRC"
  tar -xzf "$TMPFILE" -C "$SRC" --strip-components 1 || fail "could not unpack $TARBALL"
  cleanup
}

fetch_source() {
  mkdir -p "$AHOME"
  # git updates a clone and fills an empty folder. A copy that came as an archive (no git at
  # the time) is refreshed the way it came, or used as it is where that cannot be done.
  if [ "$GIT" = 1 ] && { [ -d "$SRC/.git" ] || [ ! -f "$SRC/scripts/install.sh" ]; }; then
    fetch_with_git
  elif [ -n "$DOWNLOADER" ] && have tar; then
    fetch_tarball
  else
    say "the source in $SRC came as an archive and nothing here can refresh it (curl or wget, with tar); using it as it is"
  fi
  [ -f "$SRC/scripts/install.sh" ] ||
    fail "$SRC has no scripts/install.sh: is $REPO_URL at $REF the right source?"
}

# Our --dir comes first so one the caller passed later wins. When stdin is not a terminal (the
# script came down a pipe) the installer is given the terminal if there is one: it asks questions.
hand_over() {
  say "handing over to $SRC/scripts/install.sh"
  cleanup
  if [ -t 0 ]; then
    exec bash "$SRC/scripts/install.sh" --dir "$AHOME" "$@"
  fi
  if tty_ok; then
    exec bash "$SRC/scripts/install.sh" --dir "$AHOME" "$@" </dev/tty
  fi
  exec bash "$SRC/scripts/install.sh" --dir "$AHOME" "$@"
}

main() {
  for arg in "$@"; do
    case $arg in
      -h | --help)
        usage
        exit 0
        ;;
    esac
  done
  trap cleanup EXIT
  load_settings
  check_system
  check_prereqs
  fetch_source
  hand_over "$@"
}

# Everything above only defines functions: a download cut off part-way leaves a script that is
# either a syntax error or never reaches this line, so it runs nothing. Keep this the last line.
main "$@"
