"""Live proof (T-1546): while one browser_exec call drives a page, the activity gets pictures.

Run from the checkout (the plugin module first, so an undeployed change is what is proved):
    cat hermes/plugins/aura-activity/activity.py scripts/verify/activity_live_frames_live.py \
      | docker exec -i jarvis sh -c 'cd /opt/hermes && PYTHONPATH=/opt/data/plugins \
        HERMES_E2E_BROWSER=1 timeout 300 python3 -'

A scratch HERMES_HOME and a loopback page pair: one browser_exec call opens a red page, waits,
moves the same tab to a blue page and waits again, about five seconds in all. Meanwhile the
plugin's own pre hook runs the live loop through the task's CDP supervisor (a cold browser start included: the loop waits LIVE_START_S for a first picture). The proof asserts
that frames arrive during the call, red before blue, at most one a second, and none after the
post hook.

Does not cover: the HUD transport (the frames are recorded where SEND would hand them to
aura-hud), or a cloud browser.

No ``from __future__`` import: it runs concatenated after the module, where one would be a
SyntaxError.
"""
import base64
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, "/opt/hermes")
_HOME = tempfile.mkdtemp(prefix="hermes-live-proof-")
os.environ["HERMES_HOME"] = _HOME

_COLOURS = {"/red": "#d00000", "/blue": "#0000d0"}


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        colour = _COLOURS.get(self.path, "#808080")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<!doctype html><title>p</title><body style='margin:0;background:{colour}'>"
                         .encode())

    def log_message(self, *args: object) -> None:
        pass


def _hue(data_url: str) -> str:
    from PIL import Image

    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        r, _g, b = img.convert("RGB").resize((1, 1)).getpixel((0, 0))
    return "red" if r > b + 80 else "blue" if b > r + 80 else "other"


def _main() -> int:
    mod = globals()
    if "pre_tool_call" not in mod:  # the deployed copy
        sys.path.insert(0, "/opt/data/plugins")
        import importlib

        mod = vars(importlib.import_module("aura-activity.activity".replace("-", "_")))
    seen: list[tuple[float, str, dict]] = []
    mod["SEND"] = lambda sid, event, payload: seen.append((time.time(), event, payload)) or True
    mod["enabled"] = lambda: True
    mod["PICTURE"] = lambda *a, **k: b""  # the post hook's own picture is not under test

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{srv.server_address[1]}"
    task, sid = "live-proof", "live-proof-session"
    from tools import browser_use_cli as bu

    code = (f"new_tab({origin + '/red'!r}); wait_for_load(); import time; time.sleep(2.5); "
            f"goto_url({origin + '/blue'!r}); wait_for_load(); time.sleep(2.5); print('ok')")
    try:
        mod["pre_tool_call"]("browser_exec", {"code": code}, task_id=task, session_id=sid)
        started = time.time()
        out = json.loads(bu.browser_exec(code, task_id=task, timeout_s=90))
        ended = time.time()
        assert out.get("success"), str(out.get("stderr"))[-500:]
        mod["post_tool_call"](tool_name="browser_exec", args={"code": code}, result="{}",
                              task_id=task, session_id=sid, status="ok")
        time.sleep(1.5)
        frames = [(t, p) for t, e, p in seen if e == "activity.frame" and p.get("image")]
        during = [(t, p) for t, p in frames if started <= t <= ended]
        hues = [_hue(p["image"]) for _, p in during]
        print(f"call {ended - started:.1f}s, frames during it: {len(during)}, hues: {hues}")
        assert len(during) >= 3, "too few pictures while the call ran"
        assert "red" in hues and "blue" in hues, "the pictures are not the driven tab"
        assert hues.index("red") < len(hues) - 1 - hues[::-1].index("blue"), "red must come before blue"
        gaps = [b[0] - a[0] for a, b in zip(during, during[1:])]
        assert all(g >= 0.9 for g in gaps), f"more than one picture a second: {gaps}"
        assert not [t for t, _ in frames if t > ended + 0.5], "a picture came after the call returned"
        print("activity live frames proof: OK")
        return 0
    finally:
        bu.stop_harness_daemons()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(_main())
