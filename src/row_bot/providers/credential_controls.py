"""Explicit provider credential controls shared by client presentations.

Secrets remain in the existing secret store. Reading status never probes a
provider, and receipts contain only public status and a keyed request verifier.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import threading

from row_bot.providers import auth_store
from row_bot.providers.config import load_provider_config, provider_config_transaction
from row_bot.runtime import admissions

_LOCK = threading.RLock()


class CredentialControlError(ValueError):
    def __init__(self, code: str, current_revision: str | None = None):
        self.code = code
        self.current_revision = current_revision
        super().__init__(code)


def _provider(provider_id: str) -> None:
    if provider_id not in auth_store.PROVIDER_API_KEY_ENV:
        raise CredentialControlError("not_found")


def normalize_api_key(provider_id: str, value: str) -> str:
    _provider(provider_id)
    if not isinstance(value, str) or len(value) > 16384:
        raise CredentialControlError("invalid_command")
    value = value.strip()
    if provider_id == "ollama_cloud":
        from row_bot.providers.transports.ollama_cloud import normalize_ollama_cloud_api_key
        value = normalize_ollama_cloud_api_key(value)
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CredentialControlError("invalid_command")
    return value


def credential_snapshot(provider_id: str) -> dict:
    """Return a redacted saved state, with no credential fragments or errors."""
    _provider(provider_id)
    with _LOCK:
        status = auth_store.provider_secret_status(provider_id)
        entry = load_provider_config(strict=True).get("providers", {}).get(provider_id, {})
        revision = hashlib.sha256(json.dumps({"status": status, "entry": entry},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        sources = {"environment", "secret_file", "conflict", "session", "keyring", "encrypted_file", "api_keys"}
        source = status.get("source")
        return {"schema_version": 1, "provider_id": provider_id, "revision": revision,
            "configured": bool(status.get("configured")), "source": source if source in sources else "unknown",
            "externally_managed": bool(status.get("externally_managed")),
            "storage_unavailable": bool(status.get("error")),
            "recovery_available": isinstance(entry.get("credential_previous"), dict),
            "runtime_state": "unknown"}


def save_api_key(provider_id: str, value: str, *, validate: Callable[[], None] = lambda: None,
                 command_proof: dict | None = None) -> dict:
    """Persist an explicitly supplied key using the retained credential owner."""
    value = normalize_api_key(provider_id, value)
    with _LOCK:
        return auth_store.replace_provider_api_key(provider_id, value, validate=validate, command_proof=command_proof)


def clear_api_key(provider_id: str, *, validate: Callable[[], None] = lambda: None,
                  command_proof: dict | None = None) -> dict:
    _provider(provider_id)
    with _LOCK:
        status = auth_store.provider_secret_status(provider_id, "api_key")
        if status.get("externally_managed"):
            return status
        return auth_store.replace_provider_api_key(provider_id, None, validate=validate, command_proof=command_proof)


def apply_credential_command(provider_id: str, *, owner_id: str, key: str, command: dict,
                             validate: Callable[[], None], invalidate: Callable[[], None]) -> dict:
    """Admit a bounded explicit save/clear; an interrupted write is not replayed."""
    _provider(provider_id)
    if command.get("type") not in {"provider.credential.save", "provider.credential.clear", "provider.credential.restore"}:
        raise CredentialControlError("invalid_command")
    if command["type"] == "provider.credential.save":
        normalize_api_key(provider_id, command.get("payload", {}).get("value"))
    with _LOCK, provider_config_transaction():
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, command, provider_id)
        except admissions.AdmissionError as exc:
            if str(exc) == "operation_uncertain":
                entry = load_provider_config(strict=True).get("providers", {}).get(provider_id, {})
                if entry.get("credential_command") == {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}:
                    validate()
                    invalidate()
                    result = {"command_id": command["command_id"], "status": "completed", "credential": credential_snapshot(provider_id)}
                    admissions.complete_command(owner_id, key, result)
                    return result
            raise CredentialControlError(str(exc), exc.current_revision) from exc
        if replay is not None:
            validate()
            return replay
        current = credential_snapshot(provider_id)
        try:
            if current["revision"] != command.get("expected_revision"):
                raise CredentialControlError("revision_conflict", current["revision"])
            if current["externally_managed"] or (current["storage_unavailable"] and command["type"] != "provider.credential.restore"):
                raise CredentialControlError("action_denied")
            validate()
        except CredentialControlError as exc:
            admissions.reject_command(owner_id, key, exc.code, exc.current_revision)
            raise
        # Once the store is called, failure may follow a partial effect. Keep
        # the original admission uncertain and require a fresh status read.
        proof = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
        try:
            if command["type"] == "provider.credential.save":
                save_api_key(provider_id, command["payload"]["value"], validate=validate, command_proof=proof)
            elif command["type"] == "provider.credential.clear":
                clear_api_key(provider_id, validate=validate, command_proof=proof)
            else:
                auth_store.replace_provider_api_key(provider_id, None, restore=True, validate=validate, command_proof=proof)
        except Exception:
            raise CredentialControlError("provider_credential_unconfirmed") from None
        invalidate()
        result = {"command_id": command["command_id"], "status": "completed",
                  "credential": credential_snapshot(provider_id)}
        admissions.complete_command(owner_id, key, result)
        validate()
        return result
