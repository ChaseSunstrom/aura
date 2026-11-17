#!/usr/bin/env bash
# Type-check the Hermes plugins the way the runtime loads them.
#
# The plugin directories have hyphenated names (comfyui-imagegen, gpu-telemetry, …) which are not
# valid Python package names. `mypy hermes/plugins` FATALs on the first one (exit 2, "contains
# __init__.py but is not a valid Python package name") and prints nothing — the false "plugins 0"
# baseline that the v2 baseline audit records. A per-dir `mypy <dir>` instead misreads each
# `__init__.py` as `__main__`, so `from . import tools` reports "Module __main__ has no attribute
# tools" — an invocation artifact, not a defect.
#
# Hermes loads each dir as a package aliased to its hyphen-free name
# (hermes/plugins/_plugin_loader.py: modname = dirname.replace("-", "_"), submodule search on the
# dir). This mirrors that: alias every plugin dir under a temp root via symlink, then run one mypy
# with `-p <alias>` per plugin so relative and cross-plugin (`_shared`) imports resolve exactly as at
# runtime. Exit non-zero if any plugin has a type error.
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugins="$here/hermes/plugins"
config="$here/mypy.ini"

[ -d "$plugins" ] || { echo "no $plugins" >&2; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pkgs=()
for d in "$plugins"/*/; do
	d="${d%/}"
	base="$(basename "$d")"
	[ "$base" = "__pycache__" ] && continue
	# Only real packages (an __init__.py); a bare tools dir with no package is not a plugin.
	[ -f "$d/__init__.py" ] || continue
	alias="${base//-/_}"
	ln -sfn "$d" "$tmp/$alias"
	pkgs+=("-p" "$alias")
done

[ "${#pkgs[@]}" -gt 0 ] || { echo "no plugin packages found under $plugins" >&2; exit 2; }

# aura-schema is on the path too: the plugins read their settings as typed attributes of
# `aura_schema.from_env()`, and an import that does not resolve is `Any` under
# ignore_missing_imports, so a misspelt or removed setting would pass the gate silently.
# The sibling `_shared` helpers the jarvis-* plugins import need nothing extra: the loop
# above aliases that dir under its own (already valid) name, so `from _shared... import`
# resolves through $tmp exactly as it does through the runtime sys.path shim.
MYPYPATH="$tmp:$here/aura-schema" exec mypy --config-file "$config" "${pkgs[@]}"
