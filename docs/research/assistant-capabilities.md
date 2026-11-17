# What other assistants can do, and what JARVIS can

Round 1 of the standing research mandate (T-0799, 2026-11-09): survey what ChatGPT, Claude,
Gemini, Siri, Alexa+, Home Assistant, Open WebUI and Hermes ship, ground each capability against
this repo, and build the gaps (T-0800). A round that finds fewer than three gaps worth building
ends the mandate.

**How it was done.** One web survey (standard search, a few page fetches), dated 2026-11-09, then
one grep or read per capability across `hermes/`, `aura-core/`, `aura-adapter/`,
`aura-desktop/`, `jarvis-web/src/`, `android-app/` and `deploy/`. Open source beyond Home Assistant, Open WebUI and
Hermes (Open Interpreter, Khoj, Letta, LibreChat, OpenHands, Leon, OpenVoiceOS) was not surveyed
in this round; round 2 starts there. **Announced** means a vendor
said so but nothing confirms it shipped; **unverified** means one secondary source. The survey's
weakest links: OpenAI's release notes answered 403 to a direct fetch (their dates come from search
snippets), and the Siri items are WWDC announcements.

**What this does not cover.** Quality. A row marked *present* says the capability exists and where,
not that it is as good as the other assistant's; latency and answer quality are measured in
`bench/results/`.

## The matrix

Status: **present** (built; evidence names where), **partial** (part built, the rest named),
**gap** (not built). Mark: **build** (a task for T-0800) or **skip** (with the reason).

