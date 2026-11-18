---
name: headless-browser
description: "Drive headless Chromium over CDP when browser tools won't."
version: 1.0.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, headless, automation, chromium, playwright, browser-use]
    category: web
    related_skills: [blocked-page-recovery, web-search-fallback]
---

# Headless Browser (CDP) — drive a browser the managed tools won't launch

**Since 2026-09-18 this recipe does not run from the shell.** `terminal` is a sandbox with
no network, no environment and no `/opt/browser` (ADR-0045): `$AGENT_BROWSER_EXECUTABLE_PATH`
is unset there, the binary is not visible, and a port on `127.0.0.1` — the harness's, or one
you opened — is unreachable even from the shell that opened it. The `browser_*` toolset owns
the browser now: it runs in the gateway with the image's Chromium, its profile and its
download directory (`/opt/data/files`). When it reports `chrome-not-running`, that is a
capability gap to file, not a reason to launch Chrome by hand. What follows is the recipe
for a gateway whose terminal backend is `local` (the sandbox runbook's fallback), kept for
that case.

Use when a task genuinely needs a real browser (JS rendering, clicking a link,
reading a DOM that a plain fetch returns as a shell) but the managed `browser_exec`
/ `browser_*` tools fail with `chrome-not-running` / "no supported browser is
running and none could be launched". That error means the browser-use harness
scanned the *standard* Chrome profile paths (google-chrome, chromium, edge, …)
found nothing, and would not auto-launch a browser that isn't there.

If `web_extract` can read the page, do that first — this is the last, most
expensive route (see blocked-page-recovery). Reach here only when
interaction or JS rendering is actually required.

## The working recipe (validated, 2026-09-02)

You almost certainly have a Chromium already on the machine even when no
`google-chrome` is on PATH. Find it, launch it with a CDP port, then drive it
through the `browser-harness` CLI directly.

1. **Locate the binary + harness.** In this environment the env already points at
   both: `AGENT_BROWSER_EXECUTABLE_PATH` is the playwright `chrome-headless-shell`,
   and the `browser-harness` binary lives in a uv cache venv. Discover them rather
   than guessing:
   ```bash
   echo "$AGENT_BROWSER_EXECUTABLE_PATH"            # e.g. …/chromium_headless_shell-*/chrome-headless-shell
   env | grep -iE 'playwright|browser'              # PLAYWRIGHT_BROWSERS_PATH etc.
   # harness CLI (the one whose stderr prints "browser-harness: …"):
   find / -name 'browser-harness' -type f 2>/dev/null | head
   # e.g. ~/.cache/uv/archive-v0/<hash>/bin/browser-harness
   ```
   Confirm the browser actually runs before anything else:
   ```bash
   "$AGENT_BROWSER_EXECUTABLE_PATH" --headless --no-sandbox --disable-gpu --dump-dom about:blank
   ```
   If that prints `<html>…</html>`, the browser works and the managed tools simply
   aren't finding it.

2. **Launch a dedicated headless browser with CDP** (background, daemon-style):
   ```bash
   "$AGENT_BROWSER_EXECUTABLE_PATH" --headless --no-sandbox --disable-gpu \
     --disable-dev-shm-usage --remote-debugging-port=9333 \
     --user-data-dir=/tmp/bh-profile about:blank 2>/tmp/bh-chrome.log
   ```
   - `--user-data-dir=/tmp/bh-profile` — a SCRATCH profile, never the user's real one.
   - `--disable-dev-shm-usage` — REQUIRED in containers; /dev/shm is often 64 MB
     and Chrome crashes without it. Confirm with `df -h /dev/shm` first.
   - Pick a free port (9333 is fine; avoid 9222/9223 which the harness probes).

3. **Verify CDP is up** through the harness itself: the first `page_info()` of step 4
   fails fast when nothing answers on the port, and the Chrome log's `DevTools listening
   on ws://…` line says the same. (A `/json/version` probe from the shell is no longer
   possible: the sandbox has no loopback to the gateway.)

4. **Drive it through the harness CLI, with `BU_CDP_URL` set.** See the pitfall
   below for why this is the harness CLI and NOT `browser_exec`.
   ```bash
   BU_CDP_URL=http://127.0.0.1:9333 <path>/browser-harness <<'PY'
   new_tab("http://host.docker.internal:8899/")   # first nav is new_tab
   wait_for_load()
   print(page_info())
   goto_url("http://host.docker.internal:8899/facts")
   wait_for_load()
   print(js("document.body.innerText"))
   PY
   ```

5. **Clean up when done:** kill the background Chrome process and `rm -rf /tmp/bh-profile`.

## Key pitfall: call `browser-harness` directly, not through `browser_exec`

