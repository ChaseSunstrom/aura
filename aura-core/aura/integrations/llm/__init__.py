"""`llm` integration — the model client and the tool registry the house keeps.

Configuration::

    llm:
      enabled: false           # the shipped default: no client, no registry
      url: http://127.0.0.1:11434
      model: qwen3:8b
      call_timeout: 300        # seconds for one whole model call, stall or not
      approval_ttl: 300        # seconds a held ACTION waits for a yes
      question_ttl: 1800       # seconds a QUESTION (ask_user) waits for its answer
      expose:
        domains: [light, switch, cover, climate, media_player]
        entities: [sensor.back_door_battery]
        areas: [kitchen]
        exclude_entities: [light.awkward_lamp]
      user_context:
        presence: person.chris
        driving: binary_sensor.chris_driving
        awake: binary_sensor.chris_awake
        active_device: sensor.chris_active_device
      tools_dir: tools                 # *.tool.yaml manifests, under the config dir
      tools:                           # or declare them inline
        - name: paperless_search
          description: "Search Paperless-ngx documents by query text"
          tier: 1
          service:
            method: GET
            url: "http://paperless.lan/api/documents/?query={{ query }}"
            headers: {Authorization: "Token abc123"}
            fields:
              query: {description: "search text", required: true}

A top-level ``tools:`` block in configuration.yaml is picked up too.

Services registered:

* ``llm.approve`` (request_id, approved) -> executes or discards a gated action
* ``llm.pending_requests`` -> what is waiting on approval
* ``llm.list_models`` -> what the model server serves

There is no conversation agent here: Hermes is the brain (ADR-0012), and
``conversation.process`` is never a registered service, so the REST and
WebSocket entry points answer 501 ``agent_not_found``. What ``enabled: true``
gives the house is the model client, in ``jarvis.data["llm"]`` for the coding
job, research and memory's embeddings to borrow, and the tool registry with its
approval gate, in ``jarvis.data["llm_tools"]`` for the REST API and the
console's authored tools.

Tests inject a fake Ollama with ``jarvis.data["llm_transport"] =
httpx.MockTransport(handler)`` (or a ready client in ``jarvis.data["llm_client"]``)
before calling :func:`async_setup`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

from typing import TYPE_CHECKING, Any

import httpx

from ...llm.ollama import DEFAULT_MODEL, DEFAULT_TIMEOUT, DEFAULT_URL, OllamaClient
from ...llm.openai_compat import OpenAICompatClient
from ...authored_tools import get_authored_tools
from ...tools import (
    DEFAULT_APPROVAL_TTL,
    DEFAULT_QUESTION_TTL,
    EVENT_APPROVAL_REQUIRED,
    Exposure,
    ToolRegistry,
    build_yaml_tools,
    load_tool_manifests,
    register_builtin_tools,
)
from ...services import ServiceCall

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Where `companion.ask` lives. Imported by name rather than from the module so
#: a build without the companion integration simply never finds the service.
COMPANION_DOMAIN = "companion"

#: Prepended to a question raised by a turn that had already read somebody
#: else's words. The console has a field for this; a phone notification does
#: not, so it goes in the sentence — see `_ask_on_a_device`.
UNTRUSTED_PREFIX = "[from an outside source]"

DOMAIN = "llm"
DEPENDENCIES = ["domains"]

DATA_TRANSPORT = "llm_transport"
DATA_CLIENT = "llm_client"
DATA_TOOLS = "llm_tools"


#: Strings a caller may send for "yes, run it". Everything else is a refusal.
TRUTHY = frozenset({"true", "yes", "y", "1", "on", "approve", "approved", "ok"})


def parse_approved(value: Any) -> bool:
    """Interpret the ``approved`` flag, failing *closed*.

    ``bool("false")`` is ``True``, so a plain cast turns a refusal that arrived
    over a form post, a query string, YAML (``approved: "false"``) or an MQTT
    payload into an execution of the very action the gate was holding. Only an
    explicit affirmative counts; anything unrecognised denies.

    Omitting the field entirely still means approve — ``llm.approve`` is the
    service you call to say yes, and the REST/websocket layers rely on that.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY
    return False


