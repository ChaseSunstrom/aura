#!/usr/bin/env bash
# scripts/console-gate-check.sh — the deployed console refuses its relays to anyone not signed in.
#
#   CONSOLE_BASE=https://jarvis.home.example AURA_API_KEY=… scripts/console-gate-check.sh
#
# T-0387: since the house left Tailscale the console's port is on the LAN, and with no
# console password it lends the adapter key to whoever asks. The password lives only in
# the host's .env, so a deploy that loses it reopens the hole silently. This fails then.
#
# Walks the phone's path: nothing gets 401, the key gets 200, a keyed text/html page
# load gets a jarvis_console cookie, and that cookie alone gets 200. The session it
# opens is closed again at the end. Does not cover the sockets (the unit tests do) or a
# typed password (the e2e does).
set -uo pipefail
BASE="${CONSOLE_BASE:?set CONSOLE_BASE}"
KEY="${AURA_API_KEY:?set AURA_API_KEY}"
PROBE="$BASE/hermes/v1/models"
fail() { echo "FAIL: console gate: $*" >&2; exit 1; }
code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$@"; }

c=$(code "$PROBE"); [ "$c" = 401 ] || fail "an unsigned request got $c, not 401 (is AURA_CONSOLE_PASSWORD set?)"
# The router decodes `%61` to `a`; a gate that read the raw path let this one through.
c=$(code --path-as-is "$BASE/%61pi/tiles"); [ "$c" = 401 ] || fail "an encoded /api path got $c, not 401"
c=$(code -H "Authorization: Bearer $KEY" "$PROBE"); [ "$c" = 200 ] || fail "the adapter key got $c, not 200"
cookie=$(curl -s -o /dev/null -D - -m 10 -H "Authorization: Bearer $KEY" -H 'Accept: text/html' "$BASE/" \
  | tr -d '\r' | sed -nE 's/^set-cookie: (jarvis_console=[^;]+).*/\1/Ip' | head -1)
[ -n "$cookie" ] || fail "a keyed page load set no jarvis_console cookie"
c=$(code -H "Cookie: $cookie" "$PROBE")
curl -s -o /dev/null -m 10 -X DELETE -H "Cookie: $cookie" "$BASE/api/console"
[ "$c" = 200 ] || fail "the session cookie got $c, not 200"
echo "PASS: console gate (unsigned 401, key 200, page-load session 200)"
