from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import hmac
import logging
import os
import re
from typing import Any, Callable
import uuid

import row_bot.api_keys as api_keys
import row_bot.secret_store as secret_store

from row_bot.providers.config import (
    load_provider_config, provider_config_revision, provider_config_transaction,
    save_provider_config, update_provider_config,
)
from row_bot.providers.models import AuthMethod, ProviderHealth

logger = logging.getLogger(__name__)

PROVIDER_API_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "ollama_cloud": "OLLAMA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "requesty": "REQUESTY_API_KEY",
    "opencode_zen": "OPENCODE_ZEN_API_KEY",
    "opencode_go": "OPENCODE_GO_API_KEY",
    "atlascloud": "ATLASCLOUD_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}
PROVIDER_SECRET_CHUNK_SIZE = 512
OAUTH_PROVIDER_IDS = frozenset({"codex", "claude_subscription", "xai_oauth"})
OAUTH_SECRET_NAMES = ("access_token", "refresh_token", "id_token", "user_id", "account")
CHUNK_MARKER_SUFFIX = "__chunks"
CHUNK_VALUE_PREFIX = "v1:"
_session_provider_secrets: dict[tuple[str, str], str] = {}
_last_storage_warning = ""


def _serialized_provider_writer(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with provider_config_transaction():
            return function(*args, **kwargs)
    return wrapped


def _active_api_key(provider_id: str) -> dict | None:
    entry = load_provider_config(strict=True).get("providers", {}).get(provider_id, {})
    return entry.get("credential_ref") if isinstance(entry, dict) else None


def _custom_credential_endpoint(provider_id: str, cfg: dict) -> dict | None:
    if not re.fullmatch(r"custom_openai_[a-z0-9][a-z0-9_-]{0,63}", provider_id):
        return None
    endpoint_id = provider_id.removeprefix("custom_openai_")
    return next((item for item in cfg.get("custom_endpoints", [])
                 if isinstance(item, dict) and item.get("id") == endpoint_id), None)


def _custom_credential_scope_matches(provider_id: str, cfg: dict) -> bool:
    endpoint = _custom_credential_endpoint(provider_id, cfg)
    entry = cfg.get("providers", {}).get(provider_id, {})
    return endpoint is not None and endpoint.get("credential_scope") == entry.get("credential_scope")


def _uses_staged_api_key(provider_id: str) -> bool:
    if provider_id in PROVIDER_API_KEY_ENV:
        return _active_api_key(provider_id) is not None
    if not re.fullmatch(r"custom_openai_[a-z0-9][a-z0-9_-]{0,63}", provider_id):
        return False
    cfg = load_provider_config(strict=True)
    return _custom_credential_endpoint(provider_id, cfg) is not None or isinstance(cfg.get("providers", {}).get(provider_id, {}).get("credential_ref"), dict)


def _read_api_key_ref(provider_id: str, reference: dict) -> str:
    return _read_immutable_secret_ref(provider_id, reference, "api_key")


def _read_immutable_secret_ref(provider_id: str, reference: dict, slot: str) -> str:
    if not isinstance(reference, dict):
        raise secret_store.SecretStoreError("invalid saved credential reference")
    if reference == {"cleared": True}:
        return ""
    generation, count = reference.get("generation"), reference.get("chunks")
    if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation) or type(count) is not int or not 1 <= count <= 32:
        raise secret_store.SecretStoreError("invalid saved credential reference")
    parts = []
    for index in range(count):
        part = secret_store.get_secret(f"{slot}.g.{generation}.{index:02d}", namespace=_namespace(provider_id))
        if not isinstance(part, str) or not part or len(part) > PROVIDER_SECRET_CHUNK_SIZE:
            raise secret_store.SecretStoreError("saved credential is unavailable")
        parts.append(part)
    return "".join(parts)


def _delete_immutable_secret_ref(provider_id: str, reference: object, slot: str) -> None:
    """Best-effort deletion for an unpublished or superseded generation."""
    if not isinstance(reference, dict) or reference in ({"cleared": True}, {"legacy": True}):
        return
    generation, count = reference.get("generation"), reference.get("chunks")
    if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation) or type(count) is not int or not 1 <= count <= 32:
        return
    for index in range(count):
        try:
            secret_store.delete_secret(f"{slot}.g.{generation}.{index:02d}", namespace=_namespace(provider_id))
        except secret_store.SecretStoreError:
            logger.warning("Could not retire superseded provider credential storage for %s", provider_id)


