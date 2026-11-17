"""Unit + contract tests for the jarvis-integrations plugin (no Hermes needed).

The one function that touches the network — :func:`catalogs._run`, the single
subprocess choke point — is replaced with canned CLI output, so the tests cover
argument construction, table/JSON parsing, row normalisation, command parsing,
the approval-gate message, and the audit line without a ``hermes`` binary or a
network. The contract test reads ``tests/contracts/hermes-integrations.json``
and fails if the plugin's copy of the kinds/actions/tools/row-fields drifts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_DIR.parents[2]  # …/hermes/plugins/jarvis-integrations -> repo root
CONTRACT = REPO_ROOT / "tests" / "contracts" / "hermes-integrations.json"

sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("jarvis-integrations")
integrations = plugin.integrations
catalogs = plugin.catalogs


# --------------------------------------------------------------------------- #
# Canned CLI output (shapes captured from the real `hermes` CLI, v0.21.0)
# --------------------------------------------------------------------------- #
PLUGINS_LIST_JSON = json.dumps([
    {"name": "jarvis-hud", "status": "enabled", "version": "0.1.0",
     "description": "The HUD panel surface", "source": "user"},
    {"name": "chronos", "status": "not enabled", "version": "1.0.0",
     "description": "NAS-mediated cron", "source": "bundled"},
])

PLUGINS_SEARCH_JSON = json.dumps({
    "source": "index", "query": "hud", "results": [
        {"name": "cool-hud", "description": "A community HUD", "source": "index",
         "installed": False, "version": "2.1.0"},
    ], "note": "Indexed != audited",
})

SKILLS_SEARCH_JSON = json.dumps([
    {"name": "weather", "identifier": "skills-sh/steipete/clawdis/weather",
     "source": "skills.sh", "trust_level": "community",
     "description": "Indexed by skills.sh from steipete/clawdis"},
    {"name": "Weather", "identifier": "quincy-weather", "source": "clawhub",
     "trust_level": "community", "description": "Get current weather and forecasts."},
])

# Rich table (│ cell separators, ┃ header) — `hermes skills list`.
SKILLS_LIST_TEXT = (
    "                                Installed Skills                                \n"
    "┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓\n"
    "┃ Name            ┃ Category     ┃ Source    ┃ Trust     ┃ Status  ┃\n"
    "┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩\n"
    "│ homelab-runbook │              │ local     │ local     │ enabled │\n"
    "│ weather         │ productivity │ skills.sh │ community │ disabled│\n"
    "└─────────────────┴──────────────┴───────────┴───────────┴─────────┘\n"
)

# Fixed-width, ASCII-dash underline — `hermes mcp catalog`.
MCP_CATALOG_TEXT = (
    "\n  MCP Catalog + configured servers:\n\n"
    "  Name               Status                   Description\n"
    "  ------------------ ------------------------ -----------\n"
    "  airtable           available                Bases, tables, and records.\n"
    "  filesystem         available                Local filesystem access for notes.\n"
    "  gitlab             installed (disabled)     GitLab issues and merge requests.\n"
)

# Fixed-width, box-drawing underline (─), no cell separators — `hermes mcp list`.
MCP_LIST_TEXT = (
    "\n  MCP Servers:\n\n"
    "  Name             Transport                      Tools        Status    \n"
    "  ──────────────── ────────────────────────────── ──────────── ──────────\n"
    "  notes            npx -y @modelcontextprotocol   all          ✓ enabled\n"
    "  gitlab           npx -y @zereight/mcp-gitlab    all          ✗ disabled\n"
)


@pytest.fixture
def fake_run(monkeypatch):
    """Replace catalogs._run with a canned dispatcher; record every call."""
    calls: list[dict] = []

    def runner(args, *, timeout, stdin=None):
        calls.append({"args": list(args), "timeout": timeout, "stdin": stdin})
        a = list(args)
        if a[:2] == ["plugins", "list"]:
            return 0, PLUGINS_LIST_JSON, ""
        if a[:2] == ["plugins", "search"]:
            return 0, PLUGINS_SEARCH_JSON, ""
        if a[:2] == ["plugins", "show"]:
            return 0, f"Plugin {a[2]}: details here", ""
        if a[:2] == ["skills", "search"]:
            return 0, SKILLS_SEARCH_JSON, ""
        if a[:2] == ["skills", "list"]:
            return 0, SKILLS_LIST_TEXT, ""
        if a[:2] == ["skills", "inspect"]:
            return 0, f"Skill {a[2]}: preview here", ""
        if a[:2] == ["mcp", "catalog"]:
            return 0, MCP_CATALOG_TEXT, ""
        if a[:2] == ["mcp", "list"]:
            return 0, MCP_LIST_TEXT, ""
        # mutations
        return 0, f"ok: {' '.join(a)}", ""

    monkeypatch.setattr(catalogs, "_run", runner)
    return calls


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    """Point HERMES_HOME at a tmp dir so the audit log is inspectable."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- table parsers
def test_parse_rich_table_reads_skills_list():
    rows = catalogs.parse_rich_table(SKILLS_LIST_TEXT)
    assert [r["name"] for r in rows] == ["homelab-runbook", "weather"]
    assert rows[1]["source"] == "skills.sh" and rows[1]["trust"] == "community"
    assert rows[1]["status"] == "disabled"


