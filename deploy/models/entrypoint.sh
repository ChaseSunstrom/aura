#!/bin/sh
# jarvis-models entrypoint: seed the per-model configs on a first start, then run llama-swap.
#
# Each model is one file under $AURA_MODELS_CONFIG, so the console can add, edit or remove
# a model by writing one file, and llama-swap's -watch-config picks it up without a restart.
# Seeding copies only files that are missing: a model the owner deleted from the UI is
# recorded in .removed and never comes back on the next start.
set -eu
CONF="${AURA_MODELS_CONFIG:-/models/config}"
SEED=/opt/jarvis-models/seed
mkdir -p "$CONF" "${HF_HOME:-/models/hf}"
touch "$CONF/.removed"
for f in "$SEED"/models/*.yaml; do
  name=$(basename "$f")
  [ -e "$CONF/$name" ] && continue
  grep -qx "$name" "$CONF/.removed" && continue
  cp "$f" "$CONF/$name"
  echo "$name" >> "$CONF/.fresh"  # the manager fetches these weights once (manager.fetch_fresh)
  echo "jarvis-models: seeded $name"
done
# The model manager (the console's Models panel API, :8001, compose network only). It is the one
# process here that must reach the Hub, so it starts before the offline switch below: huggingface_hub
# reads HF_HUB_OFFLINE once at import, and the llama-swap children inherit the switch, not the manager.
# Its failure must not take the LLM server down with it, hence the background and the log line.
if [ -f /opt/jarvis-models/manager.py ]; then
  (HF_HUB_OFFLINE=0 python3 /opt/jarvis-models/manager.py || echo "jarvis-models: manager exited ($?)") &
fi
# Offline unless a download is under way: a model is loaded from the local cache, and a cache
# miss must fail at once rather than start a 20 GB download inside a request.
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
#
# llama-swap answers only LLM_API_KEY (apiKeys). The key goes into a runtime copy of the base
# config, never the seed or $CONF (the console edits that and the manager's validate reads it).
# JSON is valid YAML, so json.dumps quotes any key safely. Unset, it stays open as before.
BASE="$SEED/base.yaml"
if [ -n "${LLM_API_KEY:-}" ] || [ -n "${PRELOAD_MODEL:-}" ]; then
  BASE="$(mktemp -d)/base.yaml"
  (umask 077; cp "$SEED/base.yaml" "$BASE")
fi
if [ -n "${LLM_API_KEY:-}" ]; then
  python3 -c 'import json,os;print("\napiKeys: ["+json.dumps(os.environ["LLM_API_KEY"])+"]")' >> "$BASE"
else
  echo "jarvis-models: LLM_API_KEY is not set: llama-swap answers every container on the host" >&2
fi
# Hermes's model loads at boot, not at its first turn: after a restart that turn waited for
# the whole load, 385 s on 2026-10-09 (T-0348). Compose passes LLM_MODEL; unset, nothing loads.
if [ -n "${PRELOAD_MODEL:-}" ]; then
  python3 -c 'import json,os;print("\nhooks: {on_startup: {preload: ["+json.dumps(os.environ["PRELOAD_MODEL"])+"]}}")' >> "$BASE"
fi
# A failure mark from before a restart or reboot (the container's /tmp survives both) would hold
# this start unhealthy through its whole load; only this start's own outcome counts (T-0411).
rm -f /tmp/jarvis-models-start-failed
exec llama-swap -config "$BASE" -config-dir "$CONF" -watch-config -listen ":${PORT:-8000}"