def _stage_immutable_secret(provider_id: str, value: str, slot: str, validate: Callable[[], None]) -> dict:
    generation = uuid.uuid4().hex
    chunks = [value[index:index + PROVIDER_SECRET_CHUNK_SIZE] for index in range(0, len(value), PROVIDER_SECRET_CHUNK_SIZE)]
    storage = ""
    reference = {"generation": generation, "chunks": len(chunks), "storage": storage}
    try:
        for index, chunk in enumerate(chunks):
            validate()
            storage = secret_store.set_secret(f"{slot}.g.{generation}.{index:02d}", chunk, namespace=_namespace(provider_id))
            reference["storage"] = storage
            if storage not in {"keyring", "encrypted_file"}:
                raise secret_store.SecretStoreError("durable credential storage unavailable")
        if not hmac.compare_digest(_read_immutable_secret_ref(provider_id, reference, slot).encode(), value.encode()):
            raise secret_store.SecretStoreError("credential storage verification failed")
    except Exception:
        _delete_immutable_secret_ref(provider_id, reference, slot)
        raise
    return reference


def _delete_oauth_bundle_ref(provider_id: str, reference: object) -> None:
    if not isinstance(reference, dict) or not isinstance(reference.get("values"), dict):
        return
    for name, secret_reference in reference["values"].items():
        if name in OAUTH_SECRET_NAMES:
            _delete_immutable_secret_ref(provider_id, secret_reference, f"oauth.{name}")


def _external_api_key(provider_id: str) -> str:
    """Retain file/environment authority; distinguish our legacy env projection."""
    env_var = PROVIDER_API_KEY_ENV.get(provider_id)
    if not env_var:
        return ""
    file_value = secret_store.read_server_secret(env_var, allowed_names=frozenset(PROVIDER_API_KEY_ENV.values()))
    env_value = os.environ.get(env_var, "")
    if file_value:
        if env_value and env_value != file_value:
            raise secret_store.SecretStoreError("provider secret file conflicts with environment")
        return file_value
    if env_value:
        stored = api_keys._get_stored_key(env_var) or api_keys._legacy_plaintext_keys().get(env_var, "")
        if env_value != stored and env_value != api_keys._session_keys.get(env_var):
            return env_value
    return ""


