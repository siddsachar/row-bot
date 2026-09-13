"""Reviewed saved chat defaults, separate from provider runtime activation."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import re
import sys

from row_bot.providers import saved_model_settings as settings
from row_bot.providers.config import ProviderConfigError, load_provider_config, provider_config_transaction
from row_bot.providers.model_catalog import build_saved_model_catalog_rows
from row_bot.providers.selection import model_ref, parse_model_ref
from row_bot.runtime import admissions


@dataclass(frozen=True)
class DefaultModelSnapshot:
    schema_version: int
    revision: str
    selection_ref: str | None
    provider_id: str | None
    model_id: str | None
    saved_state: str
    runtime_state: str = "unknown"


def _identity(provider_id, model_id) -> str:
    if type(provider_id) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", provider_id):
        raise ProviderConfigError("invalid_model_selection")
    if type(model_id) is not str or not model_id.strip() or model_id != model_id.strip() or len(model_id.encode("utf-8", errors="surrogatepass")) > 512 or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in model_id):
        raise ProviderConfigError("invalid_model_selection")
    return model_ref(provider_id, model_id)


def read_default_model(*, validate: Callable[[], None] = lambda: None) -> DefaultModelSnapshot:
    validate()
    raw, revision, _ = settings.read_saved_model_settings()
    stored = raw.get("model")
    parsed = parse_model_ref(stored) if isinstance(stored, str) and len(stored) <= 1024 else None
    reference = None
    if parsed:
        try:
            reference = _identity(*parsed)
        except ProviderConfigError:
            parsed = None
    result = DefaultModelSnapshot(1, revision, reference, parsed[0] if parsed else None,
        parsed[1] if parsed else None, "saved" if parsed else "missing" if stored is None else "unavailable")
    validate()
    return result


def review_default_model(settings_revision: str, provider_id: str, model_id: str, *, validate: Callable[[], None]) -> dict:
    validate()
    reference = _identity(provider_id, model_id)
    snapshot = read_default_model(validate=validate)
    if snapshot.revision != settings_revision:
        raise ProviderConfigError("revision_conflict")
    saved = _saved_catalog()
    rows = build_saved_model_catalog_rows(cloud_cache=saved.cloud_cache, ollama_rows=saved.ollama_rows,
                                          provider_config=load_provider_config(strict=True))
    if not any(row.selection_ref == reference and "chat" in row.categories for row in rows):
        raise ProviderConfigError("model_configuration_unavailable")
    intent = {"settings_revision": settings_revision, "provider_id": provider_id, "model_id": model_id}
    validate()
    return {**intent, "operation": "provider.default_model.save", "action_digest": admissions.keyed_digest(intent), "snapshot": asdict(snapshot)}


def _saved_catalog():
    from row_bot.providers.model_catalog_cache import read_model_catalog_cache
    return read_model_catalog_cache(allow_runtime_bootstrap=False, max_bytes=16 * 1024 * 1024)


def _adopt(reference: str) -> None:
    # Importing models would run legacy settings migration; only notify a runtime
    # that already exists. Cold startup will read the newly published file.
    runtime = sys.modules.get("row_bot.models")
    if runtime is not None:
        runtime.adopt_saved_default(reference)


def execute_default_model(*, owner_id: str, key: str, command: dict, validate: Callable[[], None],
                          validate_review: Callable[[dict], None]) -> dict:
    if command.get("type") != "provider.default_model.save" or not isinstance(command.get("payload"), dict):
        raise ProviderConfigError("invalid_command")
    payload = command["payload"]
    reference = _identity(payload.get("provider_id"), payload.get("model_id"))
    proof = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
    with provider_config_transaction(settings.SETTINGS_PATH):
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, command, "default_model")
        except admissions.AdmissionError as exc:
            raw, _, _ = settings.read_saved_model_settings()
            if str(exc) != "operation_uncertain" or raw.get("default_model_command") != proof:
                raise ProviderConfigError(str(exc)) from None
            validate()
            _adopt(raw["model"])
            return admissions.complete_command(owner_id, key, {"command_id": command["command_id"], "status": "completed", "selection": asdict(read_default_model(validate=validate))})
        if replay is not None:
            validate()
            return replay
        try:
            review = review_default_model(payload.get("settings_revision"), payload["provider_id"], payload["model_id"], validate=validate)
            validate_review({name: value for name, value in review.items() if name != "snapshot"})
        except ProviderConfigError as exc:
            admissions.reject_command(owner_id, key, exc.code)
            raise
        def authority() -> None:
            validate()
            current = review_default_model(payload["settings_revision"], payload["provider_id"], payload["model_id"], validate=validate)
            validate_review({name: value for name, value in current.items() if name != "snapshot"})
        settings.update_saved_model_settings(lambda raw: {**raw, "model": reference, "default_model_command": proof},
            expected_revision=payload["settings_revision"], validate=authority)
        _adopt(reference)
        result = {"command_id": command["command_id"], "status": "completed", "selection": asdict(read_default_model(validate=validate))}
        admissions.complete_command(owner_id, key, result)
        validate()
        return result


def read_default_model_receipt(*, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict | None:
    validate()
    row = admissions.read_command_metadata(owner_id, command_id)
    if row is None or row["target"] != "default_model" or row["type"] != "provider.default_model.save":
        validate()
        return None
    raw, _, _ = settings.read_saved_model_settings()
    published = raw.get("default_model_command") == {"owner_id": owner_id, "key": row["key"], "command_id": command_id}
    status = "completed" if row["status"] == "completed" or published else "rejected" if row["status"] == "rejected" else "uncertain"
    result = {"command_id": command_id, "status": status, "published": published, "selection": asdict(read_default_model(validate=validate))}
    validate()
    return result
