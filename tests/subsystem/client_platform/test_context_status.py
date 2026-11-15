"""Saved context projection is bounded, private, and never a model request."""
from __future__ import annotations

from contextlib import closing

import json
import sqlite3

from langchain_core.messages import HumanMessage
import pytest

from row_bot.application.context_status import project_live_usage, read_usage
from tests.contracts.client_platform.test_headless_lifecycle import platform as platform

pytestmark = pytest.mark.subsystem
CONTROLS = {"model_selection": {"provider_id": "fixture", "model_ref": "model:fixture:synthetic"}, "runtime_mode": "agent"}


def saved_usage(**overrides):
    from row_bot import threads
    return {"schema_version": 2, "snapshot_kind": "settled", "mode": "agent",
            "model_ref": "model:fixture:synthetic", "estimated_input_tokens": 123,
            "usable_input_tokens": 1000, "native_window_tokens": 2048,
            "compact_at_tokens": 750, "effective_limit_tokens": 1000,
            "last_confirmed_input_tokens": 117,
            "capacity_state": "ready", "status": "ready",
            "checkpoint_revision": threads.get_latest_checkpoint_revision("conversation-a"),
            "checkpoint_message_digest": "a" * 64, "preparation_fingerprint": "PRIVATE PROMPT",
            "policy_fingerprint": "PRIVATE POLICY", **overrides}


def store(value):
    from row_bot import threads
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("UPDATE thread_meta SET context_usage_json=? WHERE thread_id='conversation-a'", (json.dumps(value),))


def test_reads_saved_counts_without_checkpoint_expansion_or_private_fields(platform, monkeypatch):
    from row_bot import threads
    threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="PRIVATE conversation", id="native-input")])
    threads.save_context_usage("conversation-a", saved_usage())
    def forbidden(*args, **kwargs):
        raise AssertionError("A status read must not reconstruct a checkpoint")
    monkeypatch.setattr(threads.checkpointer, "get_tuple", forbidden)
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", forbidden)
    monkeypatch.setattr(threads, "load_context_usage", forbidden)
    monkeypatch.setattr(platform, "_metadata", forbidden)
    result = read_usage(platform, "conversation-a", CONTROLS)
    assert result == {"conversation_id": "conversation-a", "state": "saved",
                      "freshness": "current", "status": "ready",
                      "model_ref": "model:fixture:synthetic", "scope": "agent",
                      "capacity_state": "ready", "estimated_input_tokens": 123,
                      "usable_input_tokens": 1000, "compact_at_tokens": 750,
                      "native_window_tokens": 2048, "effective_limit_tokens": 1000,
                      "last_confirmed_input_tokens": 117}
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("change", ["checkpoint", "model", "mode", "active"])
def test_known_identity_changes_are_stale_but_retain_saved_model(platform, monkeypatch, change):
    from row_bot import threads
    threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="one", id="native-one")])
    store(saved_usage())
    controls = CONTROLS
    handle = None
    if change == "checkpoint":
        threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="two", id="native-two")])
    elif change == "model":
        controls = {**CONTROLS, "model_selection": {"provider_id": "fixture", "model_ref": "model:fixture:other"}}
    elif change == "mode":
        controls = {**CONTROLS, "runtime_mode": "chat_only"}
    else:
        handle = platform.registry.register("conversation-a")
    result = read_usage(platform, "conversation-a", controls)
    if handle is not None:
        platform.registry.finish(handle, status="interrupted")
    assert result["state"] == "stale" and result["model_ref"] == "model:fixture:synthetic"
    assert result["estimated_input_tokens"] == 123


def test_no_snapshot_is_unknown_not_zero_and_missing_conversation_is_rejected(platform):
    from row_bot.application.client_platform import ClientPlatformError
    result = read_usage(platform, "conversation-a", CONTROLS)
    assert result["state"] == "unknown"
    assert result["estimated_input_tokens"] is None and result["native_window_tokens"] is None
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_usage(platform, "missing-conversation", CONTROLS)


