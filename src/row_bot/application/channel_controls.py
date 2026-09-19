"""Bounded client controls over the existing channel registry and auth owners.

The module deliberately does not import channel adapters or their configuration
store at import time.  Passive reads inspect already registered adapters, expose
only coarse activity and credential status, and never send a message.  Effectful
operations are reviewed, admitted once, and delegated to the canonical channel,
configuration, credential, and pairing owners.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import re
import sys
import time
from typing import Any
from uuid import UUID

from row_bot.runtime import admissions


_CHANNEL_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}")
_OPERATIONS = frozenset({"configure", "start", "stop", "pair", "revoke"})
_PAIRING_CHANNELS = frozenset({"discord", "slack", "sms", "whatsapp"})
_MAX_CHANNELS = 128
_MAX_FIELDS = 64
_MAX_IDENTITIES = 100
_MAX_VALUE_BYTES = 16 * 1024


class ChannelControlError(ValueError):
    """A stable client-safe channel control failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identity(value: Any, *, command: bool = False) -> str:
    if type(value) is not str:
        raise ChannelControlError("invalid_command")
    if command:
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise ChannelControlError("invalid_command") from None
    elif not _CHANNEL_ID.fullmatch(value):
        raise ChannelControlError("invalid_command")
    return value


def _bounded_text(value: Any, maximum: int = 160, *, fallback: str = "") -> str:
    if type(value) is not str:
        return fallback
    try:
        if len(value.encode("utf-8")) > maximum or any(
            ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value
        ):
            return fallback
    except UnicodeError:
        return fallback
    return value


def _loaded_owner(module_name: str) -> Any | None:
    """Return an already loaded owner without causing passive startup writes."""

    return sys.modules.get(module_name)


def _passive_secret_owner() -> Any | None:
    """Load only the credential-status owner for unloaded core descriptors."""

    try:
        from row_bot.channels import auth_store
    except Exception:
        return None
    return auth_store


def _registry(owner: Any | None) -> Any:
    if owner is not None:
        return owner
    from row_bot.channels import registry

    return registry


def _owners(
    *,
    registry_owner: Any | None,
    config_owner: Any | None,
    auth_owner: Any | None,
    secret_owner: Any | None,
) -> tuple[Any, Any | None, Any | None, Any | None]:
    registry_owner = _registry(registry_owner)
    return (
        registry_owner,
        config_owner
        if config_owner is not None
        else _loaded_owner("row_bot.channels.config"),
        auth_owner
        if auth_owner is not None
        else _loaded_owner("row_bot.channels.auth"),
        secret_owner
        if secret_owner is not None
        else _loaded_owner("row_bot.channels.auth_store"),
    )


def _channel(registry_owner: Any, channel_id: str) -> Any:
    _identity(channel_id)
    value = registry_owner.get(channel_id)
    if value is None:
        raise ChannelControlError("not_found")
    return value


def _safe_call(callback: Callable[[], Any], default: Any) -> Any:
    try:
        return callback()
    except Exception:
        return default


def _source(registry_owner: Any, channel_id: str) -> dict[str, str]:
    value = _safe_call(lambda: registry_owner.get_source(channel_id), None)
    kind = _bounded_text(getattr(value, "kind", ""), 32, fallback="unknown")
    if kind not in {"core", "plugin"}:
        kind = "unknown"
    label = _bounded_text(getattr(value, "label", ""), 160)
    return {"kind": kind, "label": label}


