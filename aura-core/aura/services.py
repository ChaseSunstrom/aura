"""Service registry — every action in Jarvis is a `domain.service` call."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from .bus import Context, EventBus
from .const import EVENT_CALL_SERVICE, EVENT_SERVICE_REGISTERED
from .paired_access import REFUSED_MESSAGE, PairedRefused

_LOGGER = logging.getLogger(__name__)


class ServiceNotFound(Exception):
    def __init__(self, domain: str, service: str) -> None:
        super().__init__(f"service not found: {domain}.{service}")
        self.domain = domain
        self.service = service


@dataclass(slots=True)
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any] = field(default_factory=dict)
    context: Context = field(default_factory=Context)
    return_response: bool = False

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


ServiceHandler = Callable[[ServiceCall], Coroutine[Any, Any, Any] | Any]


@dataclass(slots=True)
class Service:
    domain: str
    service: str
    handler: ServiceHandler
    description: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    supports_response: bool = False
    #: Refused to a caller whose token the pairing flow minted, on the generic service
    #: door (`api.common.async_call_service`). In-house callers (automations, scenes)
    #: reach services through `Jarvis.async_call_service` and never meet this (T-1377).
    owner_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "fields": self.fields,
            "supports_response": self.supports_response,
        }


class ServiceRegistry:
    def __init__(
        self, bus: EventBus, is_paired: Callable[[str | None], bool] | None = None
    ) -> None:
        self._services: dict[str, dict[str, Service]] = {}
        self._bus = bus
        #: Whether a context's user_id is a paired device's token (wired by `Jarvis`).
        self._is_paired = is_paired

    def register(
        self,
        domain: str,
        service: str,
        handler: ServiceHandler,
        description: str = "",
        fields: dict[str, Any] | None = None,
        supports_response: bool = False,
        owner_only: bool = False,
    ) -> None:
        domain, service = domain.lower(), service.lower()
        self._services.setdefault(domain, {})[service] = Service(
            domain, service, handler, description, fields or {}, supports_response, owner_only
        )
        self._bus.fire(EVENT_SERVICE_REGISTERED, {"domain": domain, "service": service})

    def remove(self, domain: str, service: str) -> None:
        self._services.get(domain.lower(), {}).pop(service.lower(), None)

    def has_service(self, domain: str, service: str) -> bool:
        return service.lower() in self._services.get(domain.lower(), {})

    @property
    def services(self) -> dict[str, dict[str, Service]]:
        return self._services

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {
            domain: {name: svc.as_dict() for name, svc in svcs.items()}
            for domain, svcs in self._services.items()
        }

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        blocking: bool = True,
        context: Context | None = None,
        return_response: bool = False,
    ) -> Any:
        domain, service = domain.lower(), service.lower()
        svc = self._services.get(domain, {}).get(service)
        if svc is None:
            raise ServiceNotFound(domain, service)

        # Second layer behind the two auth doors: a paired token's identity carried into an
        # internal call (an automation, a scene, a tool it got to run) still cannot reach an
        # owner-only service. Not covered: a call with no identity, which is how the house's own
        # automations and the house-token relays arrive, and the gated lists (the assistant's
        # own approvals own those).
        if (
            svc.owner_only
            and context is not None
            and context.user_id
            and self._is_paired is not None
            and self._is_paired(context.user_id)
        ):
            raise PairedRefused(REFUSED_MESSAGE)

        ctx = context or Context()
        call = ServiceCall(domain, service, dict(service_data or {}), ctx, return_response)
        self._bus.fire(
            EVENT_CALL_SERVICE,
            {"domain": domain, "service": service, "service_data": call.data},
            ctx,
        )

        async def _run() -> Any:
            result = svc.handler(call)
            if asyncio.iscoroutine(result):
                return await result
            return result

        if not blocking:
            task = asyncio.create_task(_guard(_run(), domain, service))
            return task
        return await _run()


async def _guard(coro: Coroutine[Any, Any, Any], domain: str, service: str) -> Any:
    try:
        return await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception("Error executing %s.%s", domain, service)
        return None