def test_parse_fixed_width_reads_ascii_dash_catalog():
    rows = catalogs.parse_fixed_width_table(MCP_CATALOG_TEXT)
    names = [r["name"] for r in rows]
    assert names == ["airtable", "filesystem", "gitlab"]
    # The last column runs to the line end, so a spaced description survives whole.
    assert rows[0]["description"] == "Bases, tables, and records."
    assert rows[2]["status"] == "installed (disabled)"


def test_parse_table_falls_back_to_box_dash_for_mcp_list():
    # mcp list has no │ separators and a ─ underline: parse_table must fall through.
    assert catalogs.parse_rich_table(MCP_LIST_TEXT) == []
    rows = catalogs.parse_table(MCP_LIST_TEXT)
    assert [r["name"] for r in rows] == ["notes", "gitlab"]
    assert "enabled" in rows[0]["status"] and "disabled" in rows[1]["status"]


# --------------------------------------------------------------------------- search
def test_search_plugin_normalises_rows(fake_run):
    out = json.loads(integrations.do_search("plugin", "hud"))
    assert out["ok"] and out["kind"] == "plugin" and out["count"] == 1
    row = out["rows"][0]
    assert set(catalogs.ROW_FIELDS).issubset(row)
    assert row["id"] == "cool-hud" and row["trust"] == "unaudited" and row["version"] == "2.1.0"


def test_search_skill_maps_identifier_and_trust(fake_run):
    out = json.loads(integrations.do_search("skill", "weather"))
    ids = [r["id"] for r in out["rows"]]
    assert ids == ["skills-sh/steipete/clawdis/weather", "quincy-weather"]
    assert out["rows"][0]["source"] == "skills.sh"
    assert out["rows"][0]["trust"] == "community"
    # the skills search shells out with --json and the limit
    call = [c for c in fake_run if c["args"][:2] == ["skills", "search"]][0]
    assert "--json" in call["args"] and "--limit" in call["args"]


def test_search_mcp_filters_catalog_by_query(fake_run):
    out = json.loads(integrations.do_search("mcp", "filesystem"))
    assert out["count"] == 1 and out["rows"][0]["id"] == "filesystem"
    assert out["rows"][0]["source"] == "nous-catalog" and out["rows"][0]["trust"] == "nous-approved"


def test_search_all_spans_three_catalogues(fake_run):
    out = json.loads(integrations.do_search("all", "weather"))
    assert out["kind"] == "all"
    # plugin(0 for 'weather') + skill(2) + mcp(0 match) — at least the skills.
    assert any(r["source"] == "skills.sh" for r in out["rows"])


def test_search_unknown_kind_is_rejected(fake_run):
    out = json.loads(integrations.handle_search({"kind": "gadget", "query": "x"}))
    assert out["ok"] is False and "unknown kind" in out["error"]


# --------------------------------------------------------------------------- list / inspect
def test_list_plugin_marks_installed_and_enabled(fake_run):
    out = json.loads(integrations.do_list("plugin"))
    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["jarvis-hud"]["installed"] and by_id["jarvis-hud"]["enabled"]
    assert by_id["chronos"]["enabled"] is False
    assert by_id["chronos"]["trust"] == "bundled"


def test_list_mcp_uses_mcp_list(fake_run):
    out = json.loads(integrations.do_list("mcp"))
    assert {r["id"] for r in out["rows"]} == {"notes", "gitlab"}
    assert all(r["installed"] for r in out["rows"])


def test_inspect_skill_returns_detail(fake_run):
    out = json.loads(integrations.do_inspect("skill", "quincy-weather"))
    assert out["ok"] and out["kind"] == "skill" and "preview" in out["detail"]


