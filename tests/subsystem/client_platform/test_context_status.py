"""Saved context projection is bounded, private, and never a model request."""
from __future__ import annotations

from contextlib import closing

import json
import sqlite3

from langchain_core.messages import HumanMessage
import pytest

from row_bot.application.context_status import read_usage
from tests.contracts.client_platform.test_headless_lifecycle import platform as platform

pytestmark = pytest.mark.subsystem
CONTROLS = {"model_selection": {"provider_id": "fixture", "model_ref": "model:fixture:synthetic"}, "runtime_mode": "agent"}


def saved_usage(**overrides):
    from row_bot import threads
    return {"schema_version": 2, "snapshot_kind": "settled", "mode": "agent",
            "model_ref": "model:fixture:synthetic", "estimated_input_tokens": 123,
            "usable_input_tokens": 1000, "native_window_tokens": 2048,
            "last_confirmed_input_tokens": 117,
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
    assert result == {"conversation_id": "conversation-a", "state": "saved", "model_ref": "model:fixture:synthetic",
                      "estimated_input_tokens": 123, "usable_input_tokens": 1000,
                      "native_window_tokens": 2048, "last_confirmed_input_tokens": 117}
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
