#!/usr/bin/env bash
# post_tool_call shell hook: append one JSON line per tool call to
# $HERMES_HOME/logs/activity.jsonl so the HUD's activity feed and the nightly
# self-review can read what JARVIS did without parsing session transcripts.
payload="$(cat -)"
home="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$home/logs"
printf '%s\n' "$payload" | jq -c --arg ts "$(date -u +%FT%TZ)" \
  '{ts:$ts, event:.hook_event_name, tool:.tool_name, session:.session_id, args:(.tool_input|tostring|.[0:400]), error:(.error // null)}' \
  >> "$home/logs/activity.jsonl" 2>/dev/null
printf '{}\n'
