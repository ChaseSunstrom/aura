# What JARVIS sends out of the house

The owner, 2026-11-08: "I would like it so my location and everything is private and not
making requests out to stuff." This page is the inventory that request starts from: every
host outside the house that the stack can call, what each one is sent, the local path or the
switch that stops it, and a verdict. Location itself (the house's position, the phone's) is
T-0765; every "switch off" or "make local" verdict names the task that does it.

Two sources, which must agree (T-0767):

- **The code.** Every literal host in the images, plugins, apps, compose files and scripts,
  plus the defaults of the third-party services the stack runs (Hermes, LiteLLM, vLLM, n8n,
  SearXNG), read inside the running containers.
- **A live capture.** The non-LAN addresses the running containers held, sampled every 2 s
  for 45 minutes on 2026-11-09 by reading each container's `/proc/<pid>/net/{tcp,udp}{,6}`
  from the host (nothing that changes routing or the firewall). The window held the
  assistant's own schedule (twelve cron jobs, four of them every 10 or 30 minutes), a
  console deploy with its smoke turn and a CI pipeline's builds.

## On its own, with nobody asking

| Host | Called by | When | What leaves | Local path or off switch | Verdict |
| --- | --- | --- | --- | --- | --- |
| `api.open-meteo.com` | `weather_forecast` (`hermes/plugins/aura-hud/weather.py`), called by the 07:00 morning brief | daily at 07:00, and whenever someone asks | the house's position rounded to 0.1° (about 10 km; T-0765) | `privacy.quiet_house` (`AURA_QUIET_HOUSE=true`): a forecast a scheduled job starts is answered "The house is set to make no calls out on its own, so there is no forecast." with no request; a forecast someone asks for still goes out. The house has no forecast of its own to use instead | keep, coarsened; off with the one switch: done in T-0837 |
| `celestrak.org` | jarvis-core's sky (`sky.sources` in `aura-core/config/configuration.yaml`) | every 24 h (`refresh_hours`; never under 2 h) | a request for the space station's orbit; nothing about the house but its address | `privacy.quiet_house` (`AURA_QUIET_HOUSE=true` in jarvis-core's `.env`): no request at all, no refresh loop, the elements on disk keep answering and the sky's status says downloads are off because of the privacy switch. `sky.download: false` with the file placed by hand, or no `sky` block, still does the same for the sky alone | keep; off with the one switch: done in T-0837 |
| `naif.jpl.nasa.gov` | jarvis-core's sky | once, while `sky/de421.bsp` is missing | the download of the planets' table | the file is kept after that; place it by hand. `privacy.quiet_house` stops this download too (T-0837), so the planets and the moon then answer that the table is missing because of the privacy switch | keep; off with the one switch: done in T-0837 |
| `raw.githubusercontent.com` | the LiteLLM gateway (`jarvis-gateway`) | every start, until T-0833 | a request for LiteLLM's model price list | `LITELLM_LOCAL_MODEL_COST_MAP=True`, set by T-0833 | switch off: done in T-0833 |
| `hermes-agent.nousresearch.com` (fallback `raw.githubusercontent.com`) | Hermes's model catalogue (`hermes_cli/model_catalog.py`, on by default) | whenever the cached list is over 20 minutes old and something reads it; seen in the capture | a request for the catalogue; nothing about the house but its address | `model_catalog.enabled: false`, set by T-0839 | switch off: done in T-0839 |
| `models.dev` | Hermes's model registry (`agent/models_dev.py`, `models_dev.url` empty means this host) | at start, then from a background thread | a request for the registry | none in Hermes; T-0839 points `models_dev.url` at a closed loopback port, so Hermes keeps the copy on disk | switch off: done in T-0839 |
| `openrouter.ai` | Hermes's reasoning limits (`hermes_cli/models_reasoning_caps.py`) | when a model's limits are looked up; seen in the capture beside the 10-minute cron jobs, with no OpenRouter model configured | a request for OpenRouter's public model list | none in Hermes's config; `deploy/Dockerfile` points the URL at a closed loopback port (T-0839) | switch off: done in T-0839 |
| `www.google.com`, `update.googleapis.com`, `accounts.google.com`, `clients2.google.com`, `redirector.gvt1.com` and other `gvt1.com` hosts | the Chrome for Testing that Hermes v0.23 bakes in for `browser_exec` (T-1519) | from the first page the browser opens, a blank one included, and for minutes after; under Hermes' own `--disable-background-networking` | Chrome's update, safe-browsing, variations, optimisation and sign-in checks; nothing about the house but its address | `browser.chromium_args` in the template (added to an existing volume by `CONFIG_LEAVES`): those features off and Google's endpoints at a closed loopback port. A net log of a blank tab went from those six hosts to none | switch off: done in T-1519 |
| `www.modelscope.cn` | CosyVoice's text normaliser (`wetext`, which downloads its grammars when no paths are given) | every start of `jarvis-cosyvoice`; seen 42 s after a deploy restarted it | a check for the `pengzhendong/wetext` files, which fails without a modelscope token, so the voice never had the normaliser | `wetext` is left out of the image, so the voice starts exactly as before with no call (T-0840) | switch off: done in T-0840 |

**Pinned (T-0844).** `deploy/tests/test_hermes_egress.py` fails when one of the four Hermes switches above is gone, when Hermes's config template names a host outside the stack, or when the Hermes release moves past the one a capture was read for (v0.23.2 since T-1519, the capture below) (take a new capture, then move its `REVIEWED_TAG`). It cannot see a fetch a new Hermes release adds in its own code; only a capture can.

**The one switch (T-0837).** `privacy.quiet_house` (env `AURA_QUIET_HOUSE`, default off, restart needed) stops the first three rows: the sky's two downloads and the forecast a scheduled job starts. A turn counts as scheduled when Hermes opened its session for a cron job (`cron_` in the session id, or the `cron` platform). It does not touch a call someone asks for, nor the other rows, which each have their own switch above. Two places read it: the Hermes container (the forecast; the console or `settings_set` writes the override there) and jarvis-core, which reads `AURA_QUIET_HOUSE` from its own `.env` at start and never sees a console override, so on a host that wants the sky quiet the name goes in `aura-core/.env` as well.

## Only when someone asks

| Host | Called by | When | What leaves | Local path or off switch | Verdict |
| --- | --- | --- | --- | --- | --- |
| The search engines SearXNG keeps (Qwant, Mojeek, Bing, Yahoo, DuckDuckGo, Brave, Startpage, Wikipedia, Wikidata, OpenStreetMap, GitHub, Stack Overflow, Hacker News, arXiv, PyPI; `deploy/searxng/settings.yml`) | `web_search` through the stack's own SearXNG | each search | the query text, from the house's address; SearXNG sends no cookies or account | none: there is no local index of the web. Fewer engines is `keep_only` | keep; which engines is the owner's (G34) |
| Any site | `aura-extract` (`web_extract`) | a page the person or the model asks to read | the page request | none | keep |
| `api.github.com`, `registry.modelcontextprotocol.io` | jarvis-core's extension catalogue (`extensions.catalog.sources`) | a catalogue search or install in the console | the search text | remove the two `sources` | keep |
| `huggingface.co`, `github.com` (release files) | jarvis-core's model mirror (`aura/api/models.py`), the Models panel (`jarvis-models`) | the first time a phone asks for an on-device model; a download the owner starts | which model | the server is the mirror, so the phone itself never calls out | keep |
| `accounts.google.com`, `oauth2.googleapis.com`, `www.googleapis.com`, `gmail.googleapis.com`, `people.googleapis.com`; `github.com`, `api.github.com`; and, from T-1338, `login.microsoftonline.com`, `graph.microsoft.com`, `slack.com`, `discord.com`, `gitlab.com` (or the self-hosted address set in `OAUTH_GITLAB_BASE_URL`), `accounts.spotify.com`, `api.spotify.com`, `api.notion.com`, `todoist.com`, `api.todoist.com` | jarvis-core OAuth (`aura/integrations/oauth`) | only once an account is linked; none was on 2026-11-09 (no `oauth_tokens.enc`) | mail, calendar and contact reads, a drafted mail, and read-only calls to the other providers | do not link one | keep |
| `api.telegram.org` | jarvis-core channels | only with `TELEGRAM_BOT_TOKEN` set (unset; G33) | the messages | leave it unset | keep |
| `geocoding-api.open-meteo.com` | `weather_forecast` | a place the person named | the place name | none local | keep |
| The page, feed or search a watch names | jarvis-core's watch (`watch_add`) | every 15 minutes once the owner sets one; none was set on 2026-11-09 | the page request, or the question through SearXNG | remove the watch | keep |
| `photon.komoot.io`, `nominatim.openstreetmap.org` | the places plugin | only with `places.photon_public` on; off by default, the house's own Photon answers | the place name | leave it off | keep off |
| Any public geocoder (`LOCATION_HOSTS` in `hermes/plugins/_shared/_egress.py`: Nominatim, Photon, Google, Mapbox, …) | the model's own `browser_exec` navigations (`browser_navigate` before Hermes v0.23), `web_extract`, `terminal`, `watch_add`, `feeds` | never: the gate refuses it (`egress:location`, T-0904; the browser had opened Nominatim with the owner's address in it) | nothing | `place_search` answers from the house | refused |

