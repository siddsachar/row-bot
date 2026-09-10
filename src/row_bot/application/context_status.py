"""Bounded display of the existing owner's last saved context measurement.

This view is never request authority. Exact semantic freshness would require
reconstructing history; a saved measurement is deliberately not called current.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
import json
import sqlite3
from typing import Any, Literal, TypedDict


class ContextUsageView(TypedDict):
    conversation_id: str
    state: Literal["unknown", "saved", "stale"]
    estimated_input_tokens: int | None
    usable_input_tokens: int | None
    native_window_tokens: int | None
    last_confirmed_input_tokens: int | None
    model_ref: str | None


_MAX_SNAPSHOT_BYTES = 16 * 1024
_MAX_TOKENS = 2_147_483_647
_COUNTS = ("estimated_input_tokens", "usable_input_tokens", "native_window_tokens", "last_confirmed_input_tokens")


def _unknown(conversation_id: str) -> ContextUsageView:
    return {"conversation_id": conversation_id, "state": "unknown", "estimated_input_tokens": None,
            "usable_input_tokens": None, "native_window_tokens": None,
            "last_confirmed_input_tokens": None, "model_ref": None}


def read_usage(service: Any, conversation_id: str, controls: Mapping[str, Any]) -> ContextUsageView:
    """Read bounded saved metadata and conservatively flag known stale identity.

    A `saved` result describes the last settled measurement, not a fresh count
    of the current composer or tool configuration. No content or fingerprints
    leave this projection, and no provider/capacity discovery is performed.
    """
    _assert_readable(conversation_id)
    try:
        return _read_saved(service, conversation_id, controls)
    finally:
        _assert_readable(conversation_id)


def _assert_readable(conversation_id: str) -> None:
    from row_bot import threads
    from row_bot.runtime import admissions
    from row_bot.application.client_platform import ClientPlatformError
    if admissions.deletion_state(conversation_id) != "active":
        raise ClientPlatformError("conversation_deleting")
    threads._ensure_thread_db()
    # service._metadata loads every metadata field, including the potentially
    # malformed context blob. Existence and lifecycle checks need no content.
    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        if not connection.execute("SELECT 1 FROM thread_meta WHERE thread_id=?", (conversation_id,)).fetchone():
            raise ClientPlatformError("not_found")


def _read_saved(service: Any, conversation_id: str, controls: Mapping[str, Any]) -> ContextUsageView:
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError

    threads._ensure_thread_db()
    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        row = connection.execute(
            "SELECT CASE WHEN length(CAST(context_usage_json AS BLOB)) <= ? "
            "THEN context_usage_json ELSE '' END FROM thread_meta WHERE thread_id=?",
            (_MAX_SNAPSHOT_BYTES, conversation_id),
        ).fetchone()
    if row is None:
        raise ClientPlatformError("not_found")
    unknown = _unknown(conversation_id)
    if not row[0]:
        return unknown
    try:
        usage = json.loads(row[0])
    except (ValueError, TypeError, RecursionError):
        return unknown
    if (not isinstance(usage, dict) or type(usage.get("schema_version")) is not int
            or usage["schema_version"] != threads.CONTEXT_USAGE_SCHEMA_VERSION
            or usage.get("snapshot_kind") != "settled"
            or not isinstance(usage.get("mode"), str)
            or usage.get("mode") not in {"agent", "chat_only"}):
        return unknown
    digest = usage.get("checkpoint_message_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        return unknown
    model_ref = usage.get("model_ref")
    if (not isinstance(model_ref, str) or not model_ref or len(model_ref) > 256
            or any(ord(character) < 32 for character in model_ref)):
        return unknown
    counts = {key: usage.get(key) for key in _COUNTS}
    if counts["estimated_input_tokens"] is None or any(
            value is not None and (type(value) is not int or not 0 <= value <= _MAX_TOKENS)
            for value in counts.values()):
        return unknown
    selected = controls.get("model_selection") or {}
    expected_model = selected.get("model_ref") if isinstance(selected, Mapping) else None
    saved_revision = usage.get("checkpoint_revision")
    if saved_revision is not None and (not isinstance(saved_revision, str) or len(saved_revision) > 256):
        return unknown
    # The existing helper uses a checkpoint-ID-only SQLite query when cursor
    # exists. Never fall back to its get_tuple path on another saver backend.
    latest_revision = ""
    if callable(getattr(threads.checkpointer, "cursor", None)):
        try:
            latest_revision = threads.get_latest_checkpoint_revision(conversation_id)
        except (sqlite3.Error, OSError, ValueError):
            latest_revision = ""
    stale = (not latest_revision or saved_revision != latest_revision
             or model_ref != expected_model or usage["mode"] != controls.get("runtime_mode")
             or bool(service.registry.active(conversation_id)))
    return {"conversation_id": conversation_id, "state": "stale" if stale else "saved",
            "model_ref": model_ref, **counts}
