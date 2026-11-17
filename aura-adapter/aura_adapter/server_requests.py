"""Hermes' server→client requests, translated for apps that speak the event protocol.

Hermes v2026.9.24 stopped sending ``approval.request`` and ``clarify.request`` events. It asks
the client with a JSON-RPC request instead (``{"id": "srq-…", "method": "approval", "params":
{"session_id", …}}``) and waits for a response frame with that id -- but only on connections
that sent ``client.capabilities {server_requests: true}``. On any other it withdraws the
approval at once ("the attached client cannot answer approval requests"). Every app reaches
Hermes through this relay and none of them sent it, so from the upgrade on every gated tool was
refused on every surface, and every clarify question went unasked (2026-10-06).

:class:`RequestBridge` sits in the relay, one per app socket. It advertises the capability
upstream, turns ``approval`` and ``clarify`` requests into the events the console, the phone and
the desktop already render, turns their ``approval.respond`` / ``clarify.respond`` back into the
response Hermes waits for (a batch question through ``clarify.lock``; a batch also rides flat, its
first question as ``question``/``choices``/``options``, and an answer with no ``question_id`` locks
the question it was shown and asks the next), maps ``request.cancel`` to
``approval.cancelled`` / ``clarify.cancelled``, and answers every other request kind (sudo,
secret, vault, tour, GUI reads) with "method not found" at once, so the agent is told the client
cannot answer rather than waiting out the deadline.

A request still open when an app reconnects comes back as ``open_requests`` in the result of
``session.resume`` (or ``session.activate`` / ``session.events.since``); the apps do not read that
field, so each is asked again here as if it had just arrived, a batch from its first question not
yet locked. Without it the answer went out under an id the new socket's bridge had never seen,
and no turn started (T-0730).

An ``approval.respond`` for an ask the adapter itself holds (a sensitive one, waiting on the
phone's fingerprint: ``approval_proof.Desk``) is answered here and never goes upstream.

Does not cover: answers from an app that already speaks the new protocol (it would send its own
capabilities and responses; they pass through untouched).
"""
from __future__ import annotations

import json
from typing import Any

from . import approval_proof

#: The id of the capabilities call; its response is the relay's, not the app's.
HELLO_ID = "jarvis-adapter-server-requests"

#: Request kinds an app can show and answer; everything else is refused fast.
TRANSLATED = ("approval", "clarify")


def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _flat(question: dict[str, Any]) -> dict[str, Any]:
    """A batch question as the flat fields the phone and the desktop console read."""
    choices = question.get("choices") or []
    return {"question": question.get("question") or "", "choices": choices, "options": choices}


def _event(kind: str, session_id: str, payload: dict[str, Any]) -> str:
    return _dump({"jsonrpc": "2.0", "method": "event",
                  "params": {"type": kind, "session_id": session_id, "payload": payload}})