| # | Capability | Who has it | JARVIS | Evidence or reason | Mark |
|---|---|---|---|---|---|
| 1 | Interruptible voice, typing and talking in one session | GPT-Live, Gemini Live, Open WebUI 0.12 | present | Barge-in on the phone (`assist/hermes/BargeIn.kt`) and the console (`lib/hud/bargeIn.ts`); a spoken turn is a `prompt.submit` with `voice: true` on the same session as typed ones | — |
| 2 | Voice can use every tool mid-call | ChatGPT voice with plugins | present | A spoken turn is the same `prompt.submit` on the same session as a typed one; the adapter changes only its reasoning level (`aura-adapter/aura_adapter/effort.py`) and, for a guest's voice, the approval rows (`hermes/plugins/_shared/_approval_hook.py`) | — |
| 3 | A spoken request becomes background work that finishes later | ChatGPT Work from voice | present | `hermes/plugins/jarvis-goals`, `/goal` through `command.dispatch`; the `delegating-work` skill | — |
| 4 | Risky actions approved on a screen, not by voice alone | ChatGPT voice | present | Approval matrix `hermes/plugins/_shared/_matrix.py` (ask / free / denied for a guest), approval frames on the console, the phone's island and the car; a guest's voice is held to guest rows (`_approval_hook.py`) | — |
| 5 | Persistent, editable memory with sensitive-topic controls | Claude, Gemini, Alexa+, Hermes | present | `aura-core/aura/integrations/memory` (with reflect), `aura-adapter/aura_adapter/memory_layers.py`, the console's memory screen; removals and rewrites ask | — |
| 6 | Export memory, import another assistant's | Gemini (Mar 2026), Claude | present | Export: `GET /api/memory/export?format=json\|markdown` (`aura-core/aura/api/rest.py`), `jarvis/memory/export` on the house socket, the console's `routes/api/memory/export` (`aura-core/docs/clients.md`). Import: the `memory_import` tool (`hermes/plugins/_shared/_memory_import.py`, T-0827) reads a pasted export into short facts, leaves out lines addressed to an assistant, credentials and fenced blocks, and asks about each batch of eight on a card that lists every fact; the model stores the approved ones with `fact_store`. Paste only: no file picker in the console, no account archive (zip) | — |
| 7 | Scheduled tasks and web monitoring | ChatGPT Scheduled Tasks, Gemini scheduled actions, Claude Cowork | present | `hermes/plugins/jarvis-hud/watches.py`, `aura-core/aura/integrations/watch`, the `jarvis-cron-jobs` skill | — |
| 8 | An agent that keeps working while the owner's devices are off | Gemini Spark, Claude cloud sessions | present | Self-hosted: Hermes and its cron run on the house server around the clock | — |
| 9 | A daily brief from calendar, mail, house and commute | Gemini Daily Brief, ChatGPT Pulse (being folded into Scheduled Tasks) | present | The `catch-me-up` skill, routines (`jarvis-hud/routines.py`), cron delivery | — |
| 10 | Hand a task or thread between phone, desktop and car | Claude Dispatch, persistent agent thread | present | Sessions shared across surfaces through the adapter; `artifact_send` moves media between the owner's screens; the island hand-off (T-0806) | — |
| 11 | A browser agent in the owner's own browser | Claude in Chrome (GA Aug 2026), Gemini Spark | present | The `desktop-control` skill drives the owner's real desktop, browser included, with visible moves and take-over (`aura-desktop/aura_desktop/visible.py`); `headless-browser` for sites without a session | — |
| 12 | Computer use on the desktop | Claude Cowork | present | `jarvis-desktop` agent and the `jarvis-desktop` plugin; the Tauri app draws each move first | — |
| 13 | Ask about what a camera sees | Gemini Live camera, Siri camera mode (announced) | present | The phone's camera actions (`automation/actions/builtin/CameraActions.kt`), `jarvis-core` vision, the `image-inspection` skill | — |
| 14 | On-screen awareness: "reply to this", "add this to my calendar" | Siri (announced for iOS 27), Gemini screen share | present | The desktop sees its own screen (`desktop-control`). The phone can read and capture its screen on request (`ui_read_screen` and `screenshot` in `automation/actions/builtin/UiDelegatedActions.kt`, through `JarvisAccessibilityService`, which the owner must switch on) and accepts a share (`ACTION_SEND`); called up over another app, the assistant session reads that screen's visible text (`onHandleAssist`, `assist/ScreenContext.kt`, T-0824) and the first thing the owner says carries it, framed as content, with the app's name and a page's address. Text only: no screenshot, nothing from an app that hands no structure, and the house's session history keeps the prompt | — |
| 15 | Home control by name, alias and area | Home Assistant 2026.11, Gemini for Home, Alexa+ | present | `hermes/plugins/smart-home` (aliases in `schemas.py`), `jarvis-core` entities and areas | — |
| 16 | Automations written by speaking | Home Assistant 2026.7, Alexa+ voice routines | present | `jarvis-core` automations and the `routines-people-places` skill | — |
| 17 | Two-way audio with a doorbell, live view on a nearby screen | Home Assistant 2026.11 (Labs) | gap | No doorbell with a speaker is on the house; camera views exist (`jarvis-core` vision) | skip: needs hardware the house does not have; revisit when one is added |
| 18 | Car-safe voice: no wake word, navigation, messages, home, offline lines | Android Auto with Gemini, CarPlay conversational apps | present | The car stack (`docs/car/`), fixed lines from cached clips offline, route and limit on the car screen | — |
| 19 | Read, draft and send mail, calendar and messages with confirmation | Claude Microsoft 365 tools, ChatGPT connectors | present | `jarvis-mail`, `jarvis-calendar`, `jarvis-texts`; `mail_send`, `texts_reply` and calendar writes ask (`_matrix.py`) | — |
| 20 | Drafts to a person in the owner's style for that person | Write with Siri (announced) | have | `hermes/skills/texting-people/SKILL.md` reads how the owner writes to that person before `mail_draft` or `texts_reply` drafts (T-0828, 2026-11) | built T-0828 |
| 21 | Reachable by chat when away from a device | Hermes gateway, ChatGPT texting beta | partial | Telegram, Discord and webhooks are supported (`deploy/tests/test_gateway_platforms.py`); production has no bot token | skip: the token is the owner's to make (GAPS G33) |
| 22 | Booking and errands through services | Alexa+ with Expedia, Yelp, Square (announced) | gap | No booking integrations; the browser can fill a form up to the approval | skip: a purchase is never made, even in full auto (v2 brief §10), and each service needs the owner's accounts |
| 23 | Make a document, sheet or deck; transcribe and summarise a recording | ChatGPT Work (slides Oct 2026, audio Oct 2026) | have | `hermes/skills/making-documents/SKILL.md` with python-docx, openpyxl and python-pptx pinned in `deploy/Dockerfile` (T-0826); `recording_transcribe` in `hermes/plugins/jarvis-artifacts/recording.py` on the house's own transcriber (T-0825). Each ran once live on the deployed image, 2026-11-13 | built T-0826, T-0825 |
| 24 | Open skills and MCP, third-party skills reviewed | Hermes skills, Claude plugins, Gemini skills | present | `hermes/skills/`, MCP servers through `jarvis-integrations/connect.py` with matrix rows, `jarvis-selfext` and the `build-capability` skill | — |
| 25 | A private mode, and "nothing leaves the house" as a checkable property | ChatGPT locked chats, Home Assistant local pipeline | partial | Private chats that are stored nowhere (`jarvis-web/src/lib/hud/private.svelte.ts`, T-0462); an egress list (`hermes/plugins/_shared/_egress.py`); the full inventory is T-0767 | planned (T-0767) |

## The gaps, in build order

1. **T-0824** On the phone, the assistant reads the screen it was called over (gap 14). It turns
   "reply to this" from a share-sheet chore into one sentence.
