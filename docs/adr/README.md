# Architecture Decision Records

One record per architectural decision, MADR-style (context, decision, consequences,
status), each pointing back to the plan paragraph it comes from. ADR-0001 through
ADR-0014 are the migration decisions (the migration plan §6); ADR-0015 through ADR-0032 are the
v2 decisions (the v2 plan §10); ADR-0033 onward are the v3 decisions (the v3 plan §11). New decisions start from [the template](0000-template.md)
and take the next number.

| ADR | Decision | Status |
| --- | --- | --- |
| [ADR-0001](0001-pin-hermes-version.md) | Pin the Hermes version everywhere | Accepted |
| [ADR-0002](0002-keep-model-thinking-on.md) | Keep model thinking on | Accepted |
| [ADR-0003](0003-hud-protocol-tui-gateway-ws.md) | The HUD protocol is the TUI-gateway JSON-RPC over WebSocket | Accepted |
| [ADR-0004](0004-thin-adapter.md) | A thin jarvis-adapter fronts Hermes with one bearer key | Accepted |
| [ADR-0005](0005-auxiliary-tasks-on-main-model.md) | The goal judge and auxiliary tasks stay on the main model | Accepted |
| [ADR-0006](0006-jarvis-core-house-service.md) | jarvis-core stays a house service; the orchestrator, sandbox and browser are retired | Accepted |
| [ADR-0007](0007-local-web-search.md) | Web search stays local via SearXNG | Accepted |
| [ADR-0008](0008-voice-in-hermes-container.md) | Voice runs inside the Hermes container; the Wyoming containers are retired | Accepted |
| [ADR-0009](0009-frontends-hud-over-adapter.md) | The front-ends keep their house screens and get a new Hermes HUD over the adapter | Accepted |
| [ADR-0010](0010-volume-seed-cont-init.md) | Seed the Hermes volume from an s6 cont-init step | Accepted |
| [ADR-0011](0011-hf-hub-offline-and-hass-chat-off.md) | HF_HUB_OFFLINE in the image; the Home Assistant chat platform off | Accepted |
| [ADR-0012](0012-jarvis-core-agent-off.md) | jarvis-core runs with the agent loop off | Accepted |
| [ADR-0013](0013-proxmox-comfyui-grafana.md) | Proxmox stays unwired; ComfyUI is reachable but empty; GPU telemetry rides Grafana without a token | Accepted (ComfyUI live-gen open) |
| [ADR-0014](0014-host-is-lxc-runner-registry.md) | The JARVIS host is this LXC; the pipeline builds on a runner there; the registry is HTTPS | Accepted |
| [ADR-0015](0015-jcb-baselined-first.md) | Build and baseline the JCB before any Phase 2-8 change | Accepted |
| [ADR-0016](0016-runner-api-and-gateway.md) | The runner uses the API server for ordinary tasks and the adapter /ws for gateway-only tasks | Accepted |
| [ADR-0017](0017-one-settings-schema.md) | One settings schema generates config, the settings UI and the settings docs | Accepted (Phase 2) |
| [ADR-0018](0018-local-browser.md) | Local browser via agent-browser and a baked Chromium, with a persistent profile and a CDP connect path | Accepted (Phase 3); engine superseded by Hermes v0.23 (T-1519) |
| [ADR-0019](0019-local-extraction-by-benchmark.md) | The local extraction backend is chosen by benchmark, not by preference | Accepted (Phase 3) |
| [ADR-0020](0020-messaging-telegram-discord.md) | Telegram and Discord are the messaging surfaces | Accepted (Phase 4) |
| [ADR-0021](0021-single-model-default.md) | Default to a single model; add a route only on a measured margin | Accepted (Phase 5) |
| [ADR-0022](0022-structured-memory-provider.md) | One structured memory provider, chosen by benchmark, alongside the built-in memory | Accepted (Phase 5) |
| [ADR-0023](0023-design-system-token-source.md) | The design system is the token source of truth | Accepted (Phase 2) |
| [ADR-0024](0024-governed-self-extension.md) | The self-extension loop is the 'anything' mechanism, and every step is governed | Accepted (Phase 5) |
| [ADR-0025](0025-sandbox-disposable-ct.md) | The sandbox is a disposable CT over the ssh terminal backend | Accepted (Phase 7) |
| [ADR-0026](0026-approval-matrix-audit-log.md) | The approval matrix and the unified audit log live in the schema and one structured log | Accepted (Phase 7) |
| [ADR-0027](0027-gepa-dspy-out-of-scope.md) | GEPA/DSPy self-evolution is out of scope for this build | Accepted |
| [ADR-0028](0028-ssrf-parser-generated-copy.md) | The SSRF address parser is one file kept in two trees | Accepted (Phase 2) |
| [ADR-0029](0029-defect-only-android-lint.md) | The Android lint gate is defect-only | Accepted (Phase 2) |
| [ADR-0030](0030-glass-hud-design-system.md) | The Glass HUD is the design system | Accepted (Phase 2b) |
| [ADR-0031](0031-reactor-ii-returns.md) | Reactor II returns; the glass material is retired | Accepted (2026-09-06) |
| [ADR-0032](0032-the-room-has-no-tabs.md) | The room has no tabs; JARVIS pulls the panels up | Accepted (2026-09-07) |
| [ADR-0033](0033-cosyvoice-placement.md) | CosyVoice 3 runs beside Piper on the JARVIS host, not on the GPU | **Superseded by [ADR-0039](0039-cosyvoice-on-the-jarvis-host-gpu.md)** (2026-09-09) |
| [ADR-0034](0034-one-task-store.md) | One task store, projected from Hermes rather than owned by JARVIS | Accepted (2026-09-08) |
| [ADR-0035](0035-settings-three-tiers.md) | Settings gets three tiers over the one schema, and the tier is a schema field | Accepted (2026-09-08) |
| [ADR-0036](0036-the-map-replaces-the-tiler.md) | The HUD is a spatial map on orbital bands; the tiling window manager is retired | Accepted (2026-09-08) |
| [ADR-0037](0037-orb-rendering-architecture.md) | The orb is WebGL and the panels are DOM, positioned by one shared projection pass | Accepted (2026-09-08) |
| [ADR-0038](0038-one-reactor-component.md) | A companion is the orb with a different hue, scale and blade density — never a second implementation | Accepted (2026-09-08) |
| [ADR-0039](0039-cosyvoice-on-the-jarvis-host-gpu.md) | CosyVoice 3 runs GPU-attached on the JARVIS host; a model swap and a TTS load take turns, and the swap wins | Accepted (2026-09-09) |
| [ADR-0040](0040-one-input-layer-one-camera.md) | One layer decides what a gesture means, and it is the only thing that moves the camera | Accepted (2026-09-09) |
| [ADR-0041](0041-artifact-store-in-the-adapter.md) | The artifact store lives in the adapter, and an id is a digest validated by shape on both sides of the boundary | Accepted (2026-09-09) |
| [ADR-0042](0042-the-2d-reactor-is-the-fallback-not-a-legacy.md) | The 2D reactor is kept as the fallback, and the two renderers are bound by one contract | Accepted (2026-09-09) |
| [ADR-0043](0043-four-personas-one-memory.md) | Any of the four personas can be primary, and they share everything except who is speaking | Accepted (2026-09-09) |
| [ADR-0044](0044-settings-env-overrides.md) | Env-backed settings are written to an override file the container exports at start; the compose-fixed ones say so | Accepted (2026-09-18) |
| [ADR-0045](0045-bubblewrap-sandbox.md) | The model's shell runs under bubblewrap inside the container, as a plugin terminal backend — interim until ADR-0025's CT | Accepted (2026-09-18) |
| [ADR-0046](0046-panels-may-be-tabbed-in-a-dock.md) | Panels may be tabbed inside a dock: a third arrangement beside Orbit and Shelf (amends ADR-0032 for docks only) | Accepted (2026-09-24) |
| [ADR-0047](0047-jarvis-is-the-phone-side-of-android-auto.md) | Jarvis is the phone side of Android Auto: Headway folded in, only the car's socket on the car's network, a Jarvis car screen with tabs, one guided connect | Accepted (2026-09-24) |
| [ADR-0048](0048-the-phone-and-the-car-draw-the-consoles-orb-natively.md) | The phone and the car draw the console's 3D orb natively in OpenGL ES 3: its numbers from two contracts, its geometry, motion and shaders ported, checked pixel for pixel beside the console; the flat reactor is the fallback | Accepted (2026-09-24) |
| [ADR-0049](0049-panels-tile-in-tab-stacks-around-the-orb.md) | Panels tile in tab stacks around the orb (the default): every panel a tab on the rail, dragged out to split or join like a tiling window manager; the orb the persistent background, gliding to its own tile | Accepted (2026-09-24) |
| [ADR-0050](0050-the-apps-update-themselves-from-the-tailnet.md) | The apps update themselves from the tailnet: the pipeline publishes the branch production runs, the phone installs production builds unattended (a setting, never a test build), the desktop applies updates when idle, and the console offers the first copy with a QR | Accepted (2026-09-24) |
| [ADR-0051](0051-the-desktop-app-is-one-binary-with-the-agent-inside.md) | The desktop app is one Tauri binary per platform with the Python agent frozen inside it: every CLI action a page, click-to-pair through `jarvis://`, native approvals, an assist popup on push-to-talk; Linux and Windows built here, macOS waiting for a Mac runner | Accepted (2026-09-24) |
| [ADR-0052](0052-the-room-has-no-top-bar-and-docks-hold-groups.md) | The room has no top bar; each dock column holds groups a person drags between | Accepted (2026-10-08) |
| [ADR-0053](0053-the-install-is-one-line-and-a-setup-page.md) | The install is one line; its questions are a page the installer serves for the length of the install, and images are built from the source when no registry is named | Accepted (2026-11-16) |

## Adding an ADR

Copy `0000-template.md` to `NNNN-slug.md` with the next free number, fill in the four
sections, and add a row above. Keep the status current: mark a record `Superseded by`
with a link when a later ADR replaces it, rather than editing the old decision.