def _field_status(
    channel: Any,
    field: Any,
    *,
    config_owner: Any | None,
    secret_owner: Any | None,
) -> tuple[dict[str, Any], Any]:
    key = _bounded_text(getattr(field, "key", ""), 128)
    if not key or not _CHANNEL_ID.fullmatch(key):
        raise ChannelControlError("channel_status_unavailable")
    storage = getattr(field, "storage", "")
    field_type = getattr(field, "field_type", "")
    if storage not in {"env", "config"} or field_type not in {
        "text",
        "password",
        "number",
        "slider",
    }:
        raise ChannelControlError("channel_status_unavailable")
    common = {
        "key": key,
        "label": _bounded_text(getattr(field, "label", ""), 160, fallback=key),
        "field_type": field_type,
        "storage": storage,
        "help_text": _bounded_text(getattr(field, "help_text", ""), 512),
    }
    if storage == "env":
        env_key = _bounded_text(getattr(field, "env_key", ""), 128)
        if not env_key or secret_owner is None:
            return (
                {
                    **common,
                    "configured": None,
                    "source": "unavailable",
                    "fingerprint": "",
                    "externally_managed": False,
                    "writable": False,
                },
                {"key": key, "availability": "unavailable"},
            )
        status = _safe_call(
            lambda: secret_owner.channel_secret_status(channel.name, env_key), {}
        )
        if type(status) is not dict:
            status = {}
        source = status.get("source")
        if source not in {
            "",
            "channel keyring",
            "environment",
            "legacy api_keys",
            "secret_file",
            "conflict",
        }:
            source = "unknown"
        fingerprint = _bounded_text(status.get("fingerprint", ""), 128)
        external = status.get("externally_managed") is True
        configured = status.get("configured")
        configured = configured if type(configured) is bool else None
        return (
            {
                **common,
                "configured": configured,
                "source": source,
                "fingerprint": fingerprint,
                "externally_managed": external,
                "writable": (
                    not getattr(channel, "passive_only", False)
                    and not external
                    and "error" not in status
                ),
            },
            {
                "key": key,
                "configured": configured,
                "source": source,
                "fingerprint": fingerprint,
                "externally_managed": external,
                "error": bool(status.get("error")),
            },
        )
    if config_owner is None:
        return (
            {
                **common,
                "configured": None,
                "source": "unavailable",
                "fingerprint": "",
                "externally_managed": False,
                "writable": False,
            },
            {"key": key, "availability": "unavailable"},
        )
    marker = object()
    raw = _safe_call(lambda: config_owner.get(channel.name, key, marker), marker)
    if raw is marker:
        present = False
        proof: Any = None
    elif type(raw) in {str, int, float, bool} or raw is None:
        present = raw not in {None, ""}
        proof = raw
    else:
        raise ChannelControlError("channel_status_unavailable")
    return (
        {
            **common,
            "configured": present,
            "source": "channel config" if present else "",
            "fingerprint": "",
            "externally_managed": False,
            "writable": True,
        },
        {"key": key, "value": proof},
    )


def _activity(channel_id: str, *, clock: Callable[[], float]) -> str:
    base = _loaded_owner("row_bot.channels.base")
    if base is None:
        return "unknown"
    value = _safe_call(lambda: base.get_last_activity(channel_id), None)
    if type(value) not in {int, float}:
        return "none"
    age = max(0.0, clock() - float(value))
    if age < 60:
        return "recent"
    if age < 3600:
        return "within_hour"
    if age < 86400:
        return "within_day"
    return "older"


def _paired(
    channel_id: str, auth_owner: Any | None
) -> tuple[list[dict[str, str]], list[dict[str, str]] | None]:
    if auth_owner is None:
        return [], None
    users = _safe_call(lambda: auth_owner.get_approved_users(channel_id), None)
    names = _safe_call(lambda: auth_owner.get_user_names(channel_id), {})
    if (
        type(users) is not list
        or type(names) is not dict
        or len(users) > _MAX_IDENTITIES
    ):
        return [], None
    result: list[dict[str, str]] = []
    proof: list[dict[str, str]] = []
    for raw in users:
        if type(raw) is not str or not raw or len(raw.encode("utf-8")) > 512:
            return [], None
        opaque = admissions.keyed_digest(
            {"kind": "channel-pairing", "channel_id": channel_id, "user_id": raw},
            read_only=True,
        )
        name = _bounded_text(names.get(raw, ""), 160)
        hint = ("…" + raw[-4:]) if len(raw) > 4 else "paired account"
        result.append({"identity_id": opaque, "display_name": name, "hint": hint})
        proof.append({"identity_id": opaque, "user_id": raw})
    result.sort(key=lambda item: item["identity_id"])
    proof.sort(key=lambda item: item["identity_id"])
    return result, proof


