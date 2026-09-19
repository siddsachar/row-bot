"""Reviewed custom endpoint credentials over the canonical staged secret owner."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import re
from pathlib import Path
import sqlite3
import stat
import time

from row_bot.application.provider_settings_controls import ProviderSettingsSnapshot
from row_bot.application.provider_settings_commands import invalidate_provider_runtime
from row_bot.providers import auth_store
from row_bot.providers.config import load_provider_config, provider_config_revision, provider_config_transaction
from row_bot.providers.credential_controls import CredentialControlError
from row_bot.runtime import admissions


_TYPES = {"provider.custom_credential." + action for action in ("save", "clear", "restore")}


def _endpoint(provider_id: str, cfg: dict) -> dict:
    if type(provider_id) is not str or not re.fullmatch(r"custom_openai_[a-z0-9][a-z0-9_-]{0,63}", provider_id):
        raise CredentialControlError("not_found")
    endpoint = auth_store._custom_credential_endpoint(provider_id, cfg)
    if endpoint is None:
        raise CredentialControlError("not_found")
    return endpoint


def _value(operation: str, value: str | None) -> str | None:
    if operation not in {"save", "clear", "restore"}:
        raise CredentialControlError("invalid_command")
    if operation != "save":
        if value is not None:
            raise CredentialControlError("invalid_command")
        return None
    try:
        if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 16384:
            raise ValueError
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise ValueError
        return normalized
    except (ValueError, UnicodeError):
        raise CredentialControlError("invalid_command") from None


def read_custom_provider_credentials(provider_id: str, *, validate: Callable[[], None] = lambda: None) -> ProviderSettingsSnapshot:
    validate()
    cfg = load_provider_config(strict=True)
    endpoint = _endpoint(provider_id, cfg)
    status = auth_store.provider_secret_status(provider_id)
    if provider_config_revision(load_provider_config(strict=True)) != provider_config_revision(cfg):
        raise CredentialControlError("revision_conflict")
    entry = cfg["providers"].get(provider_id, {})
    # Immutable secure generations are identified by the canonical reference.
    # Bind the full config before any secret read on a later command review.
    revision = provider_config_revision(cfg)
    source = status.get("source")
    if source not in {"environment", "secret_file", "conflict", "session", "keyring", "encrypted_file", "api_keys"}:
        source = "unknown"
    name = str(endpoint.get("display_name") or endpoint.get("name") or provider_id)
    if len(name.encode("utf-8", errors="surrogatepass")) > 160 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in name):
        name = provider_id
    recovery = isinstance(entry.get("credential_previous"), dict) or bool(status.get("recovery_required") and (entry.get("credential_ref") or entry.get("configured")))
    result = ProviderSettingsSnapshot(1, provider_id, name, revision, bool(status.get("configured")), source,
        bool(status.get("externally_managed")), bool(status.get("error")), recovery)
    validate()
    return result


def review_custom_provider_credentials(provider_id: str, provider_revision: str, operation: str,
                                       value: str | None = None, *, validate: Callable[[], None]) -> dict:
    validate()
    _value(operation, value)
    cfg = load_provider_config(strict=True)
    _endpoint(provider_id, cfg)
    if provider_config_revision(cfg) != provider_revision:
        raise CredentialControlError("revision_conflict")
    snapshot = read_custom_provider_credentials(provider_id, validate=validate)
    if snapshot.revision != provider_revision:
        raise CredentialControlError("revision_conflict", snapshot.revision)
    if snapshot.externally_managed or (snapshot.storage_unavailable and operation != "restore"):
        raise CredentialControlError("action_denied")
    if operation == "restore" and not snapshot.recovery_available:
        raise CredentialControlError("provider_recovery_unavailable")
    intent = {"provider_id": provider_id, "provider_revision": provider_revision, "operation": operation, "value": value}
    return {"provider_id": provider_id, "provider_revision": provider_revision, "operation": operation,
            "action_digest": admissions.keyed_digest(intent), "snapshot": asdict(snapshot)}


def execute_custom_provider_credentials(*, owner_id: str, key: str, command: dict,
                                        validate: Callable[[], None], validate_review: Callable[[dict], None]) -> dict:
    validate()
    if command.get("type") not in _TYPES or not isinstance(command.get("payload"), dict):
        raise CredentialControlError("invalid_command")
    payload = command["payload"]
    provider_id, revision = payload.get("provider_id"), payload.get("provider_revision")
    operation = command["type"].removeprefix("provider.custom_credential.")
    normalized = _value(operation, payload.get("value"))
    proof = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
    with provider_config_transaction():
        validate()
        cfg = load_provider_config(strict=True)
        endpoint = _endpoint(provider_id, cfg)
        scope = endpoint.get("credential_scope")
        entry = cfg["providers"].get(provider_id, {})
        # An old command cannot recover into a replacement endpoint incarnation.
        recovered = entry.get("credential_command") == proof and entry.get("credential_scope") == scope
        try:
            replay = admissions.claim_command(owner_id, key, command, provider_id)
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain" or not recovered:
                raise CredentialControlError(str(exc)) from None
            replay = {"command_id": command["command_id"], "status": "completed",
                      "credential": asdict(read_custom_provider_credentials(provider_id, validate=validate))}
            invalidate_provider_runtime()
            return admissions.complete_command(owner_id, key, replay)
        if replay is not None:
            if not recovered:
                raise CredentialControlError("revision_conflict")
            validate()
            return replay
        try:
            review = review_custom_provider_credentials(provider_id, revision, operation, payload.get("value"), validate=validate)
            validate_review({key: value for key, value in review.items() if key != "snapshot"})
        except CredentialControlError as exc:
            admissions.reject_command(owner_id, key, exc.code)
            raise
        def authority() -> None:
            validate()
            current = read_custom_provider_credentials(provider_id, validate=validate)
            if current.revision != revision:
                raise CredentialControlError("revision_conflict")
            validate_review({name: value for name, value in review.items() if name != "snapshot"})
        try:
            auth_store.replace_provider_api_key(provider_id, normalized, restore=operation == "restore",
                validate=authority, command_proof=proof)
        except Exception:
            raise CredentialControlError("provider_credential_unconfirmed") from None
        invalidate_provider_runtime()
        result = {"command_id": command["command_id"], "status": "completed",
                  "credential": asdict(read_custom_provider_credentials(provider_id, validate=validate))}
        admissions.complete_command(owner_id, key, result)
        validate()
        return result


def read_custom_provider_credentials_receipt(provider_id: str, *, owner_id: str, command_id: str,
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
        raise CredentialControlError("provider_settings_unavailable") from None
    validate()
    if row is None or row["target"] != provider_id or row["type"] not in _TYPES:
        return None
    cfg = load_provider_config(strict=True)
    endpoint = _endpoint(provider_id, cfg)
    entry = cfg.get("providers", {}).get(provider_id, {})
    published = entry.get("credential_command") == {"owner_id": owner_id, "key": row["key"], "command_id": command_id} and entry.get("credential_scope") == endpoint.get("credential_scope")
    status = "completed" if row["status"] == "completed" or published else "rejected" if row["status"] == "rejected" else "uncertain"
    result = {"command_id": command_id, "status": status, "published": published,
              "credential": asdict(read_custom_provider_credentials(provider_id, validate=validate))}
    validate()
    return result