def test_inspect_requires_an_id(fake_run):
    out = json.loads(integrations.handle_inspect({"kind": "skill", "id": ""}))
    assert out["ok"] is False and "id is required" in out["error"]


# --------------------------------------------------------------------------- mutations + args
def test_install_plugin_lands_disabled(fake_run, tmp_home):
    out = json.loads(integrations.handle_install(
        {"kind": "plugin", "id": "cool-hud"}, session_id="s1"))
    assert out["ok"] and out["action"] == "install"
    call = [c for c in fake_run if c["args"][:2] == ["plugins", "install"]][0]
    assert call["args"] == ["plugins", "install", "cool-hud", "--no-enable"]


def test_install_skill_passes_yes(fake_run, tmp_home):
    integrations.handle_install({"kind": "skill", "id": "quincy-weather"}, session_id="s1")
    call = [c for c in fake_run if c["args"][:2] == ["skills", "install"]][0]
    assert call["args"] == ["skills", "install", "quincy-weather", "--yes"]


def test_remove_plugin_feeds_confirmation(fake_run, tmp_home):
    integrations.handle_remove({"kind": "plugin", "id": "cool-hud"}, session_id="s1")
    call = [c for c in fake_run if c["args"][:2] == ["plugins", "remove"]][0]
    assert call["stdin"] == "y\n"


def test_enable_plugin_true_and_false(fake_run, tmp_home):
    integrations.handle_enable({"kind": "plugin", "id": "cool-hud", "enabled": True}, session_id="s")
    integrations.handle_enable({"kind": "plugin", "id": "cool-hud", "enabled": False}, session_id="s")
    verbs = [c["args"][1] for c in fake_run if c["args"][0] == "plugins"]
    assert "enable" in verbs and "disable" in verbs


def test_update_mcp_is_unsupported_but_structured(fake_run, tmp_home):
    out = json.loads(integrations.handle_update({"kind": "mcp", "id": "notes"}, session_id="s"))
    assert out["ok"] is False and "not supported" in out["error"] and out["kind"] == "mcp"


def test_enable_skill_is_unsupported(fake_run, tmp_home):
    out = json.loads(integrations.handle_enable(
        {"kind": "skill", "id": "weather", "enabled": True}, session_id="s"))
    assert out["ok"] is False and "not supported" in out["error"]


def test_mutation_needs_id(fake_run, tmp_home):
    out = json.loads(integrations.handle_install({"kind": "plugin", "id": ""}, session_id="s"))
    assert out["ok"] is False and "id is required" in out["error"]


# --------------------------------------------------------------------------- audit log
def test_mutation_writes_one_audit_line(fake_run, tmp_home):
    integrations.handle_install({"kind": "skill", "id": "quincy-weather"}, session_id="sess-42")
    log = tmp_home / "logs" / "integrations.jsonl"
    assert log.exists()
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    entry = lines[0]
    assert set(entry) == {"ts", "actor", "session", "action", "kind", "id"}
    assert entry["actor"] == "tool" and entry["action"] == "install"
    assert entry["kind"] == "skill" and entry["id"] == "quincy-weather"
    assert entry["session"] == "sess-42"


def test_read_actions_do_not_write_audit(fake_run, tmp_home):
    integrations.do_search("skill", "weather")
    integrations.do_list("plugin")
    assert not (tmp_home / "logs" / "integrations.jsonl").exists()


def test_command_install_logs_actor_command(fake_run, tmp_home):
    integrations.handle_command("@sid7 install plugin cool-hud")
    entry = json.loads((tmp_home / "logs" / "integrations.jsonl").read_text().splitlines()[0])
    assert entry["actor"] == "command" and entry["session"] == "sid7"
    assert entry["action"] == "install"


def test_command_disable_logs_disable_action(fake_run, tmp_home):
    integrations.handle_command("@sid7 disable plugin cool-hud")
    entry = json.loads((tmp_home / "logs" / "integrations.jsonl").read_text().splitlines()[0])
    assert entry["action"] == "disable"


# --------------------------------------------------------------------------- approval gate
def test_build_approval_names_kind_id_source_trust_and_fetch():
    d = integrations.build_approval(
        "integration_install", {"kind": "skill", "id": "quincy-weather"},
        resolve=lambda k, i: {"source": "clawhub", "trust": "community"})
    assert d["action"] == "approve"
    msg = d["message"]
    for token in ("skill", "quincy-weather", "clawhub", "community", "download"):
        assert token in msg
    assert d["rule_key"] == "integrations:install:skill"


