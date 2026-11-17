# This result does not measure the agent

**Do not cite the 34.6% in this directory.** It was recorded at 18:38 on 2026-09-03
against the deployed stack, which was running image `jarvis:64c15083` — built at
13:07:50 and started at 13:08:01 that day, before every Phase 4 plugin existed.

That image carried ten plugins: `jarvis-goals`, `llama-swap-control`,
`gpu-telemetry`, `n8n-trigger`, `proxmox-control`, `comfyui-imagegen`,
`smart-home`, `jarvis-hud`, `jarvis-integrations`, `jarvis-settings`. Absent were
`jarvis-mail`, `jarvis-calendar`, `jarvis-contacts`, `jarvis-files`,
`jarvis-jellyfin`, `grafana-dashboards`, `jarvis-selfext`, `jarvis-extract`,
`hud-layout` and — the one that matters most — `jarvis-guard`, which registers the
`pre_tool_call` hook that applies the approval matrix.

So for every task naming a Phase 4 tool, the agent was not offered the tool the
checker was looking for. It fell back to `terminal` and the row scored as a
capability failure. That is a measurement of the deploy lag, not of the agent.
Plugins are baked into the image rather than bind-mounted, so nothing short of a
rebuild and redeploy changes it.

Verified with:

```
docker exec jarvis sh -c 'ls $HERMES_HOME/plugins'
docker inspect jarvis --format '{{.Config.Image}} {{.State.StartedAt}}'
```

Kept rather than deleted because the journal is still evidence of what the stack
answered on that image. Replace it with a run against an image that carries the
plugins under test, and delete this file with it.