@_serialized_provider_writer
def replace_provider_api_key(provider_id: str, value: str | None, *,
                             validate: Callable[[], None] = lambda: None, restore: bool = False,
                             command_proof: dict | None = None,
                             publish: Callable[[dict[str, Any]], None] | None = None,
                             prepare_config: Callable[[dict[str, Any]], None] | None = None) -> dict:
    """Verify a staged secure value before publishing its canonical reference.

    Previous bytes remain in their existing secure owner for explicit recovery.
    No provider call, session fallback, or delete-before-write is permitted.
    """
    custom_provider = bool(re.fullmatch(r"custom_openai_[a-z0-9][a-z0-9_-]{0,63}", provider_id))
    if (provider_id not in PROVIDER_API_KEY_ENV and not custom_provider) or (value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 16384)):
        raise ValueError("invalid_command")
    validate()
    if _external_api_key(provider_id):
        raise secret_store.SecretStoreError("externally managed provider secret is read-only")
    cfg = load_provider_config(strict=True)
    revision = provider_config_revision(cfg)
    if prepare_config is not None:
        prepare_config(cfg)
    entry = cfg.setdefault("providers", {}).setdefault(provider_id, {})
    endpoint = _custom_credential_endpoint(provider_id, cfg) if custom_provider else None
    if custom_provider and endpoint is None and (value is not None or restore):
        raise ValueError("not_found")
    superseded = entry.get("credential_previous")
    previous = entry.get("credential_ref", {"legacy": True})
    reference = {"cleared": True}
    staged_reference: dict | None = None
    storage = ""
    if restore:
        reference = entry.get("credential_ref", {"legacy": True}) if custom_provider and not _custom_credential_scope_matches(provider_id, cfg) else entry.get("credential_previous")
        if not isinstance(reference, dict):
            raise ValueError("provider_recovery_unavailable")
        if reference != {"legacy": True}:
            _read_api_key_ref(provider_id, reference)
        else:
            # The original legacy store is retained, never reconstructed from metadata.
            env_var = PROVIDER_API_KEY_ENV.get(provider_id)
            if custom_provider and _get_session_provider_secret(provider_id, "api_key"):
                raise ValueError("provider_recovery_unavailable")
            if not ((api_keys.get_key(env_var) if env_var else "") or _get_provider_secret_value(provider_id, "api_key")):
                raise ValueError("provider_recovery_unavailable")
        storage = str(reference.get("storage") or "keyring")
    elif value is not None:
        reference = _stage_immutable_secret(provider_id, value, "api_key", validate)
        staged_reference = reference
        storage = reference["storage"]
    try:
        validate()
        if _external_api_key(provider_id):
            raise secret_store.SecretStoreError("externally managed provider secret is read-only")
        if reference == {"legacy": True}:
            entry.pop("credential_ref", None)
        else:
            entry["credential_ref"] = reference
        entry["credential_previous"] = previous
        entry.update({"provider_id": provider_id, "auth_method": AuthMethod.API_KEY.value,
                      "configured": reference != {"cleared": True}, "source": storage,
                      "secret_storage": storage, "health": ProviderHealth.UNKNOWN.value,
                      "fingerprint": "", "last_error": "",
                      "updated_at": datetime.now(timezone.utc).isoformat()})
        if command_proof is not None:
            entry["credential_command"] = dict(command_proof)
        else:
            entry.pop("credential_command", None)
        if endpoint is not None:
            scope = endpoint.get("credential_scope")
            if not isinstance(scope, str) or not re.fullmatch(r"[a-f0-9]{32}", scope):
                scope = uuid.uuid4().hex
                endpoint["credential_scope"] = scope
            entry["credential_scope"] = scope
        if publish is not None:
            publish(cfg)
        validate()
        save_provider_config(cfg, expected_revision=revision)
    except Exception:
        if staged_reference is not None:
            _delete_immutable_secret_ref(provider_id, staged_reference, "api_key")
        raise
    if superseded not in (reference, previous):
        _delete_immutable_secret_ref(provider_id, superseded, "api_key")
    # A saved legacy key may have been copied into os.environ by api_keys.apply_keys.
    # Retire only that exact app projection after publication; retain stored bytes.
    env_var = PROVIDER_API_KEY_ENV.get(provider_id)
    projected = os.environ.get(env_var) if env_var else None
    if projected and projected == (api_keys._get_stored_key(env_var) or api_keys._legacy_plaintext_keys().get(env_var, "")):
        os.environ.pop(env_var, None)
    return provider_secret_status(provider_id)


def _namespace(provider_id: str) -> str:
    return f"providers:{provider_id}"


def _oauth_bundle_values(provider_id: str, reference: dict) -> dict[str, str]:
    if reference == {"cleared": True}:
        return {name: "" for name in OAUTH_SECRET_NAMES}
    if not isinstance(reference, dict) or set(reference) != {"values"} or not isinstance(reference["values"], dict) or not set(reference["values"]).issubset(OAUTH_SECRET_NAMES):
        raise secret_store.SecretStoreError("invalid_oauth_bundle_reference")
    return {name: _read_immutable_secret_ref(provider_id, reference["values"][name], f"oauth.{name}")
            if name in reference["values"] else "" for name in OAUTH_SECRET_NAMES}


@_serialized_provider_writer
def read_provider_oauth_bundle_snapshot(provider_id: str) -> tuple[dict[str, str], dict, str]:
    """Capture one credential generation and metadata; never refresh accounts."""
    if provider_id not in OAUTH_PROVIDER_IDS:
        raise ValueError("invalid_subscription_provider")
    cfg = load_provider_config(strict=True)
    entry = cfg.get("providers", {}).get(provider_id, {})
    if "oauth_bundle_ref" in entry:
        values = _oauth_bundle_values(provider_id, entry["oauth_bundle_ref"])
    else:
        values = {name: get_provider_secret(provider_id, name) for name in OAUTH_SECRET_NAMES}
    return values, dict(entry), provider_config_revision(cfg)


