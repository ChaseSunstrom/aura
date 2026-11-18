#!/usr/bin/env bash
# jarvis-entrypoint — runs as the container's main program (after s6's stage-2
# bootstrap has chowned the volume and seeded Hermes' defaults).
#
#   jarvis-entrypoint gateway run      # production (CMD)
#   jarvis-entrypoint <any hermes cmd> # e.g. `jarvis-entrypoint doctor`
#   jarvis-entrypoint                  # seed only, then exit — installed as
#                                      # /etc/cont-init.d/03-jarvis-seed so the volume is
#                                      # ready BEFORE s6 starts the dashboard/serve service
#                                      # (which discovers plugins once, at startup)
#
# FIRST start (no $HERMES_HOME/.jarvis-rendered): render config.yaml, SOUL.md,
# USER.md, MEMORY.md from the image's templates + environment and write the marker.
# LATER starts: add what is missing, and refresh SOUL.md/USER.md/MEMORY.md from
# the image *only* when the file is byte-identical to the one we last seeded — an
# operator's edit is never overwritten, and a correction shipped by the pipeline
# no longer stops at the first deployment (B9). config.yaml is exempt: it is
# upgraded in place by ensure_config_blocks/leaves instead.
# ALWAYS: sync code-like assets that belong to the image, not to the user
# (plugins, skills, agent-hooks, seeded model caches), so an image upgrade ships them.
set -euo pipefail
shopt -s nullglob

# s6-overlay runs cont-init scripts WITHOUT the container environment; it keeps
# the variables as files under /run/s6/container_environment. Import them so the
# render sees LLM_URL, API keys and friends in that path too.
if [ -d /run/s6/container_environment ]; then
  for f in /run/s6/container_environment/*; do
    name="$(basename "$f")"
    case "$name" in *[!A-Za-z0-9_]*) continue ;; esac
    export "$name=$(cat "$f")"
  done
fi

# Env names say AURA_ since T-0959, but the host's .env (which the repo may not rewrite)
# still says JARVIS_: each JARVIS_X is adopted as AURA_X unless AURA_X is set, here and in
# s6's environment, which is what the gateway's own services read. Names are logged, never values.
_adopted=""
for _k in $(compgen -e); do
  case "$_k" in JARVIS_*) ;; *) continue ;; esac
  _n="AURA_${_k#JARVIS_}"
  [ -n "${!_n+x}" ] && continue
  export "$_n=${!_k}"
  [ -d /run/s6/container_environment ] && printf '%s' "${!_k}" > "/run/s6/container_environment/$_n" 2>/dev/null
  _adopted="$_adopted $_k"
done
[ -z "$_adopted" ] || echo "[jarvis-entrypoint] env: legacy names read as AURA_*:$_adopted (rename them in .env when convenient)" >&2
unset _k _n _adopted

HERMES_HOME="${HERMES_HOME:-/opt/data}"
SEED=/opt/jarvis/seed
MARKER="$HERMES_HOME/.jarvis-rendered"
log() { echo "[jarvis-entrypoint] $*" >&2; }

# A value that is a swallowed comment is no value. Docker Compose's env_file
# parser keeps `KEY=   # a note` as the string "# a note" when the value before
# the comment is empty (v5.5.1, measured), and .env.example carried sixteen
# such lines: the delegated subagents' model became "# delegated subagents;
# empty = inherit the parent", llama-swap had no router for it, and every
# subagent died on its first call (2026-09-09). The example is fixed and a
# test refuses the shape; this is the guard for a host .env that still has one.
for name in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=#.*/\1/p'); do
  log "$name is a comment, not a value ('${!name}'); treating it as unset"
  unset "$name"
done

# The one way this script's python writes a file: every writer below runs through `py_atomic`,
# which puts this in front of the writer's own heredoc. It was two copies, each in a heredoc,
# and the other writers were plain open(..., "w"): a deploy recreates the container mid-start,
# and a kill after the truncate left a half config.yaml the gateway cannot start from, or a
# half cron/.seeded.json that re-seeds every job. test_config_upgrade.py scans for a plain write.
read -r -d '' WRITE_ATOMIC_PY <<'PYATOMIC' || true
import os, tempfile


