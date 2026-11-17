# Repro: greenhouse log via headless CDP (2026-09-02)

Full transcript of the exact working run in the JARVIS container (jarvis CT).
Kept concrete on purpose — the class-level SKILL.md is general; this is the
verified, copy-paste-able instance for this environment.

## Environment facts that made the default path fail
- No `google-chrome`/`chromium` on PATH; nothing in the standard profile dirs
  (`.config/google-chrome`, `.config/chromium`, …) — so browser-harness's
  auto-launch found nothing and refused (`chrome-not-running`).
- `AGENT_BROWSER_EXECUTABLE_PATH` already pointed at a working
  `chrome-headless-shell`:
  `/opt/hermes/.playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell`
  (Chrome for Testing 151.0.7922.34; runs fine: `--dump-dom about:blank` → html).
- The `browser-harness` CLI was in a uv cache venv, not on PATH:
  `/opt/data/home/.cache/uv/archive-v0/TtH34alF2MF6OCDmELVb1/bin/browser-harness`
  (found via `find / -name browser-harness`). Its `--doctor` reported all
  `[FAIL]` (no browser, no daemon, no cloud auth) — expected for a manual setup.
- `/dev/shm` was **64 MB** (`df -h /dev/shm`) — hence `--disable-dev-shm-usage`.
- Target was reachable with plain curl (HTTP 200) — but the task required
  following an in-page link, so a browser was genuinely needed.

## The four commands that produced the answer

1. Launch (background, terminal background=true, silent daemon):
   ```bash
   /opt/hermes/.playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell \
     --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
     --remote-debugging-port=9333 --user-data-dir=/tmp/gh-profile about:blank
   ```
2. Confirm CDP up:
   ```bash
   curl -sS -m5 http://127.0.0.1:9333/json/version
   # → { "Browser": "HeadlessChrome/151.0.7922.34", "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/…" }
   ```
3. Drive (note: `BU_CDP_URL` in THIS shell, because browser_exec scrubs it):
   ```bash
   cd /opt/data/home/.config/browser-harness   # harness's config/home dir
   BU_CDP_URL=http://127.0.0.1:9333 \
     /opt/data/home/.cache/uv/archive-v0/TtH34alF2MF6OCDmELVb1/bin/browser-harness <<'PY'
   new_tab("http://host.docker.internal:8899/")
   wait_for_load()
   print(page_info())   # {'title': '🐴 JCB fixture site', ...}
   links = js("Array.from(document.querySelectorAll('a')).map(a=>({text:a.innerText.trim(),href:a.href}))")
   print(links)         # 'Greenhouse log' → /facts
   goto_url("http://host.docker.internal:8899/facts")
   wait_for_load()
   print(js("document.body.innerText"))
   PY
   ```
   Page body: "Greenhouse sensor log — this week … The greenhouse sensor
   recorded a daily high of 31.5°C on Tuesday. Total rainfall for the week was
   12 mm. Soil moisture held at 42%." → answer: **12 mm**.
4. Cleanup: `process(action=kill, session_id=<chrome proc>)` and `rm -rf /tmp/gh-profile`.

## Gotchas observed live
- Re-running browser-harness from the harness config dir made `~/.config/browser-harness`
  the home; harmless. The `BU_CDP_URL` env on the CLI invocation is what routed it
  to our browser — without it, `chrome-not-running` again.
- The Chrome stderr log is full of dbus/vaapi/bluetooth WARN+ERROR lines; ignore
  them. `/json/version` is the only health signal that matters.
- First nav must be `new_tab`, not `goto_url` (harness convention).