@_serialized_provider_writer
def replace_provider_oauth_bundle(provider_id: str, values: dict[str, str] | None, *,
                                 update_metadata: Callable[[dict], None],
                                 expected_revision: str | None = None,
                                 validate: Callable[[], None] = lambda: None,
                                 command_proof: dict | None = None) -> dict:
    """Publish verified immutable token generations together with account metadata.

    None disconnects through a tombstone. Previous secure bytes are retained;
    no session fallback or external CLI credential mutation is allowed.
    """
    from row_bot.providers.config import ProviderConfigError
    if provider_id not in OAUTH_PROVIDER_IDS or (values is not None and (not isinstance(values, dict) or not set(values).issubset(OAUTH_SECRET_NAMES))):
        raise ValueError("invalid_subscription_credentials")
    if values is not None:
        for value in values.values():
            if not isinstance(value, str) or len(value.encode("utf-8", errors="surrogatepass")) > 16384 or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("invalid_subscription_credentials")
    validate()
    cfg = load_provider_config(strict=True)
    revision = provider_config_revision(cfg)
    if expected_revision is not None and revision != expected_revision:
        raise ProviderConfigError("revision_conflict")
    old = cfg.get("providers", {}).get(provider_id, {})
    old_previous = old.get("oauth_bundle_previous")
    superseded = old_previous.get("reference") if isinstance(old_previous, dict) else None
    previous = {"reference": old.get("oauth_bundle_ref", {"legacy": True}),
                "metadata": {key: value for key, value in old.items() if key not in {"oauth_bundle_previous", "oauth_bundle_ref", "oauth_bundle_command"}}}
    if values is None and old.get("oauth_bundle_ref") == {"cleared": True} and isinstance(old.get("oauth_bundle_previous"), dict):
        previous = old["oauth_bundle_previous"]
    staged_values: dict[str, dict] = {}
    reference: dict = {"cleared": True}
    try:
        if values is not None:
            for name, value in values.items():
                if value:
                    staged_values[name] = _stage_immutable_secret(provider_id, value, f"oauth.{name}", validate)
            reference = {"values": staged_values}
        update_metadata(cfg)
        entry = cfg.setdefault("providers", {}).setdefault(provider_id, {})
        entry.update({"provider_id": provider_id, "oauth_bundle_ref": reference, "oauth_bundle_previous": previous})
        if values is None:
            entry.update({"configured": False, "health": ProviderHealth.MISSING_AUTH.value})
        else:
            entry["secret_storage"] = "keyring" if all(ref["storage"] == "keyring" for ref in reference["values"].values()) else "encrypted_file"
        if command_proof is not None:
            entry["oauth_bundle_command"] = dict(command_proof)
        else:
            entry.pop("oauth_bundle_command", None)
        validate()
        save_provider_config(cfg, expected_revision=revision)
    except Exception:
        _delete_oauth_bundle_ref(provider_id, {"values": staged_values})
        raise
    retained_references = (reference, previous.get("reference"))
    if superseded not in retained_references:
        _delete_oauth_bundle_ref(provider_id, superseded)
    for name in OAUTH_SECRET_NAMES:
        _delete_session_provider_secret(provider_id, name)
    return dict(entry)


@_serialized_provider_writer
def disconnect_provider_oauth_metadata(provider_id: str, *, update_metadata: Callable[[dict], None],
                                       expected_revision: str | None = None,
                                       validate: Callable[[], None] = lambda: None) -> None:
    """Retain token accessibility for the legacy metadata-only disconnect option."""
    from row_bot.providers.config import ProviderConfigError
    if provider_id not in OAUTH_PROVIDER_IDS:
        raise ValueError("invalid_subscription_provider")
    validate()
    cfg = load_provider_config(strict=True)
    revision = provider_config_revision(cfg)
    if expected_revision is not None and revision != expected_revision:
        raise ProviderConfigError("revision_conflict")
    previous = cfg.get("providers", {}).get(provider_id, {})
    retained = {name: previous[name] for name in ("oauth_bundle_ref", "oauth_bundle_previous") if name in previous}
    update_metadata(cfg)
    if retained:
        cfg.setdefault("providers", {}).setdefault(provider_id, {}).update({**retained, "provider_id": provider_id, "configured": False})
    validate()
    save_provider_config(cfg, expected_revision=revision)