`browser_exec` builds its subprocess env via a **credential-scrubbing** step that
re-adds only a fixed allow-list of browser keys (BROWSERBASE_*, BROWSER_USE_API_KEY,
FIRECRAWL_*). `BU_CDP_URL` / `BU_CDP_WS` are NOT in that allow-list, so the browser
you launched yourself is invisible to `browser_exec` — it will still report
`chrome-not-running`. The fix is to run the `browser-harness` binary yourself with
`BU_CDP_URL` exported in that shell invocation, exactly as in step 4. The daemon
resolves `BU_CDP_URL` → WS via `/json/version` on its own, so the HTTP endpoint is
enough.

## The harness heredoc vocabulary (helpers pre-imported)

```python
new_tab(url)        # FIRST navigation (not goto_url); also switches a named session to its own tab
goto_url(url)       # navigate the current tab
wait_for_load()     # after navigation
page_info()         # → {'url','title','w','h','sx','sy','pw','ph'}
js(expr)            # evaluate JS, return value; wrap fns as js('(() => {...})()')
fill_input(sel, text)
click_at_xy(x, y)
cdp('Domain.method', **kw)   # raw CDP, e.g. cdp('Target.getTargets')
ensure_real_tab()   # recover from a stale/internal tab
```
Text-first: read state with `page_info()` and extract with `js("document.body.innerText")`
or `js("Array.from(document.querySelectorAll('a')).map(a=>({text:a.innerText,href:a.href}))")`.
You don't need a screenshot to answer a "follow the link and read X" task. A task that
*changes* something (a form sent, a booking, an order, a setting) is different: end it with
one `capture_screenshot()` of the result page. Its screenshot stays on the HUD as the owner's
receipt (one panel, replaced by the next), and "done" is said only if the page shows it.

## Signing in

A site's password comes from the vault, never from you. The vault fills only the tab
`browser_exec` drove last, so a fill cannot land in another session's tab:

1. Open the login page in `browser_exec`: `new_tab(url); wait_for_load()`. If you opened
   other tabs since, switch back to the login tab (`new_tab` with its URL) right before step 4.
2. `browser_vault action=list` names the saved logins with their site, identifier and handle.
   No item for this site: on the login page call `browser_vault action=save_login`. It asks the
   owner in a masked prompt on their screen, then saves the login and fills it, so skip to step 5.
   Never ask for the password in chat.
3. Type the identifier yourself: `fill_input('input[type=email]', 'the listed identifier')`.
4. `browser_vault action=fill handle=<handle>` writes the password into the page. The result
   reports counts only, never the value.
5. A one-time code step after it: `browser_vault action=enter_code handle=<handle>`.

Never type a password with `fill_input` or `js()`, and never read a password field back. A
page read after the fill is scrubbed anyway. When the fill answers "No fillable login field",
the tab you drove last is not the login page: drive it (step 1) and fill again, once.

## Across turns

A website task that takes several turns (a goal, or the owner coming back to it) loses its tabs
at the end of every turn: the next turn's browser starts on `about:blank`. The sign-in is kept,
because the profile's cookies are written before the browser stops.

- Before a turn ends mid-task, print where you are: `print(page_info()['url'])`, and say the
  step you were on in your reply.
- On the next turn, reopen that URL with `new_tab(url); wait_for_load()`. Do not start over
  from the home page or ask the owner where you were. Say it in one line, for example
  "Resuming at the delivery step."
- If the page asks you to sign in again, the session expired on the site's side: sign in as
  under Signing in, then go back to the URL. You are normally still signed in.
- A page that failed to load: reopen the same URL once before you report it.

## Pitfalls table

| Symptom | Cause + remedy |
|---|---|
| `chrome-not-running: … none could be launched` | No standard-Chrome profile on disk. Don't wait for it to auto-launch — bring your own (step 2). This is environment state, not a permanent defect: a real Chrome install would make the managed tools work again. |
| Browser dies on launch in a container | /dev/shm too small → add `--disable-dev-shm-usage`. Missing sandbox user → `--no-sandbox`. |
| `browser_exec` still says chrome-not-running after you launched one | You're going through the scrubbed env. Use the `browser-harness` CLI directly with `BU_CDP_URL` (step 4). |
| dbus / vaapi / bluetooth WARN+ERROR lines in the Chrome log | Harmless noise on a headless box. Judge health by `/json/version`, not the log. |
| Port already used | `ss -tlnp \| grep 9333`; pick another free port. |

## When NOT to use this

- The page is plain and `web_extract` returns real content — do that first.
- The task needs the user's *logged-in desktop* browser or native apps — that's
  `computer-use` (cua-driver), a different toolset.

## Reference

- `references/repro-2026-09-02-greenhouse.md` — a fully verified run in the JARVIS
  container: exact binary paths, the /dev/shm=64 MB finding, the four commands as
  executed, and the live gotchas. Use it to replay this recipe step-for-step.
