#!/usr/bin/env bash
# No cloud web provider — search or extraction — is configured anywhere (V2 §4, D19).
#
# The failure this exists to catch is silent. Hermes picks a web backend from its
# provider registry, and when nothing is configured it walks a legacy preference
# order and then a KEYLESS tier: Exa's and Parallel's public MCP endpoints, called
# anonymously with no credential at all (agent/web_search_registry.py). So "we set
# no API keys" is NOT the same as "no cloud provider is in use" — it is how this
# stack ended up sending every page it read to two cloud services. Nothing warns.
#
# Three things therefore have to hold at once, and this script checks all three:
#
#   1. no cloud provider credential is named anywhere the stack reads config from;
#   2. both backends are named explicitly, and both name a service on our network;
#   3. the keyless tier is off, so a broken local backend fails loudly rather than
#      quietly going online.
#
# What it does NOT prove: that the running gateway resolved the providers it was
# told to. Config and behaviour are different claims — scripts/verify/web-providers.py
# asks a live Hermes which provider it actually selected. Run that too.
#
#   scripts/verify/no-cloud-web-providers.sh          # the files: what a deploy will carry
#   scripts/verify/no-cloud-web-providers.sh --live   # also every config.yaml on this host
#
# The live pass is opt-in because it answers a different question. The files say what the
# next deploy installs; a live config.yaml says what a box is running *now*, and a volume
# rendered before this change keeps its old web block until the entrypoint's block merge
# runs on the next start. The offline gate checks the files, which is the part a merge
# request can be responsible for; --live is what an operator runs on a host.
source "$(dirname "$0")/lib.sh"
LIVE=0
[ "${1:-}" = "--live" ] && LIVE=1
verify_begin "WEB" "no cloud search or extraction provider is configured"

# The credentials every cloud web/browser backend Hermes ships would activate, plus
# the ones the brief names that Hermes does not ship (a plugin someone adds later
# would read the same conventional name). FIRECRAWL_API_URL is here too: a
# self-hosted Firecrawl is local, but the same variable with a cloud host is not,
# and this stack chose Trafilatura instead — either way it must not appear.
CLOUD_VARS='FIRECRAWL_API_KEY|FIRECRAWL_API_URL|FIRECRAWL_GATEWAY_URL|EXA_API_KEY|PARALLEL_API_KEY|BRAVE_SEARCH_API_KEY|BRAVE_API_KEY|KEENABLE_API_KEY|XAI_API_KEY|TAVILY_API_KEY|SERPER_API_KEY|SERPAPI_API_KEY|JINA_API_KEY|SCRAPINGBEE_API_KEY|SCRAPERAPI_KEY|BING_SEARCH_KEY|BING_SUBSCRIPTION_KEY|AZURE_BING_KEY|GOOGLE_CSE_KEY|GOOGLE_SEARCH_API_KEY|BROWSERBASE_API_KEY|BROWSER_USE_API_KEY|TOOL_GATEWAY_USER_TOKEN|TOOL_GATEWAY_DOMAIN'

# The backend names a config file could name. `web-searxng` and `aura-extract`
# are ours; everything else in Hermes' registry is somebody's cloud.
CLOUD_BACKENDS='firecrawl|exa|parallel|brave-free|keenable|xai|tavily|serper|jina|scrapingbee|browserbase|browser-use'

# ---------------------------------------------------------------- 1. credentials
#
# `git grep` over tracked files only: an untracked scratch file is nobody's
# configuration, and .venv/ and node_modules/ carry these names in their own
# vendored code. This script and the docs that explain the rule are excluded by
# path — a check that fails on its own subject matter teaches people to skip it.
check_sh_not "no cloud provider credential is set in a tracked file" \
    "git grep -nIE '^[^#]*($CLOUD_VARS)[[:space:]]*[=:]' -- \
        ':!scripts/verify/no-cloud-web-providers.sh' \
        ':!docs/' ':!DEVIATIONS.md' \
        ':!deploy/extract/service.py' ':!hermes/plugins/aura-extract/'"

check_not "no cloud provider credential is in deploy/.env.example" \
    grep -nE "^($CLOUD_VARS)=" deploy/.env.example