class RequestBridge:
    def __init__(self, desk: approval_proof.Desk | None = None) -> None:
        self._desk = desk
        #: request_id the app sees -> (srq id, kind)
        self._open: dict[str, tuple[str, str]] = {}
        #: a clarify batch's request_id -> (session id, its questions not yet answered)
        self._batches: dict[str, tuple[str, list[dict[str, Any]]]] = {}

    @staticmethod
    def hello() -> str:
        return _dump({"jsonrpc": "2.0", "id": HELLO_ID, "method": "client.capabilities",
                      "params": {"server_requests": True}})

    def from_upstream(self, frame: str) -> tuple[list[str], list[str]] | None:
        """(frames for the app, frames back upstream), or None to relay ``frame`` unchanged."""
        if '"srq-' not in frame and HELLO_ID not in frame and '"request.cancel"' not in frame:
            return None
        try:
            msg = json.loads(frame)
        except ValueError:
            return None
        if not isinstance(msg, dict):
            return None
        if msg.get("id") == HELLO_ID and "method" not in msg:
            return [], []
        method, rid = msg.get("method"), msg.get("id")
        if isinstance(rid, str) and rid.startswith("srq-") and isinstance(method, str):
            return self._ask(rid, method, msg.get("params"))
        result = msg.get("result")
        if isinstance(result, dict) and isinstance(result.get("open_requests"), list):
            to_app, to_up = [frame], []
            for req in result["open_requests"]:
                if isinstance(req, dict) and str(req.get("id") or "").startswith("srq-"):
                    app, up = self._ask(str(req["id"]), str(req.get("method") or ""), req.get("params"))
                    to_app += app
                    to_up += up
            return to_app, to_up
        event = msg.get("params")
        if method == "event" and isinstance(event, dict) and event.get("type") == "request.cancel":
            payload = event.get("payload")
            srq = payload.get("id") if isinstance(payload, dict) else None
            cancelled = next((s for s, (r, _k) in self._open.items() if r == srq), None)
            if cancelled is None:
                return [], []
            kind = self._open.pop(cancelled)[1]
            self._batches.pop(cancelled, None)
            reason = payload.get("reason", "") if isinstance(payload, dict) else ""
            return [_event(f"{kind}.cancelled", str(event.get("session_id") or ""),
                           {"request_id": cancelled, "reason": reason})], []
        return None

    def _ask(self, rid: str, method: str, raw: Any) -> tuple[list[str], list[str]]:
        """A server request as the event the apps render, or refused at once if no app can answer it."""
        params = dict(raw) if isinstance(raw, dict) else {}
        sid = str(params.pop("session_id", "") or "")
        if method not in TRANSLATED:
            return [], [_dump({"jsonrpc": "2.0", "id": rid,
                               "error": {"code": -32601, "message": f"this client cannot answer {method}"}})]
        # An approval carries its own request_id (the queue's); a clarify has only the srq id.
        shown = str(params.get("request_id") or rid)
        params["request_id"] = shown
        self._open[shown] = (rid, method)
        if method == "clarify":
            # A batch's answers locked before a reconnect (open_requests) are not asked again.
            locked = params.pop("answers", None) or {}
            batch = params.get("questions")
            if isinstance(batch, list) and batch:
                # Hermes asks as a batch even for one question; the phone, the car and the
                # desktop console read only a flat question, so the first rides flat here
                # and the console still reads the batch (2026-11-09, T-0798).
                pending = [q for q in batch if isinstance(q, dict) and str(q.get("qid") or "") not in locked]
                self._batches[shown] = (sid, pending)
                params = {**_flat(pending[0] if pending else batch[0]), **params, "questions": pending or batch}
            params.setdefault("options", params.get("choices"))
        return [_event(f"{method}.request", sid, params)], []

    def from_app(self, frame: str) -> tuple[list[str], list[str]] | None:
        """(frames upstream, frames back to the app), or None to relay ``frame`` unchanged."""
        if '"approval.respond"' not in frame and '"clarify.respond"' not in frame:
            return None
        try:
            msg = json.loads(frame)
        except ValueError:
            return None
        if not isinstance(msg, dict) or not isinstance(msg.get("params"), dict):
            return None
        params, app_id = msg["params"], msg.get("id")
        shown = str(params.get("request_id") or "")
        if self._desk is not None and msg.get("method") == "approval.respond" and self._desk.holds(shown):
            if self._desk.answer(shown, str(params.get("choice") or "deny"), params.get("signature")):
                return [], [_dump({"jsonrpc": "2.0", "id": app_id, "result": {"ok": True}})] if app_id is not None else []
            # Left open: the phone can still answer it.
            return [], [_dump({"jsonrpc": "2.0", "id": app_id,
                               "error": {"code": -32000, "message": approval_proof.REFUSED}})]
        if shown not in self._open:
            return None  # not one of ours: an older Hermes, or an app on the new protocol
        srq, kind = self._open[shown]
        ok = [_dump({"jsonrpc": "2.0", "id": app_id, "result": {"ok": True}})] if app_id is not None else []
        if msg.get("method") == "approval.respond" and kind == "approval":
            del self._open[shown]
            return [_dump({"jsonrpc": "2.0", "id": srq, "result": {"choice": str(params.get("choice") or "deny")}})], ok
        if msg.get("method") == "clarify.respond" and kind == "clarify":
            answer = params.get("answer", "")
            if shown in self._batches:
                # One question of a batch: Hermes locks it and keeps the request open for the
                # rest. An answer with no id (every app but the console) is the question it
                # was shown, the first not yet answered; the next is then asked the same way.
                sid, pending = self._batches[shown]
                qid = str(params.get("question_id") or (pending[0].get("qid") if pending else "") or "")
                pending[:] = [q for q in pending if str(q.get("qid") or "") != qid]
                lock = [_dump({"jsonrpc": "2.0", "id": app_id, "method": "clarify.lock",
                               "params": {"request_id": srq, "question_id": qid, "answer": answer}})]
                if not pending:
                    del self._batches[shown], self._open[shown]
                    return lock, []
                if params.get("question_id"):
                    return lock, []
                nxt = {**_flat(pending[0]), "questions": pending, "request_id": shown}
                return lock, [_event("clarify.request", sid, nxt)]
            if params.get("question_id"):
                return [_dump({"jsonrpc": "2.0", "id": app_id, "method": "clarify.lock",
                               "params": {"request_id": srq, "question_id": params["question_id"],
                                          "answer": answer}})], []
            del self._open[shown]
            return [_dump({"jsonrpc": "2.0", "id": srq, "result": {"answer": answer}})], ok
        return None
