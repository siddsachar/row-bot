"""Reviewed saved endpoint and picker configuration; no implicit refresh."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import urlsplit

from row_bot.providers import custom, selection
from row_bot.providers.config import load_provider_config, save_provider_config, provider_config_revision, provider_config_transaction, ProviderConfigError
from row_bot.runtime import admissions
from row_bot.application.provider_settings_commands import invalidate_provider_runtime


@dataclass(frozen=True)
class ProviderEndpointFields:
    endpoint_id: str
    display_name: str
    base_url: str
    profile: str = "generic_openai"
    execution_location: str = "local"
    enabled: bool = True
    auth_required: bool = False
    vision_mode: str = "auto"
    tool_mode: str = "auto"
    context_window: int | None = None
    reasoning_mode: str = "auto"
    thinking_budget: int | None = None
    supports_reasoning_content: bool = False
    supports_reasoning_replay: bool = False
    extra_body_json: str = "{}"


@dataclass(frozen=True)
class ProviderEndpointSnapshot:
    provider_id: str
    fields: ProviderEndpointFields
    probe_state: str
    model_count: int | None
    runtime_state: str = "unknown"


@dataclass(frozen=True)
class ProviderConfigurationPage:
    schema_version: int
    revision: str
    items: tuple[ProviderEndpointSnapshot, ...]
    total: int
    next_cursor: str | None
    profiles: tuple[str, ...]


def _text(value, maximum, *, empty=False):
    if type(value) is not str or len(value.encode("utf-8", errors="surrogatepass")) > maximum or (not empty and not value.strip()) or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ProviderConfigError("invalid_provider_configuration")
    return value.strip()


def _fields(value: dict) -> tuple[ProviderEndpointFields, dict]:
    try:
        fields = ProviderEndpointFields(**value)
    except (TypeError, ValueError):
        raise ProviderConfigError("invalid_provider_configuration") from None
    endpoint_id = _text(fields.endpoint_id, 64)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", endpoint_id):
        raise ProviderConfigError("invalid_provider_configuration")
    name = _text(fields.display_name, 160)
    url = _text(fields.base_url, 2048).rstrip("/")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or "\\" in url or parsed.port == 0:
            raise ValueError
    except ValueError:
        raise ProviderConfigError("invalid_provider_endpoint_url") from None
    if fields.profile not in custom.CUSTOM_ENDPOINT_PROFILES or fields.execution_location not in {"local", "remote"}:
        raise ProviderConfigError("invalid_provider_configuration")
    for mode in (fields.vision_mode, fields.tool_mode, fields.reasoning_mode):
        if mode not in {"auto", "on", "off"}:
            raise ProviderConfigError("invalid_provider_configuration")
    for boolean in (fields.enabled, fields.auth_required, fields.supports_reasoning_content, fields.supports_reasoning_replay):
        if type(boolean) is not bool:
            raise ProviderConfigError("invalid_provider_configuration")
    for integer in (fields.context_window, fields.thinking_budget):
        if integer is not None and (type(integer) is not int or not 1 <= integer <= 10_000_000):
            raise ProviderConfigError("invalid_provider_configuration")
    if type(fields.extra_body_json) is not str or len(fields.extra_body_json.encode("utf-8", errors="surrogatepass")) > 16384:
        raise ProviderConfigError("invalid_provider_configuration")
    try:
        extra = json.loads(fields.extra_body_json, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(extra, dict) or custom._extra_body_contains_credentials(extra):
            raise ValueError
        if len(json.dumps(extra, ensure_ascii=True)) > 32768:
            raise ValueError
    except (ValueError, RecursionError, UnicodeError):
        raise ProviderConfigError("invalid_provider_extra_body") from None
    manual = {key: mode == "on" for key, mode in (("vision", fields.vision_mode), ("tool_calling", fields.tool_mode)) if mode != "auto"}
    if fields.context_window is not None:
        manual["context_window"] = fields.context_window
    endpoint = {"id": endpoint_id, "name": name, "display_name": name, "base_url": url,
        "profile": fields.profile, "execution_location": fields.execution_location, "enabled": fields.enabled,
        "auth_required": fields.auth_required, "manual_capabilities": manual, "reasoning_mode": fields.reasoning_mode,
        "supports_reasoning_content": fields.supports_reasoning_content, "supports_reasoning_replay": fields.supports_reasoning_replay,
        "extra_body": extra, "thinking_budget": fields.thinking_budget}
    return fields, endpoint


def _snapshot(raw: dict) -> ProviderEndpointSnapshot:
    endpoint = custom.normalize_custom_endpoint(raw)
    manual = endpoint.get("manual_capabilities", {})
    def mode(key: str) -> str:
        return "on" if manual.get(key) is True else "off" if manual.get(key) is False else "auto"
    extra = endpoint.get("extra_body", {})
    if custom._extra_body_contains_credentials(extra):
        raise ProviderConfigError("provider_configuration_unavailable")
    fields, _ = _fields({"endpoint_id": endpoint["id"], "display_name": endpoint["display_name"], "base_url": endpoint["base_url"],
        "profile": endpoint["profile"], "execution_location": endpoint["execution_location"], "enabled": endpoint["enabled"],
        "auth_required": endpoint["auth_required"], "vision_mode": mode("vision"), "tool_mode": mode("tool_calling"),
        "context_window": manual.get("context_window"), "reasoning_mode": endpoint["reasoning_mode"],
        "thinking_budget": endpoint.get("thinking_budget"), "supports_reasoning_content": endpoint["supports_reasoning_content"],
        "supports_reasoning_replay": endpoint["supports_reasoning_replay"], "extra_body_json": json.dumps(extra)})
    probe = endpoint.get("last_probe", {})
    classification = probe.get("classification")
    return ProviderEndpointSnapshot(endpoint["provider_id"], fields,
        classification if classification in {"agent_ready", "chat_only", "unavailable"} else "unknown",
        len(endpoint["models"]) if "models" in endpoint else None)


def read_provider_configuration(*, query: str = "", cursor: str | None = None, limit: int = 20) -> ProviderConfigurationPage:
    query = _text(query, 256, empty=True).casefold()
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ProviderConfigError("invalid_provider_configuration")
    cfg = load_provider_config(strict=True)
    revision = provider_config_revision(cfg)
    matches = [item for item in cfg["custom_endpoints"] if isinstance(item, dict) and (not query or query in " ".join(str(item.get(key, "")) for key in ("id", "name", "display_name", "base_url")).casefold())]
    offset = 0
    if cursor is not None:
        try:
            if len(cursor) > 1024:
                raise ValueError
            previous, search, offset = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if previous != revision or search != query or type(offset) is not int or not 0 <= offset <= len(matches):
                raise ValueError
        except (TypeError, ValueError):
            raise ProviderConfigError("cursor_expired") from None
    items = []
    used = 0
    for raw in matches[offset:offset + limit]:
        item = _snapshot(raw)
        size = len(json.dumps(asdict(item), ensure_ascii=True).encode())
        if items and used + size > 220 * 1024:
            break
        items.append(item)
        used += size
    end = offset + len(items)
    continuation = base64.urlsafe_b64encode(json.dumps([revision, query, end]).encode()).decode() if end < len(matches) else None
    return ProviderConfigurationPage(1, revision, tuple(items), len(matches), continuation, tuple(custom.CUSTOM_ENDPOINT_PROFILES))


def review_provider_configuration(operation: str, expected_revision: str, payload: dict, *, validate: Callable[[], None]) -> dict:
    validate()
    cfg = load_provider_config(strict=True)
    if provider_config_revision(cfg) != expected_revision:
        raise ProviderConfigError("revision_conflict")
    if operation in {"provider.endpoint.create", "provider.endpoint.save"}:
        fields, _ = _fields(payload)
        old = next((item for item in cfg["custom_endpoints"] if isinstance(item, dict) and item.get("id") == fields.endpoint_id), None)
        if operation.endswith(".create") and old:
            raise ProviderConfigError("provider_endpoint_identity_conflict")
        if operation.endswith(".save") and old is None:
            raise ProviderConfigError("not_found")
        if old:
            normalized = custom.normalize_custom_endpoint(old)
            if fields.profile != normalized["profile"] or fields.execution_location != normalized["execution_location"]:
                raise ProviderConfigError("provider_endpoint_identity_conflict")
    elif operation in {"provider.endpoint.delete", "provider.endpoint.probe", "provider.endpoint.refresh"}:
        if set(payload) != {"endpoint_id"} or not any(isinstance(item, dict) and item.get("id") == payload["endpoint_id"] for item in cfg["custom_endpoints"]):
            raise ProviderConfigError("not_found")
        if operation != "provider.endpoint.delete":
            _snapshot(next(item for item in cfg["custom_endpoints"] if isinstance(item, dict) and item.get("id") == payload["endpoint_id"]))
    elif operation in {"provider.model.pin", "provider.model.unpin"}:
        if set(payload) != {"provider_id", "model_id", "surface"} or payload["surface"] not in selection.SURFACE_VISIBILITY:
            raise ProviderConfigError("invalid_provider_configuration")
        _text(payload["provider_id"], 128)
        _text(payload["model_id"], 512)
    else:
        raise ProviderConfigError("invalid_command")
    digest = admissions.keyed_digest({"operation": operation, "revision": expected_revision, "payload": payload})
    validate()
    return {"configuration_revision": expected_revision, "operation": operation, "action_digest": digest}


def execute_provider_configuration(*, owner_id: str, key: str, command: dict,
                                   validate: Callable[[], None], validate_review: Callable[[dict], None]) -> dict:
    operation = command.get("type")
    payload = command.get("payload", {})
    revision = payload.get("configuration_revision")
    fields = payload.get("fields", {})
    target = "provider_configuration"
    if operation in {"provider.endpoint.probe", "provider.endpoint.refresh"}:
        return _execute_probe(owner_id, key, command, validate, validate_review)
    with provider_config_transaction():
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, command, target)
        except admissions.AdmissionError as exc:
            proof = load_provider_config(strict=True).get("configuration_command")
            if str(exc) == "operation_uncertain" and proof == {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}:
                validate()
                invalidate_provider_runtime()
                return admissions.complete_command(owner_id, key, {"command_id": command["command_id"], "status": "completed", "configuration_revision": provider_config_revision(load_provider_config(strict=True))})
            raise ProviderConfigError(str(exc)) from None
        if replay is not None:
            validate()
            return replay
        try:
            review = review_provider_configuration(operation, revision, fields, validate=validate)
            validate_review(review)
        except ProviderConfigError as exc:
            admissions.reject_command(owner_id, key, exc.code)
            raise
        def authority() -> None:
            validate()
            validate_review(review)
        def committed(cfg: dict) -> None:
            authority()
            cfg["configuration_command"] = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
        if operation in {"provider.endpoint.create", "provider.endpoint.save"}:
            _, endpoint = _fields(fields)
            cfg = load_provider_config(strict=True)
            old = next((item for item in cfg["custom_endpoints"] if isinstance(item, dict) and item.get("id") == endpoint["id"]), {})
            custom.save_custom_endpoint({**old, **endpoint}, expected_revision=revision, validate=authority, record_commit=committed, manage_secret=False)
        elif operation == "provider.endpoint.delete":
            custom.delete_custom_endpoint_configuration(fields["endpoint_id"], expected_revision=revision, validate=authority, record_commit=committed)
        elif operation in {"provider.model.pin", "provider.model.unpin"}:
            selection.set_reviewed_model_pin(**fields, pinned=operation.endswith(".pin"), expected_revision=revision, validate=authority, record_commit=committed)
        else:
            # Network operations are deliberately outside the writer below.
            raise ProviderConfigError("provider_probe_requires_separate_admission")
        invalidate_provider_runtime()
        result = {"command_id": command["command_id"], "status": "completed", "configuration_revision": provider_config_revision(load_provider_config(strict=True))}
        admissions.complete_command(owner_id, key, result)
        authority()
        return result


def _execute_probe(owner_id: str, key: str, command: dict, validate: Callable[[], None], validate_review: Callable[[dict], None]) -> dict:
    """Admit once, release the writer for network, and never replay uncertainty."""
    operation, payload = command["type"], command["payload"]
    proof = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
    with provider_config_transaction():
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, command, "provider_configuration")
        except admissions.AdmissionError as exc:
            cfg = load_provider_config(strict=True)
            if str(exc) == "operation_uncertain" and cfg.get("configuration_command") == proof:
                validate()
                return admissions.complete_command(owner_id, key, {"command_id": command["command_id"], "status": "completed", "configuration_revision": provider_config_revision(cfg)})
            raise ProviderConfigError(str(exc)) from None
        if replay is not None:
            validate()
            return replay
        try:
            review = review_provider_configuration(operation, payload["configuration_revision"], payload["fields"], validate=validate)
            validate_review(review)
        except ProviderConfigError as exc:
            admissions.reject_command(owner_id, key, exc.code)
            raise
        endpoint_id = payload["fields"]["endpoint_id"]
        endpoint = custom.get_custom_endpoint(endpoint_id)
        captured = custom.endpoint_configuration_revision(endpoint)
        credential_config = load_provider_config(strict=True)["providers"].get(endpoint["provider_id"])
    def authority() -> None:
        validate()
        validate_review(review)
        current = custom.get_custom_endpoint(endpoint_id)
        if current is None or custom.endpoint_configuration_revision(current) != captured or load_provider_config(strict=True)["providers"].get(endpoint["provider_id"]) != credential_config:
            raise ProviderConfigError("revision_conflict")
    authority()
    if operation.endswith(".probe"):
        custom.probe_custom_endpoint(endpoint_id, validate=authority, strict=True)
    else:
        custom.refresh_custom_endpoint_models(endpoint_id, validate=authority, strict=True)
    with provider_config_transaction():
        authority()
        cfg = load_provider_config(strict=True)
        revision = provider_config_revision(cfg)
        cfg["configuration_command"] = proof
        save_provider_config(cfg, expected_revision=revision)
        invalidate_provider_runtime()
        result = {"command_id": command["command_id"], "status": "completed", "configuration_revision": provider_config_revision(cfg)}
        admissions.complete_command(owner_id, key, result)
    validate()
    return result


_TYPES = {"provider.endpoint." + operation for operation in ("create", "save", "delete", "probe", "refresh")} | {"provider.model.pin", "provider.model.unpin"}


def read_provider_configuration_receipt(*, owner_id: str, command_id: str,
                                   validate: Callable[[], None]) -> dict | None:
    """Inspect existing publication evidence without completing or replaying it.

    SQLite may maintain its WAL coordination files; no schema or configuration
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
        raise ProviderConfigError("provider_configuration_unavailable") from None
    validate()
    if row is None or row["target"] != "provider_configuration" or row["type"] not in _TYPES:
        return None
    cfg = load_provider_config(strict=True)
    published = cfg.get("configuration_command") == {"owner_id": owner_id, "key": row["key"], "command_id": command_id}
    status = "completed" if row["status"] == "completed" or published else "rejected" if row["status"] == "rejected" else "uncertain"
    result = {"command_id": command_id, "status": status, "configuration_revision": provider_config_revision(cfg)}
    validate()
    return result