2. **T-0826** Documents, sheets and decks on request (gap 23a). The Dockerfile skipped python-pptx
   because nothing asked for slides; measure what it adds to the image first.
3. **T-0825** A recording becomes a transcript and a summary on the house's own whisper (gap 23b).
4. **T-0827** Memory import (gap 6; export is built). An import is data shown for approval, never
   instructions.
5. **T-0828** Drafts in the owner's own way of writing to that person (gap 20).

Five gaps worth building: the mandate goes on to round 2 after they land.

**Landed by 2026-11-13:** T-0825, T-0826 and T-0828 (rows 20 and 23 above). Round 2 is queued as its
own research task; it starts with the open-source assistants this round left out.

## Turned down

- **Doorbell two-way audio (17):** the house has no doorbell with a speaker.
- **Booking services (22):** a purchase is never made, even in full auto (v2 brief §10), and every
  service needs an account only the owner can open. Preparing a booking up to the approval already works
  through the browser.
- **A messaging bot (21):** built and tested; switching it on needs a bot token from the owner
  (GAPS G33).

## Round 2 (T-1248, 2026-11-13): a first pass

Three standard web searches (Khoj, Letta, and the vendors' news since round 1), then one grep or
read per capability. **Thin on purpose and said so:** the vendor search returned nothing dated
after mid-2026, so "what shipped since 2026-11-09" is not answered; Open Interpreter, LibreChat,
OpenHands, Leon and OpenVoiceOS were not surveyed. This pass does not end the mandate: fewer than
three gaps were found because little was looked at, not because little is left.

| # | Capability | Who has it | JARVIS | Evidence or reason | Mark |
|---|---|---|---|---|---|
| 24 | Ask across your own documents by meaning, not file name | Khoj (search and chat over notes and files) | present | `files_search` (`hermes/plugins/jarvis-files`) lists the share, sends the house the text of changed text files (by etag, eight per search) and answers file plus passage from a second vector index in the house (`aura-core/aura/integrations/memory/files.py`), embedded by the house's own local model. Text files only; nothing runs between searches | built T-1249 |
| 25 | Custom agents: a persona with its own tools and knowledge | Khoj agents | present | `hermes/plugins/jarvis-personas` | skip |
| 26 | A research mode that plans, reads and reports | Khoj `/research` | present | `aura-core/aura/integrations/research/plan.py` | skip |
| 27 | Background memory upkeep while nobody is talking | Letta sleep-time agents | present | The nightly self-review and journal rows in `hermes/cron/jobs.tsv`; `aura-core/aura/integrations/memory/reflect.py` | skip |
| 28 | Move an agent, memory and all, between servers | Letta agent file (.af) | partial | Memory exports and imports (row 6, T-0827); skills, cron rows and settings are in the repo and the volume, with no one-file export | skip: one house, one server |
| 29 | Notebooks: chats and files kept together under a topic | Gemini notebooks (press, 2026) | gap | Sessions and artifacts are separate lists; nothing groups them under a name | skip until row 24 lands: it is the same index |

Round 2 sources: Khoj, https://github.com/khoj-ai/khoj ; Letta memory blocks,
https://www.letta.com/blog/memory-blocks ; Letta agent memory, https://www.letta.com/blog/agent-memory ;
vendor round-ups (press, low confidence), https://arahi.ai/blog/ai-assistant-capabilities-updates-2026

### Round 2, second pass (T-1250, 2026-11-13)

One recon read each project's own README or docs page and each vendor's own release notes; each
row was then grounded with one grep or read. **What was not read:** OpenAI's release notes (the
page answered 403), about a third of Gemini's page, the bodies of Apple's and Amazon's items
(headlines only), Leon's docs. Only one vendor item falls on or after 2026-11-09 (row 36).

