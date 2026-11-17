# Home Assistant beside the house (T-1523)

The house stack runs a Home Assistant, `jarvis-homeassistant`, as a device hub: HA's integrations
(Zigbee, Matter, the cloud brands, whatever its discovery finds) become lights, switches and
sensors in the house, and AURA drives them like its own. `jarvis-core` is still the brain and
the HA-shaped API the apps and Hermes talk to; HA is one more source of devices.

Nothing to set up. AURA onboards HA, keeps a token, and mirrors HA's devices into the house.

## What runs

| | |
|---|---|
| Service | `jarvis-homeassistant` in `aura-core/docker-compose.yml`, HA 2026.11.2 pinned by digest |
| Network | host networking (HA's discovery needs the LAN's multicast) |
| Page | `http://127.0.0.1:8123`: only the address in `AURA_HA_BIND` (loopback by default) |
| Config | `aura-core/home-assistant/configuration.yaml`, mounted read-only |
| State | the `homeassistant-config` volume: HA's users, devices, integrations |
| AURA's side | `aura-core/aura/integrations/home_assistant/` (`home_assistant:` in `configuration.yaml`) |

Start it with the rest of the stack (`make up`). The first start takes about a minute.

## What AURA does

On start, and every 10 minutes after, `jarvis-core` checks HA:

| HA is | AURA | `home_assistant.status` says |
|---|---|---|
| answering a token AURA has (`AURA_HA_TOKEN`, else its own) | uses it, writes nothing | `adopted` |
| fresh (the onboarding wizard) | makes the user `aura`, finishes every onboarding step, mints a long-lived token | `provisioned` |
| refusing the token, with a user AURA made | signs in through HA's login form, mints a new token | `renewed` |
| onboarded by someone else, no token that works | leaves it alone | `owned_elsewhere`: set `AURA_HA_TOKEN` |
| not answering | tries again in 10 minutes | `unreachable` |

The login and the token are sealed in `aura-core/config/.storage/home_assistant.enc`, with the key
beside it in `home_assistant.key` (both 0600). Neither is shown or logged. Back up both with the
config directory.

With a token, the bridge holds one websocket to HA:

- Every HA device entity (lights, switches, covers, climate, locks, media players, sensors …)
  appears in the house with HA's own entity id (`light.kitchen` stays `light.kitchen`). Its
  registry id is `ha:<entity_id>`.
- HA's state changes reach the house as they happen.
- A house service on one of them (`light.turn_on`, from the console, a routine or AURA) goes to
  HA as the same service call.
- If HA restarts, the mirror shows unavailable, and the bridge reconnects (2 s, then up to 60 s).

HA's own bookkeeping stays out of the house: `sun`, zones, people, updates, backups, and every
entity HA marks as diagnostic, config, hidden or disabled.

## Asking AURA

- "Is Home Assistant set up?" → `service_setup_status` (free): the state, the detail, and how
  many devices are mirrored, by domain.
- "Set Home Assistant up again" → `service_setup` with `home_assistant` (asks first).
- A service the house has no name for, on a device domain it mirrors (`media_player.join`,
  `climate.set_preset_mode`) → `home_assistant.call_service` on the house (owner only):
  `domain`, `service`, `entity_id`, `data`. Each target must be a device the house mirrors,
  in a domain other than lock, notify, script, scene, automation or the alarm panel
  (`ALLOWED_DOMAINS` in the integration); an integration's own domain (`zwave_js.*`) is not
  reachable. A mirrored lock is unlocked with the house's own `lock.unlock`, which asks.
- A camera's `access_token` and its tokened picture URL are never mirrored.

## Adding a device

Add it in HA (its page, Settings, Devices; or let discovery find it). Its entities appear in
the house within a second. Nothing to restart.

HA 2026.11 reads `server_host` from configuration.yaml once, as a five-minute trial, and binds
every interface after it unless an admin confirms it. AURA is that admin: on an HA it onboarded,
each check holds the page to `AURA_HA_BIND` (`home_assistant.status` shows `http`: `held`,
`promoted`, or `restarting` while HA restarts onto it). After the first start HA ignores the
YAML, so a new address goes through AURA.

To open HA's page from another machine, set `AURA_HA_BIND` in `aura-core/.env` to the host's LAN
address and recreate `jarvis-core` (it moves the page; HA restarts once), but only after
`home_assistant.status` says `provisioned` or `adopted`: a fresh HA's onboarding page lets
whoever reaches it first make the owner, so on the LAN before AURA onboards it someone else
could. That race shows as `owned_elsewhere`, logged as a warning. The login AURA made is sealed and is not shown. To
sign in yourself, start from a fresh volume, onboard HA yourself, then give AURA a token
(`AURA_HA_TOKEN`).

## What the config leaves out, and why

Checked on 2026.11.2:

- `default_config:` would start go2rtc, whose WebRTC port (18555) listens on every interface.
- The cloud link and the alerts feed would call Nabu Casa and home-assistant.io.
- HA's onboarding sets up `radio_browser` and `google_translate` by itself, and both reach the
  internet. AURA removes the two the onboarding added (source `onboarding`). It watches for 20
  seconds after a first onboarding and checks again on every run. One you add yourself stays. The
  radio-browser lookup may already have happened once by then; see `DEVIATIONS.md`.

## Turning it off

Delete the `home_assistant:` block from `aura-core/config/configuration.yaml` and stop
`jarvis-homeassistant`. The mirrored entities stay in the registry, unavailable, until you remove
them.
