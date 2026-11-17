#!/usr/bin/env python3
"""The stand-ins of scripts/verify/install-clean.sh: what the one-line install fetches from, and
a model endpoint for the setup page to test.

Two things on one port, standard library only:

- ``GET /<owner>/<repo>/<ref>/install.sh`` (and ``install.ps1``): the entry points, at the path
  shape the public raw-file host serves them, read from ``--tree``.
- ``GET /v1/models`` and ``POST /v1/chat/completions``: an OpenAI-compatible endpoint with one
  model that always gives the same sentence.

Not covered: anything else the public host does (TLS, redirects, rate limits), and a model that
thinks. The source itself is fetched from a git daemon the shell script runs beside this.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "proof-model"
SENTENCE = "This is the install proof's stand-in model."
ENTRY_POINTS = ("install.sh", "install.ps1")


class Origin(BaseHTTPRequestHandler):
    tree: Path

    def log_message(self, format: str, *args: object) -> None:
        print("[origin] " + format % args, flush=True)

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/v1/models":
            body = {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "proof"}]}
            self._send(200, json.dumps(body).encode(), "application/json")
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[3] in ENTRY_POINTS:
            self._send(200, (self.tree / parts[3]).read_bytes(), "text/plain; charset=utf-8")
            return
        self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            asked = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            asked = {}
        if self.path.split("?", 1)[0] != "/v1/chat/completions":
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
            return
        if isinstance(asked, dict) and asked.get("stream"):
            chunk = {"id": "proof", "object": "chat.completion.chunk", "model": MODEL,
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": SENTENCE}, "finish_reason": None}]}
            last = {"id": "proof", "object": "chat.completion.chunk", "model": MODEL,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            body = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(last)}\n\ndata: [DONE]\n\n".encode()
            self._send(200, body, "text/event-stream")
            return
        reply = {"id": "proof", "object": "chat.completion", "model": MODEL,
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": SENTENCE}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        self._send(200, json.dumps(reply).encode(), "application/json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path, required=True, help="the source tree the entry points are read from")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    Origin.tree = args.tree
    # Every address of its own container, which is on the proof's network and no other.
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Origin)
    print(f"[origin] serving on :{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