## Looks external in the code, stays in the house

- **The language model.** `jarvis-models` (llama-swap and vLLM) on this host; the LiteLLM
  gateway's cloud models are commented out (`aura-core/gateway/config.yaml`) and its
  telemetry is `false`. vLLM runs with `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1` (T-0343).
- **Update checks.** The phone (`UpdateChecker.kt`) and the desktop app's updater
  (`tauri.conf.json`) read releases from the house's own GitLab; GitHub appears only as a
  link in Settings.
- **n8n.** `N8N_TELEMETRY_ENABLED`, `N8N_DIAGNOSTICS_ENABLED` and `N8N_UPDATES_CHECK_ENABLED`
  are `false`; its model is `jarvis-models`.
- **Embeddings and reranking.** `HF_HUB_OFFLINE=1` (T-0343).
- **Fonts.** Vendored by `design/fonts.py`; there is no font CDN.
- **The phone.** No Play Services, Firebase or analytics library; it talks to the paired
  server only, and gets its models through the server's mirror.

## Setup and build only

These run on the CI runner and in `docker build`, not in the running stack:
`deb.debian.org`, PyPI, npm, Maven Central, `dl.google.com`, `api.adoptium.net`,
`sh.rustup.rs`, `nodejs.org`, `astral.sh`, `download.pytorch.org` and `huggingface.co` (image
builds); `download.geofabrik.de` and `build.protomaps.com` (the map region scripts, run by
hand); `api.osv.dev` and `nvd.nist.gov` (CI's dependency audit). The benchmark's headless
Chromium, run by CI, also reaches Google's update and optimisation services.

One of those also runs in the house stack when its profile is on: the `maps-worker`
(`--profile maps`, T-0979) reaches `download.geofabrik.de` for the region catalogue (weekly)
and for each region the owner asks for, and PyPI once for pyosmium. It sends a region's
name, never a point. The privacy switch stops the weekly refresh and a trip's own region
(`maps.auto_fetch`); `docs/runbooks/maps.md` has the rest.

## Text only, never fetched

`github.com` and `shizuku.rikka.app` (links in the phone's Settings), `claude.ai` (the session
link in the self-extension loop's commit trailers), licence and schema URLs, and the test
fixtures' `www.bbc.co.uk`, `weather.com` and `en.wikipedia.org`.

## The live capture

45 minutes on 2026-11-09, 11:00–11:45. The running stack opened four connections out of the
house, all of them already in the tables above:

| Container | Host | When | Code path |
| --- | --- | --- | --- |
| `jarvis` | `hermes-agent.nousresearch.com` (and, at 11:10, an address with no reverse name on the same provider, most likely the same catalogue) | 11:10 and 11:34 | the model catalogue (T-0839) |
| `jarvis` | `openrouter.ai` | 11:30, with the 10-minute cron jobs | the reasoning limits (T-0839) |
| `jarvis-cosyvoice` | `www.modelscope.cn` | 11:34, 42 s after a deploy restarted the voice | the text normaliser (T-0840) |

No other stack container (the adapter, jarvis-core, the gateway, the models, SearXNG, the
extractor, n8n, Photon) held an address outside the house in the window: no search was run,
and the 07:00 brief and the sky's daily refresh fell outside it.

Everything else the capture saw was not the stack: the CI pipeline's job containers (npm,
PyPI, Docker Hub and its CDN, and the benchmark's headless Chromium reaching Google's update
and optimisation services), the host's own root processes (image pulls, CI's pip, Ubuntu's
package checks) and this development session. Those are "Setup and build only".

