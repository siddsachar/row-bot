"""Reviewed metadata-only CLI references and saved xAI OAuth client options."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from row_bot.providers.config import (
    ProviderConfigError, load_provider_config, provider_config_revision, provider_config_transaction,
)
from row_bot.runtime import admissions

COMMANDS = {
    "reference": "provider.subscription.reference",
    "client_id_save": "provider.subscription.client_id.save",
    "client_id_reset": "provider.subscription.client_id.reset",
}


@dataclass(frozen=True)
class SubscriptionReferenceState:
    provider_id: str
    metadata_saved: bool


@dataclass(frozen=True)
class SubscriptionOptionsSnapshot:
    schema_version: int
    revision: str
    references: tuple[SubscriptionReferenceState, ...]
    xai_client_id_source: str
    xai_saved_client_id: str | None
    xai_default_available: bool
    runtime_state: str = "unknown"


def _client_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~:+/=-]{1,512}", value):
        raise ProviderConfigError("invalid_subscription_options")
    return value


def read_options(*, validate: Callable[[], None] = lambda: None) -> SubscriptionOptionsSnapshot:
    validate()
    from row_bot.providers import xai_oauth
    cfg = load_provider_config(strict=True)
    providers = cfg.get("providers", {})
    entry = providers.get("xai_oauth", {})
    saved = xai_oauth._client_id_override_from_entry(entry)
    try:
        saved = _client_id(saved) if saved else None
    except ProviderConfigError:
        saved = None
    environment = bool(os.environ.get(xai_oauth.ROW_BOT_XAI_OAUTH_CLIENT_ID_ENV, "").strip())
    default = bool(xai_oauth.xai_oauth_default_client_id())
    result = SubscriptionOptionsSnapshot(1, provider_config_revision(cfg), tuple(
        SubscriptionReferenceState(provider, providers.get(provider, {}).get("source") == "external_cli"
            and providers.get(provider, {}).get("external_reference_exists") is True)
        for provider in ("codex", "claude_subscription")),
        "environment" if environment else "override" if saved else "default" if default else "missing",
        saved, default)
    validate()
    return result


def _path_state(target: Path) -> tuple:
    """Reject redirected components; identities stay private to this operation."""
    result = []
    for part in reversed((target, *target.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            result.append((str(part), None))
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProviderConfigError("subscription_reference_unavailable")
        result.append((str(part), info.st_dev, info.st_ino, info.st_mode))
    return tuple(result)


def _capture_file(target: Path) -> tuple[bool, tuple]:
    target = target.expanduser().absolute()
    paths = _path_state(target)
    try:
        before = target.lstat()
    except FileNotFoundError:
        if paths != _path_state(target):
            raise ProviderConfigError("subscription_reference_changed")
        return False, (str(target), "missing", paths)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
        raise ProviderConfigError("subscription_reference_unavailable")
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise ProviderConfigError("subscription_reference_changed")
        raw = handle.read(1024 * 1024 + 1)
        final = os.fstat(handle.fileno())
    after = target.lstat()
    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    if len(raw) > 1024 * 1024 or identity(opened) != identity(final) or identity(before) != identity(after) or paths != _path_state(target):
        raise ProviderConfigError("subscription_reference_changed")
    try:
        if not isinstance(json.loads(raw.decode("utf-8")), dict):
            raise ValueError
    except (UnicodeError, ValueError, RecursionError):
        raise ProviderConfigError("subscription_reference_unavailable") from None
    return True, (str(target), identity(after), hashlib.sha256(raw).hexdigest(), paths)


def _capture_reference(provider_id: str) -> tuple[dict, str]:
    from row_bot.providers import codex, claude_subscription
    paths = [codex.codex_auth_path()] if provider_id == "codex" else [
        claude_subscription.claude_credentials_path(), claude_subscription.claude_legacy_credentials_path()]
    try:
        captures = [_capture_file(path) for path in paths]
    except (OSError, ValueError) as exc:
        if isinstance(exc, ProviderConfigError):
            raise
        raise ProviderConfigError("subscription_reference_unavailable") from None
    if not any(item[0] for item in captures):
        raise ProviderConfigError("subscription_reference_unavailable")
    metadata = {"provider_id": provider_id, "source": "external_cli", "external_reference_exists": True,
        "external_reference_label": "Codex CLI" if provider_id == "codex" else "Claude Code",
        "external_reference_metadata_only": True}
    return metadata, admissions.keyed_digest({"provider_id": provider_id, "captures": captures})


def _intent(provider_id: str, revision: str, operation: str, value: str | None) -> dict:
    if operation not in COMMANDS or provider_id not in (("codex", "claude_subscription") if operation == "reference" else ("xai_oauth",)):
        raise ProviderConfigError("invalid_subscription_options")
    if operation == "client_id_save":
        value = _client_id(value)
        from row_bot.providers.xai_oauth import _is_placeholder_xai_oauth_client_id
        if _is_placeholder_xai_oauth_client_id(value):
            raise ProviderConfigError("invalid_subscription_options")
    elif value is not None:
        raise ProviderConfigError("invalid_subscription_options")
    return {"provider_id": provider_id, "provider_revision": revision, "operation": operation, "value": value}


def review_options(provider_id: str, provider_revision: str, operation: str, value: str | None = None, *,
                   validate: Callable[[], None]) -> dict:
    intent = _intent(provider_id, provider_revision, operation, value)
    snapshot = read_options(validate=validate)
    if snapshot.revision != provider_revision:
        raise ProviderConfigError("revision_conflict")
    if operation == "reference":
        _, capture = _capture_reference(provider_id)
        intent["reference_digest"] = capture
    validate()
    return {**intent, "action_digest": admissions.keyed_digest(intent)}


def execute_options(*, owner_id: str, key: str, command: dict, validate: Callable[[], None],
                    validate_review: Callable[[dict], None]) -> dict:
    payload = command.get("payload")
    operation = next((name for name, kind in COMMANDS.items() if command.get("type") == kind), None)
    if not isinstance(payload, dict) or operation is None:
        raise ProviderConfigError("invalid_command")
    intent = _intent(payload.get("provider_id"), payload.get("provider_revision"), operation, payload.get("value"))
    provider = intent["provider_id"]
    proof = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
    with provider_config_transaction():
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, command, f"subscription_options:{provider}")
        except admissions.AdmissionError as exc:
            cfg = load_provider_config(strict=True)
            if str(exc) != "operation_uncertain" or cfg.get("providers", {}).get(provider, {}).get("subscription_options_command") != proof:
                raise ProviderConfigError(str(exc)) from None
            from row_bot.application.provider_settings_commands import invalidate_provider_runtime
            invalidate_provider_runtime()
            replay = {"command_id": command["command_id"], "status": "completed", "options": asdict(read_options(validate=validate))}
            admissions.complete_command(owner_id, key, replay)
        if replay is not None:
            validate()
            return {**replay, "options": asdict(read_options(validate=validate))}
        try:
            reviewed = review_options(provider, intent["provider_revision"], operation, intent["value"], validate=validate)
            validate_review(reviewed)
        except Exception:
            admissions.reject_command(owner_id, key, "subscription_options_review_invalid")
            raise ProviderConfigError("subscription_options_review_invalid") from None
        def authority() -> None:
            current = review_options(provider, intent["provider_revision"], operation, intent["value"], validate=validate)
            validate_review(current)
            if current != reviewed:
                raise ProviderConfigError("subscription_reference_changed")
        try:
            from row_bot.providers import codex, claude_subscription, xai_oauth
            kwargs = {"expected_revision": intent["provider_revision"], "validate": authority, "command_proof": proof}
            if operation == "reference":
                metadata, _ = _capture_reference(provider)
                module = codex if provider == "codex" else claude_subscription
                module.save_external_reference(_captured_metadata=metadata, **kwargs)
            elif operation == "client_id_save":
                xai_oauth.save_xai_oauth_client_id(intent["value"], **kwargs)
            else:
                xai_oauth.clear_xai_oauth_client_id_override(**kwargs)
            from row_bot.application.provider_settings_commands import invalidate_provider_runtime
            invalidate_provider_runtime()
            result = {"command_id": command["command_id"], "status": "completed", "options": asdict(read_options(validate=validate))}
            admissions.complete_command(owner_id, key, result)
            validate()
            return result
        except ProviderConfigError:
            raise
        except Exception:
            raise ProviderConfigError("subscription_options_unconfirmed") from None


def read_options_receipt(*, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict | None:
    validate()
    row = admissions.read_command_metadata(owner_id, command_id)
    if row is None or row["type"] not in COMMANDS.values() or row["target"] not in {f"subscription_options:{provider}" for provider in ("codex", "claude_subscription", "xai_oauth")}:
        validate()
        return None
    provider = row["target"].removeprefix("subscription_options:")
    cfg = load_provider_config(strict=True)
    published = cfg.get("providers", {}).get(provider, {}).get("subscription_options_command") == {"owner_id": owner_id, "key": row["key"], "command_id": command_id}
    result = {"command_id": command_id, "status": "completed" if row["status"] == "completed" or published else "rejected" if row["status"] == "rejected" else "uncertain",
        "published": published, "options": asdict(read_options(validate=validate))}
    validate()
    return result
