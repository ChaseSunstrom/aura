"""What the stack's Home Assistant discovered, and finishing a confirm-only one (T-1543).

HA finds devices on the network itself (zeroconf, SSDP, DHCP, Bluetooth) and holds each as a
config flow in progress until someone presses Submit on HA's own page, which the owner never
opens (T-1523). :meth:`HaFlows.discovered` lists those flows over the websocket
(``config_entries/flow/progress``, the list HA's page shows); :meth:`HaFlows.confirm` reads one
flow's current step over REST and, only when it is a form with no fields, submits it empty, the
press of Submit.

Not covered: a step that wants input (a pairing code, an API key, a password): it is named and
left for the owner, because the input is theirs to give; a flow someone started on HA's page
(``source: user``), which is theirs to finish; a flow's later steps (a confirm that leads to a
form answers that form's fields). The discovered names come from devices on the network and are
the caller's to treat as untrusted text.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

import httpx

from .setup import TIMEOUT, Connect, ws_auth, ws_connect, ws_url

#: HA's flow ids are uuid hex; anything else never reaches a URL path.
_FLOW_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
#: An HA integration domain, as the approval card names it and the owner's yes is keyed to.
_INTEGRATION = re.compile(r"[a-z0-9_]{1,64}")


class HaFlowRefused(RuntimeError):
    """Said to the caller as it is: why the flow was not listed or not confirmed."""


class HaFlows:
    def __init__(self, url: str, *, token: Callable[[], str],
                 transport: httpx.AsyncBaseTransport | None = None, connect: Connect | None = None) -> None:
        self.url = url.strip().rstrip("/")
        self.token = token
        self.transport = transport
        self.connect = connect or ws_connect

    def _token(self) -> str:
        token = self.token() or ""
        if not token:
            raise HaFlowRefused("AURA holds no token Home Assistant accepts yet (home_assistant.status says why)")
        return token

    async def _progress(self) -> list[dict[str, Any]]:
        token = self._token()
        try:
            ws = await self.connect(ws_url(self.url))
            try:
                await ws_auth(ws, token)
                await ws.send(json.dumps({"id": 1, "type": "config_entries/flow/progress"}))
                while True:
                    answer = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
                    if answer.get("id") == 1 and answer.get("type") == "result":
                        break
            finally:
                await ws.close()
        except HaFlowRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - any socket failure is "it did not answer"
            raise HaFlowRefused(f"Home Assistant did not answer ({type(exc).__name__})") from exc
        result = answer.get("result") if answer.get("success") else None
        return [f for f in result or [] if isinstance(f, dict)]

    async def discovered(self) -> list[dict[str, Any]]:
        """The flows HA started itself, oldest first: id, integration, how it was found, its name."""
        out = []
        for flow in await self._progress():
            raw = flow.get("context")
            context: dict[str, Any] = raw if isinstance(raw, dict) else {}
            source = str(context.get("source") or "")
            if source in ("", "user", "reauth", "reconfigure"):
                continue  # someone started it on HA's page, or HA is asking for a login again
            placeholders = context.get("title_placeholders")
            name = placeholders.get("name") if isinstance(placeholders, dict) else None
            out.append({"flow_id": str(flow.get("flow_id") or ""), "integration": str(flow.get("handler") or ""),
                        "found_by": source, "name": str(name or flow.get("handler") or "")[:120],
                        "step": str(flow.get("step_id") or "")})
        return out

    async def confirm(self, flow_id: str, integration: str) -> dict[str, Any]:
        """Submit a discovered flow whose current step is a bare confirmation; its new integration.

        ``integration`` is the one the approval card named and the owner's yes is keyed to: a
        flow of another integration is refused, so a yes for a Chromecast never adds a bridge.
        """
        flow_id, integration = str(flow_id or ""), str(integration or "")
        if not _FLOW_ID.fullmatch(flow_id):
            raise HaFlowRefused(f"{flow_id[:70]!r} is not a flow id; home_assistant.discovered lists them")
        if not _INTEGRATION.fullmatch(integration):
            raise HaFlowRefused(f"{integration[:70]!r} is not an integration name; "
                                "home_assistant.discovered lists each flow's")
        found = {f["flow_id"]: f for f in await self.discovered()}
        if flow_id not in found:
            raise HaFlowRefused(f"{flow_id} is not one Home Assistant discovered and is still waiting on; "
                                "home_assistant.discovered lists them")
        if found[flow_id]["integration"] != integration:
            raise HaFlowRefused(f"flow {flow_id} is {found[flow_id]['integration']}, not {integration}")
        path = f"/api/config/config_entries/flow/{flow_id}"
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            async with httpx.AsyncClient(base_url=self.url, timeout=TIMEOUT, transport=self.transport,
                                         follow_redirects=False) as client:
                step = (await client.get(path, headers=headers)).raise_for_status().json()
                fields = [str(f.get("name")) for f in step.get("data_schema") or [] if isinstance(f, dict)]
                if step.get("type") != "form" or fields:
                    wants = ", ".join(fields) or str(step.get("type"))
                    raise HaFlowRefused(f"{found[flow_id]['integration']} wants {wants} on its "
                                        f"{step.get('step_id')} step; that is the owner's to give on "
                                        "Home Assistant's page, not a confirmation")
                result = (await client.post(path, headers=headers, json={})).raise_for_status().json()
        except HaFlowRefused:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise HaFlowRefused(f"Home Assistant did not answer ({type(exc).__name__})") from exc
        if result.get("type") == "create_entry":
            return {"ok": True, "integration": found[flow_id]["integration"],
                    "title": str(result.get("title") or found[flow_id]["name"])[:120]}
        if result.get("type") == "abort":
            raise HaFlowRefused(f"Home Assistant stopped the flow: {result.get('reason')}")
        raise HaFlowRefused(f"the flow went on to a {result.get('type')} step "
                            f"({result.get('step_id')}); finish it on Home Assistant's page")
