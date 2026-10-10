"""Row-Bot's own accounts, channels and key tools in Apps: read from their owners (which stay
authoritative), listed as built-in ways to connect, and never sharing a credential with another
way to the same app. Fakes only: no service is contacted and no channel sends anything."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot import api_keys, github_account, secret_store
from row_bot.application import client_integrations as api
from row_bot.integrations import builtin, facts, plans
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import auth, config
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]
_SHARED_GITHUB_STATUS = github_account.shared_github_status  # The real one, before any fixture replaces it.


class SilentChannel:
    """A configured channel that fails the test if anything tries to send through it."""

    def __getattr__(self, name):
        if name.startswith("send"):
            raise AssertionError("a read sent a channel message")
        raise AttributeError(name)


@pytest.fixture
def owners(monkeypatch):
    state = {"google": "connected", "x": "not_configured", "github": "not_configured",
             "keys": {"TAVILY_API_KEY"}, "enabled": {"gmail", "calendar", "web_search"},
             "channels": [{"channel_id": "telegram", "display_name": "Telegram", "source": {"kind": "core"},
                           "configured": True, "running": True, "reachability_problem": None},
                          {"channel_id": "slack", "display_name": "Slack", "source": {"kind": "core"},
                           "configured": False, "running": False, "reachability_problem": None},
                          {"channel_id": "rss", "display_name": "RSS", "source": {"kind": "plugin", "plugin_id": "rss"},
                           "configured": True, "running": True, "reachability_problem": None},
                          # Ready to link but never linked: "configured" by its owner, not set up by anyone.
                          {"channel_id": "whatsapp", "display_name": "WhatsApp", "source": {"kind": "core"},
                           "configured": True, "running": False, "reachability_problem": None, "fields": [],
                           "paired_identities": [], "link_state": None}]}
    monkeypatch.setattr("row_bot.application.client_account_oauth.read_account_auth",
                        lambda account: {"state": state[account]})
    monkeypatch.setattr(github_account, "shared_github_status", lambda: SimpleNamespace(state=state["github"]))
    monkeypatch.setattr("row_bot.application.channel_controls.read_channels",
                        lambda **kwargs: {"items": state["channels"], "total": 3, "truncated": False})
    monkeypatch.setattr(api_keys, "key_status", lambda name: {"configured": name in state["keys"]})
    monkeypatch.setattr(builtin, "_tool_on", lambda name: name in state["enabled"])
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    return state


def test_accounts_channels_and_key_tools_appear_as_apps_with_their_owners_status(owners):
    rows = {row["id"]: row for row in builtin.rows()}
    google = rows["builtin:account:google"]
    assert (google["lifecycle"], google["readiness"], google["app"]["id"]) == ("installed", "ready", "google")
    assert google["source"] == "builtin" and not google["verified"]  # "Built in", never "by Google".
    assert rows["builtin:account:x"]["next_action"] == {"kind": "set_up", "label": "Set up"}
    assert (rows["builtin:channel:telegram"]["lifecycle"], rows["builtin:channel:slack"]["lifecycle"]) == ("installed", "available")
    assert "builtin:channel:rss" not in rows  # A package's own channel is listed with its package.
    assert rows["builtin:channel:whatsapp"]["lifecycle"] == "available"  # Not yours until it is linked.
    assert rows["builtin:tool:web_search"]["app"]["id"] == "tavily"
    assert rows["builtin:tool:wolfram_alpha"]["lifecycle"] == "available"  # No key yet.
    # Only a way that brings chat tools offers "Try it": the GitHub account serves skills, a channel its own app.
    owners["github"] = "connected"
    github = builtin.read("builtin:account:github")
    assert (github["readiness"], github["next_action"]["kind"]) == ("ready", "none")
    assert rows["builtin:channel:telegram"]["next_action"]["kind"] == "none"
    assert google["next_action"] == {"kind": "try", "label": "Try it"}
    owners["google"] = "expired"
    assert builtin.read("builtin:account:google")["next_action"] == {"kind": "sign_in", "label": "Sign in again"}
    owners["enabled"].discard("gmail"), owners["enabled"].discard("calendar")
    owners["google"] = "connected"
    assert builtin.read("builtin:account:google")["lifecycle"] == "off"


def test_your_apps_include_set_up_ways_and_each_opens_without_a_plan(owners):
    installed = {row["id"] for row in api.read_items(owner_id="owner", kind="app")["items"]}
    assert {"builtin:account:google", "builtin:channel:telegram", "builtin:tool:web_search"} <= installed
    assert "builtin:channel:slack" not in installed  # Not set up: found by searching, not listed as yours.
    detail, plan = api.read_item(owner_id="owner", item_id="builtin:channel:slack")
    assert plan is None and detail["entry"]["kind"] == "builtin" and detail["about"]["actions"] == []
    with pytest.raises(plans.PlanError):  # Nothing in Apps can change it but its own page.
        api.start_plan(plans.Context("owner", "owner", lambda: None), plan_id=str(uuid4()), item_id="builtin:channel:slack",
                       digest="0" * 64)


def test_an_app_shows_every_way_to_connect_in_its_own_order(owners, tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    detail, _ = api.read_item(owner_id="owner", item_id="mcp:curated:slack-mcp")
    ways = [(way["id"], way["method"]) for way in detail["about"]["ways"]]
    assert ways[0] == ("mcp:curated:slack-mcp", "hosted_sign_in") and detail["about"]["ways"][0]["recommended"]
    assert ("builtin:channel:slack", "built_in") in ways  # Talking to Row-Bot from Slack is a way too.
    google, _ = api.read_item(owner_id="owner", item_id="builtin:account:google")
    assert google["about"]["ways"][0]["id"] == "builtin:account:google" and google["about"]["ways"][0]["recommended"]


def test_a_way_already_set_up_is_listed_on_its_apps_page_as_yours_once(owners, tmp_path, monkeypatch):
    """One app with its ways: the one you added opens as what you added (its page says so), never twice."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "_config_cache", None)
    config.CONFIG_PATH.write_text(json.dumps({"version": 1, "enabled": True, "servers": {"Notion MCP": {
        "transport": "streamable_http", "url": "https://mcp.notion.com/mcp", "enabled": True,
        "source": {"marketplace": "curated", "id": "makenotion-notion-mcp-server"}}}}))
    facts.invalidate()
    try:
        added = next(row for row in facts.inventory()[0] if row["name"] == "Notion MCP")
        detail, _ = api.read_item(owner_id="owner", item_id="mcp:curated:makenotion-notion-mcp-server")
        ids = [way["id"] for way in detail["about"]["ways"]]
        assert detail["entry"]["id"] == added["id"] and ids[0] == added["id"]
        assert "mcp:curated:makenotion-notion-mcp-server" not in ids and len(ids) == len(set(ids))
    finally:
        facts.invalidate()


