# ADR-0051: the desktop app is one binary per platform, with the agent inside

- **Status:** Accepted (2026-09-24, the owner's choice)
- **Comes from:** the owner, 2026-09-24: *"can we make it so the desktop app is literally an
  executable? with something like tauri? and still using the python backend … just so we can do all
  of the stuff that would require a CLI, inside a GUI, and easily click a link to pair … and compile
  a version for all platforms as one binary"*. Offered Tauri in place of Electron, beside it, or
  Electron with the agent bundled: **"Replace Electron"**; for macOS, which cannot be built on this
  Linux host: **"Linux + Windows now"**.
- **Supersedes:** the Electron shell (M07, M110). **Keeps:** [ADR-0050](0050-the-apps-update-themselves-from-the-tailnet.md)'s update rules.

## Context

The desktop was two installs that did not know about each other. The Electron shell (tray, a
push-to-talk key, the console in a window) never started the agent; the agent was a Python package
installed with pip and run by hand or by a hand-written systemd/launchd/Task Scheduler entry. The
shell found the agent's socket only if `AURA_AGENT_PORT` was set by hand, and on Windows looked for
the agent's token under `%LOCALAPPDATA%` while the agent wrote it under `~/.local/state`. The console
already minted a `jarvis://pair` link, but nothing registered the scheme, so pairing was a paste and
a `jarvis-desktop pair` on a terminal. Everything past pairing — permissions, the audit log, the
doctor — was a CLI. The shell's consent page was never loaded, and the console never listened for
the push-to-talk and approval events the shell sent it.

## Decision

**One Tauri app (`jarvis-desktop-app/`) replaces the Electron shell, and carries the agent.**

- **The agent is frozen into a sidecar** (`tools/build-agent.sh`: PyInstaller, one file, the `full`
  extras) and started by the app as `run`. The agent announces its socket on one stdout line when the
  app asks (`AURA_SHELL_ANNOUNCE`: port and token *file*, never the token), so the app never
  guesses a directory. A stop is restarted with a capped backoff — except exit 2, "nothing paired",
  which waits for pairing rather than looping. A frozen agent stands down its own pip self-update:
  the app updates it by updating itself.
- **Every CLI action is a page** (`ui/`, the "This computer" window): status and the agent's log,
  pairing, permissions (the agent's `tiers --json` / `policy set|panic|enable|disable`), activity
  (`audit --json`), checks (`doctor`), updates, start at login. A page may run only those
  subcommands (`protocol::cli_allowed`), never `run`, `pair` or a different config.
- **Click to pair.** The app registers `jarvis://` (the installer on Windows, at start on Linux) and
  is single-instance, so the console's "Open in the desktop app" opens the running app, which runs
  the agent's `pair`, keeps the console, loads it and restarts the agent.
- **Approvals are the app's own window**: a notification and the consent window, fed by the agent's
  `ask` frames; the three answers go back over the socket, and closing the window is a denial.
- **Push-to-talk opens an assist popup**, like the phone's: a small frameless window above
  everything, loading the console's `?popup=1` view (the same voice loop). The hotkey is held to
  talk (`jarvis:push-to-talk` with `detail.down`); the tray's item toggles.
- **The app's own pages are the Settings menu's register** (rows with a fixed label column, one
  compact control height, sentence case), on the console's generated tokens and its own fonts.
- **What may load is unchanged**: loopback, `*.ts.net`, the tailnet and private ranges; the console
  window's navigation is held to that, and it is granted no IPC at all.
- **Builds**: CI builds the AppImage on Ubuntu 22.04 (glibc 2.35 and newer) and cross-builds the
  Windows NSIS installer (cargo-xwin), both signed for Tauri's updater, and publishes them with
  `latest.json`. macOS has a job waiting for a Mac runner. The pure decisions are a crate of their
  own (`src-tauri/logic`), so they are tested on any machine with cargo.

## Consequences

- One download per computer; nothing to install beside it; pairing is a click.
- The AppImage is ~98 MB (it carries WebKitGTK), the Windows installer ~15 MB (WebView2 is the
  system's). The agent binary is ~16 MB inside either.
- The update key is a host secret (`/opt/jarvis-host/tauri-updater.key`) that has to be a CI
  variable before installed apps are offered builds; until then CI says so and publishes unsigned
  installers without a feed.
- An installed Electron shell is not migrated: it keeps polling its old feed, which is no longer
  written. Install the new app once from Settings → Apps.
- **Not proven here:** a real Windows or macOS machine, Authenticode/Apple signing (neither is done;
  SmartScreen and Gatekeeper warn), and the popup's transparency on a compositor (it is opaque).
