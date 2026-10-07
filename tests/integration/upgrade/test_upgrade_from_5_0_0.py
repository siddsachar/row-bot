"""Upgrading from Row-Bot 5.0.0 keeps everything a person had.

Each test starts from a data folder shaped as 5.0.0 left it (``tests/fixtures/data_dirs/v5_0_0``: the
files, places and JSON shapes 5.0.0 wrote, read from the ``v5.0.0`` tag) and what 5.0.0 kept in the
system keychain (a fake keychain here). Every value is synthetic. Nothing is started, contacted or sent:
the suite's guard fails a test that reaches the network.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot import account_tokens, agent_profiles, api_keys, github_account, secret_store
from row_bot.application import client_integrations as api
from row_bot.integrations import facts
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.integration, pytest.mark.platform]

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "data_dirs" / "v5_0_0"
OWNER = "local-owner"
# What 5.0.0 kept in the keychain for this folder: saved keys, Telegram's channel secrets, a package's key.
KEYS = {"X_CLIENT_ID": "fixture-x-client-id", "X_CLIENT_SECRET": "fixture-x-client-secret",
        "GITHUB_TOKEN": "fixture-github-token", "TAVILY_API_KEY": "fixture-tavily-key"}
CHANNEL_SECRETS = {"TELEGRAM_BOT_TOKEN": "fixture-telegram-bot-token", "TELEGRAM_USER_ID": "123456789"}
PACKAGE_SECRET = "fixture-notes-api-key"
SIGN_IN_FILES = ("gmail/token.json", "calendar/token.json", "gmail/credentials.json", "x/token.json")
SIGN_IN_SECRETS = ("fixture-gmail-access-token", "fixture-calendar-access-token", "fixture-google-refresh-token",
                   "fixture-client-secret", "fixture-x-access-token", "fixture-x-refresh-token")
# The owners' own files: a read of Apps or Skills never rewrites them.
OWNER_FILES = ("mcp_servers.json", "skills_config.json", "plugin_state.json", "plugin_secrets.json", "channels_config.json",
               "tools_config.json", "api_keys.json", "skills", "installed_plugins")


def _keychain_as_5_0_0(root: Path) -> None:
    for name, value in KEYS.items():
        secret_store.set_secret(name, value)
    # 5.0.0's api_keys.json holds metadata only. Written here rather than shipped: the repository ignores
    # every file of that name.
    (root / "api_keys.json").write_text(json.dumps({"version": 2, "storage": "keyring", "service": secret_store.SERVICE_NAME,
        "keys": {name: {"configured": True, "fingerprint": secret_store.fingerprint(value),
                        "updated_at": "2026-08-01T10:00:00+00:00"} for name, value in KEYS.items()}}), encoding="utf-8")
    for name, value in CHANNEL_SECRETS.items():
        secret_store.set_secret(name, value, namespace="channels:telegram")
    secret_store.set_secret("fixture-notes-pack:NOTES_API_KEY", PACKAGE_SECRET, namespace="plugin_secrets")


@pytest.fixture
def v5(tmp_path, monkeypatch, reload_for_data_dir):
    root = tmp_path / "data"
    shutil.copytree(FIXTURE, root)
    tools = root / "tools_config.json"  # 5.0.0 saved the Google client file's absolute path.
    tools.write_text(tools.read_text(encoding="utf-8").replace("{{DATA_DIR}}", root.as_posix()), encoding="utf-8")
    for name in ("GITHUB_TOKEN", "GH_TOKEN", *KEYS, *CHANNEL_SECRETS):
        monkeypatch.delenv(name, raising=False)
    keychain = MemoryKeyring()
    secret_store._set_backend_for_tests(keychain)
    reload_for_data_dir(root, "row_bot.tasks", "row_bot.api_keys", "row_bot.channels.config", "row_bot.mcp_client.config",
                        "row_bot.plugins.state", "row_bot.skills")
    monkeypatch.setattr(agent_profiles, "_SCHEMA_READY", False)  # Its tables go in this folder's tasks.db.
    _keychain_as_5_0_0(root)
    # Requirement checks read this computer: here it has the runtimes the saved servers need.
    monkeypatch.setattr(facts, "_requirements", lambda cfg: [])
    github_account.clear_github_token_cache()
    account_tokens._RETRY_AT.clear()
    facts.invalidate()
    yield SimpleNamespace(root=root, keychain=keychain)
    secret_store._set_backend_for_tests(None)
    github_account.clear_github_token_cache()
    account_tokens._RETRY_AT.clear()
    facts.invalidate()


def _owner_files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for name in OWNER_FILES
            for path in ([root / name] if (root / name).is_file() else (root / name).rglob("*")) if path.is_file()}


def _items(kind: str) -> dict[str, dict]:
    """The Apps (or Skills) list's items of one kind, by name."""
    page = api.read_items(owner_id=OWNER, kind=kind, limit=50)
    assert page["sources"] == []  # Every owner's inventory read; none is "unavailable".
    return {row["name"]: row for row in page["items"] if row["kind"] == kind}