### Hermes v0.23.2's browser (T-1519, 2026-11-18)

v0.23 replaced agent-browser with `browser_exec` on a Chrome for Testing baked into the image.
The image built from this tree ran as a throwaway gateway beside production (its own network
namespace, a copy of the live config, nothing of production's platforms, MCP servers or
dispatch), sampled the same way every 2 s.

| Run | Window | Outside addresses | What they were |
| --- | --- | --- | --- |
| Hermes' own flags only | 18 min, one browser turn 10 min in | 17 | none in the 10 idle minutes before the turn; from it, Google (`1e100.net`) for 11, held open up to five minutes after it, and the pages' own hosts |
| With `browser.chromium_args` | 10 min, the same turn (example.com, then python.org) | 5 | all within the turn's 100 s: example.com, python.org's CDN, and the one Google address while python.org was open (its page loads jQuery from `ajax.googleapis.com`); none in the eight minutes after it |

The six Google hosts were named from Chrome's own net log of a blank tab for 40 s; with the
flags, that log names none. What a page loads (its CDN, its scripts) still leaves while it is
open: that is the page, as `web_extract`'s is.

## Not covered

- What the phone's operating system, Android Auto and Google's own services send. The app
  adds no library of its own that calls out.
- Packet contents: the capture sees destinations; what is sent comes from the code.
- A path that did not fire in the 45 minutes is known only from the code.
- DNS. The house's resolver sees every name the stack looks up; where it forwards them is
  the router's setting.
- The CI runner and image builds on this host, beyond the list above.