def _as_dict(config: Any) -> dict[str, Any]:
    if isinstance(config, dict):
        return config
    if isinstance(config, list) and config and isinstance(config[0], dict):
        return config[0]
    return {}


def create_http_client(jarvis: "Jarvis", timeout: float) -> httpx.AsyncClient:
    """Shared AsyncClient for both Ollama and YAML tools, honouring injection."""
    existing = jarvis.data.get(DATA_CLIENT)
    if isinstance(existing, httpx.AsyncClient):
        return existing
    transport = jarvis.data.get(DATA_TRANSPORT)
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    )
    jarvis.data[DATA_CLIENT] = client
    return client


#: `llm: backend:` values, and what each is for.
#:
#: `ollama` is the native `/api/chat` wire — NDJSON, `keep_alive`, a separate
#: `thinking` field, `/api/tags` for model management.
#:
#: `openai` is `/v1/chat/completions`, which **vLLM**, llama.cpp's server, LM
#: Studio, TGI, SGLang and Ollama itself all serve. It is what makes the
#: inference server a deployment decision instead of an architectural one, and
#: it is the only one of the two that can do guided decoding or embeddings.
BACKENDS = ("ollama", "openai")


def _scalar(value: Any) -> str:
    """A YAML scalar with the `!env_var NAME ""` artefact stripped.

    `config.py` keeps an `!env_var` default token verbatim, so an unset variable
    written with an empty-string default arrives here as the two *characters*
    `""` rather than as an empty string. Passed through, that becomes
    `Authorization: Bearer ""` — a 401 from the proxy with a config file that
    looks entirely correct.
    """
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _header_map(raw: Any) -> dict[str, str] | None:
    """`llm: headers:` — whatever else the proxy in front wants.

    LiteLLM routes on `x-litellm-tags`, some deployments want a tenant or an
    organisation id, and none of that is worth a config key each. Values go
    through `_scalar` for the same env-var reason the key does.
    """
    if not isinstance(raw, dict):
        return None
    out = {str(key): _scalar(value) for key, value in raw.items() if _scalar(value)}
    return out or None


def _detect_backend(url: str) -> str:
    """Which wire a url is asking for, when nobody said.

    A url with `/v1` in it is unambiguous — Ollama's native API has no such
    path, and every OpenAI-compatible server serves exactly that. Anything else
    defaults to `ollama`, so an existing install that never heard of this
    setting keeps the behaviour it had.

    Matched anywhere in the path and case-insensitively, to agree with
    `normalise_base_url`: that function accepted `/v1` anywhere while this one
    looked only at the final segment, so `http://litellm:4000/v1/chat/completions`
    was normalised as OpenAI and then dispatched to Ollama's `/api/chat`.

    A bare `http://host:4000` still reads as Ollama. That is deliberate and is
    the only safe default — every existing install writes its Ollama url that
    way — so a LiteLLM on a bare port needs either `/v1` on the url or an
    explicit `backend: openai`. Both are in `docs/openai-compat.md`.
    """
    text = str(url or "").rstrip("/").lower()
    tail = text.rsplit("/", 1)[-1]
    if tail in ("v1", "openai") or "/v1/" in f"{text}/":
        return "openai"
    return "ollama"


def _build_model_client(
    options: dict[str, Any],
    url: str,
    model: str,
    timeout: float,
    client: Any,
) -> Any:
    """The chat client, on whichever wire the deployment asked for.

    Both classes present the same surface — `chat`, `list_models`,
    `is_available`, `aclose` — so nothing that borrows the client from
    `jarvis.data["llm"]` learns which it got. That is the point: the wire is a
    deployment decision, not an architectural one.
    """
    backend = str(options.get("backend") or "").strip().lower()
    if not backend:
        backend = _detect_backend(url)
    elif backend not in BACKENDS:
        _LOGGER.warning(
            "Unknown llm backend %r; falling back to %r. Known: %s",
            backend,
            _detect_backend(url),
            ", ".join(BACKENDS),
        )
        backend = _detect_backend(url)

    if backend == "openai":
        api_key = _scalar(options.get("api_key"))
        _LOGGER.info(
            "LLM backend: OpenAI-compatible at %s (api key %s)",
            url,
            "set" if api_key else "not set",
        )
        return OpenAICompatClient(
            url=url,
            model=model,
            timeout=timeout,
            client=client,
            api_key=api_key or None,
            headers=_header_map(options.get("headers")),
            label=_scalar(options.get("backend_name")) or "the model server",
        )

    _LOGGER.info("LLM backend: Ollama at %s", url)
    return OllamaClient(
        url=url,
        model=model,
        timeout=timeout,
        client=client,
        keep_alive=options.get("keep_alive"),
    )


