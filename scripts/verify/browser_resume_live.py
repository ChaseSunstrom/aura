"""Live proof (T-1545): a sign-in made just before a turn ends is there on the next turn.

Run from the checkout (the plugin module first, so an undeployed change is what is proved):
    cat hermes/plugins/_shared/_browser_flush.py scripts/verify/browser_resume_live.py \
      | docker exec -i jarvis sh -c 'HERMES_E2E_BROWSER=1 timeout 300 python3 -'

A scratch HERMES_HOME with a persistent profile (production sets browser.user_data_dir), a
loopback page that sets a cookie, then Hermes's own per-turn cleanup with no wait. Without
_browser_flush the cookie was gone (Chromium writes cookies about every 30 s); with it, it is
kept. Tabs are not: the next turn starts on about:blank and reopens the URL.

Does not cover: a real site's sign-in flow, or a gateway turn end (cleanup_browser is the call
that turn end makes).

No ``from __future__`` import: it runs concatenated after the module, where one would be a
SyntaxError.
"""
import http.server
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, "/opt/hermes")
_HOME = tempfile.mkdtemp(prefix="hermes-resume-proof-")
os.environ["HERMES_HOME"] = _HOME
with open(os.path.join(_HOME, "config.yaml"), "w", encoding="utf-8") as fh:
    fh.write(f"browser:\n  user_data_dir: {_HOME}/browser/profile\n")
os.chdir("/opt/hermes")


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if self.path == "/login":
            self.send_header("Set-Cookie", "sid=proof1; Path=/; Max-Age=3600")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>page</title>")

    def log_message(self, *args: object) -> None:
        pass


def _main() -> int:
    flush_install = globals().get("install")
    if flush_install is None:  # the deployed copy
        sys.path.insert(0, "/opt/data/plugins")
        from _shared._browser_flush import install as flush_install
    assert flush_install() is True, "the browser stop could not be wrapped"

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{srv.server_address[1]}"
    task = "resume-proof"
    from tools import browser_use_cli as bu
    from tools.browser_session_lifecycle import cleanup_browser

    def run(code: str) -> str:
        out = json.loads(bu.browser_exec(code, task_id=task, timeout_s=90))
        assert out.get("success"), out
        return str(out.get("output") or "").strip()

    try:
        assert run(f"new_tab({origin + '/login'!r}); wait_for_load(); print(js('document.cookie'))") == "sid=proof1"
        cleanup_browser(task)  # the turn ends at once: no time for Chromium's own cookie write
        assert run("print(page_info()['url'])") == "about:blank", "a tab survived the turn end"
        kept = run(f"new_tab({origin + '/next'!r}); wait_for_load(); print(js('document.cookie'))")
        assert kept == "sid=proof1", f"the sign-in did not survive the turn end: {kept!r}"
        print("browser resume live proof: OK (cookie kept across the turn end, tab reopened)")
        return 0
    finally:
        bu.stop_harness_daemons()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(_main())
