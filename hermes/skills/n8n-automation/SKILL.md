---
name: n8n-automation
description: Building, running, debugging and managing n8n workflows from JARVIS — the tools, the workflow JSON shape n8n accepts, the create→activate→trigger→inspect loop, and when to drive the n8n editor in the browser instead (manual runs, the AI assistant, OAuth credentials).
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [n8n, Automation, Workflows, Homelab]
    related_skills: [homelab-runbook, headless-browser, jarvis-cron-jobs, capability-gap]
---

# n8n from here

The house's n8n is at `N8N_URL` (the `n8n-trigger` plugin). Everything below needs
`N8N_API_KEY` set on the host — n8n → Settings → *n8n API* → create key — and
`n8n_health` says whether it is (`api_key_ok`). Without it only webhooks work.

## The tools, and which ask first

| Read (free) | Write (asks the owner) |
|---|---|
| `n8n_health` — reachable, key ok | `n8n_trigger` — fire a workflow through its webhook |
| `n8n_list_workflows` — ids, names, active, webhook paths | `n8n_create_workflow` — a new definition, created **inactive** |
| `n8n_get_workflow` — one definition (nodes, connections, settings) | `n8n_update_workflow` — replaces the definition wholesale |
| `n8n_executions` — recent runs, status | `n8n_activate_workflow` — start or stop it firing on its own |
| `n8n_execution` — one run with its error and the node it stopped on | `n8n_delete_workflow` — gone with its history; prefer deactivating |
| `n8n_credential_schema` — the fields a credential type takes | `n8n_credential_create` / `n8n_credential_delete` |

## The shape n8n accepts

A workflow is `name`, `nodes`, `connections`, `settings`. Nothing else is writable
(`id`, `active`, `tags`, timestamps come back from a read and must not be sent).

```json
{
  "name": "Morning lights",
  "nodes": [
    {"name": "Every morning", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [0, 0],
     "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 7 * * *"}]}}},
    {"name": "Lights on", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
     "position": [300, 0],
     "parameters": {"method": "POST", "url": "http://host.docker.internal:8080/api/services/light/turn_on",
                    "sendBody": true, "specifyBody": "json",
                    "jsonBody": "={\"entity_id\": \"light.kitchen\"}"}}
  ],
  "connections": {"Every morning": {"main": [[{"node": "Lights on", "type": "main", "index": 0}]]}},
  "settings": {"executionOrder": "v1"}
}
```

- **Every node**: `name` (unique, what `connections` refers to), `type`, `typeVersion`,
  `position`, `parameters`. Node types are `n8n-nodes-base.<name>`; read an existing
  workflow with `n8n_get_workflow` to copy a type and version you have not used.
- **`connections`** is keyed by the *source* node's name: `{"Source": {"main": [[{node, type: "main", index}]]}}`;
  the outer list is one entry per output, the inner list the targets of that output.
- **Triggers**: `scheduleTrigger` (cron), `webhook` (`parameters.path` is what
  `n8n_trigger` fires), `manualTrigger` (only the editor can run it — see below).
- **Credentials** are referenced by id: `"credentials": {"slackApi": {"id": "3", "name": "slack"}}`
  on the node; create them with `n8n_credential_create` first, never inline a secret in a
  node parameter.
- `settings.executionOrder: "v1"` — always; without it the branches run in the old order.

## The loop

1. `n8n_get_workflow` on something similar, if one exists; copy the node shapes.
2. `n8n_create_workflow` — say what it will do in one line when the approval comes.
3. `n8n_activate_workflow active=true` for a schedule or webhook workflow.
4. Run it once: `n8n_trigger` with the webhook path (add `test: true` while it is inactive
   to hit `/webhook-test/`), or wait for the schedule.
5. `n8n_executions` for the run, `n8n_execution` for **why** it failed — the node and the
   message — then `n8n_update_workflow` with the whole corrected definition (it replaces,
   it does not patch).
6. Report what the workflow does now, its id, and whether it is active.

## When the API is not enough: the editor in the browser

Some things only the n8n editor does — running a `manualTrigger` workflow, the *Test
workflow* button, the **AI assistant** panel, an OAuth credential's consent flow,
importing from a template. Then use the `browser` toolset on `{N8N_URL}/workflow/<id>`
(or `/workflow/new`): the editor is a web app and the house browser is signed in to it.
Say what you are doing there in the reply — a person cannot see the browser — and
read the result back through the API afterwards (`n8n_get_workflow`), which is the
only source of truth.

## What not to do

- Do not delete a workflow to "start over"; deactivate it, or update it.
- Do not put a token or password in a node parameter or in a reply; credentials go
  through `n8n_credential_create` and are referenced by id.
- Do not activate a workflow that reaches outside the house (mail, messages, purchases)
  without saying so in the approval line: activation means it runs with nobody watching.
