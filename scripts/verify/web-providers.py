"""Which web providers a real Hermes actually selected (V2 §4, D19).

``scripts/verify/no-cloud-web-providers.sh`` reads the files. This reads the
runtime: it triggers Hermes' own plugin discovery against a given ``HERMES_HOME``
and asks the provider registry which backend it resolved for search and for
extract. Those are different claims. A config can name ``searxng`` and still
resolve to something else — the plugin that provides it ships disabled, so an
unregistered name falls through the registry's legacy walk and then into its
KEYLESS tier, Exa's and Parallel's public MCP endpoints. Nothing warns; the tool
just works, over the internet.

Run it where Hermes is installed::

    python3 scripts/verify/web-providers.py                     # this box's ~/.hermes
    python3 scripts/verify/web-providers.py --home /opt/data    # inside the container
    docker exec -i jarvis /opt/hermes/.venv/bin/python - < scripts/verify/web-providers.py

Exit status 0 when search resolves to ``searxng``, extract resolves to
``jarvis-extract`` and the keyless tier is off; 1 otherwise, with the reason and
the full registry listing printed. ``--expect-search`` / ``--expect-extract``
override the names for a host that deliberately runs something else.

What it does NOT prove: that the extraction service answers. Pass ``--call`` to
add a live round-trip against it (a data URL is not enough — the provider is
tested against its real endpoint or not at all).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: Where the Hermes install lives when the caller has not put it on the path.
#: The container's is ``/opt/hermes``; a git install is ``~/.hermes/hermes-agent``.
_INSTALL_CANDIDATES = (
    Path("/opt/hermes"),
    Path.home() / ".hermes" / "hermes-agent",
)


def _put_hermes_on_the_path() -> str:
    """Return the install directory that was added, or "" when Hermes imports already."""
    try:
        import agent.web_search_registry  # noqa: F401
        return ""
    except ImportError:
        pass
    for candidate in _INSTALL_CANDIDATES:
        if (candidate / "agent" / "web_search_registry.py").is_file():
            sys.path.insert(0, str(candidate))
            return str(candidate)
    raise SystemExit(
        "Hermes is not importable and no install was found at "
        + " or ".join(str(c) for c in _INSTALL_CANDIDATES)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", default=os.environ.get("HERMES_HOME", ""),
                        help="HERMES_HOME to resolve config and plugins against")
    parser.add_argument("--expect-search", default="searxng")
    parser.add_argument("--expect-extract", default="jarvis-extract")
    parser.add_argument("--call", metavar="URL",
                        help="also extract this URL through the resolved provider")
    args = parser.parse_args(argv)

    if args.home:
        # Set before Hermes is imported: hermes_constants caches the home on first read.
        os.environ["HERMES_HOME"] = args.home
    install = _put_hermes_on_the_path()
    if install:
        print(f"hermes install: {install}")
    print(f"HERMES_HOME:    {os.environ.get('HERMES_HOME') or '(default)'}")

    from tools.web_tools import _ensure_web_plugins_loaded

    _ensure_web_plugins_loaded()

    from agent.web_search_registry import (
        get_active_extract_provider,
        get_active_search_provider,
        list_providers,
    )
    from hermes_cli.config import load_config

    web = load_config().get("web") or {}
    registered = [p.name for p in list_providers()]
    search = get_active_search_provider()
    extract = get_active_extract_provider()
    search_name = getattr(search, "name", None)
    extract_name = getattr(extract, "name", None)

    print(f"registered:     {registered or '(none)'}")
    print(f"config web:     search_backend={web.get('search_backend', '')!r} "
          f"extract_backend={web.get('extract_backend', '')!r} "
          f"keyless_fallback={web.get('keyless_fallback', True)!r} "
          f"keyless_rescue={web.get('keyless_rescue', True)!r}")
    print(f"active search:  {search_name}")
    print(f"active extract: {extract_name}")

    problems: list[str] = []
    if search_name != args.expect_search:
        problems.append(f"search resolved to {search_name!r}, expected {args.expect_search!r}")
    if extract_name != args.expect_extract:
        problems.append(f"extract resolved to {extract_name!r}, expected {args.expect_extract!r}")
    # The keyless tier is the path that needs no credential and no config, so it is
    # the one that comes back on its own after a config edit somewhere else.
    if web.get("keyless_fallback", True):
        problems.append("web.keyless_fallback is on (Exa/Parallel public MCP endpoints)")
    if web.get("keyless_rescue", True):
        problems.append("web.keyless_rescue is on (same endpoints, on a provider error)")

    if args.call and extract is not None:
        results = extract.extract([args.call])
        first = results[0] if results else {}
        error = first.get("error")
        chars = len(first.get("content") or "")
        print(f"extract {args.call}: {chars} chars" + (f", error={error}" if error else ""))
        if error or not chars:
            problems.append(f"live extraction of {args.call} returned nothing usable")

    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