@pytest.mark.parametrize("override", [
    {"estimated_input_tokens": -1}, {"estimated_input_tokens": True}, {"estimated_input_tokens": 1.5},
    {"estimated_input_tokens": 2_147_483_648}, {"estimated_input_tokens": None}, {"usable_input_tokens": "1000"},
    {"native_window_tokens": False}, {"last_confirmed_input_tokens": float("nan")},
    {"model_ref": "x" * 257}, {"model_ref": "model\nPRIVATE"}, {"snapshot_kind": "transient"},
    {"schema_version": True}, {"schema_version": 1}, {"mode": "unsupported"},
    {"checkpoint_message_digest": None}, {"checkpoint_message_digest": "incomplete"},
    {"checkpoint_revision": {"private": "content"}}, {"checkpoint_revision": "x" * 257},
])
def test_invalid_saved_metadata_stays_unknown(platform, override):
    store(saved_usage(**override))
    assert read_usage(platform, "conversation-a", CONTROLS)["state"] == "unknown"


def test_saved_zero_and_unknown_capacity_remain_distinct(platform):
    store(saved_usage(estimated_input_tokens=0, usable_input_tokens=None,
                      native_window_tokens=None, last_confirmed_input_tokens=0))
    result = read_usage(platform, "conversation-a", CONTROLS)
    assert result["estimated_input_tokens"] == 0 and result["last_confirmed_input_tokens"] == 0
    assert result["usable_input_tokens"] is None and result["native_window_tokens"] is None


def test_live_context_events_expose_only_bounded_meter_fields():
    payload = saved_usage(
        snapshot_kind="transient",
        preparation_fingerprint="PRIVATE prompt",
        policy_fingerprint="PRIVATE policy",
    )
    result = project_live_usage("conversation-a", payload, "compaction_started")
    assert result["state"] == "live"
    assert result["freshness"] == "current"
    assert result["status"] == "compacting"
    assert result["compact_at_tokens"] == 750
    assert result["effective_limit_tokens"] == 1000
    assert "PRIVATE" not in json.dumps(result)


def test_invalid_live_context_event_fails_closed_to_unknown():
    result = project_live_usage(
        "conversation-a",
        {"model_ref": "fixture/model", "estimated_input_tokens": -1},
        "context_usage",
    )
    assert result["state"] == "unknown"
    assert result["status"] == "unavailable"


def test_client_platform_publishes_live_context_meter_event(platform):
    handle = platform.registry.register("conversation-a")
    try:
        platform.observe_event(
            "conversation-a",
            ("context_usage", saved_usage(snapshot_kind="transient")),
            handle,
        )
        event = platform.projection._states["conversation-a"].events[-1][0]
        assert event["type"] == "context.updated"
        assert event["payload"]["state"] == "live"
        assert event["payload"]["compact_at_tokens"] == 750
        assert "preparation_fingerprint" not in event["payload"]
    finally:
        platform.registry.finish(handle, status="interrupted")


def _record_call(thread_id: str, purpose: str = "main", created_at: float | None = None, **usage) -> None:
    from row_bot.prompt_cache import ModelUsage
    from row_bot.providers.usage_ledger import UsageRecord, record_model_usage

    assert record_model_usage(UsageRecord(
        purpose=purpose, provider="openrouter", model="anthropic/claude-sonnet-4.5",
        usage=ModelUsage(**usage), thread_id=thread_id, created_at=created_at,
    ))