@pytest.fixture
def keychain():
    keyring = MemoryKeyring()
    secret_store._set_backend_for_tests(keyring)
    yield keyring
    secret_store._set_backend_for_tests(None)


def test_the_github_account_token_and_githubs_hosted_connection_never_share_a_credential(owner, keychain, monkeypatch):
    """Two ways to one app keep two credentials: connecting GitHub's hosted server never reads or
    writes the GitHub account's token, and the account never picks up the connection's."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    api_keys.set_key("GITHUB_TOKEN", "account-token-1111")
    monkeypatch.setattr(auth, "discover_sign_in", lambda url: {"required": False})
    read = []
    original = api_keys.get_key
    monkeypatch.setattr(api_keys, "get_key", lambda name: read.append(name) or original(name))
    _, plan = api.read_item(owner_id="owner", item_id="mcp:curated:github-hosted")
    context = plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=True,
                            inputs={"token": "connection-token-2222", "read_only": "true"})
    paused = api.start_plan(context, plan_id=str(uuid4()), item_id="mcp:curated:github-hosted", digest=plan["digest"])
    assert paused["pause"] == "access", paused
    name, saved = next((name, cfg) for name, cfg in config.read_saved_configuration().document["servers"].items()
                       if "githubcopilot" in str(cfg.get("url")))
    stored = auth.read_credentials(saved["auth"]["credential_ref"])
    assert stored["values"] == {"token": "connection-token-2222"}
    launch, _ = auth.transport_options(name, saved)
    assert launch["headers"]["Authorization"] == "Bearer connection-token-2222"
    assert "GITHUB_TOKEN" not in read  # The connection never looked at the account's token.
    assert api_keys.get_key("GITHUB_TOKEN") == "account-token-1111"  # Nor replaced it.
    assert github_account.resolve_github_token(include_cli=False, use_cache=False).value == "account-token-1111"
    account = [value for (_, name), value in keychain.values.items() if name.endswith("GITHUB_TOKEN")]
    assert account == ["account-token-1111"] and "connection-token-2222" not in json.dumps(account)
    facts.invalidate()


def test_list_reads_serve_one_snapshot_until_an_owner_changes(owners, keychain, monkeypatch):
    """Apps, the catalog and the composer read the snapshot: no keychain, no saved sign-in, no GitHub
    CLI on a list read. A saved key (or any owner change) makes the next read see it."""
    assert {row["id"]: row["lifecycle"] for row in builtin.rows()}["builtin:tool:wolfram_alpha"] == "available"

    def untouchable(*args, **kwargs):
        raise AssertionError("a list read went to an owner")
    for target in ("row_bot.application.client_account_oauth.read_account_auth",
                   "row_bot.application.channel_controls.read_channels"):
        monkeypatch.setattr(target, untouchable)
    monkeypatch.setattr(github_account, "shared_github_status", untouchable)
    monkeypatch.setattr(api_keys, "key_status", untouchable)
    for _ in range(3):
        rows = {row["id"]: row for row in builtin.rows()}
    assert rows["builtin:account:google"]["readiness"] == "ready"
    rows["builtin:account:google"]["blockers"].append("mutated")  # A caller's copy never changes the snapshot.
    assert {row["id"]: row for row in builtin.rows()}["builtin:account:google"]["blockers"] == []

    monkeypatch.undo()
    owners.update(google="connected")
    monkeypatch.setattr("row_bot.application.client_account_oauth.read_account_auth", lambda account: {"state": owners[account]})
    monkeypatch.setattr(github_account, "shared_github_status", lambda: SimpleNamespace(state="not_configured"))
    monkeypatch.setattr("row_bot.application.channel_controls.read_channels", lambda **kwargs: {"items": []})
    monkeypatch.setattr(builtin, "_tool_on", lambda name: True)
    monkeypatch.setattr(api_keys, "key_status", lambda name: {"configured": True})
    assert {row["id"]: row["lifecycle"] for row in builtin.rows()}["builtin:tool:wolfram_alpha"] == "available"
    monkeypatch.setattr(secret_store, "_change_listeners", [])  # Even with no one told (the module reloaded)...
    secret_store.set_secret("WOLFRAM_ALPHA_APPID", "a-key")  # ...the keychain counts that something changed.
    assert {row["id"]: row["lifecycle"] for row in builtin.rows()}["builtin:tool:wolfram_alpha"] == "installed"


def test_catalog_search_never_waits_for_the_owners(owners, monkeypatch):
    """Before the first snapshot (a GitHub CLI can take seconds), search lists the ways without status
    and builds the snapshot in the background."""
    import threading
    release, started = threading.Event(), threading.Event()

    def slow_github():
        started.set()
        release.wait(10)
        return SimpleNamespace(state="connected")
    monkeypatch.setattr(github_account, "shared_github_status", slow_github)
    try:
        listed = {row["id"]: row for row in builtin.rows(wait=False)}
        assert listed["builtin:account:github"]["lifecycle"] == "available"
        assert started.wait(10)  # The background build is asking the owners.
    finally:
        release.set()
    assert {row["id"]: row for row in builtin.rows()}["builtin:account:github"]["lifecycle"] == "installed"


def test_no_read_starts_the_github_cli_and_an_explicit_check_does(owners, keychain, tmp_path, monkeypatch):
    """Signed in only through the GitHub CLI: Apps' list, detail and catalog search, the composer, a chat's
    Connect card and the start-up prewarm never start it, and say "Check GitHub" rather than guess; the
    explicit Check asks it, and every read then shows what that check found."""
    import threading

    from row_bot.application import client_accounts
    from row_bot.integrations import scope

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(github_account, "shared_github_status", _SHARED_GITHUB_STATUS)  # The real one.
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")  # No token of Row-Bot's own.
    monkeypatch.setattr("row_bot.developer.executables.resolve_github_cli", lambda: "fixture-gh")
    monkeypatch.setattr(client_accounts, "resolve_github_cli", lambda: "fixture-gh")
    asked = []  # Every time the (fake) GitHub CLI is started.
    monkeypatch.setattr(github_account, "_github_cli_token", lambda *a, **k: asked.append("auth token") or "cli-token")
    monkeypatch.setattr(github_account, "_github_cli_status", lambda: asked.append("auth status") or SimpleNamespace(
        installed=True, authenticated=True, user="octo", path="fixture-gh"))
    monkeypatch.setattr(github_account, "check_github_token_access", lambda token, source="", timeout=10: (
        github_account.GitHubAccountStatus(connected=True, source=token.source, fingerprint=token.fingerprint, user="octo",
                                           state="connected", authenticated=True, token_valid=True)))
    monkeypatch.setattr(github_account, "check_github_anonymous_access", lambda timeout=10: pytest.fail("not anonymous"))
    monkeypatch.setattr(scope, "_mcp_items", lambda strict=False: [])
    monkeypatch.setattr(scope, "_profile_allow", lambda conversation_id: None)
    monkeypatch.setattr("row_bot.threads.get_thread_apps_off", lambda conversation_id: [])
    github_account.clear_github_caches()
    try:
        builtin.changed()
        api.read_items(owner_id="owner", scope="catalog", kind="app", query="github", limit=50)  # Builds in the background.
        for thread in [thread for thread in threading.enumerate() if thread.name == "builtin-ways-refresh"]:
            thread.join(10)
        listed = {row["id"]: row for row in api.read_items(owner_id="owner", kind="app")["items"]}
        detail, _ = api.read_item(owner_id="owner", item_id="builtin:account:github")
        composer = scope.chat_apps("fixture-chat")
        card = scope.app_card("builtin:account:github")
        builtin.changed()
        builtin.rows()  # What the start-up prewarm (app._prewarm_apps_background) runs.
        settings = client_accounts.read_github_access(owner_id="owner")
        assert asked == []
        row = detail["entry"]
        assert listed["builtin:account:github"]["next_action"] == row["next_action"] == {"kind": "continue_setup",
                                                                                        "label": "Continue setup"}
        assert "Check GitHub" in row["blockers"][0]["message"]  # Not "connected", and not silently "not connected".
        assert "builtin:account:github" not in {app["item_id"] for app in composer} and card["name"]
        assert (settings["state"], settings["credential_source"]) == ("configured_unchecked", "github_cli")

        checked = client_accounts.execute_github_access(owner_id="owner", command_id=str(uuid4()),
                                                        expected_revision=settings["revision"], action="check")
        assert checked["snapshot"]["state"] == "connected" and sorted(asked) == ["auth status", "auth token"]
        asked.clear()
        assert builtin.read("builtin:account:github")["readiness"] == "ready"  # The check's verdict, read again.
        assert {row["id"]: row for row in builtin.rows()}["builtin:account:github"]["readiness"] == "ready"
        assert client_accounts.read_github_access(owner_id="owner")["state"] == "connected"
        assert asked == []
    finally:
        github_account.clear_github_caches()
        client_accounts._GITHUB.pop("owner", None)
        builtin.changed()


def test_a_lookup_without_the_github_cli_never_makes_the_next_one_ask_it_again(monkeypatch):
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(github_account.api_keys, "get_key", lambda _name: "")
    asked = []
    monkeypatch.setattr(github_account, "_github_cli_token", lambda: asked.append(1) or "cli-token")
    github_account.clear_github_token_cache()
    try:
        for _ in range(3):
            assert github_account.resolve_github_token(include_cli=True).value == "cli-token"
            assert github_account.resolve_github_token(include_cli=False).value == ""
        assert asked == [1]
    finally:
        github_account.clear_github_token_cache()


def test_an_apps_card_opens_its_recommended_way_built_in_first_when_recommended(owners, tmp_path, monkeypatch):
    """One card per app, opening the way the app recommends: Tavily's is Row-Bot's own web search
    (a key tool), GitHub's is GitHub's hosted server; the others are on the app's page."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    builtin.rows()  # The snapshot exists, as after start-up.
    page = api.read_items(owner_id="owner", scope="catalog", kind="app", query="tavily", limit=50)
    tavily = next(item for item in page["items"] if (item["app"] or {}).get("id") == "tavily")
    assert (tavily["id"], tavily["method"]) == ("builtin:tool:web_search", "built_in")
    # Named for what it is, as Settings › Tools names the same switch, and still found by the job.
    assert tavily["name"] == "Tavily web search"
    page = api.read_items(owner_id="owner", scope="catalog", kind="app", query="web search", limit=50)
    assert "builtin:tool:web_search" in [item["id"] for item in page["items"]]
    detail, _ = api.read_item(owner_id="owner", item_id=tavily["id"])
    assert detail["about"]["ways"][0]["id"] == "builtin:tool:web_search" and detail["about"]["ways"][0]["recommended"]
    page = api.read_items(owner_id="owner", scope="catalog", kind="app", query="github", limit=50)
    github = next(item for item in page["items"] if (item["app"] or {}).get("id") == "github")
    assert github["id"] == "mcp:curated:github-hosted" and github["verified"]