def _credential_name(credential_name: str) -> str:
    return str(credential_name or "api_key").strip() or "api_key"


def _session_key(provider_id: str, name: str) -> tuple[str, str]:
    return (str(provider_id).strip(), _credential_name(name))


def _get_session_provider_secret(provider_id: str, name: str) -> str:
    return _session_provider_secrets.get(_session_key(provider_id, name), "")


def _set_session_provider_secret(provider_id: str, name: str, value: str) -> None:
    _session_provider_secrets[_session_key(provider_id, name)] = str(value)


def _delete_session_provider_secret(provider_id: str, name: str) -> None:
    _session_provider_secrets.pop(_session_key(provider_id, name), None)


def _chunk_marker_name(name: str) -> str:
    return f"{name}.{CHUNK_MARKER_SUFFIX}"


def _chunk_name(name: str, index: int) -> str:
    return f"{name}.__chunk.{index:04d}"


def _chunk_count(provider_id: str, name: str) -> int:
    try:
        marker = secret_store.get_secret(_chunk_marker_name(name), namespace=_namespace(provider_id)) or ""
    except secret_store.SecretStoreError:
        if secret_store.persistent_server_store_configured():
            raise
        return 0
    if marker.startswith(CHUNK_VALUE_PREFIX):
        marker = marker.removeprefix(CHUNK_VALUE_PREFIX)
    try:
        return max(0, int(marker))
    except ValueError:
        return 0


def _delete_chunked_provider_secret(provider_id: str, name: str) -> None:
    count = _chunk_count(provider_id, name)
    for index in range(count):
        try:
            secret_store.delete_secret(_chunk_name(name, index), namespace=_namespace(provider_id))
        except secret_store.SecretStoreError:
            if secret_store.persistent_server_store_configured():
                raise
            pass
    try:
        secret_store.delete_secret(_chunk_marker_name(name), namespace=_namespace(provider_id))
    except secret_store.SecretStoreError:
        if secret_store.persistent_server_store_configured():
            raise
        pass


def _set_provider_secret_value(provider_id: str, name: str, value: str) -> str:
    _delete_chunked_provider_secret(provider_id, name)
    try:
        secret_store.delete_secret(name, namespace=_namespace(provider_id))
    except secret_store.SecretStoreError:
        if secret_store.persistent_server_store_configured():
            raise
        pass
    text = str(value)
    if len(text) <= PROVIDER_SECRET_CHUNK_SIZE:
        try:
            return secret_store.set_secret(name, text, namespace=_namespace(provider_id))
        except secret_store.SecretStoreError:
            if secret_store.persistent_server_store_configured():
                raise
            pass
    chunks = [text[index:index + PROVIDER_SECRET_CHUNK_SIZE] for index in range(0, len(text), PROVIDER_SECRET_CHUNK_SIZE)]
    if not chunks:
        chunks = [""]
    storage_source = ""
    for index, chunk in enumerate(chunks):
        storage_source = secret_store.set_secret(
            _chunk_name(name, index),
            chunk,
            namespace=_namespace(provider_id),
        )
    storage_source = secret_store.set_secret(
        _chunk_marker_name(name),
        f"{CHUNK_VALUE_PREFIX}{len(chunks)}",
        namespace=_namespace(provider_id),
    )
    return storage_source


def _get_provider_secret_value(provider_id: str, name: str) -> str:
    session_value = _get_session_provider_secret(provider_id, name)
    if session_value:
        return session_value
    count = _chunk_count(provider_id, name)
    if count:
        parts: list[str] = []
        for index in range(count):
            try:
                part = secret_store.get_secret(_chunk_name(name, index), namespace=_namespace(provider_id)) or ""
            except secret_store.SecretStoreError:
                if secret_store.persistent_server_store_configured():
                    raise
                return ""
            parts.append(part)
        return "".join(parts)
    try:
        return secret_store.get_secret(name, namespace=_namespace(provider_id)) or ""
    except secret_store.SecretStoreError:
        if secret_store.persistent_server_store_configured():
            raise
        return ""