def test_meters_carry_this_conversations_ledger_totals_and_validate_on_the_wire(platform, tmp_path, monkeypatch):
    from row_bot import threads
    from row_bot.api.v1.schemas import ContextUpdated, ContextUsageView

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "ledger"))
    threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="PRIVATE", id="native-input")])
    threads.save_context_usage("conversation-a", saved_usage())
    _record_call("conversation-a", input_tokens=1000, output_tokens=50, cache_read_tokens=800, cost=0.0042)
    _record_call("conversation-a", input_tokens=200, output_tokens=10)
    _record_call("conversation-b", input_tokens=99_999, output_tokens=1)

    saved = read_usage(platform, "conversation-a", CONTROLS)
    live = project_live_usage("conversation-a", saved_usage(snapshot_kind="transient"), "context_usage")

    expected = {"calls": 2, "input_tokens": 1200, "output_tokens": 60,
                "cache_read_percent": 80, "cost_usd": pytest.approx(0.0042), "last_cache_read_percent": None}
    assert saved["conversation_usage"] == expected
    assert live["conversation_usage"] == expected
    ContextUsageView.model_validate(saved)
    ContextUpdated.model_validate(live)
    assert "PRIVATE" not in json.dumps(saved)


def test_the_last_requests_cache_share_comes_from_the_newest_turn_call(platform, tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "ledger"))
    _record_call("conversation-a", created_at=100.0, input_tokens=1000, output_tokens=5, cache_read_tokens=100)
    _record_call("conversation-a", created_at=200.0, input_tokens=1000, output_tokens=5, cache_read_tokens=920)
    # A background call for the conversation (its title) is newer, but is not one of its requests.
    _record_call("conversation-a", purpose="title", created_at=300.0, input_tokens=50, output_tokens=5,
                 cache_read_tokens=0)

    usage = project_live_usage("conversation-a", saved_usage(snapshot_kind="transient"),
                               "context_usage")["conversation_usage"]
    assert usage["last_cache_read_percent"] == 92
    assert usage["cache_read_percent"] == 50  # 1,020 of 2,050 over the whole conversation

    # A provider that reports no cache numbers (a local model) leaves the last request's share out.
    _record_call("conversation-a", purpose="chat_only", created_at=400.0, input_tokens=800, output_tokens=5)
    usage = project_live_usage("conversation-a", saved_usage(snapshot_kind="transient"),
                               "context_usage")["conversation_usage"]
    assert usage["last_cache_read_percent"] is None


BREAKDOWN = {"instructions": 40, "tools": 50, "skills": 10, "memory": 3, "conversation": 20, "tool_count": 12}


def test_a_settled_breakdown_is_projected_saved_and_live_and_validates_on_the_wire(platform):
    from row_bot import threads
    from row_bot.api.v1.schemas import ContextUpdated, ContextUsageView

    threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="one", id="native-one")])
    store(saved_usage(breakdown=BREAKDOWN))

    saved = read_usage(platform, "conversation-a", CONTROLS)
    settled = project_live_usage("conversation-a", saved_usage(breakdown=BREAKDOWN), "context_usage")
    per_call = project_live_usage("conversation-a", saved_usage(snapshot_kind="transient"), "context_usage")

    assert saved["state"] == "saved" and saved["breakdown"] == BREAKDOWN
    assert settled["breakdown"] == BREAKDOWN
    assert "breakdown" not in per_call
    assert ContextUsageView.model_validate(saved).breakdown.tool_count == 12
    assert ContextUpdated.model_validate(per_call).breakdown is None


@pytest.mark.parametrize("breakdown", [
    "PRIVATE", ["instructions"], {**BREAKDOWN, "tools": -1}, {**BREAKDOWN, "memory": True},
    {**BREAKDOWN, "skills": 1.5}, {**BREAKDOWN, "conversation": 2_147_483_648},
    {key: value for key, value in BREAKDOWN.items() if key != "tool_count"},
])
def test_a_malformed_breakdown_is_dropped_and_the_meter_still_reads(platform, breakdown):
    from row_bot import threads

    threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="one", id="native-one")])
    store(saved_usage(breakdown=breakdown))

    saved = read_usage(platform, "conversation-a", CONTROLS)
    live = project_live_usage("conversation-a", saved_usage(breakdown=breakdown), "context_usage")
    assert saved["state"] == "saved" and saved["estimated_input_tokens"] == 123
    assert live["state"] == "live"
    assert "breakdown" not in saved and "breakdown" not in live
    assert "PRIVATE" not in json.dumps(saved)