#: Public alias. `integrations/vision` builds its own model client and had no
#: way to honour `backend:`/`api_key:` without duplicating the logic above; it
#: infers the wire from a url with the same rule, so the two blocks cannot read
#: one `LLM_URL` two ways.
detect_backend = _detect_backend



# ---------------------------------------------------------------------------
# "100% local", checked rather than trusted
# ---------------------------------------------------------------------------

#: Networks a model server may live on: this machine, the LAN, a link-local
#: address, or the CGNAT range Tailscale and similar overlays use. Everything
#: else is somebody else's computer.
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


#: What a look at a model server's address can say.
LOCAL = "local"
REFUSED = "refused"
UNRESOLVED = "unresolved"

#: Names that are this machine or this LAN by definition; nothing is looked up for them.
_ALWAYS_LOCAL = ("localhost", "host.docker.internal")


def _local_by_name(host: str) -> bool:
    return host in _ALWAYS_LOCAL or host.endswith(".local")


def local_verdict(url: str) -> tuple[str, str]:
    """Where this URL points: `(LOCAL | REFUSED | UNRESOLVED, why)`.

    `is_local_url` with its third answer kept apart, so a caller can let an
    unresolved name through at startup and still refuse to send it anything.
    """
    try:
        host = urlparse(url).hostname or ""
    except ValueError as err:
        return REFUSED, f"{url!r} is not a URL: {err}"
    if not host:
        return REFUSED, f"{url!r} names no host"
    return _host_verdict(host)


def _host_verdict(host: str) -> tuple[str, str]:
    """`local_verdict` for a host already taken out of its URL."""
    if _local_by_name(host):
        return LOCAL, ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        # Unresolvable today, and possibly a container that is still starting.
        return UNRESOLVED, (
            f"{host} does not resolve yet, so it cannot be shown to be on this network. "
            "Nothing is sent to it until it does; `local_only: false` turns the check off."
        )
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover
            continue
        if not any(parsed in network for network in _PRIVATE_NETWORKS):
            return REFUSED, (
                f"{host} resolves to {address}, which is a public address. "
                "Jarvis runs its model locally by design; point `llm: url:` at a "
                "server you run, or set `llm: local_only: false` if you really "
                "mean to send every conversation off this network."
            )
    return LOCAL, ""


def is_local_url(url: str) -> tuple[bool, str]:
    """Does this URL point at a machine the operator plausibly owns?

    Returns `(ok, why not)`. A name that does not resolve is NOT treated as a
    failure: a compose service that has not started yet is the ordinary case on
    a first boot, and refusing to start because DNS was not ready would be a
    worse bug than the one this prevents. That makes this the startup answer
    only: `LocalOnlyGate` is what keeps a request from going to such a name.
    """
    verdict, why = local_verdict(url)
    return (False, why) if verdict == REFUSED else (True, "")


class LocalOnlyRefused(httpx.TransportError):
    """A request the local-only guard stopped before it was sent.

    A transport error on purpose: every caller of the model server already
    handles one, and to them a refused request is a server they could not reach.
    """


