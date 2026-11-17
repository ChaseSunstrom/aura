# aura-desktop-app — Jarvis on a computer, in one binary

The desktop app (Tauri, [ADR-0051](../docs/adr/0051-the-desktop-app-is-one-binary-with-the-agent-inside.md)):
one file per platform — an AppImage on Linux, an installer on Windows — that
carries everything a computer needs:

* **the console** in a window: the same SvelteKit build a browser loads, from
  the same server, so when the console gains a page this app has it;
* **the device agent** (`jarvis-desktop`, Python) frozen inside it as a sidecar
  the app starts, watches and restarts — nothing to `pip install`, no terminal;
* a **tray** that says what the assistant is doing, in the reactor's own words;
* **push-to-talk** (`Super+Space`, held to talk) into a small **assist popup**
  above everything, like the phone's: the console's `?popup=1` view;
* **native approvals**: a notification and a consent window when the agent needs
  a person, answerable with the console closed — closing it denies;
* **click-to-pair**: the console's "Open in the desktop app" is a `jarvis://pair`
  link the app registers; clicking it pairs this computer;
* **This computer**, the pages that replace the agent's CLI: status and the
  agent's log, pairing, permissions (what Jarvis may do here, per action, and a
  switch that refuses everything), activity, checks, updates, start at login;
* **updates** of itself — and so of the agent inside it — from the house,
  applied when nothing would notice (ADR-0050).

## First open

Nothing chosen: the **Pair** page. Choose "Open in the desktop app" on the
console's Pairing panel, or paste the pairing link or just the console's
address. A console that does not answer is a page that says so — "No console
there yet", with the address it tried — retried every five seconds, never a
blank window. Until something is paired the agent is not restarted in a loop;
pairing starts it.

## Layout

| Path | What |
|---|---|
| `src-tauri/src/main.rs` | the app: plugins, the hotkey, the `jarvis://` link, start-up, the update loops |
| `src-tauri/src/shell.rs` | the agent it starts, the socket it answers the agent on, the windows (console, popup, consent, this computer), the tray, the updater |
| `src-tauri/src/commands.rs` | what the app's own pages may call; the console window is granted none (`capabilities/local.json`) |
| `src-tauri/logic/` | the decisions, apart from any window: what the console window may load, a pasted address or link, the agent's frames, the tray's words, when an update applies. Pure, so `cargo test` runs anywhere |
| `ui/` | the app's own pages, on the console's tokens and fonts, in the Settings menu's register |
| `tools/build-agent.sh` | freezes the agent with PyInstaller into `src-tauri/binaries/jarvis-desktop-agent-<target>` |

`ui/tokens.css`, `ui/tokens.ts` and `src-tauri/logic/src/states.rs` are
generated from `design/tokens.json` and `tests/contracts/reactor.json`
(`python3 design/build.py`); edit the source, never the output.

## Build

Linux needs Rust, Node 22, a Python 3.12 to freeze the agent with, and the
webkit toolchain (`libwebkit2gtk-4.1-dev libayatana-appindicator3-dev
librsvg2-dev libxdo-dev libssl-dev patchelf xdg-utils desktop-file-utils`):

```bash
cd aura-desktop-app
PYTHON=python3.12 bash tools/build-agent.sh          # the sidecar
npm ci && npx vitest run                             # the pages' logic
(cd src-tauri && cargo test --workspace)             # the Rust
npx tauri build --bundles appimage                   # src-tauri/target/release/bundle/appimage/
```

Windows is cross-built from Linux the way Tauri documents it (NSIS, `lld`,
`llvm`, `clang`, `cargo-xwin`), with the agent frozen by the Windows Python under
Wine (`PYTHON="wine python" TRIPLE=x86_64-pc-windows-msvc bash tools/build-agent.sh`):

```bash
npx tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc --bundles nsis
```

CI does all of it (`.gitlab/ci/desktop-dist.yml`) and publishes the AppImage,
the installer and `latest.json` to the project's generic package registry.
macOS has to be built on a Mac: `desktop:shell:macos` is ready for a runner
tagged `macos` and runs when `AURA_MACOS_RUNNER=1`.

## Signing

Updates are signed with the key whose public half is `plugins.updater.pubkey`
in `src-tauri/tauri.conf.json`. The private key is on the host at
`/opt/jarvis-host/tauri-updater.key` (0600; the deploy's rsync leaves it alone)
and reaches CI as the masked, protected variable `TAURI_SIGNING_PRIVATE_KEY`.
Without that variable CI publishes unsigned installers and no `latest.json`,
so installed apps are not offered them. The installers are not Authenticode or
Apple signed: Windows SmartScreen and macOS Gatekeeper warn on first run.

## Environment

| Variable | What |
|---|---|
| `AURA_CONSOLE_URL` | the console to load, over what the Pair page kept (loopback or a private range, the LAN included; anything else is refused) |
| `AURA_PUSH_TO_TALK_KEY` | the hotkey, default `Super+Space` |
| `AURA_PALETTE_KEY` | the typing key from any app: opens the popup with its line focused, default `Ctrl+Shift+K` (not bare `Ctrl+K`, which would take that chord from every browser and editor); one another app owns is logged and the tray says so |
| `AURA_DICTATION_KEY` | off until set (T-1258): hold it, speak, let go, and what the adapter heard (the same speaker gate as a spoken turn) is typed once into the focused window by the agent. Needs a modifier (`Ctrl+Alt+D`); a chord push-to-talk, the typing key or Esc already is is not registered; a hold ends at 60 s and a key still held after that arms nothing until it is let go. Only registered when the console is `https` or on loopback (the words cross to the adapter inside it); on the default plain-http LAN console the key stays unregistered and one log line says why. A press records only with an agent connected. A tap, silence or a refused transcription types nothing. Typing needs the agent's `input_automation.enabled` on for that desktop (T-1307): at start the app reads the agent's own config file (the one it starts the agent on by default; `config.json` under the user's config directory in `jarvis-desktop`) and `AURA_INPUT_ENABLED`, as the agent does, and with the switch off, or not readable, the key is not registered at all, so nothing is recorded or transcribed for words the agent would drop; the tray line says typing is switched off. The switch is read once at start: turn it on, then restart the app (a change while the app runs is not noticed). Not private from the machine: the words ride the popup's window title for an instant, so a local window-list tool can read them, and a script running inside the console page could choose the typed words on a real press (the press gates when the typing happens, not what it is) |
| `AURA_AUTO_UPDATE` | `0` stops the hourly check; "Check now" still works |

## Not covered

A real Windows or macOS machine (the installer is cross-built and its agent
frozen under Wine; WebView2 does not run under Wine), a GPU compositor's
transparency for the popup (it is an opaque flat surface), and Wayland input
automation (the agent's `pyautogui` is X11-only, `aura-desktop/README.md`).
