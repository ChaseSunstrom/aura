#!/usr/bin/env bash
# deploy/n8n-env.sh <path> -- the secrets n8n and its sandbox need, generated once (T-0630).
#
# Makes <path> (0600) if missing, adds only the keys it lacks, never changes or prints a
# value. The deploy scripts and scripts/install.sh call it before `compose up`; compose
# reads the file through env_file, so none of this is interpolated from the owner's .env
# and none of it is in git. Run it twice and the second run writes nothing.
#
# The alias keys exist because the services name one secret differently (the API calls
# the key n8n sends N8N_SANDBOX_SERVICE_API_KEY, n8n calls it N8N_INSTANCE_AI_SANDBOX_API_KEY)
# and env_file cannot rename: the alias copies the source's value, whatever it is.
set -euo pipefail
umask 077

path=${1:?usage: n8n-env.sh <path>}
mkdir -p "$(dirname "$path")"
[ -e "$path" ] || : > "$path"
chmod 600 "$path"

# A file whose last line has no newline would glue our first key onto it.
if [ -s "$path" ] && [ -n "$(tail -c1 "$path")" ]; then echo >> "$path"; fi

secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else python3 -c 'import secrets; print(secrets.token_hex(32))'; fi
}
has() { grep -q "^$1=" "$path"; }
get() { grep -m1 "^$1=" "$path" | cut -d= -f2-; }

added=""
for key in SANDBOX_API_KEYS SANDBOX_N8N_API_KEY N8N_INSTANCE_AI_SANDBOX_API_KEY \
           SANDBOX_API_RUNNER_REGISTRATION_TOKEN SANDBOX_API_RUNNER_API_KEY N8N_ENCRYPTION_KEY; do
  has "$key" || { echo "$key=$(secret)" >> "$path"; added="$added $key"; }
done
for pair in N8N_SANDBOX_SERVICE_API_KEY:N8N_INSTANCE_AI_SANDBOX_API_KEY \
            SANDBOX_RUNNER_API_KEYS:SANDBOX_API_RUNNER_API_KEY \
            SANDBOX_RUNNER_REGISTRATION_TOKEN:SANDBOX_API_RUNNER_REGISTRATION_TOKEN; do
  alias=${pair%%:*}; source=${pair##*:}
  has "$alias" || { echo "$alias=$(get "$source")" >> "$path"; added="$added $alias"; }
done
echo "n8n-env: $path${added:+ gained$added}" >&2