def _access(item_id: str) -> dict[str, str]:
    detail, _ = api.read_item(owner_id=OWNER, item_id=item_id)
    return {tool["name"]: tool["state"] for tool in detail["about"]["access"]["tools"]}


def _never(*args, **kwargs):
    raise AssertionError("a read started something")


def test_mcp_servers_are_apps_that_keep_their_tool_switches_and_approvals_and_a_read_starts_nothing(v5, monkeypatch):
    from row_bot.mcp_client import runtime
    for name in ("_schedule", "discover_enabled_servers", "launch_server_owned", "probe_server"):
        monkeypatch.setattr(runtime, name, _never)
    before = _owner_files(v5.root)
    servers = _items("mcp")
    assert {name: row["lifecycle"] for name, row in servers.items()} == {
        "Fixture Notes": "installed", "Fixture Docs": "installed", "Playwright MCP": "installed", "Old Tool": "off"}
    # Nothing has connected yet (a read starts nothing): the only thing left is the connection itself.
    assert [b["code"] for b in servers["Fixture Notes"]["blockers"]] == ["not_connected"]
    assert [b["code"] for b in servers["Fixture Docs"]["blockers"]] == ["not_connected"]
    # Off in 5.0.0 and never tested there: off still, its tools to be checked before it is used.
    assert servers["Old Tool"]["next_action"]["label"] == "Continue setup"
    # Each tool as 5.0.0 left it: switched off, asking first (locked ones always ask), or used without asking.
    assert _access(servers["Fixture Notes"]["id"]) == {"search_notes": "use", "summarize_notes": "ask", "list_tags": "off",
                                                       "create_note": "ask", "delete_note": "off"}
    assert _access(servers["Fixture Docs"]["id"]) == {"search_docs": "use"}
    assert _access(servers["Playwright MCP"]["id"]) == {"browser_snapshot": "use", "browser_click": "off"}
    detail, _ = api.read_item(owner_id=OWNER, item_id=servers["Fixture Docs"]["id"])
    assert detail["about"]["destination"] == "https://docs.fixture.example"  # Never its path, which can hold a key.
    assert "fixture-header-secret" not in json.dumps(detail) + json.dumps(servers)  # A saved header never leaves.
    assert _owner_files(v5.root) == before

    # Once start-up has connected them, they read as ready to use.
    monkeypatch.setattr(runtime, "get_passive_server_statuses",
                        lambda names: {name: {"status": "connected"} for name in names})
    facts.invalidate()
    servers = _items("mcp")
    assert {name: row["readiness"] for name, row in servers.items() if row["lifecycle"] == "installed"} == {
        "Fixture Notes": "ready", "Fixture Docs": "ready",
        # 5.0.0 ran it with npx, which fetches the package at every start: Row-Bot asks once to keep a
        # reviewed copy (it still runs as before meanwhile).
        "Playwright MCP": "needs_runtime"}
    assert [b["code"] for b in servers["Playwright MCP"]["blockers"]] == ["package_preparation"]
    assert servers["Playwright MCP"]["app"]["id"] == "playwright"


