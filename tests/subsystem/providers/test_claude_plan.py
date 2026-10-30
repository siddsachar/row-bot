"""Claude (your Claude plan) runs the person's own Claude Code for every call (N1).

A fake ``claude`` on PATH (``tests.helpers.fake_claude``) answers the commands Row-Bot runs and replays
recorded stream-json; Row-Bot's lookup, process handling, frames, tool server and stream reader are real.
Nothing reaches a network or a real Claude Code.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import psutil
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from row_bot.providers import claude_subscription as claude
from row_bot.providers import config
from tests.helpers.fake_claude import error_reply, install_fake_claude, text_reply, tool_reply
from tests.subsystem.providers.test_provider_settings_controls import store as store  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

SONNET = "claude-sonnet-5"


@pytest.fixture(autouse=True)
def model_listings(monkeypatch):
    """Claude Code's status asked afresh per test, no model catalog yet, and a requested background model
    listing only recorded."""
    import row_bot.models as models
    from row_bot.providers import model_catalog_cache

    requested: list[dict] = []
    monkeypatch.setattr(models, "_cloud_model_cache", {})
    monkeypatch.setattr(model_catalog_cache, "start_model_catalog_refresh_background",
                        lambda **kwargs: requested.append(kwargs) or True)
    claude.forget_claude_plan_status()
    yield requested
    claude.forget_claude_plan_status()


def _model(**kwargs):
    from row_bot.providers.transports.claude_plan_cli import ChatClaudePlan

    return ChatClaudePlan(model_name=kwargs.pop("model_name", SONNET), **kwargs)


def _card():
    from row_bot.providers.live_settings import read_live_provider_cards

    return next(card for card in read_live_provider_cards()["providers"] if card["provider_id"] == "claude_subscription")


# ── Settings › Providers: what Claude Code says ─────────────────────────────


@pytest.mark.parametrize(("setup", "state", "words"), [
    ({"installed": False}, "not_installed", ""),
    ({"auth": {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}, "auth_exit": 1},
     "signed_out", ""),
    ({"auth": {"loggedIn": True, "authMethod": "console", "apiProvider": "firstParty"}}, "signed_out",
     "without a Claude plan"),
    ({"version": "2.1.100 (Claude Code)"}, "too_old", ""),
    ({"version": "not a version"}, "error", "didn't say which version"),
    ({}, "ready", ""),
])
def test_settings_shows_what_claude_code_says(store, tmp_path, monkeypatch, model_listings, setup, state, words):
    install_fake_claude(tmp_path, monkeypatch, **setup)

    card = _card()

    plan = card["claude_plan"]
    assert plan["state"] == state
    assert plan["minimum_version"] == claude.MINIMUM_CLAUDE_CODE_VERSION
    assert words in plan["detail"]
    assert card["configured"] is card["runtime_enabled"] is (state == "ready")
    if state == "ready":
        assert plan["plan"] == "Claude Max" and plan["version"] == "2.1.282"
        # Just set up, no model list yet: Claude Code is asked for it in the background.
        assert model_listings == [{"reason": "claude_plan_ready", "provider_id": "claude_subscription", "force": True}]
    else:
        assert model_listings == []
    if state == "too_old":
        assert plan["version"] == "2.1.100"
    # Never the program's path, an account or a credential.
    assert str(tmp_path) not in repr(card)


def test_settings_reads_claude_code_afresh_after_a_sign_in_in_a_terminal(store, tmp_path, monkeypatch):
    fake = install_fake_claude(tmp_path, monkeypatch, auth={"loggedIn": False}, auth_exit=1)
    assert _card()["claude_plan"]["state"] == "signed_out"
    config_file = fake.bin_dir / "config.json"
    config_file.write_text(config_file.read_text(encoding="utf-8").replace(
        '"auth": {"loggedIn": false}', '"auth": {"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "pro"}'
    ).replace('"auth_exit": 1', '"auth_exit": 0'), encoding="utf-8")

    assert _card()["claude_plan"]["plan"] == "Claude Pro"


# ── A model call ─────────────────────────────────────────────────────────────


def test_a_reply_streams_and_the_conversation_goes_in_as_frames(store, tmp_path, monkeypatch):
    fake = install_fake_claude(tmp_path, monkeypatch, reply=text_reply("Friday has a dentist visit.", thinking="Read it."))
    seen = []

    chunks = list(_model().stream([
        SystemMessage(content="You are Row-Bot."),
        HumanMessage(content="What's on Friday?"),
        AIMessage(content="", tool_calls=[{"name": "read_calendar", "args": {"day": "Friday"}, "id": "toolu_1",
                                           "type": "tool_call"}]),
        ToolMessage(content="Dentist at 3pm", tool_call_id="toolu_1", name="read_calendar"),
    ]))
    for chunk in chunks:
        seen.append(chunk.content)

    assert "".join(seen) == "Friday has a dentist visit."
    assert sum(chunk.content != "" for chunk in chunks) > 1  # streamed, not one block at the end
    [run] = fake.model_runs()
    assert run["argv"] == [
        "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--include-partial-messages", "--model", SONNET, "--tools", "", "--max-turns", "1",
        "--permission-mode", "dontAsk", "--no-session-persistence", "--setting-sources", "",
        "--strict-mcp-config", "--mcp-config", run["argv"][20], "--system-prompt-file", run["argv"][22],
        "--disable-slash-commands"]
    assert run["system"] == "You are Row-Bot."
    assert [(frame["type"], frame.get("shouldQuery")) for frame in run["frames"]] == [
        ("user", False), ("assistant", None), ("user", None)]
    assert run["frames"][1]["message"]["content"][0]["name"] == "mcp__rowbot__read_calendar"
    # Claude Code ran in Row-Bot's own empty folder, and the call's files are gone.
    assert run["cwd"] == str(claude.claude_code_workdir())
    assert not os.path.exists(run["argv"][22])


def test_tool_calls_come_back_under_row_bots_names_and_claude_code_never_runs_them(store, tmp_path, monkeypatch):
    long_name = "mcp_google_workspace_calendar_events_list_for_a_shared_calendar"
    from row_bot.providers.transports.claude_plan_cli import wire_tool_name

    ran = []

    def read_calendar(day: str) -> str:
        """Read the calendar for a day."""
        ran.append(day)
        return "Dentist"

    def shared(calendar: str) -> str:
        """List a shared calendar's events."""
        ran.append(calendar)
        return "none"

    fake = install_fake_claude(tmp_path, monkeypatch, call_tool=True,
                               reply=tool_reply(wire_tool_name(long_name), {"calendar": "team"}))
    tools = [StructuredTool.from_function(read_calendar),
             StructuredTool.from_function(shared, name=long_name)]

    started = time.monotonic()
    message = _model().bind_tools(tools).invoke([HumanMessage(content="What's on the team calendar?")])

    assert message.tool_calls == [{"name": long_name, "args": {"calendar": "team"}, "id": "toolu_synthetic01",
                                   "type": "tool_call"}]
    assert ran == []  # nothing ran: the call goes back to Row-Bot's own loop
    [run] = fake.model_runs()
    assert [tool["name"] for tool in run["listed"]] == ["read_calendar", wire_tool_name(long_name)]
    assert run["listed"][0]["inputSchema"]["properties"]["day"]["type"] == "string"
    # The tool server refuses every call, whatever Claude Code does.
    assert run["tool_call"]["isError"] is True and "Nothing ran here" in run["tool_call"]["content"][0]["text"]
    # Claude Code was stopped at the model's own stop, not left to try another turn.
    assert time.monotonic() - started < 30
    for pid in (run["pid"], run["server_pid"]):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_effort_and_an_output_cap_reach_claude_code_as_its_own_flags(store, tmp_path, monkeypatch):
    from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan, ReasoningSelection

    fake = install_fake_claude(tmp_path, monkeypatch)
    plan = ReasoningRequestPlan(f"model:claude_subscription:{SONNET}", ReasoningSelection(kind="effort", effort="xhigh"),
                                ReasoningCapabilities(supported_efforts=("low", "xhigh"), request_style="anthropic"))

    _model(reasoning_plan=plan).bind(max_tokens=200).invoke([HumanMessage(content="Hi")])

    [run] = fake.model_runs()
    assert run["argv"][-2:] == ["--effort", "xhigh"]
    assert run["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "200"


def test_background_work_and_documents_run_through_claude_code_without_tools(store, tmp_path, monkeypatch):
    from row_bot.providers import runtime
    from row_bot.providers.background import background_model_for, background_reasoning

    fake = install_fake_claude(tmp_path, monkeypatch, reply=text_reply("no"))
    ref = f"model:claude_subscription:{SONNET}"

    answer = background_model_for("contradiction", thread_model=ref).invoke([HumanMessage(content="Contradicts?")])
    # Document processing captures the conversation's model at the lowest effort, as below.
    captured = runtime.capture_chat_runtime(ref, reasoning_plan=background_reasoning(ref).plan)
    with runtime.captured_chat_model(captured, validate=lambda: None, usage_purpose="document") as model:
        document = model.invoke([HumanMessage(content="Summarise this page.")])

    assert answer.content == "no" and document.content == "no"
    background, documents = fake.model_runs()
    for run in background, documents:
        assert run.get("listed") is None  # no tool server at all
        assert "--effort" in run["argv"]  # the lowest effort, as for every background call
    assert background["argv"][background["argv"].index("--effort") + 1] == "low"
    assert int(background["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]) >= 160


def test_a_document_job_needs_a_claude_code_that_can_take_it(store, tmp_path, monkeypatch):
    from row_bot.providers import runtime

    install_fake_claude(tmp_path, monkeypatch, auth={"loggedIn": False}, auth_exit=1)

    with pytest.raises(ValueError, match="document_provider_unavailable"):
        runtime.capture_chat_runtime(f"model:claude_subscription:{SONNET}")


# ── When Claude Code can't answer ────────────────────────────────────────────


@pytest.mark.parametrize(("setup", "expected", "words"), [
    ({"installed": False}, "ClaudePlanUnavailable",
     "Your Claude plan isn't set up. Choose Use my Claude plan in Settings › Providers › Claude (your Claude plan)."),
    ({"version": "2.1.100 (Claude Code)"}, "ClaudePlanUnavailable",
     "Row-Bot needs Claude Code 2.1.263 or newer. Update Claude Code in Settings › Providers"),
    ({"auth": {"loggedIn": False}, "auth_exit": 1}, "ProviderSignInRequired",
     "Claude signed you out. Sign in again to keep using your plan."),
    ({"reply": error_reply("authentication_failed", "OAuth token has expired")}, "ProviderSignInRequired",
     "Claude signed you out. Sign in again to keep using your plan."),
    ({"reply": error_reply("rate_limit", "You've hit your weekly limit · resets Mon 9am")}, "ClaudePlanUnavailable",
     "Your Claude plan's limit is reached until Mon 9am. Pick another model for now."),
    ({"reply": error_reply("rate_limit", "API Error: 429")}, "ClaudePlanUnavailable", "limiting requests"),
    ({"reply": text_reply("Half an ans")[:4] + [{"exit": 3}]}, "ClaudeCodeEnded", "ended before it finished"),
])
def test_a_failed_call_says_what_happened_and_what_to_do(store, tmp_path, monkeypatch, setup, expected, words):
    install_fake_claude(tmp_path, monkeypatch, **setup)

    with pytest.raises(Exception) as caught:
        _model().invoke([HumanMessage(content="Hello")])

    assert type(caught.value).__name__ == expected
    assert words in str(caught.value)


def test_claude_code_gone_since_it_was_checked_says_so_and_is_checked_again(store, tmp_path, monkeypatch):
    fake = install_fake_claude(tmp_path, monkeypatch)
    assert claude.claude_plan_status().ready
    for shim in fake.bin_dir.glob("claude*"):
        shim.unlink()

    with pytest.raises(claude.ClaudePlanUnavailable, match="Claude Code didn't start on this computer"):
        _model().invoke([HumanMessage(content="Hello")])
    assert claude.claude_plan_status().state == "not_installed"


def test_stop_ends_claude_code_and_everything_it_started(store, tmp_path, monkeypatch):
    from row_bot.cancellation import CancellationScope, use_cancellation_scope

    def read_calendar(day: str) -> str:
        """Read the calendar for a day."""
        return day

    fake = install_fake_claude(tmp_path, monkeypatch, reply=text_reply("One two three four five six")[:5] + [{"hang": True}])
    scope = CancellationScope()
    received = []
    with use_cancellation_scope(scope):
        for chunk in _model().bind_tools([StructuredTool.from_function(read_calendar)]).stream(
                [HumanMessage(content="Count")]):
            received.append(chunk.content)
            if chunk.content:
                threading.Thread(target=scope.cancel, daemon=True).start()

    [run] = fake.model_runs()
    assert "".join(received).startswith("One two ")
    assert "six" not in "".join(received)
    for pid in (run["pid"], run["server_pid"]):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


# ── Security (pull-request lane) ─────────────────────────────────────────────


_OPENED: list[str] | None = None


def _audit(event: str, args: tuple) -> None:
    if _OPENED is not None and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _OPENED.append(os.fsdecode(args[0]))


_HOOKED = False


@pytest.fixture
def opened_files():
    """Every file this process opens while the test runs (Python's audit events)."""
    global _OPENED, _HOOKED
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True
    _OPENED = []
    yield _OPENED
    _OPENED = None


def test_row_bot_never_opens_a_claude_credential_file(store, tmp_path, monkeypatch, opened_files):
    home = tmp_path / "fake-claude-home"
    fake = install_fake_claude(tmp_path, monkeypatch, reply=text_reply("ok"))
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "never-read"}}', encoding="utf-8")
    (home / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "never-read"}}', encoding="utf-8")
    opened_files.clear()  # from here on, only Row-Bot's own reads

    _card()
    _model().invoke([HumanMessage(content="Hello")])
    claude.list_claude_subscription_model_infos(force_refresh=True)

    assert fake.model_runs()
    touched = [path for path in opened_files if ".credentials.json" in path or path.endswith(".claude.json")
               or os.sep + ".claude" + os.sep in path]
    assert touched == []


