"""A plugin that makes web requests cannot skip the address check or the failure shape (T-1365).

Two hand-kept tables pin what a service plugin does when it talks to a server: ``CASES`` in
test_failure_shape.py (the model reads only a status and a fixed hint) and ``CASES`` in
test_service_url.py (a bad server address sends nothing). A plugin written next month would be in
neither, and nothing would say so. This walk finds every shipped plugin that imports httpx or
requests, directly or through a ``_shared`` module that does, and fails unless it is in each table
or in the matching exemption dict below with a reason.

Does not cover: a plugin that reaches the network some other way (``urllib``, a subprocess), or
whether a table row exercises the right handler (the two tables' own tests do that). An exemption
is the author's word; the reason is for a reviewer to read, not for the test to check.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# The two sibling test modules are imported by name: alone (the affected-tests run hands pytest
# this one file) nothing has put this folder on the path, and collection failed (T-1365).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_egress_hosts import PLUGINS, _shipped  # noqa: E402
from test_failure_shape import CASES as FAILURE_CASES  # noqa: E402
from test_service_url import CASES as SERVICE_URL_CASES  # noqa: E402

HTTP_LIBS = {"httpx", "requests"}

#: Plugins that make requests but are not in test_failure_shape's CASES, each with why.
#: "SHOULD BE COVERED" marks a gap that is a task of its own, not a judgement that it is fine.
NO_FAILURE_SHAPE = {
    "aura-desktop": "talks only to the house service through _shared._house and answers in its own fixed strings",
    "aura-calls": "house only through _shared._house; a refusal answers 'the house refused (N)' or the exception class, never a body",
    "aura-texts": "house only through _shared._house; a refusal answers 'the house refused (N)' or the exception class, never a body",
    "aura-connect": "house only through _shared._house; a refusal answers 'the house refused (N)' or the exception class, never a body",
    "aura-emergency": "house only through _shared._house; a failed push returns False and the alert answers its own fixed lines",
    "aura-hud": "weather, places, feeds and watches fetch public pages and answer in their own words; no service body is relayed",
    "aura-selfext": "probe_url reads a model-named page for the self-extension scan and keeps 2000 bytes as evidence, not as an answer",
    "aura-integrations": "service_setup reaches n8n only through _shared._n8n_setup, which answers in its own fixed lines (a status code or an exception class), never a body",
}

#: Plugins that make requests but are not in test_service_url's CASES, each with why.
NO_SERVICE_URL = {
    "aura-calendar": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "smart-home": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "aura-desktop": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "aura-calls": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "aura-texts": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "aura-connect": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "aura-emergency": "talks to the house on HASS_URL through _shared._house, the house's own address, not a service setting",
    "aura-hud": "public hosts only (open-meteo, Photon, Nominatim, feeds), refused inside the house by _egress.private and the allowlist",
    "aura-selfext": "the model names the URL and the fetch is bound by _egress.allowed (default-deny), not by a configured service address",
    "aura-integrations": "service_setup reaches n8n on N8N_URL through _shared._n8n_setup, which checks it with _egress.service_url (the n8n plugin's CASES row covers that address)",
}


def _imports(path: Path) -> set[str]:
    """Dotted module names imported anywhere in the file, lazy imports included."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            if node.module == "_shared":
                found.update(f"_shared.{a.name}" for a in node.names)
    return found


def _requesters(root: Path, paths) -> set[str]:
    """Plugin directories under ``root`` with a file that imports an http library, or a _shared
    module that does. ``_shared`` is not a plugin; its modules are followed instead."""
    shared = {f"_shared.{p.stem}" for p in (root / "_shared").glob("*.py")
              if {m.split(".")[0] for m in _imports(p)} & HTTP_LIBS}
    out = set()
    for path in paths:
        top = path.relative_to(root).parts[0]
        if top.startswith("_") or not (root / top).is_dir():
            continue
        if any(m.split(".")[0] in HTTP_LIBS or m in shared for m in _imports(path)):
            out.add(top)
    return out


def _problems(requesters: set[str]) -> list[str]:
    in_failure = {c[0] for c in FAILURE_CASES}
    in_service = {c[0] for c in SERVICE_URL_CASES}
    out = []
    for name in sorted(requesters):
        if name not in in_failure and name not in NO_FAILURE_SHAPE:
            out.append(f"{name}: makes requests but is not in test_failure_shape.CASES or NO_FAILURE_SHAPE")
        if name not in in_service and name not in NO_SERVICE_URL:
            out.append(f"{name}: makes requests but is not in test_service_url.CASES or NO_SERVICE_URL")
    return out


def test_request_plugins_are_in_both_tables_or_exempt_with_a_reason() -> None:
    found = _requesters(PLUGINS, _shipped())
    assert found, "the walk found no plugin that imports httpx: it is looking in the wrong place"
    problems = _problems(found)
    assert not problems, "add the plugin to the table, or exempt it here with why:\n" + "\n".join(problems)


def test_request_plugins_exemptions_are_explained_and_still_needed() -> None:
    found = _requesters(PLUGINS, _shipped())
    for table, covered in ((NO_FAILURE_SHAPE, {c[0] for c in FAILURE_CASES}),
                           (NO_SERVICE_URL, {c[0] for c in SERVICE_URL_CASES})):
        for name, why in table.items():
            assert len(why.split()) >= 6, name
            # A plugin that no longer makes requests, or is in the table now, leaves the list.
            assert name in found, f"{name} makes no requests any more: remove its exemption"
            assert name not in covered, f"{name} is in the table now: remove its exemption"


def test_request_plugins_names_a_made_up_plugin_that_imports_httpx(tmp_path: Path) -> None:
    for rel, src in {
        "jarvis-made-up/tools.py": "import httpx\n",
        "jarvis-lazy-made-up/tools.py": "def f():\n    import requests\n",
        "jarvis-via-shared/tools.py": "from _shared import _dav\n",
        "jarvis-offline/tools.py": "import json\n",
        "_shared/_dav.py": "import httpx\n",
        "_shared/_text.py": "import re\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(src, encoding="utf-8")
    found = _requesters(tmp_path, sorted(tmp_path.rglob("*.py")))
    assert found == {"jarvis-made-up", "jarvis-lazy-made-up", "jarvis-via-shared"}
    problems = "\n".join(_problems(found))
    for name in found:
        assert f"{name}: makes requests but is not in test_failure_shape.CASES" in problems
        assert f"{name}: makes requests but is not in test_service_url.CASES" in problems
    assert "jarvis-offline" not in problems