class LocalOnlyGate:
    """The guard's second look, before a request leaves for the model server.

    Startup lets a name that does not resolve through. Without this, that was
    a way round the guard: a public host name and a moment without DNS at boot,
    and every conversation went to it afterwards (T-1461). Installed as a
    request hook on the model's client, it holds every request to the model
    server's host until the name has resolved to a local address, refuses it
    when the name is public, and looks again once a minute. A request to a
    name that still does not resolve is refused too: it could not have
    succeeded, and letting it go would leave the client's own lookup, a moment
    later, free to find a public address.

    The model clients follow redirects, so the server's own answer could send
    the same request, body and all, to another host, and the request hook only
    looks at the model server's (T-1466). `on_response` judges the host a
    redirect names before the client follows it: the same host is the model
    server still, another host has to be shown local by the same rule, and a
    host a redirect was let through to is held to it for its own redirects.

    What this does not cover: the address the client connects to is resolved
    again by the client, so a record that changes between this look and that
    one (rebinding) gets one minute at most; a redirect answered by a host no
    gate is about (the same client also carries the house's own tools to the
    internet) is not looked at; and with `local_only: false` there is no gate
    at all.
    """

    #: How long an answer stands before the name is looked up again, in seconds.
    TTL = 60.0
    #: How many other hosts one model server's redirects may lead to. A real
    #: setup has one (a proxy's canonical name); past this the answer is no.
    MAX_HOPS = 8

    def __init__(self, url: str, *, what: str = "llm") -> None:
        self.url = url
        self.what = what
        # The host as the HTTP client names it, from the client's own parser: the
        # hook finds the model server's requests by comparing with
        # `request.url.host`, and urllib spells some hosts differently (a punycode
        # label stays punycode there and is decoded here), so a host taken from
        # urllib never matched and the gate was silently off for it (T-1463). A
        # URL this parser rejects cannot be requested by the client either.
        try:
            host = httpx.URL(url).host or ""
        except (httpx.InvalidURL, TypeError, ValueError):
            host = ""
        self.host = host.lower()
        self._verdict = ""
        self._why = ""
        self._checked = 0.0
        # The other hosts a redirect was let through to, and when each was last
        # shown local: {host: monotonic time}. Only local ones are kept.
        self._hops: dict[str, float] = {}

    @property
    def watches(self) -> bool:
        """False for an address or a name local by definition: there is nothing to look up."""
        if not self.host or _local_by_name(self.host):
            return False
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            return True
        return False

    def seed(self, verdict: str, why: str = "") -> None:
        """Take startup's answer, so the first request does not repeat a lookup just made."""
        if verdict in (LOCAL, REFUSED):
            self._verdict, self._why, self._checked = verdict, why, time.monotonic()

    async def verdict(self) -> tuple[str, str]:
        """The current answer for the model server, looked up again when it is stale."""
        if not self.watches:
            # An address or a name local by definition: there is nothing to look
            # up again, but an address is still judged, once. The llm and vision
            # setups refuse a public address before a gate exists and seed the
            # answer; a caller with no setup of its own (the reranker, T-1462)
            # has only this, and "an address needs no look" would have let a
            # public one through.
            if not self._verdict:
                if self.host:
                    self._verdict, self._why = _host_verdict(self.host)
                else:
                    self._verdict, self._why = REFUSED, f"{self.url!r} names no host"
                if self._verdict == REFUSED:
                    _LOGGER.error("%s: refusing requests to %s. %s", self.what, self.host or self.url, self._why)
            return self._verdict, self._why
        now = time.monotonic()
        if self._verdict and now - self._checked < self.TTL:
            return self._verdict, self._why
        # The same host the hook compares, so the answer is about the request it guards.
        verdict, why = await asyncio.to_thread(_host_verdict, self.host)
        if verdict == UNRESOLVED:
            # A name already shown to be local keeps that answer through a moment
            # without DNS: a blip must not cut a working local model.
            return (LOCAL, "") if self._verdict == LOCAL else (verdict, why)
        if verdict == REFUSED and self._verdict != REFUSED:
            _LOGGER.error("%s: refusing requests to the model server. %s", self.what, why)
        self._verdict, self._why, self._checked = verdict, why, now
        return verdict, why

    async def refusal(self) -> str:
        """Why nothing may be sent to this host now, or "" when it is shown to be local.

        For a sender that is not an httpx client this gate can hook (the
        reranker builds a client per request): it asks before it sends.
        """
        verdict, why = await self.verdict()
        if verdict == LOCAL:
            return ""
        return why or f"{self.host or self.url!r} is not shown to be on this network"

    async def __call__(self, request: httpx.Request) -> None:
        if (request.url.host or "").lower() != self.host:
            return
        verdict, why = await self.verdict()
        if verdict != LOCAL:
            raise LocalOnlyRefused(why, request=request)

    async def _hop_verdict(self, host: str) -> tuple[str, str]:
        """The answer for a host a redirect names, kept for `TTL` while it is local."""
        now = time.monotonic()
        seen = self._hops.get(host)
        if seen is not None and now - seen < self.TTL:
            return LOCAL, ""
        if seen is None and len(self._hops) >= self.MAX_HOPS:
            return REFUSED, f"its redirects have led to {self.MAX_HOPS} other hosts already"
        verdict, why = await asyncio.to_thread(_host_verdict, host)
        if verdict == UNRESOLVED and seen is not None:
            # As for the model server itself: a moment without DNS does not
            # undo an answer already given.
            return LOCAL, ""
        if verdict == LOCAL:
            self._hops[host] = now
        else:
            self._hops.pop(host, None)
        return verdict, why

    async def on_response(self, response: httpx.Response) -> None:
        """Refuse a redirect from the model server to a host that is not shown local.

        The client runs this on every answer, before it builds the request a
        redirect asks for, so a refusal here means that request is never made.
        It looks only at answers from the model server and from the hosts its
        redirects were let through to; anybody else's redirect on the same
        client is not this gate's business.
        """
        if not response.has_redirect_location:
            return
        request = response.request
        origin = (request.url.host or "").lower()
        if origin != self.host and origin not in self._hops:
            return
        try:
            target = (request.url.join(response.headers["location"]).host or "").lower()
        except (httpx.InvalidURL, TypeError, ValueError):
            target = ""
        if target == self.host:
            # The model server itself: its requests are the request hook's.
            return
        if target:
            verdict, why = await self._hop_verdict(target)
        else:
            verdict, why = REFUSED, "the redirect names no host"
        if verdict != LOCAL:
            _LOGGER.error(
                "%s: %s answered with a redirect to %s, which is not followed. %s",
                self.what, origin, target or "nowhere", why,
            )
            raise LocalOnlyRefused(
                f"{origin} answered with a redirect to {target or 'no host'}, "
                f"which is not followed: {why}",
                request=request,
            )

    def install(self, client: httpx.AsyncClient) -> None:
        """Hook this gate onto `client`, once per host.

        The redirect check goes on for every host: a server that needs no
        lookup can still answer with a redirect (T-1466). The request hook is
        skipped when there is nothing to watch, which is a name local by
        definition or an address startup already judged local. An address
        nobody has judged is hooked: the embedder's gate has no startup check
        behind it, and skipping every address let a public one through
        (T-1464). It is judged on the first request, once, with no lookup.
        """
        if not self.host:
            return
        hooks = client.event_hooks
        requests, responses = list(hooks["request"]), list(hooks["response"])
        # A response hook is the gate's bound method, so its gate is `__self__`.
        owners = [getattr(hook, "__self__", None) for hook in responses]
        if not any(isinstance(gate, LocalOnlyGate) and gate.host == self.host for gate in owners):
            responses.append(self.on_response)
        judged_local = not self.watches and self._verdict == LOCAL
        nothing_to_watch = _local_by_name(self.host) or judged_local
        if not nothing_to_watch and not any(
            isinstance(hook, LocalOnlyGate) and hook.host == self.host for hook in requests
        ):
            requests.append(self)
        client.event_hooks = {"request": requests, "response": responses}