@_serialized_provider_writer
def set_provider_secret(
    provider_id: str,
    credential_name: str,
    value: str,
    *,
    source: str = "keyring",
    auth_method: AuthMethod | str | None = None,
) -> None:
    provider_id = str(provider_id).strip()
    name = _credential_name(credential_name)
    text = str(value)
    if provider_id in OAUTH_PROVIDER_IDS and name in OAUTH_SECRET_NAMES:
        cfg = load_provider_config(strict=True)
        if "oauth_bundle_ref" in cfg.get("providers", {}).get(provider_id, {}):
            values, _, revision = read_provider_oauth_bundle_snapshot(provider_id)
            values[name] = text
            replace_provider_oauth_bundle(provider_id, values, expected_revision=revision,
                update_metadata=lambda current: current["providers"][provider_id].update({"configured": bool(values["access_token"])}))
            return
    if name == "api_key" and _uses_staged_api_key(provider_id):
        replace_provider_api_key(provider_id, text)
        return
    if name == "api_key":
        env_var = PROVIDER_API_KEY_ENV.get(provider_id)
        if env_var and secret_store.read_server_secret(
            env_var,
            allowed_names=frozenset(PROVIDER_API_KEY_ENV.values()),
        ):
            raise secret_store.SecretStoreError(
                "externally managed provider secret is read-only"
            )
    storage_source = "keyring"
    metadata_source = source
    try:
        storage_source = _set_provider_secret_value(provider_id, name, text)
        _delete_session_provider_secret(provider_id, name)
        _clear_storage_warning()
    except secret_store.SecretStoreError as exc:
        _set_session_provider_secret(provider_id, name, text)
        storage_source = "session"
        if source == "keyring":
            metadata_source = "session"
        _set_storage_warning(
            f"Secure provider secret storage is unavailable; {provider_id}/{name} is saved for this session only."
        )
        logger.warning("Using session-only provider secret storage for %s/%s: %s", provider_id, name, exc)
    fingerprint = secret_store.fingerprint(value)
    now = datetime.now(timezone.utc).isoformat()
    method = auth_method or (AuthMethod.API_KEY if name == "api_key" else AuthMethod.CUSTOM)
    method_value = method.value if isinstance(method, AuthMethod) else str(method)

    def _update(cfg: dict[str, Any]) -> None:
        providers = cfg.setdefault("providers", {})
        entry = providers.setdefault(provider_id, {})
        entry.update({
            "provider_id": provider_id,
            "auth_method": method_value,
            "health": ProviderHealth.CONNECTED.value,
            "configured": True,
            "source": metadata_source,
            "secret_storage": storage_source,
            "fingerprint": fingerprint,
            "updated_at": now,
            "last_error": "",
        })

    update_provider_config(_update)


def get_provider_secret(provider_id: str, credential_name: str = "api_key") -> str:
    provider_id = str(provider_id).strip()
    name = _credential_name(credential_name)
    if provider_id in OAUTH_PROVIDER_IDS and name in OAUTH_SECRET_NAMES:
        cfg = load_provider_config(strict=True)
        entry = cfg.get("providers", {}).get(provider_id, {})
        if "oauth_bundle_ref" in entry:
            return _oauth_bundle_values(provider_id, entry["oauth_bundle_ref"])[name]
    if name == "api_key" and provider_id.startswith("custom_openai_"):
        cfg = load_provider_config(strict=True)
        entry = cfg.get("providers", {}).get(provider_id, {})
        endpoint = _custom_credential_endpoint(provider_id, cfg)
        if entry.get("credential_scope") is not None or entry.get("credential_ref") is not None or (entry and endpoint and endpoint.get("credential_scope")):
            if not _custom_credential_scope_matches(provider_id, cfg):
                return ""
            if entry.get("credential_ref") is not None:
                return _read_api_key_ref(provider_id, entry["credential_ref"])
    if name == "api_key" and provider_id in PROVIDER_API_KEY_ENV:
        external = _external_api_key(provider_id)
        if external:
            return external
        active = _active_api_key(provider_id)
        if active is not None:
            return _read_api_key_ref(provider_id, active)
    if name == "api_key":
        env_var = PROVIDER_API_KEY_ENV.get(provider_id)
        if env_var:
            file_value = secret_store.read_server_secret(
                env_var,
                allowed_names=frozenset(PROVIDER_API_KEY_ENV.values()),
            )
            env_value = os.environ.get(env_var, "")
            if file_value and env_value and file_value != env_value:
                raise secret_store.SecretStoreError(
                    "provider secret file conflicts with environment"
                )
            if file_value:
                return file_value
            legacy_value = api_keys.get_key(env_var)
            if legacy_value:
                return legacy_value
    return _get_provider_secret_value(provider_id, name)


