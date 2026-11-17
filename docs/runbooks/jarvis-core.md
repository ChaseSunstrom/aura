# Runbook: `jarvis-core` (house service)

`jarvis-core` is the house service — Home Assistant plus JARVIS's device/area model —
running from its **own** stack (`aura-core/docker-compose.yml`) on host networking.
Its agent loop is **off**: it runs with `llm.enabled: false`
(`AURA_AGENT_ENABLED`), so `POST /api/conversation/process` answers `501` and Hermes
is the only agent ([ADR-0012](../adr/0012-jarvis-core-agent-off.md)). Hermes reaches it
through the built-in `homeassistant` toolset (its `/api/states` and
`/api/services/{domain}/{service}` calls) plus the `smart-home` plugin
([ADR-0006](../adr/0006-jarvis-core-house-service.md)).

- **Compose:** `aura-core/docker-compose.yml`
- **Reached by the Hermes stack:** over `host.docker.internal` (this stack has no
  direct link to it)
- **API used by Hermes:** `/api/states`, `/api/services/{domain}/{service}` (verified
  against jarvis-core `:8080`)

## Start / stop

```bash
cd /opt/jarvis/aura-core
docker compose up -d --wait
docker compose down
```

`make up` brings the house service up; the Hermes stack is deployed to the host's
`/opt/jarvis-host` by `make deploy` or the pipeline (`operations.md`). `make down`
stops the house. `--wait` blocks until every service with a healthcheck is healthy.

## Health

```bash
curl -fsS http://127.0.0.1:8080/api/health      # or the console's House pages
```

## Logs

```bash
cd /opt/jarvis/aura-core && docker compose logs -f jarvis-core
```

## Common failures

- **`501` from `/api/conversation/process`.** Expected — the agent loop is off by
  design ([ADR-0012](../adr/0012-jarvis-core-agent-off.md)). If you want the house to
  answer prose, that is Hermes's job, not jarvis-core's.
- **Hermes cannot reach the house.** Check `AURA_URL`/`AURA_TOKEN` on
  [`jarvis-web`](jarvis-web.md) and the `homeassistant` toolset config; the Hermes
  stack talks to the house over `host.docker.internal`, so the house must be up on the
  host first.
- **The Home Assistant chat platform keeps reconnecting.** It is switched off in the
  template on purpose ([ADR-0011](../adr/0011-hf-hub-offline-and-hass-chat-off.md)); we
  use the *toolset*, not the platform.

## Emergency outreach: the fallback when the phone cannot send it

A smoke, carbon-monoxide or gas alarm turning on starts the phone's cancellable alert to the owner's
contacts (`integrations/emergency`, T-1013). When the phone cannot send it (off, out of signal, not the one
online phone, an error, or no contacts saved) the house says so at `critical`, and, **only if**
`AURA_EMERGENCY_FALLBACK_URL` is set in `aura-core/.env` (`emergency.fallback_url`, a restart), makes one
`POST` itself to that URL (T-1208): JSON `{"what": "...", "kind": "smoke|carbon_monoxide|gas", "time": "<UTC>"}`,
with no contact name, number or location. An ntfy topic URL shows it as the message text; an n8n webhook gets
the fields.

- **Unset by default.** With no URL nothing leaves the house, as before.
- **It is one attempt, not a delivery.** Five seconds, no redirect followed, no proxy from the environment,
  no retry. The notice says whether the post was accepted (2xx) or why not (refused, redirected, timed out,
  unreachable); an accepted post uses the ten-minute quiet, a failed one does not, so the next alarm tries again.
- **Confirm before telling anyone.** A 2xx means the owner's flow took the post, not that anyone was reached.
  The flow behind the URL should confirm with the owner (a push with a cancel) before it messages or calls
  anyone: the house sends no countdown of its own. The house never messages the contacts itself.
- The URL is a credential (a topic name or a webhook path), kept out of the notice and the logs.
- Prove it with no real alert: `python3 -m pytest aura-core/tests/test_emergency_outreach.py -q -k fallback`
  posts only to a stub on loopback.

### The phone keeps the contacts for its fall watch (T-1262)

The phone's fall and crash watch (T-1209) needs the owner's list on the phone. The house hands it over: when
the owner saves the contacts, every connected phone is sent the stored list, and a phone that connects (a
new socket, a few seconds after it registers, once per link) is sent it too. With none stored the house
sends an empty list, so a phone forgets a person the owner removed. It is the device command
`emergency_contacts_sync` (`tests/contracts/emergency-contacts-sync.json`), tier 1, and the phone only
**stores** it: no call, no text. A phone whose manifest does not list it (an older app) is skipped without a
prompt. A list the phone cannot take whole is refused and its stored one kept. The list is never logged on
either side, only a count and a status, and the model cannot send the command (`RESERVED_ACTIONS`).
Who gets it: only a link whose socket authenticated with a token **the pairing flow minted** (a phone the
owner paired with the pairing secret), read live, so a revoked phone gets nothing. The platform and
manifest a link declares are its own words and only say whether its app knows the command. The hub keeps
no owner scope on a token, so this is the nearest identity it has: a REST token, the first-run token and
`AURA_TOKEN` never get the list. A phone paired before this change holds a token with no mark and must pair
again to receive it. On the phone the action is accepted only when the house's device channel dispatches
it; a task, the assistant, the bridge's `dispatchCommand` and the UI are refused (`ActionRegistry.HOUSE_ONLY`).
On the house the command is refused for **every** caller of `DeviceLink.dispatch` (`device_control.run`, a
`run_sequence` step, an automation or script action, any service call with any token): the answer is `denied`
and no frame goes out. Only the emergency integration's `_push_list` sends it, through
`DeviceLink.dispatch_house_only`, which no service payload names. The ids that are house-only are one list,
`house_only_actions` in the contract, that the house (`HOUSE_ONLY_ACTIONS`) and the phone read alike.
No route was added: the fingerprint proof on the save is unchanged and the house still never messages anyone.
Prove it with no real contact: `python3 -m pytest aura-core/tests/test_emergency_outreach.py -q -k sync`.

## Optional profiled services

Voice, MQTT, cameras, geocoding and the test fixtures are separate compose profiles in
this stack — see [side-services.md](side-services.md).
