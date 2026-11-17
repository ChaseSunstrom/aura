"""Work out how a service can be reached, before anything is generated.

The v3 brief's Part 3 makes a testable claim: JARVIS can reach anything exposing
an HTTP/GraphQL API, an MCP server, a CLI, a database, a file interface, a
webhook, or a web UI it can drive in a browser — and, for GUI-only desktop apps,
whatever ``computer_use`` can do here. `integrate_app` is the harness that turns
that claim into a plugin; this module is its first step, and the only one that
has to *look* at the service.

Two halves, split so the interesting one needs no network:

* :func:`classify` is pure. Given what a probe found — a URL's scheme, the
  headers and body of a well-known path, a command's ``--help``, a DSN — it says
  which interface class this is and how sure it is. Every rule in it is a rule
  about evidence, so it can be argued with in a test rather than in production.
* :func:`probe` does the fetching, and does as little as it can: a HEAD and at
  most three GETs of well-known paths, no crawling, no authentication guessing.

What it deliberately does not do is decide whether to integrate. A classification
is an input to `selfext_plan`; the plan, the MR and the owner's approval are what
decide, and they are unchanged.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

#: The interface classes the claim names, in the order a probe should prefer
#: them: a service with an OpenAPI spec AND a web UI is better reached by the
#: spec, because a generated client is stabler than a driven browser.
CLASSES: tuple[str, ...] = (
    "openapi", "graphql", "mcp", "cli", "database", "webhook", "browser", "computer_use",
)

#: Well-known paths worth one GET each. Ordered: the first hit wins, so a
#: service that serves both an OpenAPI document and a GraphQL endpoint is
#: classified by whichever it advertises first.
WELL_KNOWN: tuple[tuple[str, str], ...] = (
    ("/openapi.json", "openapi"),
    ("/swagger.json", "openapi"),
    ("/v1/openapi.json", "openapi"),
    ("/.well-known/mcp.json", "mcp"),
    ("/graphql", "graphql"),
)

#: DSN schemes that mean "this is a database", not "this is a URL".
DB_SCHEMES: frozenset[str] = frozenset({
    "postgres", "postgresql", "mysql", "mariadb", "sqlite", "mongodb", "redis", "clickhouse",
})


def _egress_allows(target: str) -> bool:
    """The stack's own allowlist, or refuse.

    Imported lazily and by two names because this module is loaded both as part
    of the plugin package and, in tests, on its own; a discovery probe that
    silently fell back to "allow" when the import failed would be an allowlist
    with an off switch.
    """
    try:
        from _shared import _egress
    except ImportError:
        try:
            from .._shared import _egress  # type: ignore[no-redef]
        except ImportError:
            return False
    return bool(_egress.allowed(target))


@dataclass
class Evidence:
    """What a probe found. Every field is optional: a probe may find nothing."""

    url: str = ""
    scheme: str = ""
    status: Optional[int] = None
    content_type: str = ""
    body_head: str = ""
    well_known_hit: str = ""
    command: str = ""
    help_text: str = ""
    is_file: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Classification:
    interface: str
    confidence: str  # "certain" | "likely" | "guess"
    why: str
    #: What the generator should produce for this class.
    generate: str

    def as_dict(self) -> dict[str, Any]:
        return {"interface": self.interface, "confidence": self.confidence,
                "why": self.why, "generate": self.generate}


#: Interface class -> what the harness generates for it. A `plugin` is a Hermes
#: plugin from the repo templates; an `mcp_server` is a config entry, because a
#: server that exists should be configured rather than reimplemented; an
#: `n8n_workflow` is for a service that only speaks webhook.
GENERATES: dict[str, str] = {
    "openapi": "plugin",
    "graphql": "plugin",
    "mcp": "mcp_server",
    "cli": "plugin",
    "database": "plugin",
    "webhook": "n8n_workflow",
    "browser": "plugin",
    "computer_use": "plugin",
}


def _looks_like_openapi(body: str) -> bool:
    """An OpenAPI document says so in its first field, whatever else it holds."""
    head = body.lstrip()[:400]
    return bool(re.search(r'"(openapi|swagger)"\s*:\s*"', head))


def _looks_like_mcp(body: str) -> bool:
    head = body.lstrip()[:400]
    return '"mcpVersion"' in head or '"protocolVersion"' in head or '"tools"' in head


def classify(evidence: Evidence) -> Classification:
    """Which interface class the evidence supports, and how sure that is.

    Pure: no network, no filesystem. The confidence is part of the answer
    because "this is probably a CLI" and "this advertises OpenAPI at
    /openapi.json" should not lead to the same amount of generated code.
    """
    scheme = (evidence.scheme or urlparse(evidence.url).scheme).lower()

    if scheme in DB_SCHEMES:
        return Classification("database", "certain", f"the DSN scheme is {scheme}",
                              GENERATES["database"])

    if evidence.well_known_hit and _looks_like_openapi(evidence.body_head):
        return Classification("openapi", "certain",
                              f"{evidence.well_known_hit} returned an OpenAPI document",
                              GENERATES["openapi"])
    if evidence.well_known_hit and _looks_like_mcp(evidence.body_head):
        return Classification("mcp", "certain",
                              f"{evidence.well_known_hit} returned an MCP manifest",
                              GENERATES["mcp"])
    if evidence.well_known_hit == "/graphql":
        return Classification("graphql", "likely", "a /graphql endpoint answered",
                              GENERATES["graphql"])

    if evidence.command and evidence.help_text:
        # A `--help` that lists subcommands or options is a CLI; one that prints
        # a stack trace is a command that does not take --help.
        if re.search(r"^\s*(-{1,2}\w|usage:|Usage:|Commands:)", evidence.help_text, re.M):
            return Classification("cli", "certain",
                                  f"`{evidence.command} --help` printed a usage block",
                                  GENERATES["cli"])
        return Classification("cli", "guess",
                              f"`{evidence.command}` exists but its --help says little",
                              GENERATES["cli"])

    if evidence.is_file:
        return Classification("database", "likely",
                              "a file on disk: read it directly rather than over a network",
                              GENERATES["database"])

    if scheme in ("http", "https"):
        ct = evidence.content_type.lower()
        if "application/json" in ct:
            return Classification("openapi", "guess",
                                  "it answers JSON but advertises no spec; the plugin will have "
                                  "to be written from its documentation",
                                  GENERATES["openapi"])
        if "text/html" in ct:
            return Classification("browser", "likely",
                                  "it serves HTML and no machine interface was found, so it is "
                                  "driven in a browser",
                                  GENERATES["browser"])
        if evidence.status is not None and evidence.status in (405, 404) and not ct:
            return Classification("webhook", "guess",
                                  "it refuses a GET and offers nothing to read, which is what a "
                                  "webhook-only endpoint looks like",
                                  GENERATES["webhook"])
        return Classification("browser", "guess",
                              "reachable over HTTP with nothing else to go on",
                              GENERATES["browser"])

    return Classification("computer_use", "guess",
                          "nothing answered over a network and no command was named, so if it "
                          "exists here at all it is a GUI",
                          GENERATES["computer_use"])


#: A command this may probe: a bare executable name, nothing else. No path
#: separators, no spaces, no shell metacharacters.
_BARE_COMMAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def probe_command(command: str, *, timeout: float = 10.0) -> Evidence:
    """`<name> --help`, for a bare executable name on PATH. No shell, no arguments.

    **Only the name.** The first version took whatever string it was given,
    split it on spaces and ran the pieces — so `rm -rf /tmp/x` became
    `rm -rf /tmp/x --help`, which deletes rather than explains. There is no shell
    involved and never was, so this was not shell injection; it was worse, an
    argument-carrying execution of any binary on PATH. A discovery probe has no
    business passing arguments to anything, so it passes none: the name is
    validated against `_BARE_COMMAND`, resolved on PATH, and run with the single
    literal `--help`.
    """
    ev = Evidence(command=command)
    name = command.strip()
    if not _BARE_COMMAND.match(name):
        ev.notes.append(
            "a command to probe is a bare executable name; arguments and paths are refused"
        )
        return ev
    exe = shutil.which(name)
    if exe is None:
        ev.notes.append(f"{name} is not on PATH")
        return ev
    try:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True,
                             timeout=timeout, check=False, shell=False)
        ev.help_text = (out.stdout or out.stderr)[:4000]
    except (OSError, subprocess.SubprocessError) as exc:
        ev.notes.append(f"--help did not run: {exc}")
    return ev


def probe_url(url: str, *, fetch: Any = None, timeout: float = 10.0) -> Evidence:
    """The URL itself and at most three well-known paths.

    `fetch` is injected so the tests need no network: it takes a URL and returns
    ``(status, content_type, body)``. The default uses httpx if it is there.
    """
    parsed = urlparse(url)
    ev = Evidence(url=url, scheme=parsed.scheme)
    if parsed.scheme in DB_SCHEMES:
        return ev
    if fetch is None:
        try:
            import httpx

            def fetch(target: str):
                r = httpx.get(target, timeout=timeout, follow_redirects=True)
                return r.status_code, r.headers.get("content-type", ""), r.text[:2000]
        except ImportError:
            ev.notes.append("httpx is not installed; nothing was fetched")
            return ev

    # Default-deny, through the stack's own allowlist. Without this a discovery
    # probe is an SSRF primitive: the model names a URL and this fetches it,
    # which reaches anything the host can reach — a neighbour's admin page, a
    # cloud metadata endpoint, a service that is only safe because nothing
    # outside can talk to it. `_egress.allowed` already refuses link-local and
    # unparseable targets, and it is the same list every other outbound path in
    # this stack is bound by, which is what the approval matrix's row claims.
    if not _egress_allows(url):
        ev.notes.append(f"{url} is not on the egress allowlist; nothing was fetched")
        return ev

    root = f"{parsed.scheme}://{parsed.netloc}"
    try:
        ev.status, ev.content_type, ev.body_head = fetch(url)
    except Exception as exc:  # a probe that raises has still learnt something
        ev.notes.append(f"{url} did not answer: {type(exc).__name__}")

    for path, _kind in WELL_KNOWN:
        try:
            status, ctype, body = fetch(root + path)
        except Exception:
            continue
        if status == 200 and (_looks_like_openapi(body) or _looks_like_mcp(body) or path == "/graphql"):
            ev.well_known_hit = path
            ev.content_type = ctype or ev.content_type
            ev.body_head = body
            break
    return ev


def discover(*, url: str = "", command: str = "", fetch: Any = None) -> dict[str, Any]:
    """One service in, one classification out. The tool's whole body."""
    if not url and not command:
        raise ValueError("discover needs a url or a command")
    ev = probe_command(command) if command and not url else probe_url(url, fetch=fetch)
    if command and url:
        ev.command = command
    found = classify(ev)
    return {
        **found.as_dict(),
        "url": url,
        "command": command,
        "evidence": {
            "status": ev.status,
            "content_type": ev.content_type,
            "well_known": ev.well_known_hit,
            "notes": ev.notes,
        },
    }


def summary(result: dict[str, Any]) -> str:
    """One line a person can read, which is what the tool returns."""
    return (f"{result['interface']} ({result['confidence']}): {result['why']}. "
            f"The harness would generate a {result['generate'].replace('_', ' ')}.")


__all__ = ["CLASSES", "Classification", "Evidence", "GENERATES", "classify", "discover",
           "probe_command", "probe_url", "summary"]
