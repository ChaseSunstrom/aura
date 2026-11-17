#!/usr/bin/env bash
# scripts/verify/private-chat-canary.sh — prove, on the running stack, that a private chat
# leaves no trace (T-0462, docs/explanation/private-chats.md).
#
# Says a one-off word in a private chat and speaks it privately, then looks for that word in
# every container's log since, in Hermes' whole home (sessions, state.db, memory, logs), in
# the adapter's artifacts and data, and in the voice and model containers' scratch space. It
# also checks that the model server keeps no copy of requests (captureBuffer: 0).
#
# Does not cover: the browser or phone that typed the line, vLLM's KV cache on the GPU (RAM,
# overwritten by the next request), or a disk write that a grep cannot read (a compressed
# file). It needs the deployed stack and the app key (AURA_API_KEY, else read from
# /opt/jarvis-host/.env without printing it).
#
#   AURA_BASE=http://<the adapter's LAN address>:8643 bash scripts/verify/private-chat-canary.sh
source "$(dirname "$0")/lib.sh"

BASE="${AURA_BASE:?set AURA_BASE to the adapter, e.g. http://10.20.30.40:8643}"
KEY="${AURA_API_KEY:-$(grep -m1 -E '^(AURA|JARVIS)_API_KEY=' /opt/jarvis-host/.env 2>/dev/null | cut -d= -f2-)}"
CANARY="private-canary-$(date +%s)-$RANDOM$RANDOM"
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
export BASE KEY CANARY OUT

verify_begin "T-0462" "a private chat is never logged or stored"

check "the app key is known (AURA_API_KEY or /opt/jarvis-host/.env)" test -n "$KEY"

check_sh "a private chat answers, streamed, to the end" '
    curl -sS -N --max-time 180 -o "$OUT/chat" -w "%{http_code}" \
        -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
        -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Remember the word $CANARY and say it back.\"}]}" \
        "$BASE/api/private/chat" | grep -qx 200 && grep -q "data: \[DONE\]" "$OUT/chat" \
        && ! grep -q "\"error\"" "$OUT/chat"'

check_sh "a private line is spoken" '
    curl -sS --max-time 120 -o "$OUT/pcm" -w "%{http_code}" \
        -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
        -d "{\"text\":\"Noted, $CANARY.\",\"private\":true}" \
        "$BASE/api/audio/speak-pcm" | grep -qx 200 && test -s "$OUT/pcm"'

# grep exits 1 when it found nothing; 0 (found) and 2 (could not read) both fail, so a missing
# container or an unreadable store is never read as "nothing there". The one error forgiven is
# a file gone between listing and reading (a SQLite -wal checkpointed mid-scan).
nowhere() {
    local rc=0 err
    err="$("$@" 2>&1 >/dev/null)" || rc=$?
    if [ "$rc" -eq 2 ] && ! grep -qv 'No such file or directory$' <<<"$err"; then rc=1; fi
    [ "$rc" -eq 1 ] || { printf '%s\n' "$err"; "$@" 2>/dev/null; return 1; }
}
for c in jarvis jarvis-adapter jarvis-cosyvoice jarvis-models jarvis-core jarvis-gateway; do
    check "$c's log since the chat was read" sh -c "docker logs --since '$SINCE' $c >'$OUT/$c.log' 2>&1"
    check "$c logged nothing of it" nowhere grep -F -- "$CANARY" "$OUT/$c.log"
done
check "the adapter did not log that a private chat was asked" \
    nowhere grep -F /api/private "$OUT/jarvis-adapter.log"

check "Hermes' home holds nothing of it (sessions, state.db, memory, logs)" \
    nowhere docker exec -u 0 jarvis grep -rlaF -- "$CANARY" /opt/data /tmp
check "the adapter's artifacts, data and scratch hold nothing of it" \
    nowhere docker exec -u 0 jarvis-adapter grep -rlaF -- "$CANARY" /opt/artifacts /opt/data /tmp
# The voice card and the house drop every capability, so their root cannot read their own
# user's 0600 files ("Permission denied", a failed search); they are searched as that user.
check "the voice card's scratch holds nothing of it" \
    nowhere docker exec jarvis-cosyvoice grep -rlaF -- "$CANARY" /tmp /dev/shm
check "the house holds nothing of it" \
    nowhere docker exec jarvis-core grep -rlaF -- "$CANARY" /config /tmp
check_sh "the model server keeps no copy of requests (captureBuffer: 0)" \
    'docker exec jarvis-models sh -c "grep -qx \"captureBuffer: 0\" \$(tr \"\\0\" \"\\n\" </proc/1/cmdline | sed -n \"/^-config\$/{n;p;q}\")"'

verify_end
