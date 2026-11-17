#!/usr/bin/env bash
# jarvis-backup — encrypt the Hermes volume (/opt/data, mounted read-only) into /backups.
#
#   jarvis-backup once          # one encrypted backup now (+ a copy in $BACKUP_MIRROR_DIR)
#   jarvis-backup loop          # nightly at 03:15 (container TZ), the drill on Sundays
#   jarvis-backup drill [FILE]  # restore the newest backup into a scratch dir and read it back
#   jarvis-backup restore FILE  # print the restore procedure (documented in README)
#
# Each backup is `hermes-home-<utc>.tar.gz.age`: tar | gzip | age, piped, so no clear tarball
# ever exists on disk. Keeps the newest $BACKUP_KEEP (default 14) in /backups and in the mirror.
# Sessions DB and caches are included; the piper/hf caches are excluded (re-seeded from the image).
#
#   BACKUP_AGE_RECIPIENT  age public key (age1…) the backups are encrypted to. Required by `once`.
#   BACKUP_AGE_IDENTITY   path to the matching private key, in this container. Only `drill` reads
#                         it; the nightly never needs it.
#   BACKUP_MIRROR_DIR     a second location (a mount on another disk or host). Optional for
#                         `once`; the drill fails while it is unset, because a backup on the
#                         disk it protects is one copy.
#
#   BACKUP_STATUS_DIR     where `once` and `drill` leave `nightly.status` / `drill.status` (one line:
#                         kind, epoch, ok|failed) for the daily health check. Optional: unset, no
#                         status is written and nothing else changes.
#
# Does not cover: the identity (lose it and every backup is noise: keep a copy off this host),
# or a mirror that is only another directory on the same disk.
set -euo pipefail
SRC="${HERMES_HOME:-/opt/data}"
DST="${BACKUP_DEST:-/backups}"
KEEP="${BACKUP_KEEP:-14}"
MIRROR="${BACKUP_MIRROR_DIR:-}"
RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
IDENTITY="${BACKUP_AGE_IDENTITY:-}"
STATUS_DIR="${BACKUP_STATUS_DIR:-}"
PATTERN='hermes-home-*.tar.gz.age'
# A KEEP of 0 would make prune delete the backup just written (tail -n +1 is everything).
if [[ "$KEEP" =~ ^0*[1-9][0-9]*$ ]]; then
  KEEP=$((10#$KEEP))
else
  echo "[jarvis-backup] BACKUP_KEEP=$KEEP is not a whole number of 1 or more: keeping 14" >&2
  KEEP=14
fi

# newest-first by name: the timestamp is in it, an mtime moves when a file is copied
listing() { ls -1 "$1"/$PATTERN 2>/dev/null | sort -r || true; }
prune() {
  listing "$1" | tail -n +$((KEEP + 1)) | xargs -r -d '\n' rm -f --
  echo "[jarvis-backup] keeping $(listing "$1" | wc -l) backup(s) in $1"
}

# `loop` runs once and drill as `once || …`, where bash ignores set -e: every step that can fail
# is checked by hand, or a failed mkdir would go on to write into the wrong place.
once() {
  if [ -z "$RECIPIENT" ]; then
    echo "[jarvis-backup] FAILED: BACKUP_AGE_RECIPIENT is not set, nothing written (a backup is never written in clear)" >&2
    return 2
  fi
  mkdir -p "$DST" || { echo "[jarvis-backup] FAILED: cannot create $DST, nothing written" >&2; return 1; }
  local name="hermes-home-$(date -u +%Y%m%dT%H%M%SZ).tar.gz.age" rc=0 arc=0 ps
  # tar exits 1 when a file changed while it was read, and a live volume always
  # has one (the sessions database, a WAL). Thirteen of sixteen nightly runs on
  # the host were left as `.part` for that alone (2026-09-02 to 09-17), the
  # "wrote" line printing an empty size for a file that was never renamed, and
  # nothing pruning the parts. Exit 1 is a warning; 2 and above is a failure and
  # leaves no half-written backup behind. pipefail would report tar's 1 as the
  # pipeline's, so each side's status is read from PIPESTATUS.
  set +e
  tar --warning=no-file-changed --exclude='./cache/hf' --exclude='./cache/piper-voices' \
      --exclude='./logs/*.log' -C "$SRC" -czf - . | age -r "$RECIPIENT" -o "$DST/$name.part"
  ps=("${PIPESTATUS[@]}"); rc=${ps[0]}; arc=${ps[1]}  # the first assignment would reset PIPESTATUS
  set -e
  if [ "$rc" -gt 1 ] || [ "$arc" -ne 0 ]; then
    rm -f "$DST/$name.part"
    echo "[jarvis-backup] FAILED: tar exit $rc, age exit $arc, nothing written" >&2
    return "$(( rc > 1 ? rc : arc ))"
  fi
  mv "$DST/$name.part" "$DST/$name" || {
    rm -f "$DST/$name.part"
    echo "[jarvis-backup] FAILED: could not move the finished backup into place" >&2
    return 1
  }
  echo "[jarvis-backup] wrote $DST/$name ($(du -h "$DST/$name" | cut -f1))"
  prune "$DST"
  [ -n "$MIRROR" ] || { echo "[jarvis-backup] BACKUP_MIRROR_DIR is not set: this is the only copy" >&2; return 0; }
  # A failed mirror is reported, not fatal to the local copy that already exists.
  if mkdir -p "$MIRROR" && cp "$DST/$name" "$MIRROR/$name.part" && mv "$MIRROR/$name.part" "$MIRROR/$name"; then
    echo "[jarvis-backup] mirrored to $MIRROR/$name"
    prune "$MIRROR"
  else
    rm -f "$MIRROR/$name.part" 2>/dev/null || true
    echo "[jarvis-backup] FAILED: the mirror copy to $MIRROR did not complete (the local backup is kept)" >&2
    return 3
  fi
}

# Reads the restored home back: config parses, the sessions DB passes its own integrity check
# and lists a table, at least one skill has its SKILL.md. Prints one line per thing that failed.
readback() {
  python3 - "$1" <<'PY'
import sqlite3, sys
from pathlib import Path

home, bad, ok = Path(sys.argv[1]), [], []
cfg = home / "config.yaml"
try:
    import yaml
    doc = yaml.safe_load(cfg.read_text())
    if not isinstance(doc, dict) or not doc:
        raise ValueError("it is empty or not a mapping")
    ok.append("config.yaml")
except Exception as exc:
    bad.append(f"config.yaml did not read back: {exc}".splitlines()[0])
db = home / "state.db"
try:
    if not db.is_file():
        raise FileNotFoundError("the file is not in the backup")
    con = sqlite3.connect(db)
    try:
        verdict = con.execute("pragma integrity_check").fetchone()[0]
        tables = con.execute("select count(*) from sqlite_master where type = 'table'").fetchone()[0]
    finally:
        con.close()
    if verdict != "ok" or not tables:
        raise ValueError(f"integrity_check says {verdict!r}, {tables} table(s)")
    ok.append(f"state.db (integrity ok, {tables} tables)")
except Exception as exc:
    bad.append(f"state.db (the sessions database) did not read back: {exc}".splitlines()[0])
skills = sorted((home / "skills").glob("**/SKILL.md")) if (home / "skills").is_dir() else []
if skills:
    ok.append(f"{len(skills)} skill(s)")
else:
    bad.append("skills/ has no SKILL.md in it")
for line in bad:
    print(line)
sys.exit(1 if bad else 0)
PY
}

# exit, not return: drill runs in a subshell, and the loop calls it under `||`, where set -e
# is off and a returned 1 would let the next check run on a restore that already failed.
fail() { echo "[jarvis-backup] DRILL FAILED: $*" >&2; exit 1; }

# Restore the newest backup into a scratch dir and read it back. Touches nothing else: not
# the live volume (it never reads $SRC), not the backups (read only). Exit 0 only if every
# check passes, including that the newest file also exists, byte for byte, in the mirror.
drill() (
  local file="${1:-}" scratch err rc arc out ps
  [ -n "$file" ] || file="$(listing "$DST" | head -n 1)"
  [ -n "$file" ] && [ -f "$file" ] || fail "no backup to restore in $DST"
  [ -n "$IDENTITY" ] || fail "BACKUP_AGE_IDENTITY is not set: nothing can decrypt $file"
  [ -r "$IDENTITY" ] || fail "BACKUP_AGE_IDENTITY ($IDENTITY) is not a readable file"
  # Under `||` set -e is off: an empty scratch here would extract into /home and run rm -rf "".
  scratch="$(mktemp -d)" || fail "cannot create a scratch dir (TMPDIR=${TMPDIR:-/tmp})"
  [ -n "$scratch" ] && [ -d "$scratch" ] || fail "cannot create a scratch dir (TMPDIR=${TMPDIR:-/tmp})"
  trap 'rm -rf "$scratch"' EXIT
  err="$scratch/age.err"
  mkdir "$scratch/home" || fail "cannot create $scratch/home"
  set +e
  age -d -i "$IDENTITY" "$file" 2>"$err" | tar -xz -C "$scratch/home" 2>>"$err"
  ps=("${PIPESTATUS[@]}"); arc=${ps[0]}; rc=${ps[1]}
  set -e
  if [ "$arc" -ne 0 ]; then
    if grep -qi "no identity matched" "$err"; then
      fail "the wrong identity: $IDENTITY does not open $(basename "$file")"
    fi
    fail "$(basename "$file") is corrupt or truncated, or the scratch space is full (age said: $(grep -v '^$' "$err" | head -n 1))"
  fi
  [ "$rc" -eq 0 ] || fail "$(basename "$file") decrypts but is corrupt or truncated inside, or the scratch space is full (tar said: $(grep -v '^$' "$err" | head -n 1))"
  out="$(readback "$scratch/home")" || fail "$(basename "$file") restored, but $(echo "$out" | paste -sd ';' - | sed 's/;/; /g')"
  if [ -z "$MIRROR" ]; then
    fail "restored and read back, but there is no second copy: BACKUP_MIRROR_DIR is not set"
  elif [ ! -f "$MIRROR/$(basename "$file")" ] || ! cmp -s "$file" "$MIRROR/$(basename "$file")"; then
    fail "restored and read back, but there is no second copy of $(basename "$file") in $MIRROR (missing or different)"
  fi
  echo "[jarvis-backup] DRILL OK: $(basename "$file") restored to a scratch home; config.yaml, state.db and skills read back; mirror copy identical"
)

# One loud line per unset owner setting, so a half-configured stack says so at start and not only
# at 03:15 (or the first Sunday).
preflight() {
  [ -n "$RECIPIENT" ] || echo "[jarvis-backup] WARNING: BACKUP_AGE_RECIPIENT is not set: no backups at all will be written (a backup is never written in clear)" >&2
  [ -n "$IDENTITY" ] || echo "[jarvis-backup] WARNING: BACKUP_AGE_IDENTITY is not set: no drill can run, so no backup is proven restorable" >&2
  [ -n "$MIRROR" ] || echo "[jarvis-backup] WARNING: BACKUP_MIRROR_DIR is not set: there is one copy only, and the drill fails" >&2
}

# The daily health check (aura-hud/health.py) cannot read the backups or this log, and must not:
# the assistant never sees the backup directory. So each run leaves one line, `kind epoch ok|failed`,
# in a small volume the assistant mounts read-only. No path, key or file name goes in it. Written
# to a temp name and moved, so a reader never sees half a line. It never changes the exit code or
# the drill's verdict: a status that cannot be written is one stderr line, not a failed backup.
write_status() {
  [ -n "$STATUS_DIR" ] || return 0
  local verdict=failed tmp="$STATUS_DIR/.$1.status.$$"
  [ "$2" -eq 0 ] && verdict=ok
  { mkdir -p "$STATUS_DIR" && printf '%s %s %s\n' "$1" "$(date +%s)" "$verdict" > "$tmp" \
      && mv "$tmp" "$STATUS_DIR/$1.status"; } 2>/dev/null \
    || { rm -f "$tmp" 2>/dev/null; echo "[jarvis-backup] could not write the $1 status to $STATUS_DIR (the health check will not see this run)" >&2; }
  return 0
}
# `once`/`drill` as the entry points call them: the result is recorded, then returned unchanged.
run_once() { local rc=0; once || rc=$?; write_status nightly "$rc"; return "$rc"; }
run_drill() { local rc=0; drill "$@" || rc=$?; write_status drill "$rc"; return "$rc"; }

# Sourced (by the tests), the functions are defined and nothing runs.
[ "${BASH_SOURCE[0]}" = "$0" ] || return 0

case "${1:-once}" in
  once) run_once ;;
  drill) shift; run_drill "$@" ;;
  loop)
    preflight
    echo "[jarvis-backup] nightly at 03:15 ($(date +%Z)), keep $KEEP, drill on Sundays"
    while true; do
      now=$(date +%s); target=$(date -d "today 03:15" +%s); [ "$target" -le "$now" ] && target=$(date -d "tomorrow 03:15" +%s)
      sleep $((target - now)); run_once || echo "[jarvis-backup] FAILED" >&2
      # The nightly's own file is the newest, so Sunday's drill proves the backup just written.
      [ "$(date +%u)" = 7 ] && { run_drill || echo "[jarvis-backup] DRILL FAILED: the newest backup is not proven restorable" >&2; }
    done ;;
  restore)
    cat <<EOF
Restore procedure (on the JARVIS host, stack stopped; needs age and the identity file):
  cd /opt/jarvis && docker compose down
  age -d -i /path/to/backup-age.key ${BACKUP_DIR:-/opt/jarvis/backups}/${2:-hermes-home-<timestamp>.tar.gz.age} | \\
    docker run --rm -i -v jarvis-hermes-home:/opt/data alpine \\
      sh -c 'rm -rf /opt/data/* && tar -C /opt/data -xzf - && chown -R 10000:10000 /opt/data'
  docker compose up -d && curl -fsS http://\$TAILSCALE_IP:8642/health
EOF
    ;;
  *) echo "usage: jarvis-backup once|loop|drill [FILE]|restore FILE" >&2; exit 2 ;;
esac
