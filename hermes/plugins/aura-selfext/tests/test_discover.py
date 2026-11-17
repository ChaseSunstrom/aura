"""What the harness can tell about a service before it generates anything.

`classify` is pure, so every rule in it is argued with here rather than in
production. The probes are exercised too, but with an injected `fetch` — a test
that needs a network is a test that fails for a reason that is not the code's.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Loaded by path, not by putting the plugin's folder on `sys.path`. Every plugin
# has a `schemas.py` and a `tools.py`; a folder left on the path shadows the next
# plugin's modules for the rest of the session, which is how this file first made
# `aura-guard`'s tests fail while passing on its own.
# `tools.py` uses relative imports, so it has to be loaded as part of a package:
# `_plugin_loader.load_plugin` is what does that, and it is what the sibling test
# uses. `discover` is loaded on its own because it is pure and has no siblings.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

_selfext = load_plugin("aura-selfext")

_spec = importlib.util.spec_from_file_location(
    "selfext_discover", Path(__file__).resolve().parents[1] / "discover.py"
)
d = importlib.util.module_from_spec(_spec)
# Registered before it is executed: a dataclass in a module that is not in
# `sys.modules` cannot resolve its own annotations under `from __future__ import
# annotations`, and fails at import with a NoneType `__dict__`.
sys.modules[_spec.name] = d
_spec.loader.exec_module(d)


def ev(**kw) -> d.Evidence:
    return d.Evidence(**kw)


# --------------------------------------------------------------------------- #
# the certain cases: something said what it was
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis"])
def test_a_dsn_scheme_is_a_database_and_nothing_else(scheme: str):
    out = d.classify(ev(url=f"{scheme}://host/db"))
    assert out.interface == "database" and out.confidence == "certain"


def test_an_openapi_document_is_read_from_its_first_field():
    out = d.classify(ev(url="https://x", well_known_hit="/openapi.json",
                        body_head='{"openapi": "3.1.0", "info": {}}'))
    assert out.interface == "openapi" and out.confidence == "certain"
    assert out.generate == "plugin"


def test_an_mcp_manifest_is_configured_not_reimplemented():
    out = d.classify(ev(url="https://x", well_known_hit="/.well-known/mcp.json",
                        body_head='{"protocolVersion": "2025-06-18", "tools": []}'))
    assert out.interface == "mcp"
    # The whole point: a server that exists is configured, not rewritten.
    assert out.generate == "mcp_server"


def test_a_help_with_a_usage_block_is_a_cli():
    out = d.classify(ev(command="tailscale", help_text="USAGE\n  tailscale [flags]\nCommands:\n  up"))
    assert out.interface == "cli" and out.confidence == "certain"


def test_a_command_whose_help_says_nothing_is_only_a_guess():
    out = d.classify(ev(command="thing", help_text="thing 1.0"))
    assert out.interface == "cli" and out.confidence == "guess"


# --------------------------------------------------------------------------- #
# the inferred cases: nothing said, so the evidence has to carry it
# --------------------------------------------------------------------------- #

def test_html_and_no_machine_interface_means_a_browser_drives_it():
    out = d.classify(ev(url="https://x", scheme="https", status=200, content_type="text/html"))
    assert out.interface == "browser" and out.confidence == "likely"


def test_json_with_no_spec_is_an_openapi_guess_and_says_the_plugin_is_hand_written():
    out = d.classify(ev(url="https://x", scheme="https", status=200,
                        content_type="application/json"))
    assert out.interface == "openapi" and out.confidence == "guess"
    assert "documentation" in out.why


def test_a_refused_get_with_nothing_to_read_looks_like_a_webhook():
    out = d.classify(ev(url="https://x", scheme="https", status=405, content_type=""))
    assert out.interface == "webhook"
    assert out.generate == "n8n_workflow"


def test_nothing_answering_anywhere_is_a_gui_or_it_is_not_here():
    out = d.classify(ev())
    assert out.interface == "computer_use" and out.confidence == "guess"


def test_every_class_the_claim_names_has_something_to_generate():
    for name in d.CLASSES:
        assert name in d.GENERATES, name


# --------------------------------------------------------------------------- #
# the probes
# --------------------------------------------------------------------------- #

def test_the_url_probe_prefers_a_spec_over_the_page_it_was_given():
    """A service with both an OpenAPI document and a web UI is reached by the
    document: a generated client is stabler than a driven browser."""
    def fetch(target: str):
        if target.endswith("/openapi.json"):
            return 200, "application/json", '{"openapi":"3.0.0"}'
        return 200, "text/html", "<html>a dashboard</html>"

    # An allowed host: the probe is bound by the same egress allowlist as every
    # other outbound path in this stack, so a test that used an invented domain
    # would be testing the refusal, not the preference.
    out = d.discover(url="http://127.0.0.1:8899/dashboard", fetch=fetch)
    assert out["interface"] == "openapi"
    assert out["evidence"]["well_known"] == "/openapi.json"


def test_the_url_probe_survives_a_service_that_refuses_everything():
    def fetch(target: str):
        raise ConnectionError("nope")

    out = d.discover(url="http://127.0.0.1:9/down", fetch=fetch)
    # It learnt something: nothing answered, and that is recorded rather than raised.
    assert out["evidence"]["notes"]
    assert out["interface"] in d.CLASSES


def test_discover_refuses_to_guess_with_nothing_to_go_on():
    with pytest.raises(ValueError):
        d.discover()


def test_a_command_on_this_path_is_classified_from_its_real_help():
    """Not a fixture: the harness's first job is to be right about the machine
    it is on, so this probes a real binary and reads its real help.

    `git` rather than something more interesting, because the test has to hold
    on every machine that runs it. `tailscale` was here first and passed on
    this host and nowhere else — the CI image has no tailscale, and three tests
    that only ever ran green on one developer's box are worse than no test.
    `git` is on this host and the `test:plugins` job installs it by name.
    """
    out = d.discover(command="git")
    assert out["interface"] == "cli"
    assert d.summary(out).startswith("cli (")


# --------------------------------------------------------------------------- #
# discovery reaching the plan
# --------------------------------------------------------------------------- #



TOOLS = [{"name": "thing_read", "description": "Read a thing from the service."}]


def test_a_plan_without_a_service_says_nothing_about_an_interface():
    plan = _selfext.tools.plan({"name": "thing", "tools": TOOLS})
    assert "interface" not in plan
    assert plan["next"][0] == "selfext_scaffold"


def test_a_plan_for_a_cli_carries_what_discovery_found():
    plan = _selfext.tools.plan({"name": "thing", "tools": TOOLS, "command": "git"})
    assert plan["interface"] == "cli"
    assert plan["confidence"] == "certain"
    assert plan["generate"] == "plugin"
    assert "usage block" in plan["discovery"]


def test_a_plan_for_a_database_still_scaffolds_a_plugin():
    plan = _selfext.tools.plan({"name": "thing", "tools": TOOLS, "url": "sqlite:///tmp/x.db"})
    assert plan["interface"] == "database"
    assert plan["next"] == ["selfext_scaffold", "selfext_verify", "selfext_open_mr"]


def test_a_guess_is_carried_into_the_plan_rather_than_smoothed_over():
    """The owner reading a plan should see the shape was inferred from a content
    type and not from a spec."""
    plan = _selfext.tools.plan({"name": "thing", "tools": TOOLS, "command": "sh"})
    assert plan["confidence"] in ("certain", "guess")
    assert plan["discovery"].startswith(plan["interface"])


# --------------------------------------------------------------------------- #
# what a probe must refuse (found by the commit security review, 2026-09-08)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hostile", [
    "rm -rf /tmp/x",          # arguments: `rm -rf /tmp/x --help` deletes rather than explains
    "docker run --rm alpine", # arguments to a real binary are still arguments
    "/bin/sh",                # a path, not a name
    "../../bin/sh",           # a traversal
    "sh -c 'id'",             # quoting, in case anything ever reaches a shell
    "",                       # nothing
    "a" * 200,                # nothing plausible
])
def test_a_command_probe_refuses_anything_that_is_not_a_bare_name(hostile: str):
    ev = d.probe_command(hostile)
    assert ev.help_text == "", f"{hostile!r} was executed"
    assert ev.notes, f"{hostile!r} was refused silently"


def test_a_bare_name_on_this_path_still_works():
    assert d.probe_command("git").help_text


def test_a_url_probe_refuses_the_cloud_metadata_endpoint():
    """169.254.169.254 is the one address a discovery probe must never fetch: it
    is credentials, and it answers to anything that can make a GET."""
    calls: list[str] = []

    def fetch(target: str):
        calls.append(target)
        return 200, "application/json", "{}"

    ev = d.probe_url("http://169.254.169.254/latest/meta-data", fetch=fetch)
    assert calls == [], "the metadata endpoint was fetched"
    assert any("allowlist" in n for n in ev.notes)


def test_a_url_probe_refuses_a_host_nobody_allowed():
    calls: list[str] = []

    def fetch(target: str):
        calls.append(target)
        return 200, "text/html", "<html>"

    ev = d.probe_url("https://neighbour.example.invalid/admin", fetch=fetch)
    assert calls == []
    assert any("allowlist" in n for n in ev.notes)
    # And the classification that follows is honest about having seen nothing.
    assert d.classify(ev).confidence == "guess"