def _snapshot(
    channel: Any,
    *,
    registry_owner: Any,
    config_owner: Any | None,
    auth_owner: Any | None,
    secret_owner: Any | None,
    clock: Callable[[], float],
) -> tuple[dict[str, Any], dict[str, Any]]:
    channel_id = _identity(getattr(channel, "name", None))
    fields = list(getattr(channel, "config_fields", ()) or ())
    if len(fields) > _MAX_FIELDS:
        raise ChannelControlError("channel_status_unavailable")
    public_fields: list[dict[str, Any]] = []
    field_proof: list[dict[str, Any]] = []
    for field in fields:
        public, private = _field_status(
            channel, field, config_owner=config_owner, secret_owner=secret_owner
        )
        public_fields.append(public)
        field_proof.append(private)
    public_fields.sort(key=lambda item: item["key"])
    field_proof.sort(key=lambda item: item["key"])
    passive_only = getattr(channel, "passive_only", False) is True
    configured = _safe_call(channel.is_configured, None)
    running = _safe_call(channel.is_running, None)
    configured = configured if type(configured) is bool else None
    running = running if type(running) is bool else None
    if passive_only:
        required = tuple(getattr(channel, "required_fields", ()) or ())
        by_key = {field["key"]: field for field in public_fields}
        packaged = getattr(channel, "packaged_configuration", None)
        if packaged is not None:
            configured = _safe_call(packaged.is_file, None)
        elif all(key in by_key for key in required):
            configured = all(by_key[key]["configured"] is True for key in required)
        # Passive descriptors are not loaded adapters, so they are stopped in
        # this process even though activity history remains unavailable.
        running = False
    source = _source(registry_owner, channel_id)
    identities, identity_proof = _paired(channel_id, auth_owner)
    pairing_available = (
        "unsupported"
        if passive_only
        else "available"
        if source["kind"] == "core"
        and channel_id in _PAIRING_CHANNELS
        and auth_owner is not None
        else "unsupported"
        if source["kind"] == "core"
        else "unavailable"
    )
    activity = "unknown" if passive_only else _activity(channel_id, clock=clock)
    private = {
        "channel_id": channel_id,
        "source": source,
        "configured": configured,
        "running": running,
        "fields": field_proof,
        "identities": identity_proof,
    }
    revision = hashlib.sha256(
        json.dumps(private, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    capabilities = getattr(channel, "capabilities", None)
    capability_names = (
        "photo_in",
        "voice_in",
        "document_in",
        "photo_out",
        "document_out",
        "buttons",
        "streaming",
        "typing",
        "reactions",
        "slash_commands",
    )
    return (
        {
            "schema_version": 1,
            "channel_id": channel_id,
            "display_name": _bounded_text(
                getattr(channel, "display_name", ""), 160, fallback=channel_id
            ),
            "source": source,
            "revision": revision,
            "configured": configured,
            "running": running,
            "activity": activity,
            "activity_history": (
                [{"kind": "last_inbound", "recency": activity}]
                if activity not in {"none", "unknown"}
                else []
            ),
            "fields": public_fields,
            "paired_identities": identities,
            "capabilities": [
                name
                for name in capability_names
                if getattr(capabilities, name, False) is True
            ],
            "availability": {
                "configuration": "available"
                if all(field["writable"] for field in public_fields)
                else "limited",
                "lifecycle": "available"
                if configured is True and not passive_only
                else "configuration_required",
                "pairing": pairing_available,
                "monitor": "available" if activity != "unknown" else "unavailable",
            },
        },
        private,
    )


def read_channel_status(
    channel_id: str,
    *,
    validate: Callable[[], None] = lambda: None,
    registry_owner: Any | None = None,
    config_owner: Any | None = None,
    auth_owner: Any | None = None,
    secret_owner: Any | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Read one redacted channel status without loading or starting an adapter."""

    validate()
    registry_owner, config_owner, auth_owner, secret_owner = _owners(
        registry_owner=registry_owner,
        config_owner=config_owner,
        auth_owner=auth_owner,
        secret_owner=secret_owner,
    )
    value, _private = _snapshot(
        _channel(registry_owner, channel_id),
        registry_owner=registry_owner,
        config_owner=config_owner,
        auth_owner=auth_owner,
        secret_owner=secret_owner,
        clock=clock,
    )
    validate()
    return value


def read_channels(
    *,
    query: str = "",
    limit: int = 50,
    validate: Callable[[], None] = lambda: None,
    registry_owner: Any | None = None,
    config_owner: Any | None = None,
    auth_owner: Any | None = None,
    secret_owner: Any | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Return one bounded, replacement-style channel page."""

    validate()
    if type(query) is not str or len(query.encode("utf-8")) > 128:
        raise ChannelControlError("invalid_query")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ChannelControlError("invalid_limit")
    use_passive_core = registry_owner is None
    use_default_secret_owner = secret_owner is None
    registry_owner, config_owner, auth_owner, secret_owner = _owners(
        registry_owner=registry_owner,
        config_owner=config_owner,
        auth_owner=auth_owner,
        secret_owner=secret_owner,
    )
    channels = list(registry_owner.all_channels())
    if use_passive_core and not channels:
        from row_bot.channels.passive_catalog import passive_core_channels

        channels = list(passive_core_channels())
        if use_default_secret_owner:
            secret_owner = _passive_secret_owner()
    if len(channels) > _MAX_CHANNELS:
        raise ChannelControlError("channel_status_unavailable")
    needle = query.casefold().strip()
    items: list[dict[str, Any]] = []
    for channel in channels:
        value, _private = _snapshot(
            channel,
            registry_owner=registry_owner,
            config_owner=config_owner,
            auth_owner=auth_owner,
            secret_owner=secret_owner,
            clock=clock,
        )
        if (
            needle
            and needle
            not in (value["channel_id"] + " " + value["display_name"]).casefold()
        ):
            continue
        items.append(value)
    items.sort(key=lambda item: (item["display_name"].casefold(), item["channel_id"]))
    validate()
    return {
        "schema_version": 1,
        "total": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _field(channel: Any, key: str) -> Any:
    fields = list(getattr(channel, "config_fields", ()) or ())
    if len(fields) > _MAX_FIELDS:
        raise ChannelControlError("channel_status_unavailable")
    matches = [field for field in fields if getattr(field, "key", None) == key]
    if len(matches) != 1:
        raise ChannelControlError("not_found")
    return matches[0]


def _configuration_value(field: Any, value: Any) -> Any:
    field_type = getattr(field, "field_type", "")
    storage = getattr(field, "storage", "")
    if storage not in {"env", "config"}:
        raise ChannelControlError("action_unavailable")
    if value is None:
        return None
    if field_type in {"text", "password"}:
        if type(value) is not str:
            raise ChannelControlError("invalid_command")
        try:
            if not 1 <= len(value.encode("utf-8")) <= _MAX_VALUE_BYTES or any(
                ord(char) < 32 or ord(char) == 127 for char in value
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            raise ChannelControlError("invalid_command") from None
        normalized = value.strip()
        if not normalized:
            raise ChannelControlError("invalid_command")
        return normalized
    if field_type in {"number", "slider"}:
        if type(value) not in {int, float}:
            raise ChannelControlError("invalid_command")
        if field_type == "slider" and not (
            getattr(field, "slider_min", 0)
            <= value
            <= getattr(field, "slider_max", 100)
        ):
            raise ChannelControlError("invalid_command")
        return value
    raise ChannelControlError("invalid_command")


def _review(
    channel_id: str,
    revision: str,
    operation: str,
    *,
    field_key: str | None,
    value: Any,
    identity_id: str | None,
    validate: Callable[[], None],
    registry_owner: Any,
    config_owner: Any | None,
    auth_owner: Any | None,
    secret_owner: Any | None,
) -> tuple[dict[str, Any], Any, Any | None]:
    validate()
    _identity(channel_id)
    if type(revision) is not str or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ChannelControlError("invalid_command")
    if operation not in _OPERATIONS:
        raise ChannelControlError("invalid_command")
    channel = _channel(registry_owner, channel_id)
    snapshot, private = _snapshot(
        channel,
        registry_owner=registry_owner,
        config_owner=config_owner,
        auth_owner=auth_owner,
        secret_owner=secret_owner,
        clock=time.time,
    )
    if snapshot["revision"] != revision:
        raise ChannelControlError("revision_conflict")
    normalized: Any = None
    selected: Any | None = None
    if operation == "configure":
        if type(field_key) is not str or identity_id is not None:
            raise ChannelControlError("invalid_command")
        selected = _field(channel, field_key)
        normalized = _configuration_value(selected, value)
        public = next(
            field for field in snapshot["fields"] if field["key"] == field_key
        )
        if not public["writable"]:
            raise ChannelControlError("action_denied")
    elif field_key is not None or value is not None:
        raise ChannelControlError("invalid_command")
    if operation == "start":
        if snapshot["configured"] is not True:
            raise ChannelControlError("configuration_required")
        if snapshot["running"] is True:
            raise ChannelControlError("already_running")
    elif operation == "stop":
        if snapshot["running"] is not True:
            raise ChannelControlError("already_stopped")
    elif operation == "pair":
        if (
            identity_id is not None
            or snapshot["availability"]["pairing"] != "available"
        ):
            raise ChannelControlError("action_unavailable")
    elif operation == "revoke":
        if type(identity_id) is not str or private["identities"] is None:
            raise ChannelControlError("invalid_command")
        if not any(
            item["identity_id"] == identity_id for item in private["identities"]
        ):
            raise ChannelControlError("not_found")
    else:
        if identity_id is not None:
            raise ChannelControlError("invalid_command")
    intent = {
        "channel_id": channel_id,
        "revision": revision,
        "operation": operation,
        "field_key": field_key,
        "value": normalized,
        "identity_id": identity_id,
    }
    review = {
        "channel_id": channel_id,
        "revision": revision,
        "operation": operation,
        "field_key": field_key,
        "identity_id": identity_id,
        "action_digest": admissions.keyed_digest(intent),
    }
    validate()
    return review, selected, normalized


def review_channel_command(
    channel_id: str,
    revision: str,
    operation: str,
    *,
    field_key: str | None = None,
    value: Any = None,
    identity_id: str | None = None,
    validate: Callable[[], None],
    registry_owner: Any | None = None,
    config_owner: Any | None = None,
    auth_owner: Any | None = None,
    secret_owner: Any | None = None,
) -> dict[str, Any]:
    """Review one exact channel action without invoking the adapter."""

    owners = _owners(
        registry_owner=registry_owner,
        config_owner=config_owner,
        auth_owner=auth_owner,
        secret_owner=secret_owner,
    )
    return _review(
        channel_id,
        revision,
        operation,
        field_key=field_key,
        value=value,
        identity_id=identity_id,
        validate=validate,
        registry_owner=owners[0],
        config_owner=owners[1],
        auth_owner=owners[2],
        secret_owner=owners[3],
    )[0]


def _public_receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


async def execute_channel_command(
    *,
    owner_id: str,
    key: str,
    command: dict[str, Any],
    validate: Callable[[], None],
    validate_review: Callable[[dict[str, Any]], None],
    registry_owner: Any | None = None,
    config_owner: Any | None = None,
    auth_owner: Any | None = None,
    secret_owner: Any | None = None,
) -> dict[str, Any]:
    """Run one reviewed operation; an uncertain original is never replayed."""

    validate()
    if type(command) is not dict or command.get("type") != "channel.control":
        raise ChannelControlError("invalid_command")
    _identity(command.get("command_id"), command=True)
    payload = command.get("payload")
    if type(payload) is not dict or set(payload) != {
        "channel_id",
        "revision",
        "operation",
        "field_key",
        "value",
        "identity_id",
    }:
        raise ChannelControlError("invalid_command")
    owners = _owners(
        registry_owner=registry_owner,
        config_owner=config_owner,
        auth_owner=auth_owner,
        secret_owner=secret_owner,
    )
    channel_id = payload["channel_id"]
    operation = payload["operation"]
    target = f"settings:channel:{channel_id}"
    initial = {
        "command_id": command["command_id"],
        "status": "partial",
        "code": "channel_operation_unconfirmed",
    }
    try:
        replay = admissions.claim_command(
            owner_id,
            key,
            command,
            target,
            exclusive_target=True,
            initial_result=initial,
        )
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise ChannelControlError(str(error)) from None
        retained = admissions.read_command_receipt(owner_id, command["command_id"])
        return _public_receipt(retained or initial)
    if replay is not None:
        validate()
        return _public_receipt(replay)
    try:
        review, field, normalized = _review(
            channel_id,
            payload["revision"],
            operation,
            field_key=payload["field_key"],
            value=payload["value"],
            identity_id=payload["identity_id"],
            validate=validate,
            registry_owner=owners[0],
            config_owner=owners[1],
            auth_owner=owners[2],
            secret_owner=owners[3],
        )
        validate_review(review)
    except Exception as error:
        admissions.reject_command(
            owner_id, key, getattr(error, "code", "action_denied")
        )
        raise

    def authority() -> None:
        current = _review(
            channel_id,
            payload["revision"],
            operation,
            field_key=payload["field_key"],
            value=payload["value"],
            identity_id=payload["identity_id"],
            validate=validate,
            registry_owner=owners[0],
            config_owner=owners[1],
            auth_owner=owners[2],
            secret_owner=owners[3],
        )[0]
        if current != review:
            raise ChannelControlError("channel_review_stale")
        validate_review(current)

    channel = _channel(owners[0], channel_id)
    code: str | None = None
    pairing_code: str | None = None
    try:
        authority()
        if operation == "configure":
            if getattr(field, "storage", "") == "env":
                if owners[3] is None:
                    raise ChannelControlError("action_unavailable")
                env_key = getattr(field, "env_key", "")
                if normalized is None:
                    owners[3].delete_channel_secret(channel_id, env_key)
                else:
                    owners[3].set_channel_secret(channel_id, env_key, normalized)
            else:
                if owners[1] is None:
                    raise ChannelControlError("action_unavailable")
                owners[1].set(channel_id, getattr(field, "key", ""), normalized)
            _safe_call(owners[0].clear_agent_cache_if_loaded, None)
        elif operation == "start":
            started = await channel.start()
            if started is not True:
                code = "channel_start_failed"
            elif owners[1] is not None:
                owners[1].set(channel_id, "auto_start", True)
            _safe_call(owners[0].clear_agent_cache_if_loaded, None)
        elif operation == "stop":
            await channel.stop()
            if owners[1] is not None:
                owners[1].set(channel_id, "auto_start", False)
            _safe_call(owners[0].clear_agent_cache_if_loaded, None)
        elif operation == "pair":
            if owners[2] is None:
                raise ChannelControlError("action_unavailable")
            pairing_code = owners[2].generate_pairing_code(channel_id)
            if type(pairing_code) is not str or not re.fullmatch(
                r"[A-Z0-9]{8}", pairing_code
            ):
                raise ChannelControlError("channel_pairing_unconfirmed")
        elif operation == "revoke":
            if owners[2] is None:
                raise ChannelControlError("action_unavailable")
            _current, private = _snapshot(
                channel,
                registry_owner=owners[0],
                config_owner=owners[1],
                auth_owner=owners[2],
                secret_owner=owners[3],
                clock=time.time,
            )
            matched = next(
                (
                    item
                    for item in (private["identities"] or [])
                    if item["identity_id"] == payload["identity_id"]
                ),
                None,
            )
            if (
                matched is None
                or owners[2].revoke_user(channel_id, matched["user_id"]) is not True
            ):
                raise ChannelControlError("channel_revoke_unconfirmed")
        else:
            raise ChannelControlError("invalid_command")
        result = {
            "command_id": command["command_id"],
            "status": "completed",
            "channel": read_channel_status(
                channel_id,
                validate=validate,
                registry_owner=owners[0],
                config_owner=owners[1],
                auth_owner=owners[2],
                secret_owner=owners[3],
            ),
            "operation": operation,
            "code": code,
            **({"pairing_code": pairing_code} if pairing_code is not None else {}),
        }
        return _public_receipt(admissions.complete_command(owner_id, key, result))
    except Exception:
        # The adapter or a publication following it may have completed.  Keep
        # the admitted original uncertain and never invoke it on a retry.
        retained = admissions.read_command_receipt(owner_id, command["command_id"])
        return _public_receipt(retained or initial)


def read_channel_receipt(
    channel_id: str,
    *,
    owner_id: str,
    command_id: str,
    validate: Callable[[], None],
) -> dict[str, Any] | None:
    """Passively read one authenticated, bounded channel command receipt."""

    validate()
    _identity(channel_id)
    _identity(command_id, command=True)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None:
        validate()
        return None
    if (
        metadata.get("target") != f"settings:channel:{channel_id}"
        or metadata.get("type") != "channel.control"
    ):
        raise ChannelControlError("channel_operation_unavailable")
    value = admissions.read_command_receipt(owner_id, command_id)
    validate()
    return _public_receipt(value) if value is not None else None
