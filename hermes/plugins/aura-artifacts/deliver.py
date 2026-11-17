"""A publish shows the artifact where the person is.

The media brief's M2b gate: *"Publishing emits an event on the HUD channel so
the artifact appears where I am."* The Hermes side emits, not the adapter: this
plugin and aura-hud load into the same gateway process and a tool handler is
handed the session, which the adapter (a proxy) never sees.

What ``delivered`` does not guarantee: that a person saw it. It is the HUD
store's own word for "a live client of this session was routed the frame", and
it is False on the api_server platform and in a cron turn, which is a fact about
the session, not a failure of the publish -- the artifact is in the store either
way, which is why nothing here can fail the tool call.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from . import surfaces

_LOGGER = logging.getLogger(__name__)

#: aura-hud's module, by path: the plugin is a sibling directory, not a package
#: this one can import through the tree (Hermes loads each as ``hermes_plugins.<slug>``).
_HUD_SOURCE = Path(__file__).resolve().parents[1] / "aura-hud" / "hud.py"
#: The name the fallback load registers under, so a second publish reuses it.
_FALLBACK_NAME = "jarvis_hud_hud"

#: The capability an artifact needs (``surfaces.capability_for``) → the HUD kind
#: that renders it. Checked against the loaded aura-hud's ``KINDS`` at call
#: time, because the media kinds (M3) and this delivery (M2b) land separately
#: and the deployed copies may not match: a kind the running contract lacks
#: would be rejected by ``validate`` and shown nowhere, so it becomes a
#: ``notice`` naming the artifact and its URL instead.
_KIND_FOR: dict[str, str] = {
    "images": "image", "video": "video", "audio": "audio", "documents": "document",
}


def _hud_module() -> ModuleType | None:
    """aura-hud's ``hud`` module: the loaded one when Hermes has it, else by path.

    The loaded copy first, because ``STORE`` is per process: a second copy loaded
    by path has its own panel table, so a panel shown through it routes to the
    client but never appears in ``hud_list`` and is not dismissed by ``*``. Hermes
    names the plugin ``hermes_plugins.aura_hud`` (the test loader ``aura_hud``),
    so the match is on the module name's tail. None when the plugin is absent.
    """
    for name, mod in list(sys.modules.items()):
        if name.endswith("aura_hud.hud") and hasattr(mod, "STORE"):
            return mod
    cached = sys.modules.get(_FALLBACK_NAME)
    if cached is not None:
        return cached
    if not _HUD_SOURCE.is_file():
        return None
    spec = importlib.util.spec_from_file_location(_FALLBACK_NAME, _HUD_SOURCE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_FALLBACK_NAME] = module
    spec.loader.exec_module(module)
    return module


def _deliver(session_id: str | None, out: dict[str, Any], *,
             panel_id: str | None = None) -> dict[str, Any]:
    """Show the published artifact on the session's HUD; ``delivered`` plus why not.

    ``out`` is the adapter's publish response. The panel carries its
    ``signed_url``, never the bare ``url``: a media element cannot send the
    bearer, so the bare one is a 401 on every surface (M-D2). A ``panel_id``
    replaces that panel rather than adding one. Never raises --
    a screen that cannot be reached is not a reason to report the publish failed.
    """
    signed = str(out.get("signed_url") or "")
    if not signed:
        return {"delivered": False,
                "reason": "the store returned no signed_url, and a panel cannot authenticate the bare one"}
    try:
        hud = _hud_module()
    except Exception as exc:  # noqa: BLE001 - a broken sibling plugin must not fail the publish
        _LOGGER.exception("aura-hud failed to load for artifact delivery")
        return {"delivered": False, "reason": f"aura-hud failed to load: {exc}"}
    if hud is None:
        return {"delivered": False, "reason": "aura-hud is not installed beside this plugin"}
    title = str(out.get("title") or out.get("id") or "artifact")
    mime = str(out.get("mime") or "")
    kind = _KIND_FOR.get(surfaces.capability_for(mime), "notice")
    if kind not in getattr(hud, "KINDS", ()):
        kind = "notice"
    if kind == "image":
        payload: dict[str, Any] = {"url": signed, "alt": title}
    elif kind == "notice":
        payload = {"level": "info", "text": f"{title} ({mime or 'file'}) is ready: {signed}",
                   "url": signed, "mime": mime, "artifact_id": out.get("id")}
    else:
        # The media kinds' shape from the contract's examples: url + mime, the
        # title, and the provenance the store recorded (tool, source_url).
        payload = {"url": signed, "mime": mime, "title": title,
                   "provenance": out.get("provenance") or {}}
    try:
        record, delivered = hud.STORE.show(
            hud._state_key(session_id), kind, title, payload, panel_id=panel_id, origin="system",
        )
    except Exception as exc:  # noqa: BLE001 - a rejected panel is reported, not raised
        return {"delivered": False, "reason": f"the HUD refused the panel: {exc}"}
    if not delivered:
        return {"delivered": False, "panel_id": record["id"],
                "reason": "no HUD client is on this session"}
    return {"delivered": True, "panel_id": record["id"]}
