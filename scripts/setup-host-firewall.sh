#!/usr/bin/env bash
# scripts/setup-host-firewall.sh — let the stack's containers reach jarvis-core (the house
# service, host network, port 8080) through the host firewall. The JARVIS host runs ufw with
# INPUT DROP and only the Tailscale interface allowed, so the console's relay to jarvis-core
# and Hermes's Home Assistant toolset timed out from inside the containers (2026-09-03).
# Idempotent; needs root. Only the Docker private ranges, only that port.
#
#   sudo scripts/setup-host-firewall.sh [port]
#   sudo LAN_ALLOW=<your LAN>/24 scripts/setup-host-firewall.sh
# LAN_ALLOW (comma-separated subnets) also opens the console (8199) and the adapter (8643) to
# them: without Tailscale (WireGuard into the LAN, a reverse proxy) ufw otherwise drops every
# request and the proxy answers 502 (2026-10-27). It does not open 8642 or the webhook port.
set -euo pipefail
PORT="${1:-8080}"
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
command -v ufw >/dev/null || { echo "ufw not installed — nothing to open" >&2; exit 0; }
for net in 172.16.0.0/12; do
  if ufw status | grep -qE "^${PORT}/tcp\s+ALLOW IN\s+${net}"; then
    echo "already allowed: ${net} → ${PORT}/tcp"
  else
    ufw allow from "$net" to any port "$PORT" proto tcp comment 'jarvis-core for the stack containers'
  fi
done
ufw status | grep -E "^${PORT}/tcp" || true
for net in ${LAN_ALLOW//,/ }; do
  ufw allow from "$net" to any port 8199,8643 proto tcp comment 'jarvis console+adapter (LAN/WireGuard)'
done