#: Where a house keeps the gates of the other services it sends private text to, by host.
DATA_GATES = "local_only_gates"


def local_only_gate(jarvis: Any, url: str, *, what: str) -> LocalOnlyGate | None:
    """The house's local-only gate for another service's url, or None when the rule is off.

    The reranker is sent memory texts, the user's query and research snippets
    to a url of its own, and nothing held that url to the promise `llm:
    local_only` makes (T-1462). There is one rule for the house, so this reads
    the `llm:` block's setting (on unless it says false), whether or not the
    house runs a chat model of its own. One gate per host for the life of the
    process: research builds a reranker per run, and an answer has to outlive
    the run for the minute's TTL and the DNS-blip tolerance to mean anything.

    What this does not cover: a sender that never asks the gate. Nothing is
    hooked here: the caller asks `refusal()` before it sends (the reranker,
    which builds a client per request) or installs the gate on the client it
    sends with (memory's embedder, T-1464).
    """
    config = getattr(jarvis, "config", None)
    llm = config.get("llm") if isinstance(config, dict) else None
    if isinstance(llm, dict) and not llm.get("local_only", True):
        return None
    gate = LocalOnlyGate(url, what=what)
    gates = jarvis.data.setdefault(DATA_GATES, {})
    return gates.setdefault(gate.host, gate)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = _as_dict(config)

    # House-service mode (the Hermes rebuild): `llm: {enabled: false}`, the
    # shipped default, leaves no model client and no tool registry behind —
    # Hermes is the brain and every other integration runs as before. There is
    # no conversation agent to switch on either way: `conversation.process` is
    # never a registered service, so the REST/WebSocket entry points answer 501
    # `agent_not_found` (ADR-0012 and its addendum).
    if not _truthy(options.get("enabled", True)):
        _LOGGER.info("LLM client disabled by config (llm.enabled: false) — this jarvis-core is a house service only")
        return True

    url = str(options.get("url") or options.get("host") or DEFAULT_URL)
    model = str(options.get("model") or DEFAULT_MODEL)

    # The promise the whole project rests on, checked rather than trusted.
    # Nothing else in the code stops `url:` naming a cloud endpoint, and a
    # promise nothing verifies is a hope.
    gate: LocalOnlyGate | None = None
    if options.get("local_only", True):
        verdict, why = local_verdict(url)
        if verdict == REFUSED:
            _LOGGER.error("llm: refusing to use a non-local model server. %s", why)
            return False
        # An unresolved name comes up (a compose service may still be starting),
        # and the gate is what keeps a request from going to it until it is
        # shown to be local.
        gate = LocalOnlyGate(url)
        gate.seed(verdict, why)
    timeout = float(options.get("timeout") or DEFAULT_TIMEOUT)
    # The absolute bound on ONE call, as distinct from `timeout`, which is
    # httpx's per-read one and is reset by every keepalive byte. See
    # `OllamaClient.call_timeout`: without this a stalled server hangs a turn
    # for ever, which is what it did.
    call_timeout = float(options.get("call_timeout") or 0.0)
    approval_ttl = float(options.get("approval_ttl") or DEFAULT_APPROVAL_TTL)
    # Its own clock, never derived from `approval_ttl`: the two are different
    # waits for different reasons (see `DEFAULT_QUESTION_TTL`), and an
    # operator shortening approvals to a minute should not have every
    # question lapse in a minute too.
    question_ttl = float(options.get("question_ttl") or DEFAULT_QUESTION_TTL)

    client = create_http_client(jarvis, timeout)
    if gate is not None:
        gate.install(client)
    ollama = _build_model_client(options, url, model, timeout, client)
    if call_timeout:
        # Never below the per-read timeout: raising `timeout` must not quietly
        # lower the real bound.
        ollama.call_timeout = max(call_timeout, timeout)

    exposure = Exposure.from_config(options.get("expose"))
    registry = ToolRegistry(
        jarvis, exposure=exposure, approval_ttl=approval_ttl, question_ttl=question_ttl
    )
    register_builtin_tools(registry, _as_dict(options.get("user_context")))

    specs: list[Any] = []
    if options.get("tools_dir"):
        directory = jarvis.config_dir / str(options["tools_dir"])
        specs.extend(load_tool_manifests(directory))
    for source in (options.get("tools"), (jarvis.config or {}).get("tools")):
        if isinstance(source, list):
            specs.extend(source)
        elif isinstance(source, dict):
            specs.extend(
                {**value, "name": value.get("name", key)}
                for key, value in source.items()
                if isinstance(value, dict)
            )
    if specs:
        built = build_yaml_tools(registry, specs, client_factory=lambda: client)
        _LOGGER.info("Registered %d YAML-defined tool(s)", len(built))

    # Console-created tools, built last so `validate`'s refusal to shadow an
    # existing name is enforced by the same ordering at boot as at create time:
    # everything else is already registered by the time these are added.
    authored = get_authored_tools(jarvis)
    authored_specs = await authored.async_load()
    if authored_specs:
        made = build_yaml_tools(registry, authored_specs, client_factory=lambda: client)
        _LOGGER.info("Registered %d console-defined tool(s)", len(made))

    # The client itself, not a wrapper around it. The coding job, research and
    # memory's embeddings borrow `chat`/`embed` and `model` from here, and the
    # settings page reads `known_models` off it; a holder carrying the retired
    # agent's attribute names would be that loop's ghost.
    jarvis.data[DOMAIN] = ollama
    jarvis.data[DATA_TOOLS] = registry

    _register_services(jarvis, ollama, registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    async def _shutdown() -> None:
        await ollama.aclose()
        if jarvis.data.get(DATA_CLIENT) is client and not client.is_closed:
            await client.aclose()

    jarvis.register_shutdown(_shutdown)
    _LOGGER.info(
        "LLM client ready: model=%s url=%s tools=%d", model, url, len(registry.tools)
    )
    # A reachability probe, in the background so a model server that is still
    # starting does not hold up boot. It does two jobs: it fills
    # `client.known_models`, which the settings page reads to offer a model
    # dropdown and which was empty on every install because nothing ever called
    # `list_models`; and it puts one clear line in the log when the model
    # server cannot be reached, which is the single most common way this
    # install is misconfigured and previously produced no output at all until
    # somebody spoke to it.
    jarvis.async_create_task(_probe_model_server(ollama, url))
    return True


async def _probe_model_server(client: Any, url: str) -> None:
    """Warm the model list, and say plainly what is wrong before anybody speaks.

    Two failures, both of which used to be silent until the first turn:

    * the server is not there at all;
    * the server is there and does not have the model this install is set to.
      That is not hypothetical — putting the LiteLLM gateway in front of the
      model server (M40) renamed every model, and a `llm.model` an operator had
      chosen in the console went on pointing at the OLD name. Each turn then
      came back as a 400 from the proxy with the model name in it, which is a
      log line that means nothing to anybody who did not build the gateway.
    """
    try:
        models = await client.list_models()
    except Exception as err:
        _LOGGER.warning(
            "Could not reach the model server at %s: %s. Jarvis will start, but "
            "every turn will fail until it is reachable.",
            url,
            err,
        )
        return
    _LOGGER.info("Model server at %s is serving %d model(s)", url, len(models))

    # Only when the server actually answered with a list: an endpoint that
    # serves no `/models` at all is not evidence that the model is missing.
    wanted = str(getattr(client, "model", "") or "")
    if wanted and models and wanted not in models:
        _LOGGER.error(
            "The model this install is set to (%r) is not one %s serves. It has: %s. "
            "Every turn will fail until this is changed — in the console under "
            "Settings, or by clearing the stored `llm.model` override so the "
            "LLM_MODEL environment variable is used again.",
            wanted,
            url,
            ", ".join(sorted(models)[:12]) or "nothing",
        )


def _bridge_questions_to_the_phone(jarvis: "Jarvis", registry: ToolRegistry) -> None:
    """Put a question on the user's phone as well as on the console.

    A held request that names an `answerable` argument is a QUESTION rather than
    an action — see `ask_user`. The console draws it in its approvals banner,
    which is the right place when somebody is sitting at a screen. Most of the
    time they are not: they asked out loud, walked off, and the console is in
    another room with nobody looking at it.

    `companion.ask` already knows how to reach whichever device the user is
    actually at, render options as buttons and take a spoken answer. So a
    question goes to both, and **whichever answers first wins** — safely and
    without any coordination, because `approve_request` pops the request before
    it does anything. The loser gets "unknown, expired or already-used", which
    is exactly the truth.

    Only questions are bridged. A tier-3 ACTION deliberately stays on the
    surfaces that already show it: this is a route for supplying a fact, not a
    second, quieter way to consent to unlocking a door.
    """

    def _on_request(event: Any) -> None:
        data = getattr(event, "data", None) or {}
        if not data.get("answerable"):
            return
        request_id = str(data.get("request_id") or "")
        if not request_id:
            return
        arguments = data.get("arguments") or {}
        question = str(arguments.get("question") or data.get("description") or "").strip()
        if not question:
            return
        if not jarvis.services.has_service(COMPANION_DOMAIN, "ask"):
            return
        jarvis.async_create_task(_ask_on_a_device(jarvis, registry, request_id, question, data))

    jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, _on_request)


