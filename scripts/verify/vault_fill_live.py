"""Live proof (T-1544): browser_vault fills the login tab browser_exec drives, in the deployed image.

Run from the checkout:
    docker exec -i jarvis sh -c 'HERMES_E2E_BROWSER=1 timeout 300 python3 -' < scripts/verify/vault_fill_live.py

Why our own and not upstream's evals/vault_fill_live_e2e.py: upstream made the tab browser_exec last drove
(set_driven_target) the ONLY fill candidate on its origin, so a fill can never land in another session's
tab. Its eval still drives /checkout last and expects the /login tab, so it fails in v0.23.2 by design.
This proof drives the login tab last (what the headless-browser skill teaches) and pins the other half: with
/checkout driven, a login fill refuses instead of jumping tabs.

Scratch HERMES_HOME and a loopback page server: nothing touches the live vault or a real site. It does not
cover a manager-backed vault (1Password, Bitwarden) or a page whose form renders after load.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, "/opt/hermes")
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="hermes-vault-proof-")
os.chdir("/opt/hermes")

SECRET = "pw-PROOF-6203"
PAGES = {
    "/login": b"""<!doctype html><title>login</title><form><input name=email type=email autocomplete=username>
<input name=pw type=password autocomplete=current-password><input type=submit></form>""",
    "/checkout": b"""<!doctype html><title>checkout</title><form><input name=cardnum placeholder="Card number">
<input name=email type=email></form>""",
}
TASK = "vault-proof"


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = PAGES.get(self.path, b"<!doctype html><title>x</title>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def main() -> int:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{srv.server_address[1]}"

    from tools import browser_use_cli as bu
    from tools import browser_vault_tool as bvt
    from tools.browser_supervisor import SUPERVISOR_REGISTRY
    from agent.vault_store import get_vault_store

    def run(code: str) -> None:
        out = json.loads(bu.browser_exec(code, task_id=TASK, timeout_s=90))
        assert out.get("success"), out

    try:
        # The login page is neither the first tab nor the one the supervisor attached to first.
        run("new_tab('about:blank')")
        run(f"new_tab({origin + '/checkout'!r}); wait_for_load()")
        run(f"new_tab({origin + '/login'!r}); wait_for_load()")
        sup = SUPERVISOR_REGISTRY.get(TASK)
        assert sup is not None, "browser_exec attached no supervisor"

        login = get_vault_store().add_item(
            "login", "site", {"identifier_type": "email", "identifier": "a@example.com", "password": SECRET},
            origin=origin)
        listed = bvt.browser_vault_list()
        assert login.id in listed and SECRET not in listed, "the list result lacked the handle or carried the password"

        raw = bvt.browser_vault_fill(login.id, task_id=TASK)
        out = json.loads(raw)
        assert out.get("success") and out.get("filled_fields") == 1, out
        assert SECRET not in raw, "the fill result carried the password"
        dom = sup.evaluate_runtime("location.pathname + ' ' + document.querySelector('input[name=pw]').value")
        assert dom.get("result") == f"/login {SECRET}", "the password did not land in the /login tab"
        print("1 fill reached the /login tab browser_exec drove; result:", sorted(out))

        # Drive /checkout: the fill must refuse rather than go looking for the /login tab.
        run(f"new_tab({origin + '/checkout'!r}); wait_for_load()")
        raw = bvt.browser_vault_fill(login.id, task_id=TASK)
        out = json.loads(raw)
        assert not out.get("success"), out
        assert SECRET not in raw
        print("2 with /checkout driven the fill refused:", str(out.get("error"))[:80])
        print("vault fill live proof: OK")
        return 0
    finally:
        srv.shutdown()
        bu.stop_harness_daemons()  # the harness daemon this proof spawned; the gateway's are other processes'


if __name__ == "__main__":
    sys.exit(main())