def test_meters_leave_usage_out_before_any_call_and_when_unknown(platform, tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "ledger"))
    assert "conversation_usage" not in project_live_usage(
        "conversation-a", saved_usage(snapshot_kind="transient"), "context_usage")
    _record_call("conversation-a", input_tokens=10, output_tokens=1)

    unknown = project_live_usage("conversation-a", {"model_ref": "fixture/model", "estimated_input_tokens": -1},
                                 "context_usage")
    assert unknown["state"] == "unknown" and "conversation_usage" not in unknown
    assert read_usage(platform, "conversation-a", CONTROLS)["state"] == "unknown"
    assert "conversation_usage" not in read_usage(platform, "conversation-a", CONTROLS)


def test_a_broken_ledger_never_breaks_the_meter(platform, monkeypatch):
    from row_bot.providers import usage_ledger

    monkeypatch.setattr(usage_ledger, "conversation_usage",
                        lambda _thread: (_ for _ in ()).throw(RuntimeError("ledger unavailable")))
    result = project_live_usage("conversation-a", saved_usage(snapshot_kind="transient"), "context_usage")
    assert result["state"] == "live" and "conversation_usage" not in result


def test_oversized_snapshot_is_rejected_before_json_parsing(platform, monkeypatch):
    from row_bot.application import context_status
    store(saved_usage(private_extra="x" * (17 * 1024)))
    monkeypatch.setattr(context_status.json, "loads", lambda *_: (_ for _ in ()).throw(AssertionError("Oversized metadata parsed")))
    assert read_usage(platform, "conversation-a", CONTROLS)["state"] == "unknown"


def test_non_sqlite_saver_never_falls_back_to_full_checkpoint(platform, monkeypatch):
    from row_bot import threads
    store(saved_usage())
    class NonSqliteSaver:
        def get_tuple(self, *args):
            raise AssertionError("History expansion is forbidden")
    monkeypatch.setattr(threads, "checkpointer", NonSqliteSaver())
    assert read_usage(platform, "conversation-a", CONTROLS)["state"] == "stale"


@pytest.mark.parametrize("encoded", ["{invalid", "null", "[]", "["])
def test_malformed_saved_json_is_unknown(platform, encoded):
    from row_bot import threads
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("UPDATE thread_meta SET context_usage_json=? WHERE thread_id='conversation-a'", (encoded,))
    assert read_usage(platform, "conversation-a", CONTROLS)["state"] == "unknown"


@pytest.mark.parametrize("mode", [[], {}], ids=["list", "object"])
def test_nested_mode_metadata_is_unknown(platform, mode):
    store(saved_usage(mode=mode))
    assert read_usage(platform, "conversation-a", CONTROLS)["state"] == "unknown"


def test_deleting_conversation_is_denied_before_saved_data_read(platform, monkeypatch):
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.runtime import admissions
    from row_bot.application import context_status
    admissions.close_admission("conversation-a")
    monkeypatch.setattr(context_status, "_read_saved", lambda *_: (_ for _ in ()).throw(AssertionError("Data read after deletion admission closed")))
    with pytest.raises(ClientPlatformError, match="conversation_deleting"):
        read_usage(platform, "conversation-a", CONTROLS)


@pytest.mark.parametrize("physical", [False, True])
def test_delete_race_never_returns_a_saved_measurement(platform, monkeypatch, physical):
    from row_bot import threads
    from row_bot.runtime import admissions
    from row_bot.application.client_platform import ClientPlatformError
    store(saved_usage())
    def delete_during_read(_thread):
        if physical:
            with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
                connection.execute("DELETE FROM thread_meta WHERE thread_id='conversation-a'")
        else:
            admissions.close_admission("conversation-a")
        return "revision"
    monkeypatch.setattr(threads, "get_latest_checkpoint_revision", delete_during_read)
    with pytest.raises(ClientPlatformError, match="not_found" if physical else "conversation_deleting"):
        read_usage(platform, "conversation-a", CONTROLS)