async def _ask_on_a_device(
    jarvis: "Jarvis",
    registry: ToolRegistry,
    request_id: str,
    question: str,
    data: dict[str, Any],
) -> None:
    """Deliver one question and, if it is answered there, resolve it."""
    tainted = bool(data.get("tainted"))
    try:
        answered = await jarvis.services.async_call(
            COMPANION_DOMAIN,
            "ask",
            {
                # The phone renders this verbatim and has no field for
                # provenance, so provenance goes in the words. A turn that has
                # read a hostile page can compose this sentence, and somebody
                # glancing at a lock screen has no other way to know that.
                "question": f"{UNTRUSTED_PREFIX} {question}" if tainted else question,
                "options": list(data.get("choices") or []),
                # The clock the request is already on. Asking a phone for longer
                # than the request lives would put a live-looking prompt in
                # somebody's hand for an answer nothing can still accept.
                "timeout": max(5.0, float(data.get("expires_at", 0)) - time.time()),
                # The single voice (M66): a question raised by a spoken turn is
                # said once, by the reply, on the surface the user spoke to.
                # The phone gets it as a card to tap and does not read it out
                # again — it used to, and the operator heard the question
                # twice. A typed turn's question is still spoken by the phone,
                # because nothing else will say it.
                "spoken": bool(data.get("spoken")),
            },
            blocking=True,
            return_response=True,
        )
    except Exception:  # pragma: no cover - a phone that is not there is normal
        _LOGGER.debug("Could not put the question on a device", exc_info=True)
        return
    reply = (answered or {}).get("answer") if isinstance(answered, dict) else None
    if reply is None or str(reply).strip() == "":
        # Dismissed, timed out, or no device. The console's copy is still live.
        return
    result = await registry.approve_request(request_id, True, str(reply))
    if result.get("status") == "error":
        # Somebody answered on the console first. Nothing to do and nothing
        # wrong: the pop is what made the race safe.
        _LOGGER.debug("The question %s was already answered elsewhere", request_id)