check_not "no cloud provider credential is in the root .env.example" \
    grep -nE "^($CLOUD_VARS)=" .env.example

# The generated config template is what the container renders on first start. A
# cloud backend named there would outlive any .env.
check_not "no cloud backend is named in the rendered config template" \
    grep -nE "_backend:[[:space:]]*[\"']?($CLOUD_BACKENDS)\b" deploy/config.template.yaml

# ------------------------------------------------------------------ 2. both ours
check "search goes to the stack's SearXNG" \
    grep -qE '^[[:space:]]*search_backend:[[:space:]]*searxng[[:space:]]*$' deploy/config.template.yaml

check "extraction goes to the stack's own service" \
    grep -qE '^[[:space:]]*extract_backend:[[:space:]]*aura-extract[[:space:]]*$' deploy/config.template.yaml

# Naming a backend whose plugin is not enabled leaves the provider unregistered and
# the registry falls back — the exact hole this file is about.
check_sh "both backends' plugins are enabled in the template" \
    "grep -qE '^[[:space:]]*- web-searxng[[:space:]]*\$' deploy/config.template.yaml && \
     grep -qE '^[[:space:]]*- aura-extract[[:space:]]*\$' deploy/config.template.yaml"

check_sh "every bundled cloud web/browser plugin is named in plugins.disabled" \
    "for p in web-firecrawl web-exa web-parallel web-brave-free web-keenable web-xai web-ddgs \
              browser-browser-use browser-browserbase browser-firecrawl; do
         grep -qE \"^[[:space:]]*- \$p[[:space:]]*\\\$\" deploy/config.template.yaml || { echo \"not disabled: \$p\"; exit 1; }
     done; echo 'all ten named'"

# ------------------------------------------------------------------ 3. no keyless
check "the keyless tier (Exa/Parallel public MCP) is off" \
    grep -qE '^[[:space:]]*keyless_fallback:[[:space:]]*false[[:space:]]*$' deploy/config.template.yaml

check "the keyless rescue path is off too" \
    grep -qE '^[[:space:]]*keyless_rescue:[[:space:]]*false[[:space:]]*$' deploy/config.template.yaml

# ------------------------------------------------------- the config actually live
#
# The template is only what a FIRST start renders; later starts never overwrite
# $HERMES_HOME/config.yaml (deploy/entrypoint.sh). So a stack deployed before this
# change keeps its old web block, and the template passing above says nothing about
# it. Every config.yaml this host can see is checked, and the check fails when it can
# see none — under --live, "there was nothing to look at" must not read as green.
if [ "$LIVE" = 1 ]; then
check_sh "every reachable live config.yaml agrees with the template" '
    set -o pipefail
    found=0
    check_one() {  # $1 = label, $2 = file contents on stdin
        local label="$1" body="$2"
        printf "%s" "$body" | grep -qE "^[[:space:]]*search_backend:[[:space:]]*searxng" \
            || { echo "$label: search_backend is not searxng"; return 1; }
        printf "%s" "$body" | grep -qE "^[[:space:]]*extract_backend:[[:space:]]*aura-extract" \
            || { echo "$label: extract_backend is not aura-extract"; return 1; }
        printf "%s" "$body" | grep -qE "^[[:space:]]*keyless_fallback:[[:space:]]*false" \
            || { echo "$label: keyless_fallback is not false"; return 1; }
        echo "$label: searxng + aura-extract, keyless off"
    }
    if [ -f "${HERMES_HOME:-$HOME/.hermes}/config.yaml" ]; then
        found=1
        check_one "dev box" "$(cat "${HERMES_HOME:-$HOME/.hermes}/config.yaml")" || exit 1
    fi
    for c in $(docker ps --format "{{.Names}}" 2>/dev/null | grep -E "^jarvis$|^jarvis-ci-[0-9]+$|^jarvis-bench-[0-9]+$" || true); do
        body=$(docker exec "$c" cat /opt/data/config.yaml 2>/dev/null) || continue
        found=1
        check_one "container $c" "$body" || exit 1
    done
    [ "$found" = 1 ] || { echo "no config.yaml reachable on this host — nothing was checked"; exit 1; }
'
else
    printf "  note  live config.yaml not inspected (pass --live on a host that runs one)\n"
fi

verify_end
