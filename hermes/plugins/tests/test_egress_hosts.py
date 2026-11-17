"""Every public host the plugins' code names is declared, with why (T-0573).

The allowlist (`_egress.allowed`) is derived from the settings, so a host a plugin types into its
code instead never passes through it: the weather's open-meteo hosts went out that way unnoticed
until T-0765. This scan makes a new one fail CI, naming the file and line, until it is added to
`_egress.PLUGIN_HOSTS` with its reason.

Does not cover: a host built at runtime from a setting (that is the derived allowlist); a host in
a comment or a docstring, which reaches nothing; jarvis-core, the adapter and the apps
(deploy/tests/test_location_egress.py scans those for the location services).
"""

from __future__ import annotations

import ast
import ipaddress
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _egress  # noqa: E402

PLUGINS = Path(__file__).resolve().parents[1]
#: The host of an http(s) URL in a string, past any user:password@.
URL = re.compile(r"https?://(?:[^/@\s'\"]*@)?([A-Za-z0-9.-]+)")
#: Names that never leave the house: RFC 2606/6761 placeholders and the LAN's own suffixes.
PLACEHOLDER = re.compile(r"(?:^|\.)(?:example(?:\.com|\.net|\.org)?|test|invalid|localhost|lan|local|internal|home\.arpa)$")


def _public(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host and not PLACEHOLDER.search(host.lower())
    return ip.is_global


def _shipped():
    for path in sorted(PLUGINS.rglob("*.py")):
        parts = path.relative_to(PLUGINS).parts
        if any(p in ("tests", "test", "__pycache__") for p in parts) or path.name.startswith("test_"):
            continue
        yield path


def _named(path: Path):
    """(line, host) for each URL host in a string constant that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            for host in URL.findall(node.value):
                yield node.lineno, host.lower().rstrip(".")


def test_every_public_host_the_plugins_name_is_declared():
    undeclared = [
        f"{path.relative_to(PLUGINS)}:{line} {host}"
        for path in _shipped()
        for line, host in _named(path)
        if _public(host) and host not in _egress.PLUGIN_HOSTS
    ]
    assert not undeclared, "declare these in _egress.PLUGIN_HOSTS, with why:\n" + "\n".join(undeclared)


def test_each_declared_host_says_why_and_is_still_named():
    named = {host for path in _shipped() for _, host in _named(path)}
    for host, why in _egress.PLUGIN_HOSTS.items():
        assert len(why.split()) >= 4, host
        # A host no plugin names any more is removed, not kept as a standing permission.
        assert host in named, host


def test_the_scan_sees_through_userinfo_and_skips_placeholders():
    assert URL.findall("https://me:pw@cloud.lan/dav") == ["cloud.lan"]
    assert not any(map(_public, ["cloud.lan", "x.example", "paste.example.com", "cloud.test", "127.0.0.1", "host"]))
    assert _public("api.open-meteo.com") and _public("8.8.8.8")