def test_inherited_keys_endpoints_and_other_clouds_never_reach_claude_code(store, tmp_path, monkeypatch):
    fake = install_fake_claude(tmp_path, monkeypatch, reply=text_reply("ok"))
    for name, value in {
        "ANTHROPIC_API_KEY": "sk-ant-stray", "ANTHROPIC_AUTH_TOKEN": "stray-bearer",
        "ANTHROPIC_BASE_URL": "https://relay.example", "ANTHROPIC_CUSTOM_HEADERS": "Authorization: Bearer x",
        "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1",
        "CLAUDE_CODE_OAUTH_TOKEN": "env-token", "OPENAI_API_KEY": "row-bot-key",
    }.items():  # all of these are in Row-Bot's own environment
        monkeypatch.setenv(name, value)

    _card()
    _model().invoke([HumanMessage(content="Hello")])

    runs = fake.runs()
    assert {tuple(run["argv"][:2]) for run in runs} >= {("--version",), ("auth", "status"), ("-p", "--input-format")}
    for run in runs:
        assert not [name for name in run["env"] if name.upper().startswith("ANTHROPIC_")], run["argv"]
        assert not {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                    "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"} & set(run["env"]), run["argv"]
        assert run["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        # No Row-Bot setting of Claude Code's own: no beta headers, extra settings or bare (API-key) mode.
        assert not {"--betas", "--settings", "--bare", "--dangerously-skip-permissions"} & set(run["argv"])


def test_row_bot_has_no_claude_sign_in_of_its_own(store):
    from row_bot.application import subscription_controls, subscription_options, subscription_probes

    assert "claude_subscription" not in subscription_controls.PROVIDERS
    assert all(account.provider_id != "claude_subscription" for account in subscription_controls.read_accounts().accounts)
    with pytest.raises(subscription_controls.SubscriptionError):
        subscription_controls.review_action("claude_subscription", "0" * 64, "start", validate=lambda: None)
    with pytest.raises(config.ProviderConfigError):
        subscription_options.review_options("claude_subscription", "0" * 64, "reference", None, validate=lambda: None)
    assert "claude_subscription" not in subscription_probes.PROVIDER_KINDS


# ── Upgrading from the old Claude sign-in ────────────────────────────────────


def _old_claude_sign_in(store) -> None:
    from row_bot import secret_store
    from row_bot.providers import auth_store

    namespace = "providers:claude_subscription"
    current = auth_store._stage_immutable_secret("claude_subscription", "old-access", "oauth.access_token", lambda: None)
    previous = auth_store._stage_immutable_secret("claude_subscription", "older-access", "oauth.access_token", lambda: None)
    secret_store.set_secret("refresh_token", "legacy-refresh", namespace=namespace)
    config.save_provider_config({"providers": {"claude_subscription": {
        "provider_id": "claude_subscription", "auth_method": "oauth_pkce", "source": "oauth_pkce", "configured": True,
        "fingerprint": "abc", "oauth_bundle_ref": {"values": {"access_token": current}},
        "oauth_bundle_previous": {"reference": {"values": {"access_token": previous}}, "metadata": {}},
    }}})


def test_upgrading_deletes_the_old_claude_tokens_and_asks_to_set_claude_up_again(store, tmp_path, monkeypatch):
    _old_claude_sign_in(store)
    assert any("claude_subscription" in repr(key) for key in store.values)

    assert claude.retire_old_claude_sign_in() is True

    assert not [key for key in store.values if "claude_subscription" in repr(key)]
    assert config.load_provider_config()["providers"]["claude_subscription"] == {
        "provider_id": "claude_subscription", "set_up_again": True}
    assert claude.retire_old_claude_sign_in() is False  # once
    install_fake_claude(tmp_path, monkeypatch, auth={"loggedIn": False}, auth_exit=1)
    assert _card()["claude_plan"]["set_up_again"] is True
    install_fake_claude(tmp_path, monkeypatch)
    assert _card()["claude_plan"]["set_up_again"] is False  # set up: said once, then gone
    assert "set_up_again" not in config.load_provider_config()["providers"]["claude_subscription"]


def test_a_profile_that_never_used_the_old_sign_in_is_left_alone(store):
    config.save_provider_config({"providers": {"openai": {"configured": True}}})
    before = config.CONFIG_PATH.read_bytes()

    assert claude.retire_old_claude_sign_in() is False
    assert config.CONFIG_PATH.read_bytes() == before


# ── Models: Claude Code's own list ───────────────────────────────────────────


def test_the_models_are_claude_codes_own_list_and_listing_them_asks_no_model(store, tmp_path, monkeypatch):
    fake = install_fake_claude(tmp_path, monkeypatch)

    infos = claude.list_claude_subscription_model_infos(force_refresh=True)

    assert [(info.model_id, info.display_name, info.context_window) for info in infos] == [
        ("claude-opus-5-5[1m]", "Claude Opus 5.5 with 1M context", 1_000_000),
        ("claude-sonnet-5", "Claude Sonnet 5", 200_000),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 200_000),
    ]
    assert infos[0].reasoning["supported_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert infos[2].reasoning is None  # Haiku 4.5 takes no effort level
    assert all(info.tool_calling and "vision" in info.capabilities for info in infos)
    [listing] = [run for run in fake.runs() if "-p" in run["argv"]]
    assert [frame["type"] for frame in listing["frames"]] == ["control_request"]  # no message to any model
    assert [info.model_id for info in claude.list_claude_subscription_model_infos_for_status()] == [
        info.model_id for info in infos]


def test_a_chat_saved_on_an_older_claude_model_keeps_its_choice(store, tmp_path, monkeypatch):
    fake = install_fake_claude(tmp_path, monkeypatch)

    info = claude.claude_plan_model_info("claude-sonnet-4-6")
    _model(model_name="claude-sonnet-4-6").invoke([HumanMessage(content="Hi")])

    assert info is not None and info.tool_calling is True
    assert fake.model_runs()[0]["argv"][8] == "claude-sonnet-4-6"
    assert claude.claude_plan_model_info("not-a-claude-model") is None