@_serialized_provider_writer
def delete_provider_secret(provider_id: str, credential_name: str = "api_key") -> None:
    provider_id = str(provider_id).strip()
    name = _credential_name(credential_name)
    if provider_id in OAUTH_PROVIDER_IDS and name in OAUTH_SECRET_NAMES:
        cfg = load_provider_config(strict=True)
        if "oauth_bundle_ref" in cfg.get("providers", {}).get(provider_id, {}):
            values, _, revision = read_provider_oauth_bundle_snapshot(provider_id)
            values[name] = ""
            replace_provider_oauth_bundle(provider_id, values, expected_revision=revision,
                update_metadata=lambda current: current["providers"][provider_id].update({"configured": bool(values["access_token"])}))
            return
    if name == "api_key" and _uses_staged_api_key(provider_id):
        replace_provider_api_key(provider_id, None)
        return
    if name == "api_key":
        env_var = PROVIDER_API_KEY_ENV.get(provider_id)
        if env_var and secret_store.read_server_secret(
            env_var,
            allowed_names=frozenset(PROVIDER_API_KEY_ENV.values()),
        ):
            raise secret_store.SecretStoreError(
                "externally managed provider secret is read-only"
            )
    _delete_session_provider_secret(provider_id, name)
    _delete_chunked_provider_secret(provider_id, name)
    try:
        secret_store.delete_secret(name, namespace=_namespace(provider_id))
    except secret_store.SecretStoreError:
        pass
    if name == "api_key":
        env_var = PROVIDER_API_KEY_ENV.get(provider_id)
        if env_var:
            status = api_keys.key_status(env_var)
            if status.get("configured") and status.get("source") != "environment":
                api_keys.delete_key(env_var)

    def _update(cfg: dict[str, Any]) -> None:
        entry = cfg.setdefault("providers", {}).setdefault(provider_id, {})
        entry.update({
            "provider_id": provider_id,
            "configured": bool(get_provider_secret(provider_id, name)),
            "health": ProviderHealth.UNKNOWN.value,
            "fingerprint": "",
            "last_error": "",
        })

    update_provider_config(_update)


