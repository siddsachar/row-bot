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
    detail, _ = api.read_item(owner_id="owner", item_id=tavily["id"])
    assert detail["about"]["ways"][0]["id"] == "builtin:tool:web_search" and detail["about"]["ways"][0]["recommended"]
    page = api.read_items(owner_id="owner", scope="catalog", kind="app", query="github", limit=50)
    github = next(item for item in page["items"] if (item["app"] or {}).get("id") == "github")
    assert github["id"] == "mcp:curated:github-hosted" and github["verified"]