def write_atomic(path, text):
    """Temp file in the same directory, then rename: a kill mid-write leaves the old file, not a
    truncated one. The entrypoint is root first and uid 10000 later, so an existing file's owner
    and mode are carried over; a file that does not exist yet gets what a plain write would give
    it (the umask), since the later chown of the volume is what hands it to the runtime user.
    Does not cover a power cut: the rename is atomic, the data is fsynced first."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        st = None
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix="." + os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if st is not None:
            os.chown(tmp, st.st_uid, st.st_gid)
            os.chmod(tmp, st.st_mode & 0o7777)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
PYATOMIC
py_atomic() {  # py_atomic <arg>... — python3 over the heredoc on stdin, write_atomic already defined
  python3 -c "$WRITE_ATOMIC_PY"$'\n'"$(cat)" "$@"
}

render() {  # render <template> <dest>  — ${VAR} substitution with envsubst-like semantics, no shell eval
  py_atomic "$1" "$2" <<'PY'
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
# An unset variable takes its setting's default from the schema. A setting added after a
# host's .env was written is unset there, and rendering it empty turned the default off:
# ACTIVITY_ENABLED (default true) reached production as `enabled:` and the activity view,
# which fails closed on an unreadable switch, captured nothing (2026-09-23).
try:
    from aura_schema import SETTINGS
    defaults = {s.env: s.default for s in SETTINGS if s.env and s.default is not None
                and isinstance(s.default, (bool, int, float, str))}
except Exception:  # noqa: BLE001 -- the schema absent: the old rule, empty
    defaults = {}
missing, defaulted = [], []
def sub(m):
    name = m.group(1)
    if name in os.environ:
        return os.environ[name]
    if name in defaults:
        value = defaults[name]
        defaulted.append(name)
        return ("true" if value else "false") if isinstance(value, bool) else str(value)
    missing.append(name)
    return ""
out = re.sub(r"\$\{([A-Z0-9_]+)\}", sub, text)
if defaulted:
    print(f"[jarvis-entrypoint] note: unset variables took the schema's default: {sorted(set(defaulted))}", file=sys.stderr)
if missing:
    print(f"[jarvis-entrypoint] note: unset variables rendered empty: {sorted(set(missing))}", file=sys.stderr)
write_atomic(dst, out)
PY
}

# `browser/profile` is the persistent Chrome profile (`browser.user_data_dir` in config.yaml)
# and `files` is where downloads and delivered files land — both on the volume, which is what
# makes a login and a downloaded file outlive a restart or a redeploy.
mkdir -p "$HERMES_HOME"/{memories,notes,work,plugins,skills,agent-hooks,cache,logs,files,browser/profile}
# A container that was replaced while Chrome held the profile (every deploy that lands during
# a browser task) leaves SingletonLock/Cookie/Socket pointing at the old container's hostname
# and pid, and Chrome then refuses the profile as "in use by another Chromium process on
# another computer", exit 21, with no dialog tool to say so: every browser tool failed on
# production from the deploy of 2026-09-18 11:50 until the links were removed by hand. A
# fresh container has no Chrome yet, so on start they are always stale.
rm -f "$HERMES_HOME"/browser/profile/Singleton{Lock,Cookie,Socket} 2>/dev/null || true

# `hermes doctor` looks for its own command at ~/.local/bin/hermes, and `hermes` in this
# image is a privilege-drop shim that runs as uid 10000, whose home IS this volume — so
# the link has to be made here, not in the image, and remade on every start because the
# volume outlives the image. Without it doctor reported one ✗ for ever (2026-09-07).
mkdir -p "$HERMES_HOME/.local/bin"
ln -sfn /opt/hermes/.venv/bin/hermes "$HERMES_HOME/.local/bin/hermes"
chown -R hermes:hermes "$HERMES_HOME/.local" 2>/dev/null || true

# Where the digest of the last-installed seed for each prose file is kept, so a
# later start can tell "the operator changed this" from "the image did".
SEED_STATE="$HERMES_HOME/.seed-digests"
mkdir -p "$SEED_STATE"

digest() { sha256sum "$1" | cut -d" " -f1; }

# The shell's counterpart of write_atomic above: stdin to <dst> through a temp file beside it,
# then a rename. `cp -f src target` and `echo > stamp` truncate first, so a start killed
# between that and the last byte (a deploy recreates the container mid-start) left a half
# SOUL.md or an empty digest, and seed_file read either as "this host has local edits" and
# never refreshed the file again. An existing dst keeps its owner and mode, as it did under
# `cp -f` (root now, the gateway's uid on the second pass); a new one gets the mode of
# <mode-from> when one is named, as `cp` gave it (a rendered MEMORY.md comes from a 0600
# mktemp file and must not turn readable by every user in the container), else what a
# redirect gives it (the umask). The temp is beside dst because a rename across filesystems
# is a copy.
# Does not cover a power cut (no fsync), and a kill leaves one dotted temp file behind.
atomic_write() {  # atomic_write <dst> [mode-from]  — content on stdin
  local dst="$1" like="${2:-}" tmp
  tmp="$(mktemp "$(dirname "$dst")/.$(basename "$dst").XXXXXX")"
  if cat > "$tmp"; then
    if [ -e "$dst" ]; then
      chown --reference="$dst" "$tmp" 2>/dev/null || true
      chmod --reference="$dst" "$tmp"
    elif [ -n "$like" ]; then
      chmod --reference="$like" "$tmp"
    else
      chmod "$(printf '%o' $((0666 & ~0$(umask))))" "$tmp"
    fi
    mv -f "$tmp" "$dst" && return 0
  fi
  rm -f "$tmp"
  return 1
}

# Install a seeded prose file, refreshing it only when nobody has edited it.
#
# The old rule was `if [ ! -e ]` — add what is missing, never touch what is
# there. That meant a corrected SOUL.md shipped by the pipeline never reached a
# host that had already started once, which is B9: the fix went out and the
# deployment kept the old text for ever. Copying unconditionally is the opposite
# failure and worse, because it would silently discard an operator's edits.
#
# So we remember the digest of what we last wrote. Still that? Then the file is
# ours and a newer image may update it. Different? Then somebody edited it, it is
# theirs, and we say so in the log rather than overwrite.
seed_file() {  # seed_file <src-under-$SEED> <dst-under-$HERMES_HOME>
  local src="$SEED/$1" dst="$2" target="$HERMES_HOME/$2" stamp new cur
  stamp="$SEED_STATE/${dst//\//_}.sha256"
  # A seed that names a setting (MEMORY.md's ${HOUSE_DOMAIN}, T-0841) is rendered first and its
  # digest is the rendered text's: a host whose value changes is refreshed like an image update,
  # and a seed with no ${…} keeps the digest it always had. It renders to a fresh temp file:
  # this script runs once as root and again as the gateway's uid, and a fixed path the root
  # pass wrote is one the second pass cannot overwrite, which crash-looped production
  # (2026-11-09). ponytail: the temp file stays in the container's /tmp, a few KB per start.
  if grep -q '\${[A-Z0-9_]*}' "$src"; then
    local rendered
    rendered="$(mktemp)"
    render "$src" "$rendered"
    src="$rendered"
  fi
  new="$(digest "$src")"

  if [ ! -e "$target" ]; then
    log "adding missing $dst"
    # shellcheck disable=SC2094  # $src is read twice (its bytes, its mode), never written
    atomic_write "$target" "$src" < "$src"
    echo "$new" | atomic_write "$stamp"
    return
  fi

  cur="$(digest "$target")"

  if [ ! -e "$stamp" ]; then
    # A volume seeded before this tracking existed. We cannot tell an operator's
    # edit from an older seed, so adopt it only when it is byte-identical to what
    # the image ships; otherwise leave it alone and write no stamp, which keeps
    # it in this branch and safe from every future start.
    if [ "$cur" = "$new" ]; then
      echo "$new" | atomic_write "$stamp"
    else
      log "$dst has no seed digest and differs from the image; leaving it untouched"
    fi
    return
  fi

  if [ "$cur" != "$(cat "$stamp")" ]; then
    [ "$new" = "$(cat "$stamp")" ] || log "not refreshing $dst: the image ships a new one and this host has local edits"
    return
  fi

  if [ "$new" != "$cur" ]; then
    log "refreshing $dst from the image (unmodified since it was seeded)"
    cp -f "$target" "$target.jarvis-prev" 2>/dev/null || true
    atomic_write "$target" < "$src"
    echo "$new" | atomic_write "$stamp"
  fi
}

# --- operator overrides of env-backed settings (ADR-0044) ---------------------------------
# The settings plugin cannot reach the host .env, so a value an operator sets for an
# env-backed leaf lands in $HERMES_HOME/.storage/environment.json ({"ENV_NAME": "value"},
# 0640, written by the gateway's uid). It is exported here, before config.yaml is rendered,
# so the render and every later step see it, and written into s6's container_environment
# too: in the official image the gateway is an s6 service that takes its environment from
# there, not from this shell. Every name goes through the schema (aura_schema.load_overrides)
# first. This runs as root, and a file the gateway's uid can write must never be able to name
# LD_PRELOAD or a path this script chowns: a name that is not an overridable JARVIS setting is
# dropped, and with the schema unimportable nothing is applied at all. Names are logged, values
# never — the file holds the same secrets the host .env does.
apply_env_overrides() {  # apply_env_overrides <environment.json>
  local file="$1" py=python3 name value
  [ -f "$file" ] || return 0
  [ -x /opt/hermes/.venv/bin/python ] && py=/opt/hermes/.venv/bin/python
  while IFS= read -r -d '' name && IFS= read -r -d '' value; do
    export "$name=$value"
    if [ -d /run/s6/container_environment ]; then
      printf '%s' "$value" > "/run/s6/container_environment/$name" 2>/dev/null || true
    fi
  done < <("$py" - "$file" <<'PYOVERRIDES'
import sys
path = sys.argv[1]
try:
    from aura_schema import load_overrides
except ImportError:
    print(f"[jarvis-entrypoint] overrides: aura_schema is not importable, {path} not applied", file=sys.stderr)
    sys.exit(0)
try:
    overrides = load_overrides(path)
except (OSError, ValueError) as exc:
    print(f"[jarvis-entrypoint] overrides: {path} unreadable ({exc}); none applied", file=sys.stderr)
    sys.exit(0)
for name in sorted(overrides):
    # NUL-separated pairs: a value may hold a newline, never a NUL (the env cannot either).
    sys.stdout.write(name + "\0" + overrides[name].replace("\0", "") + "\0")
sys.stdout.flush()
if overrides:
    print(f"[jarvis-entrypoint] overrides: {len(overrides)} applied from {path}: " + " ".join(sorted(overrides)), file=sys.stderr)
else:
    print(f"[jarvis-entrypoint] overrides: none in {path}", file=sys.stderr)
PYOVERRIDES
  )
}
apply_env_overrides "$HERMES_HOME/.storage/environment.json"

# The house's DNS zone (T-0841). The repo names no domain, so a host whose .env leaves
# HOUSE_DOMAIN unset gets the zone GitLab is in (gitlab.<zone> -> <zone>). The egress allowlist
# (_egress.house_suffix) and MEMORY.md's render read it; it goes into s6's environment too,
# since the dashboard's serve is an s6 service. A host with neither has no house zone, and then
# only the hosts its settings name are reachable.
if [ -z "${HOUSE_DOMAIN:-}" ] && [ -n "${GITLAB_API_URL:-}" ]; then
  gitlab_host="${GITLAB_API_URL#*://}"
  gitlab_host="${gitlab_host%%[/:]*}"
  if [ "${gitlab_host#*.}" != "$gitlab_host" ]; then export HOUSE_DOMAIN="${gitlab_host#*.}"; fi
fi
if [ -n "${HOUSE_DOMAIN:-}" ] && [ -d /run/s6/container_environment ]; then
  printf '%s' "$HOUSE_DOMAIN" > /run/s6/container_environment/HOUSE_DOMAIN 2>/dev/null || true
fi

if [ ! -e "$MARKER" ]; then
  log "first start: rendering config.yaml, SOUL.md, USER.md, MEMORY.md into $HERMES_HOME"
  render "$SEED/config.template.yaml" "$HERMES_HOME/config.yaml"
  # After the render above has finished, so a kill leaves no marker and the next start renders
  # again; the marker is still only tested with -e, never read, so a stray one changes nothing.
  date -u +%FT%TZ | atomic_write "$MARKER"
else
  # config.yaml is deliberately not in `seed_file`: it is rendered once and then
  # upgraded in place on every start by ensure_config_blocks/leaves, so its
  # content legitimately diverges from the template and a digest says nothing.
  if [ ! -e "$HERMES_HOME/config.yaml" ]; then
    log "adding missing config.yaml"
    render "$SEED/config.template.yaml" "$HERMES_HOME/config.yaml"
  fi
fi

for pair in "home/SOUL.md:SOUL.md" "home/MEMORY.md:memories/MEMORY.md"; do
  seed_file "${pair%%:*}" "${pair##*:}"
done
# USER.md is about a person, not about the product: the image ships a blank profile, and it is
# written only where there is none. Never refreshed, edited or not: the image's copy was the
# first owner's own profile until the release, every unedited host holds exactly those bytes,
# and a refresh would have replaced a profile in use with the blank one.
[ -e "$HERMES_HOME/memories/USER.md" ] || seed_file "home/USER.md" "memories/USER.md"

# Tool lessons about a plugin this image changes are set aside before the copy below makes
# the two trees equal (memory_health.py, T-0208). As the gateway's user: a WAL file root
# created beside memory_store.db would lock Hermes out of its own memory. Never fails a start.
if [ -f /opt/jarvis/bin/memory_health.py ] && command -v python3 >/dev/null; then
  if [ "$(id -u)" != 0 ]; then
    python3 /opt/jarvis/bin/memory_health.py "$HERMES_HOME" "$SEED/plugins" || true
  elif command -v s6-setuidgid >/dev/null; then
    s6-setuidgid hermes python3 /opt/jarvis/bin/memory_health.py "$HERMES_HOME" "$SEED/plugins" || true
  fi
fi

# The plugins and two skills moved from jarvis-<x> to aura-<x> (T-1517). The copy below
# adds aura-<x> beside the volume's old jarvis-<x>, and ensure_plugins_enabled only ever
# appends, so an upgraded volume would load both: two guards each gating every tool call,
# two copies of every tool. Each old folder goes, and the live config's names follow it:
# the plugin lists and the backends a plugin registers by name (web.extract_backend is
# not pinned from the env the way terminal.backend is). Only a name the image ships as
# aura-<x> is touched; a user's own jarvis-<x> plugin, or the jarvis-extract service's
# URL, is not. The plugin-data/<name> state folders keep their names on purpose.
rename_plugin_config() {  # rename_plugin_config <config.yaml> <old-name>...
  py_atomic "$@" <<'PYRENAME'
import re, sys
path, olds = sys.argv[1], sys.argv[2:]
try:
    text = open(path, encoding="utf-8").read()
except FileNotFoundError:
    sys.exit(0)
alts = "|".join(re.escape(o.removeprefix("jarvis-")) for o in olds)
# A whole value that is an old name: a list item (`- jarvis-guard`) or a one-line key
# (`extract_backend: jarvis-extract`), quoted or not, with or without a trailing comment.
rx = re.compile(rf"^(\s*(?:-\s+|[A-Za-z_][\w-]*:\s+))(['\"]?)jarvis-({alts})\2([ \t]*(?:#.*)?)$", re.M)
new, n = rx.subn(lambda m: f"{m.group(1)}{m.group(2)}aura-{m.group(3)}{m.group(2)}{m.group(4)}", text)
if new != text:
    write_atomic(path, new)
    print(f"[jarvis-entrypoint] config.yaml: {n} jarvis-* plugin name(s) now aura-*", file=sys.stderr)
PYRENAME
}
migrate_renamed() {  # the old folders out, then the config's names
  renamed=""
  for d in "$SEED"/plugins/aura-*/ "$SEED"/skills/aura-*/; do
    [ -d "$d" ] || continue
    kind="$(basename "$(dirname "$d")")"; new="$(basename "$d")"; old="jarvis-${new#aura-}"
    if [ -e "$HERMES_HOME/$kind/$old" ]; then
      log "$kind: removing '$old' (now $new)"
      rm -rf "${HERMES_HOME:?}/$kind/$old"
    fi
    # The curator may have filed the old skill under a category (skills/<category>/<name>).
    [ "$kind" = skills ] && find "$HERMES_HOME/skills" -mindepth 2 -maxdepth 2 -type d -name "$old" -exec rm -rf {} + 2>/dev/null
    [ "$kind" = plugins ] && renamed="$renamed $old"
  done
  # shellcheck disable=SC2086
  [ -z "$renamed" ] || rename_plugin_config "$HERMES_HOME/config.yaml" $renamed
}
migrate_renamed

# Code-like assets: the image is the source of truth. Existing user-authored
# plugins/skills with other names are left alone.
for d in "$SEED"/plugins/*/; do
  name="$(basename "$d")"; rm -rf "$HERMES_HOME/plugins/$name"; cp -r "$d" "$HERMES_HOME/plugins/$name"
done
# Enable every shipped plugin in the LIVE config on every start. config.yaml is rendered
# once, so a plugin a newer image ships stayed disabled on an older volume (pipeline 59:
# aura-hud copied in, `hud` not a command, the rollout rolled back). A text merge that
# appends the missing names under `plugins.enabled` and touches nothing else — not
# `hermes plugins enable`, which re-serialises the whole file with Hermes's defaults.
ensure_plugins_enabled() {  # ensure_plugins_enabled <config.yaml> <name>...
  py_atomic "$@" <<'PYPLUGINS'
import re, sys
path, names = sys.argv[1], sys.argv[2:]
try:
    text = open(path).read()
except FileNotFoundError:
    sys.exit(0)
lines = text.split("\n")
# find `plugins:` (top level) and its `enabled:` list
p = next((i for i, l in enumerate(lines) if l.strip() == "plugins:" and not l.startswith(" ")), None)
if p is None:
    block = ["plugins:", "  enabled:"] + [f"    - {n}" for n in names]
    if lines and lines[-1] == "": lines = lines[:-1]
    lines += [""] + block + [""]
else:
    e = next((i for i in range(p + 1, len(lines)) if lines[i].strip() == "enabled:" and lines[i].startswith("  ")), None)
    if e is None:
        lines[p + 1:p + 1] = ["  enabled:"] + [f"    - {n}" for n in names]
    else:
        i = e + 1; have = []
        while i < len(lines) and re.match(r"^\s+- ", lines[i]):
            have.append(lines[i].split("- ", 1)[1].strip().strip("'\""))
            i += 1
        indent = re.match(r"^(\s+)- ", lines[e + 1]).group(1) if i > e + 1 else "    "
        missing = [n for n in names if n not in have]
        lines[i:i] = [f"{indent}- {n}" for n in missing]
new = "\n".join(lines)
if new != text:
    write_atomic(path, new)
    print("[jarvis-entrypoint] plugins.enabled += " + " ".join(n for n in names if n not in text), file=sys.stderr)
PYPLUGINS
}
# Only manifested directories are plugins; `_shared/` (helpers the jarvis-* plugins import)
# is copied above so they resolve, but naming it under plugins.enabled would make Hermes
# look for a plugin that is not one.
shipped=""; for d in "$SEED"/plugins/*/; do [ -f "$d/plugin.yaml" ] && shipped="$shipped $(basename "$d")"; done
# Bundled Hermes plugins JARVIS depends on. These are not in $SEED — they ship inside
# the Hermes image — so the loop above cannot find them, and the template's
# plugins.enabled list only reaches a FIRST render. web-searxng ships disabled, so on an
# existing volume `web.search_backend: searxng` would name a provider that never
# registers and the registry would fall back to its keyless tier (Exa/Parallel's public
# MCP endpoints) — the cloud path this stack is meant not to have.
bundled="web-searxng"
# shellcheck disable=SC2086
[ -n "$shipped$bundled" ] && ensure_plugins_enabled "$HERMES_HOME/config.yaml" $shipped $bundled

# A top-level block a NEWER template introduces is added to the live config on every
# start. config.yaml is rendered once, so the routing table, the delegation block and the
# memory provider (D21/D22) would ship in the image and never take effect on a volume that
# already had a config — the same failure ensure_plugins_enabled exists for, one level up.
# Only *missing* top-level keys are appended: a block the operator has since edited keeps
# their version, which is the rule the render marker enforces for the file as a whole.
ensure_config_blocks() {  # ensure_config_blocks <config.yaml> <rendered-template>
  py_atomic "$@" <<'PYBLOCKS'
import re, sys
live_path, template_path = sys.argv[1], sys.argv[2]
try:
    live = open(live_path, encoding="utf-8").read()
    template = open(template_path, encoding="utf-8").read()
except FileNotFoundError:
    sys.exit(0)
top = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):")
lines = template.split("\n")
have = {m.group(1) for line in live.split("\n") if (m := top.match(line))}
starts = [i for i, line in enumerate(lines) if top.match(line)]
added = []
for n, i in enumerate(starts):
    name = top.match(lines[i]).group(1)
    if name in have:
        continue
    # Keep the comment lines directly above the block: they are what say why it is there.
    j = i
    while j > 0 and lines[j - 1].startswith("#"):
        j -= 1
    end = starts[n + 1] if n + 1 < len(starts) else len(lines)
    while end > i and (lines[end - 1].strip() == "" or lines[end - 1].startswith("#")):
        end -= 1
    added.append((name, "\n".join(lines[j:end])))
if not added:
    sys.exit(0)
out = live.rstrip("\n") + "\n"
for name, block in added:
    out += "\n" + block + "\n"
write_atomic(live_path, out)
print("[jarvis-entrypoint] config.yaml += " + " ".join(n for n, _ in added), file=sys.stderr)
PYBLOCKS
}
# One level deeper than ensure_config_blocks, and for the same reason. That function adds a
# whole top-level block a newer template introduces; a *leaf* a newer template adds INSIDE a
# block the volume already has is invisible to it, so it ships in the image and never takes
# effect. `stt.local.initial_prompt` is the case that found this: the live config has an
# `stt:` block, so the new hint would have sat in the image forever while the deployed
# recogniser kept hearing "Jarvis dismissed the notification" for "Jarvis, dismiss the
# notification".
#
# Named paths, not a deep merge. A merge would re-serialise the file (losing every comment
# that says why a setting is what it is — the thing ensure_plugins_enabled avoids) and would
# resurrect any leaf an operator deliberately deleted. This adds exactly the leaves listed
# here, only when they are absent, and never touches one that is present.
#
# A path written `=dotted.path` is PINNED: the env owns it, so the template's value
# replaces the live one on every start (a scalar on one line, nothing nested). That is
# what makes `TTS_PROVIDER=piper` in the host's .env mean anything on a volume whose
# config.yaml was rendered when the answer was piper, and `openai` (CosyVoice) on the
# next start after the default changed (2026-09-09). Only one-line scalars are pinned;
# a pinned mapping would need the re-serialising merge this file refuses.
# `delegation.max_concurrent_children` and `delegation.model` are pinned too: the
# volume kept the first start's 5 while the host's .env said 20, and the twenty-agent
# proof (P1.4, 2026-09-18) was refused at the first delegate_task with "max_concurrent
# _children is 5" — the depth leaf had been pinned, its neighbour had not.
# `model.base_url` and `providers.qwen-vllm.api` are pinned too: the separate LXC that served the
# model was deleted on 2026-10-09 and LLM_URL in .env moved to the house's own server, but
# every Hermes call kept going to the dead host ("Hermes can't reach the model provider"):
# the volume's config.yaml still had the first start's URL.
# `model.api_key` and `providers.qwen-vllm.api_key` are pinned for the same reason: llama-swap
# answers only LLM_API_KEY, and a volume rendered before the key existed would send none.
# `model.context_length` and its provider twin are pinned too: the volume kept the first
# start's 262144 while jarvis-models serves --max-model-len 256000, and LLM_CONTEXT_LENGTH
# in .env could not correct it (2026-10-09).
# `browser.cloud_provider` is pinned `local` (T-1519): on Hermes v0.23 an unset provider is
# auto-detected from whatever Browser Use or Browserbase credentials turn up, which would move
# every page the model opens to a cloud browser. `browser.user_data_dir` is only added.
CONFIG_LEAVES="=model.base_url =providers.qwen-vllm.api =model.api_key =providers.qwen-vllm.api_key =model.context_length =providers.qwen-vllm.context_length stt.local.initial_prompt providers.qwen-vllm.extra_body providers.qwen-vllm.extra_body.chat_template_kwargs tts.openai =tts.openai.voice =tts.provider model.supports_vision agent.image_input_mode model.max_tokens security.allow_lazy_installs delegation.max_spawn_depth =delegation.max_concurrent_children =delegation.model =terminal.backend terminal.container_persistent terminal.sandbox_dir =activity.enabled platform_toolsets.tui plugins.hook_callback_timeout =browser.cloud_provider browser.user_data_dir browser.chromium_args"
# The other direction: a list item an older template put on the volume and the
# current one no longer names. `ensure_config_leaves` only adds, so `context_engine`
# and `a2a` stayed in `tools.enabled` on production a week after the template
# dropped them (audit §10, 2026-09-18) — inert, since Hermes' own rules switch both
# off, but a config that lists what the image does not mean is the drift the
# audit keeps finding. Named items only: a list the operator extended by hand is
# theirs, and pruning "whatever the template lacks" would take those too.
RETIRED_TOOLSETS="context_engine a2a"
prune_config_list() {  # prune_config_list <config.yaml> <dotted.path> <item>... (a list's `- item` or a one-line `item: value`)
  py_atomic "$@" <<'PYPRUNE'
import os, re, sys
live_path, path, items = sys.argv[1], sys.argv[2].split("."), set(sys.argv[3:])
lines = open(live_path, encoding="utf-8").read().split("\n")
def indent_of(line):
    return len(line) - len(line.lstrip(" "))
def block(path):
    """(start, end) of the lines under a dotted path, walked by indentation the way
    find_leaf does below, or None when there is no such block."""
    start, end, indent = 0, len(lines), -1
    for key in path:
        found = None
        for i in range(start, end):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if indent_of(line) <= indent:
                break
            m = re.match(r"^(\s*)" + re.escape(key) + r":", line)
            if m and (indent == -1 or indent_of(line) > indent):
                found = i
                break
        if found is None:
            return None
        indent = indent_of(lines[found])
        start, end = found + 1, len(lines)
        # A `- ` at the key's own indent is that key's list, not its next sibling: PyYAML writes
        # `enabled:` then `- web` in one column. Ending the block there made `enabled` empty,
        # so its retired items were never pruned (T-0820).
        for j in range(start, len(lines)):
            s = lines[j].lstrip()
            if (s and not s.startswith("#") and indent_of(lines[j]) <= indent
                    and not (indent_of(lines[j]) == indent and re.match(r"-(\s|$)", s))):
                end = j
                break
    return start, end
def names(start, end):
    """Each line's name if it is a direct child that can go on its own, else None.
    Direct children only: the retired `extra_body.reasoning_effort` must not take the
    `extra_body.chat_template_kwargs.reasoning_effort` that replaced it. A key whose value
    is not on its line (a block beneath it, `|`, `>`, a flow `{`/`[`) never matches:
    removing its key line alone would leave the rest orphaned and the file unparseable."""
    child = min((indent_of(l) for l in lines[start:end] if l.strip() and not l.lstrip().startswith("#")), default=0)
    def name_of(line):
        if indent_of(line) != child:
            return None
        m = (re.match(r"^\s*-\s*([A-Za-z0-9_.-]+)\s*(#.*)?$", line)
             or re.match(r"^\s*([A-Za-z0-9_.-]+):\s*[^\s#|>{\[]", line))
        return m.group(1) if m else None
    return [name_of(l) for l in lines[start:end]]
at = block(path)
if at is None:
    sys.exit(0)  # no such block: nothing to prune
start, end = at
found = names(start, end)
removed = [n for n in found if n in items]
if not removed:
    sys.exit(0)
# PRUNE_ONLY_IF names the leaf that replaces what goes. Until it is there the old one
# stays: a volume whose operator wrote their own chat_template_kwargs gets no new default
# copied in, and pruning its old bound would leave every request with no level of its
# own unbounded (T-0814's review; the 4000-token alert turns of pipeline 145).
need = os.environ.get("PRUNE_ONLY_IF", "")
if need:
    *parent, leaf = need.split(".")
    under = block(parent)
    if under is None or leaf not in names(*under):
        print("[jarvis-entrypoint] config.yaml keeps " + ".".join(path) + ": " + " ".join(removed)
              + " until " + need + " is there", file=sys.stderr)
        sys.exit(0)
lines[start:end] = [l for l, n in zip(lines[start:end], found) if n not in items]
write_atomic(live_path, "\n".join(lines))
print("[jarvis-entrypoint] config.yaml -= " + ".".join(path) + ": " + " ".join(removed), file=sys.stderr)
PYPRUNE
}
ensure_config_leaves() {  # ensure_config_leaves <config.yaml> <rendered-template> <dotted.path>...
  py_atomic "$@" <<'PYLEAVES'
import os, re, sys
live_path, template_path, paths = sys.argv[1], sys.argv[2], sys.argv[3:]


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def in_block(line, indent):
    """True when `line` still belongs to the key at `indent`: blank, deeper, or a `- ` at the
    key's own indent. PyYAML writes `enabled:` then `- web` in one column; ending the block at
    that first dash cut the list off its key (prune_config_list had the same fault, T-0820)."""
    s = line.lstrip()
    return (not s or indent_of(line) > indent
            or (indent_of(line) == indent and re.match(r"-(\s|$)", s) is not None))


def block_range(lines, start, end, key, indent):
    """(line index of `key:` at `indent`, index one past its children) or None."""
    want = re.compile(r"^ {%d}%s\s*:" % (indent, re.escape(key)))
    for i in range(start, end):
        if want.match(lines[i]):
            j = i + 1
            while j < end and (lines[j].lstrip().startswith("#") or in_block(lines[j], indent)):
                j += 1
            return i, j
    return None


def find_leaf(lines, path):
    """(insert-at, child indent, line index of the leaf or None) for a dotted path."""
    start, end, indent = 0, len(lines), 0
    for key in path[:-1]:
        found = block_range(lines, start, end, key, indent)
        if found is None:
            return None
        start, end = found[0] + 1, found[1]
        child = next((indent_of(lines[i]) for i in range(start, end)
                      if lines[i].strip() and not lines[i].lstrip().startswith("#")), indent + 2)
        indent = child
    leaf = block_range(lines, start, end, path[-1], indent)
    at = end
    while at > start and not lines[at - 1].strip():
        at -= 1
    return at, indent, (leaf[0] if leaf else None)


live = open(live_path, encoding="utf-8").read().split("\n")
template = open(template_path, encoding="utf-8").read().split("\n")
added = []
for dotted in paths:
    pinned = dotted.startswith("=")
    dotted = dotted.lstrip("=")
    path = dotted.split(".")
    src = find_leaf(template, path)
    if src is None or src[2] is None:
        continue  # the template does not carry it (yet) — nothing to add
    dst = find_leaf(live, path)
    if dst is None:
        continue  # the block is missing: ensure_config_blocks' job, not this one
    if pinned and dst[2] is not None:
        # The env's value, verbatim from the rendered template, over whatever the
        # volume has; the live line's indent is kept because it is the live file's.
        want = template[src[2]].split(":", 1)[1]
        have = live[dst[2]].split(":", 1)[1]
        if want.strip() and want.strip() != have.strip():
            live[dst[2]] = live[dst[2]].split(":", 1)[0] + ":" + want
            added.append(dotted + " (pinned: " + have.strip() + " -> " + want.strip() + ")")
        continue
    if dst[2] is not None:
        # The leaf is there. If it is a bare key with nothing under it while the
        # template's has children, it is the empty key the one-line copier left on
        # volumes upgraded by pipeline 146, and the children go in beneath it.
        leaf_line = live[dst[2]]
        has_children = dst[2] + 1 < len(live) and live[dst[2] + 1].strip() != "" and in_block(live[dst[2] + 1], indent_of(leaf_line))
        tpl_has_children = src[2] + 1 < len(template) and template[src[2] + 1].strip() != "" and in_block(template[src[2] + 1], indent_of(template[src[2]]))
        if has_children or not tpl_has_children or leaf_line.rstrip().split(":", 1)[1].strip() != "":
            continue
        leaf_indent = indent_of(template[src[2]])
        end = src[2] + 1
        while end < len(template) and in_block(template[end], leaf_indent):
            end += 1
        while end > src[2] + 1 and template[end - 1].strip() == "":
            end -= 1
        shift = indent_of(leaf_line) - leaf_indent
        children = [(" " * (indent_of(line) + shift) + line.lstrip(" ")) if line.strip() else "" for line in template[src[2] + 1:end]]
        live[dst[2] + 1:dst[2] + 1] = children
        added.append(dotted + " (children of an empty key)")
        continue
    # The leaf and everything nested under it: a mapping leaf (`extra_body:` with
    # `reasoning_effort:` beneath) copied as one line lands as an empty key, which
    # is what pipeline 146 did — the live config gained `extra_body:` and nothing
    # under it, and the bound never reached the model server. Children are the
    # following lines indented deeper than the leaf; blank lines inside the block
    # travel with it, trailing ones do not.
    leaf_indent = indent_of(template[src[2]])
    end = src[2] + 1
    while end < len(template) and in_block(template[end], leaf_indent):
        end += 1
    while end > src[2] + 1 and template[end - 1].strip() == "":
        end -= 1
    block = template[src[2]:end]
    shift = dst[1] - leaf_indent
    moved = [(" " * (indent_of(line) + shift) + line.lstrip(" ")) if line.strip() else "" for line in block]
    live[dst[0]:dst[0]] = moved
    added.append(dotted)
# A leaf whose value is a swallowed comment -- `model: "# delegated subagents; empty =
# inherit the parent"` -- was rendered from a host .env line of the form `KEY=   # note`
# (Docker Compose keeps the note as the value). The env guard above stops it happening
# again, but a config.yaml rendered before the guard still carries it, and every
# subagent on this host died on "no router for requested model" a full deploy after the
# .env was fixed (2026-09-10). Any such leaf takes the template's line for the same
# path: a value that is a comment is no value, and the template's is the env's.
comment_valued = re.compile(r'^(\s*)([A-Za-z0-9_.-]+):\s*"#[^"]*"\s*$')
stack: list[tuple[int, str]] = []
for i, line in enumerate(live):
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    m = re.match(r"^(\s*)([A-Za-z0-9_.-]+):", line)
    if not m:
        continue
    indent = len(m.group(1))
    while stack and stack[-1][0] >= indent:
        stack.pop()
    stack.append((indent, m.group(2)))
    if not comment_valued.match(line):
        continue
    dotted = ".".join(key for _, key in stack)
    src = find_leaf(template, dotted.split("."))
    if src is None or src[2] is None:
        continue
    live[i] = m.group(1) + template[src[2]].lstrip(" ")
    added.append(dotted + " (was a comment: " + line.strip() + ")")
if added:
    write_atomic(live_path, "\n".join(live))
    print("[jarvis-entrypoint] config.yaml += " + " ".join(added), file=sys.stderr)
PYLEAVES
}

if [ -e "$MARKER" ]; then
  rendered_template="$(mktemp)"
  render "$SEED/config.template.yaml" "$rendered_template"
  ensure_config_blocks "$HERMES_HOME/config.yaml" "$rendered_template"
  # shellcheck disable=SC2086
  ensure_config_leaves "$HERMES_HOME/config.yaml" "$rendered_template" $CONFIG_LEAVES
  # shellcheck disable=SC2086
  prune_config_list "$HERMES_HOME/config.yaml" tools.enabled $RETIRED_TOOLSETS
  # T-0937: T-0935 opted kanban in with Hermes' legacy top-level `toolsets:` list, which reached
  # webhook, telegram, discord and cron as well as tui; the template's tui list (CONFIG_LEAVES) is
  # the grant now, so the legacy item goes from a volume that took it.
  prune_config_list "$HERMES_HOME/config.yaml" toolsets kanban
  # T-0814: the provider's default reasoning level moved under chat_template_kwargs
  # (CONFIG_LEAVES brings it); at extra_body's top level it overwrote every session's own.
  PRUNE_ONLY_IF=providers.qwen-vllm.extra_body.chat_template_kwargs.reasoning_effort \
    prune_config_list "$HERMES_HOME/config.yaml" providers.qwen-vllm.extra_body reasoning_effort
  rm -f "$rendered_template"
fi
if [ -d "$SEED/skills" ]; then
  for d in "$SEED"/skills/*/; do
    name="$(basename "$d")"
    rm -rf "$HERMES_HOME/skills/$name"
    # The curator files a skill it wrote under a category (skills/homelab/aura-voice); once
    # that skill is curated into the repo it seeds at the top level, and both copies would
    # then declare the same `name:` and appear twice in the skills index. Drop the nested
    # copy — the repo is the source of truth for a seeded skill — without touching the
    # bundled skills that share its category directory.
    find "$HERMES_HOME/skills" -mindepth 2 -maxdepth 2 -type d -name "$name" -exec rm -rf {} + 2>/dev/null || true
    cp -r "$d" "$HERMES_HOME/skills/$name"
  done
fi
# Skills the image retires. A skill on the volume that tells the model to reach a service
# by shell walks around the typed tool and its row in the approval matrix: `gitlab` opens
# merge requests through the API where selfext_open_mr is the gated tool, `outbound-email`
# probes for himalaya/msmtp/swaks and a raw SMTP port where the mail plugin is, `himalaya`
# documents a CLI the image does not carry, and `calendar-lookup` reads an .ics out of
# notes/ where calendar_events is (2026-09-18). Three were written at runtime, `himalaya` is
# a Hermes bundle; all four are handled the way plugins are — gone on every start, logged —
# and the name goes into Hermes' own `.curator_suppressed`, which its bundled-skill sync (it
# runs after this, and would seed `himalaya` onto a fresh volume) honours before it copies
# anything. A skill at any other path is never touched.
RETIRED_SKILLS="productivity/calendar-lookup email/outbound-email email/himalaya gitlab"
for rel in $RETIRED_SKILLS; do
  if [ -e "$HERMES_HOME/skills/$rel" ]; then
    log "skills: removing '$rel' (retired by the image)"
    rm -rf "$HERMES_HOME/skills/$rel"
  fi
  grep -qxF "${rel##*/}" "$HERMES_HOME/skills/.curator_suppressed" 2>/dev/null \
    || echo "${rel##*/}" >> "$HERMES_HOME/skills/.curator_suppressed"
done
cp -f "$SEED"/agent-hooks/* "$HERMES_HOME/agent-hooks/" 2>/dev/null || true
chmod +x "$HERMES_HOME"/agent-hooks/* 2>/dev/null || true
# Cron scripts are code the scheduler runs (`--script <name>` resolves under this dir), so
# they follow the plugins/skills rule: the image owns them and an upgrade replaces them.
if [ -d "$SEED/cron/scripts" ]; then
  mkdir -p "$HERMES_HOME/scripts"
  cp -f "$SEED"/cron/scripts/* "$HERMES_HOME/scripts/" 2>/dev/null || true
  chmod +x "$HERMES_HOME"/scripts/* 2>/dev/null || true
fi

# --- the self-extension checkout (D24) ---------------------------------------------------
# aura-selfext hides every selfext_* tool until $SELFEXT_REPO_PATH is a git work tree, and
# nothing ever made one: on every deployment so far the loop was invisible, and the four
# selfext bench tasks ran against tools the model could not see (2026-09-18). The build
# context carries no .git (.dockerignore keeps it to the seed assets), so the checkout comes
# from the tree baked at $SEED/repo: a `git archive` of the image's commit that build:jarvis
# writes as deploy/seed-repo.tar and the Dockerfile ADDs. An image built without one (every
# one before 2026-09-18) says so on every start and does nothing; a later deploy never
# refreshes a checkout that already exists. An empty `git init` is deliberately not the
# fallback: selfext_verify runs the repository's own gate inside the checkout, so the tools
# would appear and fail on first use — the case the plugin's `available()` exists to
# prevent. The checkout's remote has no password: selfext_open_mr's push alone reads
# GITLAB_TOKEN from the gateway's environment through a one-command credential helper.
provision_selfext_checkout() {  # provision_selfext_checkout <checkout> <baked tree>
  local dst="$1" src="$2"
  [ -e "$dst/.git" ] && return 0
  if [ ! -d "$src" ]; then
    log "selfext: no checkout at $dst — the image carries no source tree at $src, so the selfext_* tools stay hidden"
    return 0
  fi
  mkdir -p "$dst" && cp -a "$src/." "$dst/"
  if [ ! -e "$dst/.git" ]; then
    git -C "$dst" init -q -b "${SELFEXT_TARGET_BRANCH:-main}"
    git -C "$dst" -c user.name=jarvis -c user.email=jarvis@localhost add -A
    git -C "$dst" -c user.name=jarvis -c user.email=jarvis@localhost \
      commit -q -m "The image's tree, committed by jarvis-entrypoint"
  fi
  [ "$(id -u)" != 0 ] || chown -R hermes:hermes "$dst" 2>/dev/null || true
  log "selfext: checkout provisioned at $dst from $src"
}
provision_selfext_checkout "${SELFEXT_REPO_PATH:-$HERMES_HOME/work/jarvis}" "$SEED/repo"

# --- the scheduled work the image ships -------------------------------------------------
# Seeded on EVERY start, not only the first. A brief or a watchdog in hermes/cron/jobs.tsv is
# a code-like asset like a plugin or a skill: an image that could not change it would freeze
# the 07:00 brief at whatever the first-ever start of the volume wrote (the same failure
# `ensure_plugins_enabled` exists for, one directory over).
#
# A cron store is live state, though, so the seeder is narrow: `.seeded.json` records what it
# put there, and it touches ONLY jobs whose name appears in jobs.tsv. A job JARVIS created
# for itself, or one the owner wrote, is never removed or rewritten. A shipped name that is
# present but unrecorded (seeded by an older image, before this manifest existed) is adopted
# — a job called "Evening summary" is the one this file ships, whatever wrote it.
cron_plan() {  # cron_plan <rendered jobs.tsv> — one \x1f-separated action per line
  python3 - "$1" "$HERMES_HOME" <<'PYCRONPLAN'
import base64, hashlib, json, os, sys

tsv_path, home = sys.argv[1], sys.argv[2]
# The field separator for the plan this prints. NOT a tab: bash treats every whitespace
# character in IFS as a *run* separator, so two adjacent tabs around an empty `script`
# column collapse into one and every field after it shifts left by one.
SEP = "\x1f"
# `house` is JARVIS' own delivery mode, not one of Hermes': the scheduler stores the output
# and the job pushes it through house_tell_user itself (the console's Notifications panel
# and any paired device), which is the only path that reaches the owner while no messaging
# platform is connected. Any other value goes to --deliver untouched.
#
# The scheduler prepends its own "DELIVERY: your final response will be automatically
# delivered — do NOT deliver it yourself" to EVERY cron prompt, even a `local` one it then
# delivers nowhere. That line contradicted the old suffix, the model sided with it and
# hedged, and every shipped job ran for a week without a result reaching anyone
# (2026-09-18). So this suffix names that line and overrides it in so many words.
HOUSE_PUSH = (
    "\n\nDELIVERY: the scheduler's delivery note above does not apply to this job: its "
    "deliver target is local and nothing is delivered unless you call house_tell_user once "
    "with the finished text; it lands on the console and on any paired phone. Then end your "
    "response with the same text. A reply of exactly [SILENT] is not delivered — do not "
    "call house_tell_user for it. If the push fails, say so plainly in your final response."
)
rows, retire, unmet = [], [], []
with open(tsv_path, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        parts = (line.split("\t") + ["", "", "", "", ""])[:6]
        name, schedule, deliver, script, prompt, needs = (p.strip() for p in parts)
        if not name:
            continue
        if schedule == "-":
            retire.append(name)
            continue
        # A line that names the settings it needs ships only where one of them is set. The GPU
        # watchdog reads Grafana or InfluxDB; on an install with neither it sat on the home as
        # the next routine and its first tick told the owner to check a Grafana nobody had
        # set up (T-1502). Spaces are not a value.
        wanted = [n.strip() for n in needs.split("|") if n.strip()]
        if wanted and not any(os.environ.get(n, "").strip() for n in wanted):
            unmet.append((name, "needs " + " or ".join(wanted) + ", not set on this install"))
            continue
        deliver = deliver or "house"
        if deliver == "house":
            deliver_flag = "local"
            # A script with no prompt is a no-agent job (stdout is the result); a script with
            # a prompt is the model's gate (watch-check.py) and the model still pushes.
            if prompt or not script:
                prompt += HOUSE_PUSH
        else:
            deliver_flag = deliver
        digest = hashlib.sha256("\x00".join(
            [schedule, deliver_flag, script, prompt]).encode("utf-8")).hexdigest()[:16]
        rows.append((name, schedule, deliver_flag, script, prompt, digest))

def _names(path, key=None):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and key:
        data = data.get(key) or []
    if isinstance(data, list):
        return {str(j.get("name")): j for j in data if isinstance(j, dict) and j.get("name")}
    return data if isinstance(data, dict) else {}

live = _names(os.path.join(home, "cron", "jobs.json"), "jobs")
seeded = _names(os.path.join(home, "cron", ".seeded.json"))
shipped = {r[0] for r in rows}

for name in retire:
    if name in live and name not in shipped:
        print(SEP.join(["REMOVE", name, "retired by the image"]))
# Taken back only when this seeder's own record says it created it: with no source set, a job
# of that name the record does not hold is the owner's or JARVIS' own and is left as it is.
for name, why in unmet:
    if name in live and name in seeded:
        print(SEP.join(["REMOVE", name, why]))
    elif name not in live:
        print(SEP.join(["SKIP", name, why]))
for name, schedule, deliver, script, prompt, digest in rows:
    if name in live and seeded.get(name) == digest:
        continue
    if name in live:
        why = "changed in jobs.tsv" if name in seeded else "adopting a job an older image seeded"
        print(SEP.join(["REMOVE", name, why]))
    # The prompt rides base64: it is the only field that can hold a newline, and one
    # newline in it would end the record halfway through and hand `read` a line of
    # nonsense as the next job.
    body = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    print(SEP.join(["CREATE", name, schedule, deliver, script, digest, body]))
PYCRONPLAN
}

cron_record() {  # cron_record <name> <digest> — remember one job this start created; an empty digest forgets it
  py_atomic "$HERMES_HOME" "$1" "$2" <<'PYCRONREC'
import json, os, sys
home, name, digest = sys.argv[1], sys.argv[2], sys.argv[3]
path = os.path.join(home, "cron", ".seeded.json")
try:
    data = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
if digest:
    data[name] = digest
else:
    # Removed, so no longer ours: a record left behind would make the seeder take back a job
    # of the same name that the owner creates afterwards.
    data.pop(name, None)
os.makedirs(os.path.dirname(path), exist_ok=True)
write_atomic(path, json.dumps(data, indent=1, sort_keys=True))
PYCRONREC
}

hermes_as_runtime_user() {  # run `hermes …` the way the gateway will
  # stdin closed, always: the only caller runs inside `while read` over the seeding plan, and
  # a child that read one byte of that stream would silently swallow the next job.
  if [ "$(id -u)" = 0 ] && command -v s6-setuidgid >/dev/null; then
    HOME="$HERMES_HOME" s6-setuidgid hermes hermes "$@" < /dev/null
  else
    hermes "$@" < /dev/null
  fi
}

if [ -f "$SEED/cron/jobs.tsv" ] && command -v hermes >/dev/null; then
  cron_tsv="$(mktemp)"
  render "$SEED/cron/jobs.tsv" "$cron_tsv"
  while IFS=$'\x1f' read -r action name a b c d e; do  # CREATE: schedule deliver script digest prompt-b64
    case "$action" in
      REMOVE)
        log "cron: removing '$name' ($a)"
        if hermes_as_runtime_user cron remove "$name" >/dev/null 2>&1; then
          cron_record "$name" ""
        else
          log "cron: could not remove '$name'"
        fi
        ;;
      SKIP)
        log "cron: leaving out '$name' ($a)"
        ;;
      CREATE)
        log "cron: creating '$name' ($a, deliver $b${c:+, script $c})"
        # An array, not `set --`: the script's own "$@" is the hermes command line it execs
        # at the end, and overwriting it here would turn `gateway run` into a cron argv.
        argv=(cron create "$a" --name "$name" --deliver "$b")
        prompt="$(printf %s "$e" | base64 -d)"
        # A script alone is the job (no model); a script with a prompt gates the model, which
        # runs only when the script's last line is not {"wakeAgent": false}.
        if [ -n "$c" ] && [ -z "$prompt" ]; then argv+=(--script "$c" --no-agent)
        elif [ -n "$c" ]; then argv+=(--script "$c"); fi
        [ -n "$prompt" ] && argv+=("$prompt")
        if hermes_as_runtime_user "${argv[@]}" >/dev/null 2>&1; then
          cron_record "$name" "$d"
        else
          log "cron: could not create '$name'"
        fi
        ;;
    esac
  done < <(cron_plan "$cron_tsv")
  rm -f "$cron_tsv"
elif [ -f "$SEED/cron/jobs.tsv" ]; then
  log "cron: hermes not on PATH, no scheduled jobs seeded"
fi

# Model caches seeded from the image so the first voice turn does not download anything.
if [ -d "$SEED/cache/piper-voices" ] && [ ! -e "$HERMES_HOME/cache/piper-voices/.seeded" ]; then
  mkdir -p "$HERMES_HOME/cache/piper-voices"; cp -n "$SEED"/cache/piper-voices/* "$HERMES_HOME/cache/piper-voices/" || true
  touch "$HERMES_HOME/cache/piper-voices/.seeded"
fi
if [ -d "$SEED/cache/hf" ] && [ ! -e "$HERMES_HOME/cache/hf/.seeded" ]; then
  mkdir -p "$HERMES_HOME/cache/hf"; cp -rn "$SEED"/cache/hf/. "$HERMES_HOME/cache/hf/" || true
  touch "$HERMES_HOME/cache/hf/.seeded"
fi
export HF_HOME="${HF_HOME:-$HERMES_HOME/cache/hf}"

# When run as root (cont-init), everything seeded must belong to the runtime user. This list
# mirrors the mkdir near the top, and `files` and `browser` were missing from it: root 0755
# on the volume, so the gateway (uid hermes) could not save a download or a delivered file
# into the directory the file-delivery skill names, and
# Chrome could not write the profile that is meant to keep a login across restarts
# (2026-09-18, bench browser-download-003). test_config_render.py pins the two lists.
if [ "$(id -u)" = 0 ] && id hermes >/dev/null 2>&1; then
  for f in config.yaml SOUL.md memories plugins skills agent-hooks cache notes work logs files browser .jarvis-rendered; do
    [ -e "$HERMES_HOME/$f" ] && chown -R hermes:hermes "$HERMES_HOME/$f" 2>/dev/null || true
  done
fi

# Never persist secrets into the volume: Hermes reads them from the environment.
if [ -z "${API_SERVER_KEY:-}" ]; then log "WARNING: API_SERVER_KEY is not set — the API server will refuse to start"; fi

# Say it every start, until someone fixes it. On 2026-09-07 three of this host's four
# secrets were still the dev placeholders the repository's fixtures use — the apps'
# bearer was `devadapterkey0123456789`, which is in `HermesPairingTest.kt` — so anyone
# who had read the repository could open the console and talk to the adapter. Nothing
# refuses to start over it: that would take a running house down at the worst moment,
# and the person who can rotate a key is not the person reading this at 3am. A loud
# line on every start is the pressure that is safe to apply.
for _k in API_SERVER_KEY HERMES_SERVE_TOKEN AURA_API_KEY AURA_CONSOLE_PASSWORD; do
  # Indirect expansion, not `eval`: a password holding a `$` or a quote would be
  # re-parsed as shell by an eval, and the one thing this loop must never do is
  # execute a secret.
  _v="${!_k:-}"
  case "$_v" in
    dev*|test*|change*|ci-*|"") [ -n "$_v" ] && log "SECURITY: $_k is still a placeholder ($(printf '%s' "$_v" | cut -c1-4)…) — rotate it (README, Key rotation)" ;;
  esac
done
unset _k _v

if [ "$#" -eq 0 ]; then
  log "seed complete"
  exit 0
fi

# `gateway run` in the official image: s6 owns the per-profile gateway service
# (auto-restart on crash, state restored across container restarts by the boot
# reconciler). A foreground gateway next to it would be refused as a duplicate,
# so start the supervised slot and hold the container open as its main program.
if [ "${1:-}" = "gateway" ] && [ "${2:-}" = "run" ] && [ -d /run/service ]; then
  log "starting the s6-supervised gateway (hermes gateway start)"
  hermes gateway start 2>&1 | sed 's/^/[jarvis-entrypoint] /' >&2 || true
  exec sleep infinity
fi
log "starting: hermes $*"
exec hermes "$@"