def provider_secret_status(provider_id: str, credential_name: str = "api_key") -> dict[str, Any]:
    provider_id = str(provider_id).strip()
    name = _credential_name(credential_name)
    if provider_id in OAUTH_PROVIDER_IDS and name in OAUTH_SECRET_NAMES:
        cfg = load_provider_config(strict=True)
        entry = cfg.get("providers", {}).get(provider_id, {})
        if "oauth_bundle_ref" in entry:
            try:
                value = _oauth_bundle_values(provider_id, entry["oauth_bundle_ref"])[name]
                return {"configured": bool(value), "source": str(entry.get("secret_storage") or ""), "fingerprint": secret_store.fingerprint(value)}
            except (secret_store.SecretStoreError, ValueError):
                return {"configured": False, "source": "", "fingerprint": "", "error": "secure storage unavailable"}
    if name == "api_key" and provider_id.startswith("custom_openai_"):
        cfg = load_provider_config(strict=True)
        entry = cfg.get("providers", {}).get(provider_id, {})
        endpoint = _custom_credential_endpoint(provider_id, cfg)
        if entry.get("credential_scope") is not None or entry.get("credential_ref") is not None or (entry and endpoint and endpoint.get("credential_scope")):
            if not _custom_credential_scope_matches(provider_id, cfg):
                return {"configured": False, "source": "", "fingerprint": "", "recovery_required": True}
            if entry.get("credential_ref") is not None:
                try:
                    value = _read_api_key_ref(provider_id, entry["credential_ref"])
                    return {"configured": bool(value), "source": entry["credential_ref"].get("storage", ""), "fingerprint": secret_store.fingerprint(value)}
                except (secret_store.SecretStoreError, ValueError):
                    return {"configured": False, "source": "", "fingerprint": "", "error": "secure storage unavailable"}
    if name == "api_key" and provider_id in PROVIDER_API_KEY_ENV:
        try:
            external = _external_api_key(provider_id)
            if external:
                env_var = PROVIDER_API_KEY_ENV[provider_id]
                file_value = secret_store.read_server_secret(env_var, allowed_names=frozenset(PROVIDER_API_KEY_ENV.values()))
                return {"configured": True, "source": "secret_file" if file_value else "environment",
                        "externally_managed": True, "fingerprint": secret_store.fingerprint(external)}
            active = _active_api_key(provider_id)
            if active is not None:
                value = _read_api_key_ref(provider_id, active)
                return {"configured": bool(value), "source": active.get("storage", ""),
                        "fingerprint": secret_store.fingerprint(value)}
        except (secret_store.SecretStoreError, ValueError):
            return {"configured": False, "source": "", "fingerprint": "",
                    "error": "secure storage unavailable", "externally_managed": bool(os.environ.get(PROVIDER_API_KEY_ENV[provider_id]))}
    value = ""
    source = ""
    if name == "api_key" and provider_id in PROVIDER_API_KEY_ENV:
        env_var = PROVIDER_API_KEY_ENV[provider_id]
        try:
            file_value = secret_store.read_server_secret(
                env_var,
                allowed_names=frozenset(PROVIDER_API_KEY_ENV.values()),
            )
        except secret_store.SecretStoreError as exc:
            return {
                "configured": False,
                "source": "secret_file",
                "fingerprint": "",
                "externally_managed": True,
                "error": str(exc),
            }
        env_value = os.environ.get(env_var, "")
        if file_value:
            if env_value and env_value != file_value:
                return {
                    "configured": False,
                    "source": "conflict",
                    "fingerprint": "",
                    "externally_managed": True,
                    "error": "secret file conflicts with environment",
                }
            return {
                "configured": True,
                "source": "secret_file",
                "fingerprint": secret_store.fingerprint(file_value),
                "externally_managed": True,
            }
        status = api_keys.key_status(env_var)
        value = api_keys.get_key(env_var)
        source = str(status.get("source") or "")
        if value:
            fingerprint = str(status.get("fingerprint") or secret_store.fingerprint(value))
            if env_value and source == "keyring":
                try:
                    stored_value = secret_store.get_secret(env_var) or ""
                except secret_store.SecretStoreError:
                    stored_value = ""
                is_override = (
                    env_value != stored_value
                    if stored_value
                    else secret_store.fingerprint(env_value) != fingerprint
                )
                if is_override:
                    return {
                        "configured": True,
                        "source": "environment",
                        "fingerprint": secret_store.fingerprint(env_value),
                    }
            return {
                "configured": True,
                "source": source or "api_keys",
                "fingerprint": fingerprint,
            }
    session_value = _get_session_provider_secret(provider_id, name)
    if session_value:
        return {
            "configured": True,
            "source": "session",
            "fingerprint": secret_store.fingerprint(session_value),
        }
    try:
        value = _get_provider_secret_value(provider_id, name)
        provider_config = load_provider_config().get("providers", {}).get(provider_id, {})
        configured_storage = str(provider_config.get("secret_storage") or "")
        source = (
            "encrypted_file"
            if value and configured_storage == "encrypted_file"
            else "keyring" if value else ""
        )
    except secret_store.SecretStoreError:
        return {
            "configured": False,
            "source": "",
            "fingerprint": "",
            "error": "secure storage unavailable",
        }
    return {
        "configured": bool(value),
        "source": source,
        "fingerprint": secret_store.fingerprint(value),
    }


def get_storage_warning() -> str:
    return _last_storage_warning


def _set_storage_warning(message: str) -> None:
    global _last_storage_warning
    _last_storage_warning = message


def _clear_storage_warning() -> None:
    global _last_storage_warning
    _last_storage_warning = ""


def _clear_session_secrets_for_tests() -> None:
    _session_provider_secrets.clear()
    _clear_storage_warning()
