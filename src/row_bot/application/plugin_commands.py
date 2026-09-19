"""Passive plugin views and reviewed client-side settings commands.

This adapter deliberately does not add a second plugin installer.  The current
installer has no recoverable client-command worker for install, update, or
removal, so those capabilities are reported as unavailable.  Enablement and
configuration continue through the canonical plugin state and loader owners.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import UUID

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime import admissions

_ID = re.compile(r"[a-z][a-z0-9-]{1,63}")
_ACTIONS = {
    "plugin.enable",
    "plugin.disable",
    "plugin.configure",
    "plugin.install",
    "plugin.update",
    "plugin.remove",
}
_EXECUTABLE = {"plugin.enable", "plugin.disable", "plugin.configure"}
_MAX_JSON = 16 * 1024 * 1024
_MAX_MANIFEST = 512 * 1024
_MAX_PLUGINS = 512
_MAX_FIELDS = 128


class PluginCommandError(ValueError):
    """Stable public failure code for plugin client operations."""


def _error(code: str) -> PluginCommandError:
    return PluginCommandError(code)


def _plugin_id(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise _error("invalid_plugin_command")
    return value


def _safe_component(path: Path, *, directory: bool | None = None) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError:
        raise _error("plugin_catalog_unavailable") from None
    reparse = bool(getattr(value, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(value.st_mode) or reparse:
        raise _error("plugin_catalog_unavailable")
    if directory is True and not stat.S_ISDIR(value.st_mode):
        raise _error("plugin_catalog_unavailable")
    if directory is False and (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1):
        raise _error("plugin_catalog_unavailable")
    return value


def _root() -> Path:
    return get_row_bot_data_dir(create=False).absolute()


def _read_json(path: Path, maximum: int, *, missing: object = None) -> object:
    try:
        path.lstat()
    except FileNotFoundError:
        return missing
    except OSError:
        raise _error("plugin_catalog_unavailable") from None
    before = _safe_component(path, directory=False)
    if before.st_size > maximum:
        raise _error("plugin_catalog_unavailable")
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            encoded = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        final = _safe_component(path, directory=False)
        identities = {
            (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
            for item in (before, opened, after, final)
        }
        if len(encoded) > maximum or len(identities) != 1:
            raise ValueError
        return json.loads(encoded)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise _error("plugin_catalog_unavailable") from None


def _state_documents(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    state_value = _read_json(root / "plugin_state.json", _MAX_JSON, missing={})
    secret_value = _read_json(root / "plugin_secrets.json", _MAX_JSON, missing={})
    if type(state_value) is not dict or type(secret_value) is not dict:
        raise _error("plugin_catalog_unavailable")
    return state_value, secret_value


def _secret_status(document: dict[str, Any], plugin_id: str) -> dict[str, bool]:
    if document.get("version") == 2:
        plugins = document.get("plugins", {})
        values = plugins.get(plugin_id, {}) if type(plugins) is dict else {}
        if type(values) is not dict:
            raise _error("plugin_catalog_unavailable")
        return {
            str(key): bool(type(value) is dict and value.get("configured") is True)
            for key, value in list(values.items())[:_MAX_FIELDS]
            if type(key) is str
        }
    # Legacy files may contain plaintext. Presence is the only public fact.
    values = document.get(plugin_id, {})
    if type(values) is not dict:
        return {}
    return {
        str(key): bool(type(value) is str and value)
        for key, value in list(values.items())[:_MAX_FIELDS]
        if type(key) is str
    }


def _field_specs(value: object) -> dict[str, dict[str, Any]]:
    if type(value) is not dict or len(value) > _MAX_FIELDS:
        raise _error("plugin_catalog_unavailable")
    result: dict[str, dict[str, Any]] = {}
    for name, spec in value.items():
        if type(name) is not str or not name or type(spec) is not dict:
            raise _error("plugin_catalog_unavailable")
        field_type = str(spec.get("type", "text"))
        result[name] = {
            "label": str(spec.get("label") or name)[:128],
            "type": field_type,
            "required": spec.get("required") is True,
            "options": [str(item)[:256] for item in spec.get("options", [])[:128]]
            if type(spec.get("options", [])) is list
            else [],
            **({"minimum": spec.get("min")} if type(spec.get("min")) in {int, float} else {}),
            **({"maximum": spec.get("max")} if type(spec.get("max")) in {int, float} else {}),
        }
    return result


def _manifest(path: Path) -> tuple[object, dict[str, Any], str]:
    raw = _read_json(path / "plugin.json", _MAX_MANIFEST)
    if type(raw) is not dict:
        raise _error("plugin_catalog_unavailable")
    try:
        from row_bot.plugins.manifest import _validate

        manifest = _validate(raw, path)
    except Exception:
        raise _error("plugin_catalog_unavailable") from None
    revision = hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return manifest, raw, revision


def _installed(root: Path) -> list[tuple[object, dict[str, Any], str]]:
    plugins = root / "installed_plugins"
    if not plugins.exists():
        return []
    _safe_component(plugins, directory=True)
    try:
        children = sorted(plugins.iterdir(), key=lambda item: item.name)
    except OSError:
        raise _error("plugin_catalog_unavailable") from None
    if len(children) > _MAX_PLUGINS:
        raise _error("plugin_catalog_unavailable")
    result = []
    for child in children:
        if _ID.fullmatch(child.name) is None:
            continue
        _safe_component(child, directory=True)
        item = _manifest(child)
        if item[0].id != child.name:
            raise _error("plugin_catalog_unavailable")
        result.append(item)
    return result


def _marketplace(root: Path) -> dict[str, dict[str, Any]]:
    raw = _read_json(root / "marketplace_cache.json", _MAX_JSON, missing={})
    if type(raw) is not dict:
        raise _error("plugin_catalog_unavailable")
    values = raw.get("plugins", [])
    if type(values) is not list or len(values) > 2000:
        raise _error("plugin_catalog_unavailable")
    result = {}
    for entry in values:
        if type(entry) is not dict or type(entry.get("id")) is not str:
            continue
        plugin_id = entry["id"]
        if _ID.fullmatch(plugin_id) is None or plugin_id in result:
            continue
        provides = entry.get("provides", {})
        result[plugin_id] = {
            "id": plugin_id,
            "name": str(entry.get("name") or plugin_id)[:256],
            "version": str(entry.get("version") or "")[:64],
            "description": str(entry.get("description") or "")[:2048],
            "tags": [str(item)[:64] for item in entry.get("tags", [])[:64]]
            if type(entry.get("tags", [])) is list
            else [],
            "verified": entry.get("verified") is True,
            "permissions": [str(item)[:64] for item in entry.get("permissions", [])[:64]]
            if type(entry.get("permissions", [])) is list
            else [],
            "provides": {
                key: max(0, value if type(value) is int else len(value) if type(value) is list else 0)
                for key, value in (provides.items() if type(provides) is dict else [])
                if key in {"native_tools", "mcp_servers", "channels", "skills"}
            },
        }
    return result


def _revision(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _newer(candidate: object, installed: object) -> bool:
    def parts(value: object) -> tuple[int, ...]:
        result = []
        for part in str(value).replace("-", ".").split("."):
            if not part.isdigit():
                break
            result.append(int(part))
        return tuple(result)

    return bool(parts(candidate)) and parts(candidate) > parts(installed)


def _health(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("health", {})
    if type(value) is not dict or not value:
        return {"status": "unknown", "checks": []}
    checks = value.get("checks", [])
    public = []
    if type(checks) is list:
        for check in checks[:64]:
            if type(check) is dict:
                public.append({
                    "label": str(check.get("label") or "Check")[:128],
                    "status": str(check.get("status") or "unknown")[:64],
                })
    return {"status": "passed" if value.get("ok") is True else "failed", "checks": public}


def _capabilities(*, installed: bool, enabled: bool, setup: bool, healthy: bool) -> dict[str, dict[str, Any]]:
    unavailable = {"available": False, "code": "plugin_lifecycle_worker_unavailable"}
    return {
        "install": dict(unavailable),
        "update": dict(unavailable),
        "remove": dict(unavailable),
        "configure": {
            "available": installed and not enabled,
            "code": None if installed and not enabled else "disable_plugin_to_configure",
        },
        "enable": {
            "available": installed and not enabled and setup and healthy,
            "code": None if installed and not enabled and setup and healthy else "plugin_setup_or_test_required",
        },
        "disable": {"available": installed and enabled, "code": None if installed and enabled else "plugin_already_disabled"},
    }


def _catalog(validate: Callable[[], None]) -> tuple[list[dict[str, Any]], str]:
    validate()
    root = _root()
    try:
        root.lstat()
    except FileNotFoundError:
        validate()
        return [], _revision([])
    except OSError:
        raise _error("plugin_catalog_unavailable") from None
    for component in (*reversed(root.parents), root):
        _safe_component(component, directory=True)
    state, secrets = _state_documents(root)
    cached = _marketplace(root)
    items: list[dict[str, Any]] = []
    seen = set()
    for manifest, _raw, manifest_revision in _installed(root):
        seen.add(manifest.id)
        record = state.get(manifest.id, {})
        if type(record) is not dict:
            raise _error("plugin_catalog_unavailable")
        settings_source = manifest.settings.get("config", manifest.settings)
        secrets_source = manifest.secrets or manifest.settings.get("api_keys", {})
        settings = _field_specs(settings_source)
        secret_specs = _field_specs(secrets_source)
        config = record.get("config", {})
        if type(config) is not dict:
            raise _error("plugin_catalog_unavailable")
        configured_secrets = _secret_status(secrets, manifest.id)
        setup = all(not spec["required"] or config.get(name) not in (None, "", []) for name, spec in settings.items())
        setup = setup and all(not spec["required"] or configured_secrets.get(name, False) for name, spec in secret_specs.items())
        health = _health(record)
        enabled = record.get("enabled") is True
        market = cached.get(manifest.id)
        item = {
            "plugin_id": manifest.id,
            "name": str(manifest.name)[:256],
            "version": str(manifest.version)[:64],
            "description": str(manifest.description)[:2048],
            "source": "installed",
            "installed": True,
            "enabled": enabled,
            "setup_complete": setup,
            "health": health["status"],
            "update_version": market["version"] if market and _newer(market["version"], manifest.version) else None,
            "permissions": [str(value)[:64] for value in manifest.permissions[:64]],
            "provides": {
                "native_tools": manifest.native_tool_count,
                "mcp_servers": manifest.mcp_server_count,
                "channels": manifest.channel_count,
                "skills": manifest.skill_count,
            },
            "manifest_revision": manifest_revision,
            "capabilities": _capabilities(installed=True, enabled=enabled, setup=setup, healthy=health["status"] == "passed"),
        }
        items.append(item)
    for plugin_id, market in cached.items():
        if plugin_id in seen:
            continue
        items.append({
            "plugin_id": plugin_id,
            **{key: deepcopy(market[key]) for key in ("name", "version", "description", "permissions", "provides")},
            "source": "marketplace",
            "installed": False,
            "enabled": False,
            "setup_complete": False,
            "health": "unknown",
            "update_version": None,
            "manifest_revision": None,
            "capabilities": _capabilities(installed=False, enabled=False, setup=False, healthy=False),
        })
    items.sort(key=lambda item: (str(item["name"]).casefold(), item["plugin_id"]))
    catalog_revision = _revision(items)
    validate()
    return items, catalog_revision


def read_plugin_catalog(*, query: str = "", source: str = "all", cursor: str | None = None,
                        limit: int = 50, validate: Callable[[], None]) -> dict[str, Any]:
    """Read installed and cached marketplace metadata without importing plugins."""
    if type(query) is not str or len(query) > 256 or source not in {"all", "installed", "marketplace"}:
        raise _error("invalid_plugin_query")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise _error("invalid_plugin_query")
    items, revision = _catalog(validate)
    needle = query.strip().casefold()
    values = [item for item in items if (source == "all" or item["source"] == source)
              and (not needle or needle in f"{item['name']} {item['plugin_id']} {item['description']}".casefold())]
    offset = 0
    if cursor:
        try:
            saved, raw_offset = cursor.rsplit(":", 1)
            offset = int(raw_offset)
            if saved != revision or not 0 < offset < len(values):
                raise ValueError
        except (AttributeError, ValueError):
            raise _error("cursor_expired") from None
    page = values[offset:offset + limit]
    validate()
    return {"schema_version": 1, "revision": revision, "availability": "available", "items": page,
            "total": len(values), "next_cursor": f"{revision}:{offset + limit}" if offset + limit < len(values) else None}


def read_plugin_detail(plugin_id: str, *, validate: Callable[[], None]) -> dict[str, Any]:
    plugin_id = _plugin_id(plugin_id)
    validate()
    root = _root()
    try:
        root.lstat()
    except FileNotFoundError:
        raise _error("plugin_not_found")
    except OSError:
        raise _error("plugin_catalog_unavailable") from None
    for component in (*reversed(root.parents), root):
        _safe_component(component, directory=True)
    state, secrets = _state_documents(root)
    target = None
    for manifest, raw, manifest_revision in _installed(root):
        if manifest.id == plugin_id:
            target = (manifest, raw, manifest_revision)
            break
    if target is None:
        raise _error("plugin_not_found")
    manifest, _raw, manifest_revision = target
    record = state.get(plugin_id, {})
    if type(record) is not dict:
        raise _error("plugin_catalog_unavailable")
    config = record.get("config", {})
    if type(config) is not dict:
        raise _error("plugin_catalog_unavailable")
    settings_source = manifest.settings.get("config", manifest.settings)
    secrets_source = manifest.secrets or manifest.settings.get("api_keys", {})
    settings = _field_specs(settings_source)
    secret_specs = _field_specs(secrets_source)
    configured_secrets = _secret_status(secrets, plugin_id)
    fields = []
    for name, spec in settings.items():
        present = name in config and config[name] not in (None, "", [])
        # Configuration reads expose status and schema only. Values can contain
        # local paths, account identifiers, or plugin-specific credentials even
        # when a manifest labels the field as ordinary text.
        fields.append({"name": name, **spec, "configured": present, "value": None})
    secret_fields = [{"name": name, **spec, "value": None,
                      "configured": configured_secrets.get(name, False)} for name, spec in secret_specs.items()]
    health = _health(record)
    enabled = record.get("enabled") is True
    setup = all(not field["required"] or field["configured"] for field in fields + secret_fields)
    detail = {
        "schema_version": 1,
        "plugin_id": plugin_id,
        "revision": _revision({"manifest": manifest_revision, "enabled": enabled, "config": config,
                               "secrets": configured_secrets, "health": health}),
        "name": str(manifest.name)[:256],
        "version": str(manifest.version)[:64],
        "description": str(manifest.description)[:2048],
        "enabled": enabled,
        "settings": fields,
        "secrets": secret_fields,
        "health": health,
        "permissions": [str(value)[:64] for value in manifest.permissions[:64]],
        "capabilities": _capabilities(installed=True, enabled=enabled, setup=setup, healthy=health["status"] == "passed"),
    }
    validate()
    return detail


def _validate_value(spec: dict[str, Any], value: object) -> object:
    kind = spec["type"]
    if kind == "checkbox" and type(value) is not bool:
        raise _error("invalid_plugin_command")
    if kind == "number" and type(value) not in {int, float}:
        raise _error("invalid_plugin_command")
    if kind == "multi-select" and (type(value) is not list or len(value) > 128 or any(type(item) is not str for item in value)):
        raise _error("invalid_plugin_command")
    if kind not in {"checkbox", "number", "multi-select"} and type(value) is not str:
        raise _error("invalid_plugin_command")
    try:
        if len(json.dumps(value, allow_nan=False, ensure_ascii=False).encode()) > 64 * 1024:
            raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise _error("invalid_plugin_command") from None
    if spec.get("options") and value not in spec["options"] and not (kind == "multi-select" and all(item in spec["options"] for item in value)):
        raise _error("invalid_plugin_command")
    return deepcopy(value)


def review_plugin_command(action: str, payload: dict[str, Any], *, validate: Callable[[], None]) -> dict[str, Any]:
    validate()
    if action not in _ACTIONS or type(payload) is not dict:
        raise _error("invalid_plugin_command")
    if action not in _EXECUTABLE:
        raise _error("plugin_lifecycle_worker_unavailable")
    expected = {"plugin_id", "revision", "settings", "secrets"} if action == "plugin.configure" else {"plugin_id", "revision"}
    if payload.keys() != expected or type(payload.get("revision")) is not str:
        raise _error("invalid_plugin_command")
    detail = read_plugin_detail(payload["plugin_id"], validate=validate)
    if detail["revision"] != payload["revision"]:
        raise _error("plugin_changed")
    capability = action.removeprefix("plugin.")
    if not detail["capabilities"][capability]["available"]:
        raise _error(str(detail["capabilities"][capability]["code"]))
    intent: dict[str, Any] = {"plugin_id": detail["plugin_id"], "revision": detail["revision"], "action": action}
    changes: dict[str, Any] = {}
    if action == "plugin.configure":
        settings, secrets = payload["settings"], payload["secrets"]
        if type(settings) is not dict or type(secrets) is not dict or len(settings) + len(secrets) > _MAX_FIELDS:
            raise _error("invalid_plugin_command")
        setting_specs = {field["name"]: field for field in detail["settings"]}
        secret_specs = {field["name"]: field for field in detail["secrets"]}
        if not settings.keys() <= setting_specs.keys() or not secrets.keys() <= secret_specs.keys():
            raise _error("invalid_plugin_command")
        checked_settings = {name: _validate_value(setting_specs[name], value) for name, value in settings.items()}
        checked_secrets = {}
        for name, value in secrets.items():
            if value is not None and (type(value) is not str or not value or len(value.encode()) > 16 * 1024):
                raise _error("invalid_plugin_command")
            checked_secrets[name] = value
        intent.update(settings=checked_settings, secrets=checked_secrets)
        changes = {"settings": sorted(checked_settings),
                   "secrets": {name: "clear" if value is None else "replace" for name, value in sorted(checked_secrets.items())}}
    digest = admissions.keyed_digest(intent)
    validate()
    return {"schema_version": 1, "plugin_id": detail["plugin_id"], "action": action,
            "revision": detail["revision"], "action_digest": digest, "changes": changes,
            "disclosures": (["The plugin remains disabled while its saved configuration changes."]
                            if action == "plugin.configure" else
                            ["Enabling loads the reviewed plugin through the existing sandbox and registration gates."]
                            if action == "plugin.enable" else
                            ["Disabling revokes tools, channels, webhooks, and in-flight registration."])}


def _uuid(value: object) -> str:
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise _error("invalid_plugin_command") from None
    return value


def _public_receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value[key]) for key in ("command_id", "status", "code", "plugin") if key in value}


def read_plugin_receipt(plugin_id: str, command_id: str, *, owner_id: str,
                        validate: Callable[[], None]) -> dict[str, Any] | None:
    validate()
    plugin_id, command_id = _plugin_id(plugin_id), _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None:
        validate()
        return None
    if metadata["target"] != "settings:plugin:" + plugin_id or metadata["type"] not in _EXECUTABLE:
        return None
    value = admissions.read_command_receipt(owner_id, command_id)
    if value is None:
        return None
    if metadata["status"] not in {"completed", "rejected"}:
        value = {"command_id": command_id, "status": "partial", "code": "plugin_operation_unconfirmed"}
    validate()
    return _public_receipt(value)


def execute_plugin_command(*, owner_id: str, key: str, command: dict[str, Any], validate: Callable[[], None],
                           validate_review: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    validate()
    if type(command) is not dict:
        raise _error("invalid_plugin_command")
    command_id = _uuid(command.get("command_id"))
    action, payload = command.get("type"), command.get("payload")
    if key != command_id or action not in _EXECUTABLE or type(payload) is not dict:
        raise _error("invalid_plugin_command")
    expected = {"plugin_id", "revision", "settings", "secrets", "action_digest"} if action == "plugin.configure" else {"plugin_id", "revision", "action_digest"}
    if payload.keys() != expected or type(payload.get("action_digest")) is not str:
        raise _error("invalid_plugin_command")
    plugin_id = _plugin_id(payload.get("plugin_id"))
    previous = admissions.read_command_metadata(owner_id, command_id)
    if previous is not None:
        try:
            saved = admissions.claim_command(owner_id, key, command, "settings:plugin:" + plugin_id)
            if saved:
                return _public_receipt(saved)
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise
        saved_receipt = read_plugin_receipt(plugin_id, command_id, owner_id=owner_id, validate=validate)
        if saved_receipt is None:
            raise _error("plugin_operation_unconfirmed")
        return saved_receipt
    review_payload = {name: deepcopy(value) for name, value in payload.items() if name != "action_digest"}
    review = review_plugin_command(action, review_payload, validate=validate)
    if review["action_digest"] != payload["action_digest"]:
        raise _error("plugin_review_changed")
    validate_review(review)
    target = "settings:plugin:" + plugin_id
    admissions.claim_command(owner_id, key, command, target, exclusive_target=True,
                             initial_result={"command_id": command_id, "status": "accepted",
                                             "plugin": {"plugin_id": plugin_id, "action": action}})
    result: dict[str, Any]
    try:
        validate()
        from row_bot.plugins import state as plugin_state

        if action == "plugin.configure":
            for name, value in review_payload["settings"].items():
                validate()
                plugin_state.set_plugin_config(plugin_id, name, value)
                if plugin_state.get_plugin_config(plugin_id, name) != value:
                    raise _error("plugin_configuration_unconfirmed")
            for name, value in review_payload["secrets"].items():
                validate()
                if value is None:
                    plugin_state.delete_plugin_secret(plugin_id, name)
                    if plugin_state.get_plugin_secret(plugin_id, name) is not None:
                        raise _error("plugin_configuration_unconfirmed")
                else:
                    plugin_state.set_plugin_secret(plugin_id, name, value)
                    if plugin_state.get_plugin_secret(plugin_id, name) != value:
                        raise _error("plugin_configuration_unconfirmed")
            enabled = False
        else:
            enabled = action == "plugin.enable"
            validate()
            plugin_state.set_plugin_enabled(plugin_id, enabled)
            if plugin_state.is_plugin_enabled(plugin_id) is not enabled:
                raise _error("plugin_enablement_unconfirmed")
            validate()
            from row_bot.plugins import loader

            loaded = loader.refresh_plugin_runtime("reviewed plugin enablement")
            if enabled and not any(item.plugin_id == plugin_id and item.success for item in loaded):
                raise _error("plugin_runtime_unavailable")
        validate()
        detail = read_plugin_detail(plugin_id, validate=validate)
        result = {"command_id": command_id, "status": "completed", "plugin": {
            "plugin_id": plugin_id, "action": action, "enabled": detail["enabled"], "revision": detail["revision"]}}
    except Exception:
        # Once admitted, never infer rollback or replay.  The original receipt
        # remains the sole recovery handle and contains no setting/secret value.
        result = {"command_id": command_id, "status": "partial", "code": "plugin_operation_unconfirmed",
                  "plugin": {"plugin_id": plugin_id, "action": action}}
    admissions.complete_command(owner_id, key, result)
    return _public_receipt(result)