def _register_services(jarvis: "Jarvis", client: Any, registry: ToolRegistry) -> None:
    async def handle_approve(call: ServiceCall) -> dict[str, Any]:
        request_id = str(call.get("request_id") or "")
        # `answer` reaches exactly one argument, named by the tool itself, and
        # is ignored for every tool that did not opt in — see
        # `ToolRegistry.approve_request` and `Tool.answerable`.
        return await registry.approve_request(
            request_id, parse_approved(call.get("approved")), call.get("answer")
        )

    jarvis.services.register(
        DOMAIN,
        "approve",
        handle_approve,
        description="Approve (or deny) an action the safety gate is holding.",
        fields={
            "request_id": {"description": "Id from the approval request.", "required": True},
            "approved": {"description": "true to execute, false to discard."},
            "answer": {
                "description": (
                    "What the human replied, for a request that is a question. "
                    "Ignored by tools that do not take an answer."
                )
            },
        },
        supports_response=True,
    )

    async def handle_pending(call: ServiceCall) -> dict[str, Any]:
        return {"pending": registry.pending_requests()}

    jarvis.services.register(
        DOMAIN,
        "pending_requests",
        handle_pending,
        description="List actions waiting on human approval.",
        supports_response=True,
    )

    async def handle_list_models(call: ServiceCall) -> dict[str, Any]:
        return {"models": await client.list_models()}

    jarvis.services.register(
        DOMAIN,
        "list_models",
        handle_list_models,
        description="Models the model server has available.",
        supports_response=True,
    )