def test_skills_keep_their_on_off_state_pins_and_source(v5):
    from row_bot import skills
    before = _owner_files(v5.root)
    library = {row["id"].removeprefix("skill:"): row for row in _items("skill").values()}
    on = {name for name, row in library.items() if row["lifecycle"] == "installed"}
    off = {name for name, row in library.items() if row["lifecycle"] == "off"}
    saved = json.loads((v5.root / "skills_config.json").read_text(encoding="utf-8"))["skills"]
    assert {"meeting_prep", "pdf_toolkit", "deep_research"} <= on and {"brain_dump", "humanizer"} <= off
    assert on == {name for name in library if saved[name]} and off == {name for name in library if not saved[name]}
    assert not {name for name in library if name.endswith("_guide")}  # Tool guides follow their tools, as before.
    assert skills.read_client_skills()["pinned"] == ["proactive_agent", "meeting_prep"]
    # The skill added from a catalog keeps its source, its files and its update check.
    detail, _ = api.read_item(owner_id=OWNER, item_id="skill:pdf_toolkit")
    assert detail["entry"]["source"] == "github" and "update" in detail["about"]["actions"]
    assert {item["path"] for item in detail["about"]["files"]} == {"SKILL.md", "references/fields.md"}
    assert library["meeting_prep"]["source"] == "user"
    assert _owner_files(v5.root) == before


def test_a_package_lists_with_its_included_items_without_running_its_code(v5, monkeypatch):
    from row_bot.mcp_client import runtime
    from row_bot.plugins import loader
    monkeypatch.setattr(loader, "load_plugins", _never)
    monkeypatch.setattr(runtime, "_schedule", _never)
    before = _owner_files(v5.root)
    package = _items("plugin")["Fixture Notes Pack"]  # 5.0.0 manifests name their skills by id only.
    assert (package["lifecycle"], package["version"], package["publisher"]) == ("installed", "1.2.0", "Fixture Publisher")
    assert {(child["kind"], child["name"]) for child in package["children"]} == {("skill", "notes_style"),
                                                                                ("mcp", "notes_server")}
    detail, _ = api.read_item(owner_id=OWNER, item_id=package["id"])
    assert detail["entry"]["id"] == package["id"] and PACKAGE_SECRET not in json.dumps(detail) + json.dumps(package)
    plugin = v5.root / "installed_plugins" / "fixture-notes-pack"
    assert not (plugin / "plugin-code-ran.txt").exists() and not (plugin / "server-ran.txt").exists()
    assert _owner_files(v5.root) == before


def test_google_and_x_sign_ins_move_into_the_keychain_once_verified_and_read_as_connected_ways(v5):
    saved = {path: json.loads((v5.root / path).read_text(encoding="utf-8")) for path in SIGN_IN_FILES}
    assert account_tokens.migrate() == {"migrated": 3, "kept": 0}  # As start-up does.
    assert not [path for path in SIGN_IN_FILES if (v5.root / path).exists()]
    # 5.0.0 signed Gmail and Calendar in together, then each refreshed its own copy for its own scope only: the
    # one grant is kept with both scopes, without an access token that serves only one of them.
    gmail, calendar = saved["gmail/token.json"], saved["calendar/token.json"]
    google = account_tokens.read("google")
    assert set(google.pop("scopes")) == {*gmail["scopes"], *calendar["scopes"]}
    assert google == {key: value for key, value in gmail.items() if key not in {"token", "expiry", "scopes"}}
    assert account_tokens.read("google_client") == saved["gmail/credentials.json"]
    assert account_tokens.read("x") == saved["x/token.json"]
    assert (v5.root / "x/tier_info.json").is_file()  # X's own working file is not a sign-in.
    assert not [path for path in v5.root.rglob("*") if path.is_file()
                and any(secret.encode() in path.read_bytes() for secret in SIGN_IN_SECRETS)]
    assert account_tokens.migrate() == {"migrated": 0, "kept": 0}  # Once.
    ways = _items("builtin")
    for name, app in (("Google account", "google"), ("X account", "x"), ("GitHub account", "github")):
        assert (ways[name]["lifecycle"], ways[name]["readiness"], ways[name]["app"]["id"]) == ("installed", "ready", app)
        assert ways[name]["source"] == "builtin"
    # Each kept its own credential: the X app's keys and the GitHub token are where 5.0.0 saved them.
    assert api_keys.get_key("X_CLIENT_ID") == KEYS["X_CLIENT_ID"]
    assert github_account.resolve_github_token(include_cli=False, use_cache=False).value == KEYS["GITHUB_TOKEN"]
    assert not any(secret in json.dumps(ways) for secret in (*SIGN_IN_SECRETS, *KEYS.values()))