def test_build_approval_falls_back_when_resolve_empty():
    d = integrations.build_approval(
        "integration_install", {"kind": "plugin", "id": "x"}, resolve=lambda k, i: {})
    # static per-kind description still names a source and trust
    assert "plugin" in d["message"] and "unaudited" in d["message"]


def test_build_approval_survives_resolver_raising():
    def boom(k, i):
        raise RuntimeError("network down")
    d = integrations.build_approval(
        "integration_install", {"kind": "mcp", "id": "notes"}, resolve=boom)
    assert d and "notes" in d["message"] and "Nous" in d["message"]


def test_build_approval_enable_vs_disable_rule_key():
    on = integrations.build_approval("integration_enable",
                                     {"kind": "plugin", "id": "p", "enabled": True},
                                     resolve=lambda k, i: {})
    off = integrations.build_approval("integration_enable",
                                      {"kind": "plugin", "id": "p", "enabled": False},
                                      resolve=lambda k, i: {})
    assert on["rule_key"].endswith("enable:plugin")
    assert off["rule_key"].endswith("disable:plugin")


def test_read_tools_are_not_gated():
    for tool in ("integration_search", "integration_inspect", "integration_list"):
        assert integrations.build_approval(tool, {"kind": "plugin", "id": "x"}) is None


# --------------------------------------------------------------------------- command parsing
def test_split_session():
    assert integrations.split_session("@abc search skill weather") == ("abc", "search skill weather")
    assert integrations.split_session("list plugin") == (None, "list plugin")


def test_command_requires_session_prefix():
    assert "@<session_id>" in integrations.handle_command("search skill weather")


def test_command_search_roundtrip(fake_run):
    out = json.loads(integrations.handle_command("@abc search skill weather"))
    assert out["ok"] and out["kind"] == "skill" and out["count"] == 2


def test_command_list_defaults_to_all(fake_run):
    out = json.loads(integrations.handle_command("@abc list"))
    assert out["kind"] == "all"


def test_command_unknown_verb(fake_run):
    out = json.loads(integrations.handle_command("@abc frobnicate plugin x"))
    assert out["ok"] is False and "unknown verb" in out["error"]


def test_command_empty_shows_usage(fake_run):
    assert "usage:" in integrations.handle_command("@abc")


# --------------------------------------------------------------------------- registration
def test_register_wires_seven_tools_hook_and_command():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) - {"mcp_add", "api_add", "connector_list", "connector_enable", "connector_remove", "connector_call"} == set(integrations.TOOLS)
    assert all(ctx.tools[t]["toolset"] == "integrations" for t in ctx.tools)
    assert "pre_tool_call" in ctx.hooks
    assert integrations.COMMAND_NAME in ctx.commands


def test_registered_hook_gates_only_mutations():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    gate = ctx.hooks["pre_tool_call"]
    assert gate("integration_install", {"kind": "skill", "id": "x"}, "")["action"] == "approve"
    assert gate("integration_search", {"kind": "skill", "query": "x"}, "") is None


def test_registered_command_never_raises(monkeypatch):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)

    def boom(_):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(integrations, "handle_command", boom)
    assert ctx.commands["jintegrations"]("@s list plugin").startswith("integrations error:")


def test_tool_schema_names_match_registration():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    for name, entry in ctx.tools.items():
        assert entry["schema"]["name"] == name


# --------------------------------------------------------------------------- catalogue error handling
def test_catalog_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(catalogs, "_run", lambda args, *, timeout, stdin=None: (2, "", "boom"))
    out = json.loads(integrations.handle_list({"kind": "plugin"}))
    assert out["ok"] is False and "catalogue error" in out["error"]


# --------------------------------------------------------------------------- contract
def test_matches_the_shared_contract():
    data = json.loads(CONTRACT.read_text())
    assert integrations.KINDS == data["kinds"]
    assert integrations.ACTIONS == data["actions"]
    assert integrations.GATED_TOOLS == data["gated_tools"]
    assert integrations.ROW_FIELDS == data["row_fields"]
    assert integrations.TOOLS == data["tools"]
    assert integrations.TOOLSET == data["toolset"]
    assert integrations.COMMAND_NAME == data["command"]["name"]
    assert integrations.COMMAND_VERBS == data["command"]["verbs"]
    assert integrations.AUDIT_LOG == data["audit_log"]
    assert integrations.COMMAND_SHAPES == data["command"]["shapes"]


def test_audit_fields_match_contract():
    data = json.loads(CONTRACT.read_text())
    # the fields audit() writes must equal the contract's audit_fields (order-free)
    assert set(data["audit_fields"]) == {"ts", "actor", "session", "action", "kind", "id"}