| # | Capability | Who has it | JARVIS | Evidence or reason | Mark |
|---|---|---|---|---|---|
| 30 | A renamed device still answers to its old name | Home Assistant 2026.11 | have | Areas and entities: `AreaRegistry.update` and `EntityRegistry.update` (`aura-core/aura/registry.py`) add the replaced name to `aliases` once (not when the case alone changes, not when the same call passes `aliases`); an entity with no name of its own keeps its original name. Devices have no `aliases` field, so a renamed device is not covered; `rename` changes the id, not the name, and is not covered either (tests: `aura-core/tests/test_registries.py`) | built T-1257 |
| 31 | Dictate into whatever window has the keyboard | Gemini on macOS (Fn key) | partial | The desktop agent has `type_text` (`aura-desktop/aura_desktop/actions/inputauto.py`, a fresh approval each time) and a microphone, but no key that turns speech straight into typing | **build** T-1258 |
| 32 | Branch a conversation from an earlier message | LibreChat (fork, edit and resubmit) | gap | Sessions resume and list; nothing forks one (`docs/hermes-hud-protocol.md`) | skip: sessions are Hermes's own; a fork is an upstream feature, not a plugin |
| 33 | Search across every past message | LibreChat | present | `session_search` (free in `hermes/plugins/_shared/_matrix.py`) | skip |
| 34 | Image generation and editing | LibreChat | present | `hermes/plugins/comfyui-imagegen` | skip |
| 35 | An outside event starts an automation (webhook) | OpenHands automations | present | Webhook triggers (`aura-core/aura/automation/triggers.py`) | skip |
| 36 | Join a video call as a participant, take notes, make slides | Claude Meetings (2026-11-12, beta) | partial | A recording is transcribed and summarised (row 23, T-0825) and a deck is made (row 20); nothing joins a call | skip: needs a meeting account only the owner can open, and speaks to other people |
| 37 | Install or remove a skill while running | OpenVoiceOS | present | `skill_manage` (asked about, `_matrix.py`); skills are read per turn | skip |
| 38 | Layered memory: lasting preferences, the day, the recent talk | Leon | present | USER/MEMORY, the journal and session history (rows 6 and 27) | skip |
| 39 | A camera's live view on a screen when asked | Home Assistant 2026.11 (Echo Show) | partial | Snapshots and what a camera sees (`aura-core/aura/integrations/vision/camera.py`); no live stream on the console | skip until the house has a camera worth streaming (GAPS) |
| 40 | A 3D map of the home to see and control it | Alexa Home Map (headline only) | partial | The console's House screen lists rooms and devices; no floor plan | skip: no floor plan of the house exists to draw |
| 41 | Link bank accounts and ask about spending | Claude Personal Finance (2026-11-05, US beta) | gap | Nothing | turned down: credentials to a bank are not something the house should hold |
| 42 | Several coding agents behind one interface, a sandbox each | OpenHands, Open Interpreter | partial | Jarvis Code runs one agent in its sandbox | skip: one owner, one agent |

Second-pass sources: https://github.com/OpenHands/OpenHands ; https://github.com/leon-ai/leon ;
https://github.com/OpenVoiceOS/ovos-core ; https://openvoiceos.github.io/ovos-technical-manual/ ;
https://github.com/openinterpreter/open-interpreter ; https://www.openinterpreter.com/docs/terminal ;
https://github.com/danny-avila/LibreChat ; https://support.claude.com/en/articles/12138966-release-notes ;
https://www.home-assistant.io/blog/2026/11/04/release-202611/ ; https://gemini.google/release-notes/ ;
https://www.aboutamazon.com/news/devices (headlines) ; https://www.apple.com/newsroom/archive/ (headlines)

**Decision.** Build rows 30 and 31 (T-1257, T-1258). The rest are present, partial with the reason given, or turned down. OpenAI's notes are the first thing to retry next round.

## Sources

Primary sources are vendor release notes; the rest are press and may be wrong.

- OpenAI: ChatGPT release notes, https://help.openai.com/en/articles/6825453-chatgpt-release-notes ;
  Scheduled Tasks, https://alternativeto.net/news/2026/6/chatgpt-launches-new-scheduled-tasks-and-prepares-to-sunset-pulse/ ;
  voice and Work, https://andrew.ooo/answers/what-is-new-in-chatgpt-voice-plugins-work-gpt-6-switching-september-2026/
- Anthropic: Claude release notes, https://support.claude.com/en/articles/12138966-release-notes ;
  Claude in Chrome GA, https://gigazine.net/gsc_news/en/20260827-claude-chrome-available/
- Google: Gemini release notes, https://gemini.google/release-notes/ ;
  Spark, https://thenextweb.com/news/google-gemini-spark-agentic-assistant-gmail-io-2026 ;
  Gemini for Home, https://support.google.com/googlehome/answer/16618650 ;
  Android Auto, https://9to5google.com/2025/11/06/android-auto-gemini-starts-rolling-out/
- Apple (announced): https://www.ubergizmo.com/2026/06/apple-siri-ai-redesign-new-features-and-dedicated-app-announced-at-wwdc-2026/ ;
  CarPlay, https://macrumors.com/2026/02/18/ios-26-4-carplay-support
- Amazon: Alexa+ booking, https://www.aboutamazon.com/news/devices/alexa-plus-voice-booking-integrations
- Home Assistant: 2026.11, https://www.home-assistant.io/blog/2026/11/04/release-202611/ ;
  2026.7, https://www.home-assistant.io/blog/2026/07/01/release-20267/
- Open source: Open WebUI 0.12, https://openwebui.com/blog/v0-12-0-a-voice-that-talks-back-and-a-face-to-go-with-it ;
  Hermes Agent, https://parallel.ai/articles/what-is-hermes-agent