def test_gmail_and_calendar_signed_in_apart_before_3_12_are_never_merged_and_google_asks_for_a_new_sign_in(v5):
    """A folder whose Gmail and Calendar each signed in on their own (before 3.12), which 5.0.0 kept as two
    grants: Gmail's moves, Calendar's file is never deleted, and Google reads as needing a new sign-in."""
    calendar = v5.root / "calendar/token.json"
    own = {**json.loads(calendar.read_text(encoding="utf-8")), "refresh_token": "fixture-calendar-refresh-token"}
    calendar.write_text(json.dumps(own), encoding="utf-8")
    assert account_tokens.migrate() == {"migrated": 3, "kept": 1}
    assert json.loads(calendar.read_text(encoding="utf-8")) == own and not (v5.root / "gmail/token.json").exists()
    assert account_tokens.read("google")["refresh_token"] == "fixture-google-refresh-token"
    google = _items("builtin")["Google account"]
    assert (google["readiness"], google["next_action"]["label"]) == ("needs_sign_in", "Sign in again")


def test_a_sign_in_the_keychain_cannot_keep_stays_in_its_file_and_keeps_working(v5):
    originals = {path: (v5.root / path).read_bytes() for path in SIGN_IN_FILES}
    v5.keychain.fail = True
    assert account_tokens.migrate() == {"migrated": 0, "kept": 3}
    assert {path: (v5.root / path).read_bytes() for path in SIGN_IN_FILES} == originals  # Nothing deleted.
    assert account_tokens.read("google") is not None and account_tokens.read("x") is not None  # Read-only meanwhile.
    v5.keychain.fail = False
    account_tokens._RETRY_AT.clear()  # The next start.
    assert account_tokens.migrate() == {"migrated": 3, "kept": 0}


def test_a_configured_channel_is_a_built_in_way_with_its_settings_and_nothing_starts_or_sends(v5):
    """Sending or starting would reach Telegram: the suite's network guard fails the test."""
    from row_bot.application.channel_controls import read_channels
    before = _owner_files(v5.root)
    way = _items("builtin")["Telegram channel"]
    # Saved and set to start with Row-Bot, as in 5.0.0; not running until start-up starts it.
    assert (way["lifecycle"], way["readiness"], way["app"]["id"], way["blockers"]) == ("off", "ready", "telegram", [])
    assert way["next_action"]["label"] == "Turn on"
    detail, plan = api.read_item(owner_id=OWNER, item_id=way["id"])
    assert plan is None and detail["entry"]["id"] == "builtin:channel:telegram"
    channel = next(item for item in read_channels()["items"] if item["channel_id"] == "telegram")
    assert {field["key"]: field["configured"] for field in channel["fields"]} == {"bot_token": True, "user_id": True}
    assert channel["running"] is False
    assert not any(secret in json.dumps(channel) + json.dumps(detail) for secret in CHANNEL_SECRETS.values())
    assert _owner_files(v5.root) == before


