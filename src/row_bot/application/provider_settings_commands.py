"""Authenticated credential commands over the existing configuration owner."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

from row_bot.application.provider_settings_controls import review_provider_credential
from row_bot.providers import credential_controls as credentials
from row_bot.providers.config import load_provider_config
from row_bot.runtime import admissions

_TYPES = {"provider.credential." + operation for operation in ("save", "clear", "restore")}


def _intent(provider_id: str, revision: str, operation: str, value: str | None) -> dict:
    if type(provider_id) is not str or provider_id not in credentials.auth_store.PROVIDER_API_KEY_ENV:
        raise credentials.CredentialControlError("not_found")
    if type(revision) is not str or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise credentials.CredentialControlError("invalid_command")
    if operation not in {"save", "clear", "restore"}:
        raise credentials.CredentialControlError("invalid_command")
    if operation == "save":
        try:
            if type(value) is not str or len(value.encode("utf-8")) > 16384:
                raise ValueError
        except (ValueError, UnicodeError):
            raise credentials.CredentialControlError("invalid_command") from None
        credentials.normalize_api_key(provider_id, value)
    elif value is not None:
        raise credentials.CredentialControlError("invalid_command")
    return {"provider_id": provider_id, "provider_revision": revision, "operation": operation, "value": value}


def review_provider_settings_command(provider_id: str, provider_revision: str, operation: str,
                                     value: str | None = None, *, validate: Callable[[], None]) -> dict:
    validate()
    intent = _intent(provider_id, provider_revision, operation, value)
    snapshot = review_provider_credential(provider_id, provider_revision, operation, value)
    digest = admissions.keyed_digest(intent)
    validate()
    return {"provider_id": provider_id, "provider_revision": provider_revision, "operation": operation,
            "action_digest": digest, "snapshot": asdict(snapshot)}


def invalidate_provider_runtime() -> None:
    """Invalidate only instantiated caches; do not import or start runtimes."""
    for name, method in (("row_bot.agent", "clear_agent_cache"), ("row_bot.models", "clear_llm_cache")):
        module = sys.modules.get(name)
        clear = getattr(module, method, None)
        if clear is not None:
            clear()


def execute_provider_settings_command(*, owner_id: str, key: str, command: dict,
                                      validate: Callable[[], None],
                                      validate_review: Callable[[dict], None],
                                      invalidate: Callable[[], None] = invalidate_provider_runtime) -> dict:
    validate()
    payload = command.get("payload", {})
    if command.get("type") not in _TYPES or not isinstance(payload, dict):
        raise credentials.CredentialControlError("invalid_command")
    intent = _intent(payload.get("provider_id"), payload.get("provider_revision"),
                     command["type"].removeprefix("provider.credential."), payload.get("value"))
    # This digest binds the exact input, including whitespace, before normalisation.
    review = {"provider_id": intent["provider_id"], "provider_revision": intent["provider_revision"],
              "operation": intent["operation"], "action_digest": admissions.keyed_digest(intent)}

    def current_authority() -> None:
        validate()
        validate_review(review)

    current_authority()
    mapped = {**command, "expected_revision": intent["provider_revision"],
              "wire_expected_revision": command.get("expected_revision")}
    return credentials.apply_credential_command(intent["provider_id"], owner_id=owner_id, key=key,
        command=mapped, validate=current_authority, invalidate=invalidate)


def read_provider_settings_receipt(provider_id: str, *, owner_id: str, command_id: str,
                                   validate: Callable[[], None]) -> dict | None:
    """Inspect existing publication evidence without completing or replaying it.

    SQLite may maintain its WAL coordination files; no schema or credential
    writes are performed. A later replacement does not prove an older outcome.
    """
    validate()
    from row_bot import tasks
    path = Path(tasks._DB_PATH).absolute()
    row = None
    try:
        for leaf in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            for component in (*reversed(leaf.parents), leaf):
                try:
                    info = component.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError
        if not path.exists():
            validate()
            return None
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            conn.row_factory = sqlite3.Row
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
            deadline = time.monotonic() + 2
            steps = 0
            def interrupted() -> bool:
                nonlocal steps
                steps += 1000
                return steps >= 10_000_000 or time.monotonic() >= deadline
            conn.set_progress_handler(interrupted, 1000)
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = [entry for entry in conn.execute("PRAGMA table_list") if entry[0] == "main" and entry[1] == "client_commands"]
            if tables:
                if tables[0][2] != "table" or any(entry[6] for entry in conn.execute('PRAGMA table_xinfo("client_commands")')):
                    raise ValueError
                row = conn.execute("SELECT key,target,type,status FROM client_commands WHERE owner_id=? AND command_id=?",
                                   (owner_id, command_id)).fetchone()
        finally:
            conn.close()
    except (OSError, ValueError, sqlite3.Error):
        raise credentials.CredentialControlError("provider_settings_unavailable") from None
    validate()
    if row is None or row["target"] != provider_id or row["type"] not in _TYPES:
        return None
    entry = load_provider_config(strict=True).get("providers", {}).get(provider_id, {})
    published = entry.get("credential_command") == {"owner_id": owner_id, "key": row["key"], "command_id": command_id}
    status = "completed" if row["status"] == "completed" or published else "rejected" if row["status"] == "rejected" else "uncertain"
    result = {"command_id": command_id, "status": status, "published": published,
              "credential": credentials.credential_snapshot(provider_id)}
    validate()
    return result