def test_monitor_fixes_kept_by_5_0_0_open_the_pages_that_replaced_accounts_channels_mcp_and_plugins(v5):
    from row_bot.application.client_diagnosis import read_system_health
    fixes = {check["id"]: check["fix"]["href"] for check in read_system_health()["checks"]}
    assert fixes == {"gmail-oauth": "/settings/apps/google", "calendar-oauth": "/settings/apps/google",
                     "x-oauth": "/settings/apps/x", "github": "/settings/apps/github",
                     "channel:telegram": "/settings/apps/telegram", "mcp": "/settings/apps", "plugins": "/settings/apps",
                     "skills": "/settings/skills"}


@pytest.mark.slow  # Starts the client API (about 1.5 s); the reads above stay in the pull request lane.
def test_the_apps_api_serves_a_5_0_0_folder(service, v5):  # noqa: F811
    from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for
    from tests.subsystem.client_protocol.test_protocol_security import bootstrap
    with client_for(service) as client:
        _, headers = bootstrap(client)
        response = client.get("/api/v1/integrations/items", params={"kind": "app"}, headers=headers)
        assert response.status_code == 200, response.text
        items = {row["name"]: row for row in response.json()["items"]}
        assert {"Fixture Notes", "Fixture Docs", "Playwright MCP", "Old Tool", "Fixture Notes Pack", "Google account",
                "X account", "GitHub account", "Telegram channel", "Web search"} <= set(items)
        for name in ("Fixture Notes Pack", "Fixture Notes", "Google account"):
            detail = client.get("/api/v1/integrations/detail", params={"item_id": items[name]["id"]}, headers=headers)
            assert detail.status_code == 200, detail.text
        skills = client.get("/api/v1/integrations/items", params={"kind": "skill"}, headers=headers)
        assert {"Meeting prep", "Pdf Toolkit"} <= {row["name"] for row in skills.json()["items"]}
        text = response.text + skills.text
        assert not any(secret in text for secret in (*SIGN_IN_SECRETS, *KEYS.values(), PACKAGE_SECRET,
                                                      "fixture-header-secret"))


@pytest.mark.slow  # Three owners' changes (about 1.5 s).
def test_items_from_5_0_0_turn_off_from_apps_and_keep_their_other_settings(v5, monkeypatch):
    from uuid import uuid4
    from row_bot.integrations import plans
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(runtime, "_schedule", _never)
    servers_before = json.loads((v5.root / "mcp_servers.json").read_text(encoding="utf-8"))["servers"]
    context = plans.Context(OWNER, OWNER, lambda: None, local_owner=True)
    for name, kind in (("Fixture Notes", "mcp"), ("Meeting prep", "skill"), ("Fixture Notes Pack", "plugin")):
        item_id = _items(kind)[name]["id"]
        _, plan = api.read_item(owner_id=OWNER, item_id=item_id, intent="turn_off")
        done = api.start_plan(context, plan_id=str(uuid4()), item_id=item_id, intent="turn_off", digest=plan["digest"])
        assert done["state"] == "completed", done
        facts.invalidate()
        assert _items(kind)[name]["lifecycle"] == "off"
    servers = json.loads((v5.root / "mcp_servers.json").read_text(encoding="utf-8"))["servers"]
    assert servers["Fixture Notes"] == {**servers_before["Fixture Notes"], "enabled": False}  # Tool policy kept.
    assert {name: cfg for name, cfg in servers.items() if name != "Fixture Notes"} == {
        name: cfg for name, cfg in servers_before.items() if name != "Fixture Notes"}
    assert json.loads((v5.root / "skills_config.json").read_text(encoding="utf-8"))["skills"]["meeting_prep"] is False
    package = json.loads((v5.root / "plugin_state.json").read_text(encoding="utf-8"))["fixture-notes-pack"]
    assert package["enabled"] is False and package["config"] == {"workspace": "fixture-workspace"}
    assert not (v5.root / "installed_plugins" / "fixture-notes-pack" / "plugin-code-ran.txt").exists()
