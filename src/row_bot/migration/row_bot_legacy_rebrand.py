"""One-shot copy-first migration from legacy Thoth data to Row-Bot data."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from collections.abc import MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot.brand import (
    APP_DATA_DIR_ENV,
    DEFAULT_DATA_DIR_NAME,
    DEFAULT_WORKSPACE_DIR_NAME,
    KEYRING_SERVICE_PREFIX,
)

logger = logging.getLogger(__name__)

MIGRATION_ID = "row-bot-v4-rebrand"
MIGRATION_VERSION = "1"
POLICY_OPTION = "option-1-automated-copy-first"

LEGACY_DATA_DIR_ENV = "THOTH_DATA_DIR"
LEGACY_DATA_DIR_NAME = ".thoth"
LEGACY_WORKSPACE_DIR_NAME = "Thoth"
LEGACY_SERVICE_PREFIX = "Thoth"
TARGET_SERVICE_PREFIX = KEYRING_SERVICE_PREFIX

MARKER_REL = Path("migrations") / f"{MIGRATION_ID}.json"
REPORTS_REL = Path("migration_reports")
REPORT_PREFIX = f"{MIGRATION_ID}-"

CRITICAL_FILES = {
    "threads.db",
    "tasks.db",
    "memory.db",
    "user_config.json",
    "providers.json",
    "api_keys.json",
    "tools_config.json",
}

KNOWN_API_KEY_NAMES = {
    "ANTHROPIC_API_KEY",
    "DISCORD_BOT_TOKEN",
    "DISCORD_USER_ID",
    "GOOGLE_API_KEY",
    "MINIMAX_API_KEY",
    "NGROK_AUTHTOKEN",
    "OLLAMA_API_KEY",
    "OPENCODE_GO_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "SLACK_APP_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_USER_ID",
    "SMS_USER_PHONE",
    "TAVILY_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_USER_ID",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "WHATSAPP_USER_PHONE",
    "XAI_API_KEY",
    "X_CLIENT_ID",
    "X_CLIENT_SECRET",
}

CHANNEL_SECRET_NAMES = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID"),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_USER_ID"),
    "sms": ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "SMS_USER_PHONE"),
    "discord": ("DISCORD_BOT_TOKEN", "DISCORD_USER_ID"),
    "whatsapp": ("WHATSAPP_USER_PHONE",),
}

APP_JSON_FILES = {
    "api_keys.json",
    "app_config.json",
    "cloud_config.json",
    "model_settings.json",
    "providers.json",
    "skills_activation.json",
    "skills_config.json",
    "tools_config.json",
    "update_config.json",
    "user_config.json",
    "plugin_secrets.json",
    "plugin_state.json",
}

TOOL_ID_REPLACEMENTS = {
    "thoth_status": "row_bot_status",
    "thoth_update_setting": "row_bot_update_setting",
    "thoth_create_skill": "row_bot_create_skill",
    "thoth_patch_skill": "row_bot_patch_skill",
    "thoth_updater": "row_bot_updater",
    "thoth_check_for_updates": "row_bot_check_for_updates",
    "thoth_install_update": "row_bot_install_update",
    "thoth_agent_consult": "row_bot_agent_consult",
    "thoth_agent_control": "row_bot_agent_control",
    "thoth_status_guide": "row_bot_status_guide",
    "min_thoth_version": "min_row_bot_version",
}

TECHNICAL_STRING_REPLACEMENTS = {
    **TOOL_ID_REPLACEMENTS,
    "~/.thoth": "~/.row-bot",
    ".thoth": ".row-bot",
    "~/.local/share/thoth": "~/.local/share/row-bot",
    "~/.local/bin/thoth": "~/.local/bin/row-bot",
    "data-thoth-": "data-row-bot-",
    "thoth-": "row-bot-",
    "__thoth_routes__": "__row_bot_routes__",
    "__thothRuntime": "__rowBotRuntime",
    "__thothBridge": "__rowBotBridge",
    "__thothDesignerBridgeId": "__rowBotDesignerBridgeId",
    "__thothDesignerListener": "__rowBotDesignerListener",
    "window.thoth": "window.rowBot",
    "window._thoth": "window._rowBot",
    "ThothRealtimeVoice": "RowBotRealtimeVoice",
    "thoth_origin": "row_bot_origin",
    "active_thoth_generation_id": "active_row_bot_generation_id",
    "set_active_thoth_generation": "set_active_row_bot_generation",
    "clear_active_thoth_generation": "clear_active_row_bot_generation",
    "thoth_consult_started": "row_bot_consult_started",
    "thoth_tool_started": "row_bot_tool_started",
    "thoth_tool_done": "row_bot_tool_done",
    "thoth_tool_running": "row_bot_tool_running",
    "realtime_thoth_generation_active": "realtime_row_bot_generation_active",
    "realtime_thoth_generation_finished": "realtime_row_bot_generation_finished",
    "stale_thoth_generation_clear_ignored": "stale_row_bot_generation_clear_ignored",
    "speech_stop_to_thoth_start": "speech_stop_to_row_bot_start",
    "speech_stop_to_first_spoken_thoth": "speech_stop_to_first_spoken_row_bot",
}

DESIGNER_TECHNICAL_SUFFIXES = {".css", ".html", ".js", ".json"}
BUDDY_TECHNICAL_DIRS = (
    Path("buddy") / "packs",
    Path("buddy") / "generated",
    Path("buddy_hatches"),
)
_RUN_RESULT: dict[str, Any] | None = None


class MigrationBlockingError(RuntimeError):
    """Raised when migration cannot safely continue startup."""


def ensure_legacy_rebrand_migration(
    *,
    environ: MutableMapping[str, str] | None = None,
    home: Path | None = None,
    keyring_backend: Any | None = None,
) -> dict[str, Any]:
    """Run the v4 Row-Bot migration once and activate the target data dir.

    The migration never mutates the legacy source directory. It copies missing
    files into the Row-Bot target, rewrites known app-owned technical config,
    writes a marker/report, and then points the Row-Bot data env var at the target.
    """
    global _RUN_RESULT
    if environ is None and _RUN_RESULT is not None:
        return dict(_RUN_RESULT)

    env = os.environ if environ is None else environ
    home_dir = (home or Path.home()).expanduser()
    started_at = _utc_now()
    target = _target_dir(env, home_dir)
    source, source_warning = _legacy_source_dir(env, home_dir)

    report = _base_report(started_at, source, target)
    if source_warning:
        report["warnings"].append(source_warning)
    _warn_old_workspace(report, home_dir)
    _ensure_workspace_report(report, home_dir)

    try:
        result = _run_migration(
            source=source,
            target=target,
            report=report,
            keyring_backend=keyring_backend,
            home=home_dir,
        )
    except MigrationBlockingError:
        raise
    except Exception as exc:
        report["blocking_errors"].append(str(exc))
        raise MigrationBlockingError(str(exc)) from exc

    _activate_target_env(env, target)
    if environ is None:
        _RUN_RESULT = dict(result)
    return result


def _run_migration(
    *,
    source: Path | None,
    target: Path,
    report: dict[str, Any],
    keyring_backend: Any | None,
    home: Path,
) -> dict[str, Any]:
    marker = _marker_path(target)
    if source is None:
        _ensure_target_writable(target)
        existing = _read_json(marker)
        if isinstance(existing, dict) and existing.get("status") == "completed":
            report["status"] = "already_completed"
            _rewrite_copied_config(target, report, home=home)
            report["completed_at"] = _utc_now()
            _write_repair_report_if_needed(target, report)
        else:
            report["status"] = "fresh_install"
            report["completed_at"] = _utc_now()
        return report

    source = source.expanduser()
    target = target.expanduser()
    same_path = _same_path(source, target)
    if same_path:
        _ensure_target_writable(target)
        report["status"] = "source_is_target"
        report["completed_at"] = _utc_now()
        return report

    _guard_unsafe_path_relationship(source, target)
    if not source.exists() or not source.is_dir():
        raise MigrationBlockingError(f"Legacy data directory is not readable: {source}")

    _ensure_target_writable(target)
    target_preexisting = _target_has_user_files(target)
    report["source_fingerprint"] = _source_fingerprint(source)
    report["target_preexisting"] = target_preexisting
    logger.info("Row-Bot migration detected legacy data at %s; target=%s", source, target)

    if marker.exists():
        existing = _read_json(marker)
        if existing.get("status") == "completed":
            report["status"] = "already_completed"
            _rewrite_copied_config(target, report, home=home)
            _migrate_known_secrets(source, target, report, keyring_backend=keyring_backend)
            report["completed_at"] = _utc_now()
            _write_repair_report_if_needed(target, report)
            _log_secret_migration_summary(report)
            return report
        raise MigrationBlockingError(
            f"Partial Row-Bot migration marker found at {marker}; use the migration report before retrying."
        )

    _copy_missing_tree(source, target, report)
    _rewrite_copied_config(target, report, home=home)
    _migrate_known_secrets(source, target, report, keyring_backend=keyring_backend)

    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    _write_marker_and_report(target, report)
    _log_secret_migration_summary(report)
    logger.info(
        "Row-Bot migration complete; copied=%s rewritten=%s report=%s",
        report.get("files_copied_count", 0),
        report.get("files_rewritten_count", 0),
        report.get("report_path", ""),
    )
    return report


def _target_dir(env: MutableMapping[str, str], home: Path) -> Path:
    return Path(env.get(APP_DATA_DIR_ENV) or home / DEFAULT_DATA_DIR_NAME).expanduser()


def _legacy_source_dir(env: MutableMapping[str, str], home: Path) -> tuple[Path | None, str]:
    explicit = env.get(LEGACY_DATA_DIR_ENV)
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.exists() and explicit_path.is_dir():
            default_legacy = home / LEGACY_DATA_DIR_NAME
            warning = ""
            if default_legacy.exists() and not _same_path(explicit_path, default_legacy):
                warning = (
                    f"{LEGACY_DATA_DIR_ENV} points to {explicit_path}; default legacy data also exists at "
                    f"{default_legacy}. Using the explicit source."
                )
            return explicit_path, warning
        return None, f"{LEGACY_DATA_DIR_ENV} is set to {explicit_path}, but that directory does not exist."

    default_source = home / LEGACY_DATA_DIR_NAME
    if default_source.exists() and default_source.is_dir():
        return default_source, ""
    return None, ""


def _activate_target_env(env: MutableMapping[str, str], target: Path) -> None:
    value = str(target.expanduser())
    env[APP_DATA_DIR_ENV] = value


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return left.absolute() == right.absolute()


def _guard_unsafe_path_relationship(source: Path, target: Path) -> None:
    source_resolved = source.resolve(strict=False)
    target_resolved = target.resolve(strict=False)
    if target_resolved in source_resolved.parents or source_resolved == target_resolved:
        raise MigrationBlockingError(f"Unsafe migration target inside legacy source: {target}")
    if source_resolved in target_resolved.parents:
        raise MigrationBlockingError(f"Unsafe legacy source inside Row-Bot target: {source}")


def _ensure_target_writable(target: Path) -> None:
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".row-bot-migration-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise MigrationBlockingError(f"Row-Bot data directory is not writable: {target}") from exc


def _target_has_user_files(target: Path) -> bool:
    if not target.exists():
        return False
    ignored = {"migrations", "migration_reports", ".row-bot-migration-write-test"}
    return any(child.name not in ignored for child in target.iterdir())


def _copy_missing_tree(source: Path, target: Path, report: dict[str, Any]) -> None:
    try:
        children = list(source.iterdir())
    except OSError as exc:
        raise MigrationBlockingError(f"Cannot read legacy data directory: {source}") from exc
    for child in children:
        _copy_missing_item(child, target / child.name, child.name, report)


def _copy_missing_item(src: Path, dst: Path, rel: str, report: dict[str, Any]) -> None:
    try:
        if src.is_dir() and not src.is_symlink() and dst.exists() and dst.is_dir():
            for child in src.iterdir():
                _copy_missing_item(child, dst / child.name, f"{rel}/{child.name}", report)
            return
        if dst.exists() or dst.is_symlink():
            _record(report, "files_skipped", rel)
            report["files_skipped_count"] += 1
            report["conflicts"].append(f"Skipped existing target path: {rel}")
            return
        if src.is_symlink():
            try:
                os.symlink(os.readlink(src), dst)
            except OSError:
                target = src.resolve(strict=False)
                if target.is_dir():
                    shutil.copytree(src, dst, symlinks=True)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                report["warnings"].append(f"Copied symlink target instead of symlink for {rel}.")
            _record(report, "files_copied", rel)
            report["files_copied_count"] += 1
            return
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            _record(report, "files_copied", rel + "/")
            report["files_copied_count"] += 1
            for child in src.iterdir():
                _copy_missing_item(child, dst / child.name, f"{rel}/{child.name}", report)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        _record(report, "files_copied", rel)
        report["files_copied_count"] += 1
    except OSError as exc:
        message = f"Failed to copy {rel}: {exc}"
        if Path(rel).parts and Path(rel).parts[0] in CRITICAL_FILES:
            raise MigrationBlockingError(message) from exc
        report["warnings"].append(message)


def _rewrite_copied_config(target: Path, report: dict[str, Any], *, home: Path) -> None:
    for name in APP_JSON_FILES:
        path = target / name
        if path.exists() and path.is_file():
            _rewrite_json_file(path, report, target=target, home=home)
    for base in (
        target / "designer" / "projects",
        target / "designer" / "history",
        target / "designer" / "published",
    ):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in DESIGNER_TECHNICAL_SUFFIXES:
                if path.suffix.lower() == ".json":
                    _rewrite_json_file(path, report, target=target, home=home, designer_file=True)
                else:
                    _rewrite_text_file(path, report)
    for rel_base in BUDDY_TECHNICAL_DIRS:
        base = target / rel_base
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            if path.is_file():
                _rewrite_json_file(path, report, target=target, home=home)


def _rewrite_json_file(
    path: Path,
    report: dict[str, Any],
    *,
    target: Path,
    home: Path,
    designer_file: bool = False,
) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        report["warnings"].append(f"Skipped config rewrite for {path.name}: {exc}")
        return

    new_service = _service_name_for(TARGET_SERVICE_PREFIX, target)
    rewritten = _rewrite_json_value(data, designer_file=designer_file)
    if path.name == "user_config.json" and isinstance(rewritten, dict):
        identity = rewritten.get("identity")
        if isinstance(identity, dict) and identity.get("name") == "Thoth":
            identity["name"] = "Row-Bot"
    if path.name in {"api_keys.json", "plugin_secrets.json"} and isinstance(rewritten, dict):
        if rewritten.get("storage") == "keyring" or "service" in rewritten:
            rewritten["service"] = new_service
    if path.name == "tools_config.json" and isinstance(rewritten, dict):
        _rewrite_filesystem_workspace_config(rewritten, report, home=home)

    if rewritten != data:
        encoded = json.dumps(rewritten, indent=2, ensure_ascii=False) + "\n"
        path.write_text(encoded, encoding="utf-8")
        _record(report, "files_rewritten", _rel_to_report(path, target))
        report["files_rewritten_count"] += 1


def _rewrite_json_value(value: Any, *, designer_file: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            _rewrite_technical_string(str(k), designer_file=designer_file): _rewrite_json_value(v, designer_file=designer_file)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_json_value(item, designer_file=designer_file) for item in value]
    if isinstance(value, str):
        return _rewrite_technical_string(value, designer_file=designer_file)
    return value


def _rewrite_text_file(path: Path, report: dict[str, Any]) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    except OSError as exc:
        report["warnings"].append(f"Skipped designer token rewrite for {path}: {exc}")
        return
    rewritten = _rewrite_technical_string(raw, designer_file=True)
    if rewritten != raw:
        path.write_text(rewritten, encoding="utf-8")
        target = _find_report_root(path)
        _record(report, "files_rewritten", _rel_to_report(path, target))
        report["files_rewritten_count"] += 1


def _rewrite_technical_string(text: str, *, designer_file: bool = False) -> str:
    rewritten = text
    for old, new in TECHNICAL_STRING_REPLACEMENTS.items():
        if old.startswith("data-") or old.startswith("__"):
            if designer_file:
                rewritten = rewritten.replace(old, new)
        else:
            rewritten = rewritten.replace(old, new)
    return rewritten


def _migrate_known_secrets(
    source: Path,
    target: Path,
    report: dict[str, Any],
    *,
    keyring_backend: Any | None,
) -> None:
    secret_stats = {
        "api_keys": {"copied": 0, "skipped": 0, "failed": 0, "metadata_updated": 0},
        "channel_secrets": {"copied": 0, "skipped": 0, "failed": 0},
        "plugin_secrets": {"copied": 0, "skipped": 0, "failed": 0},
        "provider_secrets": {"copied": 0, "skipped": 0, "failed": 0},
    }
    report["secret_migration"] = secret_stats

    backend = keyring_backend or _load_keyring_backend(report)
    if backend is None:
        return

    old_service = _service_name_for(LEGACY_SERVICE_PREFIX, source)
    new_service = _service_name_for(TARGET_SERVICE_PREFIX, target)

    api_key_names = _api_key_names(target / "api_keys.json")
    for name in api_key_names:
        _copy_keyring_value(backend, old_service, new_service, "api_keys", name, secret_stats["api_keys"])
    _sync_api_key_metadata(backend, new_service, target / "api_keys.json", api_key_names, secret_stats["api_keys"])

    for channel_name, env_names in CHANNEL_SECRET_NAMES.items():
        for env_name in env_names:
            _copy_keyring_value(
                backend,
                old_service,
                new_service,
                f"channels:{channel_name}",
                env_name,
                secret_stats["channel_secrets"],
            )
            _copy_cross_namespace_keyring_value(
                backend,
                old_service,
                new_service,
                "api_keys",
                f"channels:{channel_name}",
                env_name,
                secret_stats["channel_secrets"],
            )

    for plugin_id, key in _plugin_secret_names(target / "plugin_secrets.json"):
        account_name = f"{plugin_id}:{key}"
        _copy_keyring_value(
            backend,
            old_service,
            new_service,
            "plugin_secrets",
            account_name,
            secret_stats["plugin_secrets"],
        )

    for provider_id, credential_name in _provider_secret_names(target / "providers.json"):
        _copy_provider_secret(
            backend,
            old_service,
            new_service,
            provider_id,
            credential_name,
            secret_stats["provider_secrets"],
        )


def _load_keyring_backend(report: dict[str, Any]) -> Any | None:
    try:
        import keyring  # type: ignore

        return keyring
    except Exception as exc:
        report["warnings"].append(f"Keyring unavailable during migration; credentials may need reconnecting: {exc}")
        return None


def _copy_keyring_value(
    backend: Any,
    old_service: str,
    new_service: str,
    namespace: str,
    name: str,
    stats: dict[str, int],
) -> None:
    account = f"{namespace}:{name}"
    try:
        existing = backend.get_password(new_service, account)
        if existing:
            stats["skipped"] += 1
            return
        value = backend.get_password(old_service, account)
        if not value:
            stats["skipped"] += 1
            return
        backend.set_password(new_service, account, value)
        stats["copied"] += 1
    except Exception:
        stats["failed"] += 1


def _copy_cross_namespace_keyring_value(
    backend: Any,
    old_service: str,
    new_service: str,
    old_namespace: str,
    new_namespace: str,
    name: str,
    stats: dict[str, int],
) -> None:
    old_account = f"{old_namespace}:{name}"
    new_account = f"{new_namespace}:{name}"
    try:
        existing = backend.get_password(new_service, new_account)
        if existing:
            stats["skipped"] += 1
            return
        value = backend.get_password(old_service, old_account)
        if not value:
            stats["skipped"] += 1
            return
        backend.set_password(new_service, new_account, value)
        stats["copied"] += 1
    except Exception:
        stats["failed"] += 1


def _copy_provider_secret(
    backend: Any,
    old_service: str,
    new_service: str,
    provider_id: str,
    credential_name: str,
    stats: dict[str, int],
) -> None:
    namespace = f"providers:{provider_id}"
    marker_name = f"{credential_name}.__chunks"
    marker_account = f"{namespace}:{marker_name}"
    try:
        marker = backend.get_password(old_service, marker_account)
        if marker:
            if backend.get_password(new_service, marker_account):
                stats["skipped"] += 1
                return
            count = int(str(marker).removeprefix("v1:"))
            for index in range(max(0, count)):
                part_name = f"{credential_name}.__chunk.{index:04d}"
                part_account = f"{namespace}:{part_name}"
                part = backend.get_password(old_service, part_account)
                if part:
                    backend.set_password(new_service, part_account, part)
            backend.set_password(new_service, marker_account, marker)
            stats["copied"] += 1
            return
        _copy_keyring_value(backend, old_service, new_service, namespace, credential_name, stats)
    except Exception:
        stats["failed"] += 1


def _api_key_names(path: Path) -> set[str]:
    result = set(KNOWN_API_KEY_NAMES)
    data = _read_json(path)
    if not isinstance(data, dict):
        return result
    if isinstance(data.get("keys"), dict):
        result.update(str(k) for k, v in data["keys"].items() if isinstance(v, dict) and v.get("configured"))
        return result
    result.update(str(k) for k, v in data.items() if isinstance(v, str) and v)
    return result


def _sync_api_key_metadata(
    backend: Any,
    service: str,
    path: Path,
    names: set[str],
    stats: dict[str, int],
) -> None:
    try:
        data = _read_json(path)
        if not isinstance(data, dict) or _is_legacy_plaintext_api_key_file(data):
            data = {}
        data.setdefault("version", 2)
        data.setdefault("storage", "keyring")
        data["service"] = service
        keys = data.setdefault("keys", {})
        if not isinstance(keys, dict):
            keys = {}
            data["keys"] = keys
        changed = False
        for name in sorted(names):
            value = backend.get_password(service, f"api_keys:{name}")
            if not value:
                continue
            existing = keys.get(name)
            if isinstance(existing, dict) and existing.get("configured") and existing.get("fingerprint"):
                continue
            keys[name] = {
                "configured": True,
                "fingerprint": _fingerprint(value),
                "updated_at": _utc_now(),
            }
            stats["metadata_updated"] = stats.get("metadata_updated", 0) + 1
            changed = True
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        stats["failed"] += 1


def _is_legacy_plaintext_api_key_file(data: dict[str, Any]) -> bool:
    return bool(data) and data.get("version") != 2 and all(isinstance(v, str) for v in data.values())


def _fingerprint(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"


def _plugin_secret_names(path: Path) -> set[tuple[str, str]]:
    data = _read_json(path)
    result: set[tuple[str, str]] = set()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if isinstance(plugins, dict):
        for plugin_id, plugin_meta in plugins.items():
            if isinstance(plugin_meta, dict):
                for key, entry in plugin_meta.items():
                    if isinstance(entry, dict) and entry.get("configured"):
                        result.add((str(plugin_id), str(key)))
    return result


def _provider_secret_names(path: Path) -> set[tuple[str, str]]:
    data = _read_json(path)
    result = {
        ("openai", "api_key"),
        ("anthropic", "api_key"),
        ("google", "api_key"),
        ("xai", "api_key"),
        ("minimax", "api_key"),
        ("openrouter", "api_key"),
        ("ollama_cloud", "api_key"),
        ("codex", "access_token"),
        ("codex", "refresh_token"),
        ("codex", "id_token"),
        ("codex", "account"),
    }
    providers = data.get("providers") if isinstance(data, dict) else None
    if isinstance(providers, dict):
        for provider_id in providers:
            result.add((str(provider_id), "api_key"))
    return result


def _write_marker_and_report(target: Path, report: dict[str, Any]) -> None:
    marker = _marker_path(target)
    reports_dir = target / REPORTS_REL
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"{REPORT_PREFIX}{timestamp}.json"
    report["marker_path"] = str(marker)
    report["report_path"] = str(report_path)
    report["rollback_instructions"] = (
        "Quit Row-Bot. The legacy data directory was not modified. To retry, rename or remove the "
        "Row-Bot target directory, restore any desired backup into the target, or set ROW_BOT_DATA_DIR "
        "to a corrected location before starting Row-Bot again."
    )
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        marker.write_text(
            json.dumps(
                {
                    "migration_id": MIGRATION_ID,
                    "migration_version": MIGRATION_VERSION,
                    "policy_option": POLICY_OPTION,
                    "status": "completed",
                    "source": report.get("source"),
                    "target": report.get("target"),
                    "source_fingerprint": report.get("source_fingerprint"),
                    "completed_at": report.get("completed_at"),
                    "report_path": str(report_path),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise MigrationBlockingError(f"Migration data copied but marker/report could not be written: {exc}") from exc


def _write_repair_report_if_needed(target: Path, report: dict[str, Any]) -> None:
    if not (_repair_changed(report) or _workspace_report_needed(target, report)):
        return
    reports_dir = target / REPORTS_REL
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"{REPORT_PREFIX}repair-{timestamp}.json"
    report["report_path"] = str(report_path)
    report["rollback_instructions"] = (
        "Quit Row-Bot. The legacy data directory was not modified. This repair only updated Row-Bot "
        "metadata/config paths and copied any missing legacy secrets into the Row-Bot keyring service. "
        "To undo credential repairs, disconnect the affected providers/channels in Settings."
    )
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info(
            "Row-Bot migration repair completed; rewritten=%s report=%s",
            report.get("files_rewritten_count", 0),
            report_path,
        )
    except OSError as exc:
        report["warnings"].append(f"Could not write migration repair report: {exc}")


def _secret_migration_changed(report: dict[str, Any]) -> bool:
    stats = report.get("secret_migration")
    if not isinstance(stats, dict):
        return False
    for section in stats.values():
        if not isinstance(section, dict):
            continue
        if int(section.get("copied", 0) or 0) > 0:
            return True
        if int(section.get("metadata_updated", 0) or 0) > 0:
            return True
    return False


def _repair_changed(report: dict[str, Any]) -> bool:
    return bool(int(report.get("files_rewritten_count", 0) or 0) > 0 or _secret_migration_changed(report))


def _workspace_report_needed(target: Path, report: dict[str, Any]) -> bool:
    if not isinstance(report.get("workspace_migration"), dict):
        return False
    reports_dir = target / REPORTS_REL
    if not reports_dir.exists():
        return True
    try:
        candidates = [path for path in reports_dir.glob(f"{REPORT_PREFIX}*.json") if path.is_file()]
    except OSError:
        return False
    if not candidates:
        return True
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    latest_report = _read_json(latest)
    return not isinstance(latest_report.get("workspace_migration"), dict)


def _log_secret_migration_summary(report: dict[str, Any]) -> None:
    stats = report.get("secret_migration")
    if not isinstance(stats, dict):
        return
    parts = []
    for name, values in stats.items():
        if not isinstance(values, dict):
            continue
        copied = int(values.get("copied", 0) or 0)
        failed = int(values.get("failed", 0) or 0)
        metadata = int(values.get("metadata_updated", 0) or 0)
        if copied or failed or metadata:
            detail = f"{name}: copied={copied}"
            if metadata:
                detail += f" metadata={metadata}"
            if failed:
                detail += f" failed={failed}"
            parts.append(detail)
    if parts:
        logger.info("Row-Bot migration secret repair summary: %s", "; ".join(parts))


def _marker_path(target: Path) -> Path:
    return target / MARKER_REL


def _read_json(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _source_fingerprint(source: Path) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for name in sorted(CRITICAL_FILES):
        path = source / name
        if not path.exists():
            continue
        try:
            stat = path.stat()
            selected[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        except OSError:
            selected[name] = {"error": "stat_failed"}
    return {
        "path": str(source),
        "selected_files": selected,
        "migration_version": MIGRATION_VERSION,
    }


def _service_name_for(prefix: str, data_dir: Path) -> str:
    path = data_dir.resolve(strict=False)
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _legacy_default_workspace(home: Path) -> Path:
    return home / "Documents" / LEGACY_WORKSPACE_DIR_NAME


def _target_default_workspace(home: Path) -> Path:
    return home / "Documents" / DEFAULT_WORKSPACE_DIR_NAME


def _workspace_summary_template(home: Path) -> dict[str, Any]:
    legacy = _legacy_default_workspace(home)
    target = _target_default_workspace(home)
    return {
        "policy": "workspace-content-not-moved",
        "legacy_default_workspace": str(legacy),
        "new_default_workspace": str(target),
        "legacy_default_exists": legacy.exists(),
        "new_default_exists": target.exists(),
        "configured_workspace_before": "",
        "configured_workspace_after": "",
        "configured_workspace_kind": "unconfigured",
        "action": "default_will_use_row_bot",
        "user_guidance": (
            f"Row-Bot will create {target} when the filesystem workspace is first used. "
            f"Legacy workspace files in {legacy} were not moved."
        ),
    }


def _ensure_workspace_report(report: dict[str, Any], home: Path) -> dict[str, Any]:
    existing = report.get("workspace_migration")
    template = _workspace_summary_template(home)
    if not isinstance(existing, dict):
        report["workspace_migration"] = template
        return template
    existing.update(
        {
            "legacy_default_workspace": template["legacy_default_workspace"],
            "new_default_workspace": template["new_default_workspace"],
            "legacy_default_exists": template["legacy_default_exists"],
            "new_default_exists": template["new_default_exists"],
            "policy": template["policy"],
        }
    )
    return existing


def _filesystem_workspace_config(data: dict[str, Any]) -> dict[str, Any] | None:
    tool_configs = data.get("tool_configs")
    if not isinstance(tool_configs, dict):
        return None
    filesystem = tool_configs.get("filesystem")
    if not isinstance(filesystem, dict):
        return None
    return filesystem


def _workspace_path_from_config(value: str, home: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return Path()
    if text == "~":
        path = home
    elif text.startswith("~/") or text.startswith("~\\"):
        path = home / text[2:]
    else:
        path = Path(text)
        if not path.is_absolute():
            path = home / path
    return path


def _normalized_path_text(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=False)))
    except OSError:
        return os.path.normcase(str(path.absolute()))


def _workspace_kind(value: str, home: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return "unconfigured"
    path = _workspace_path_from_config(text, home)
    legacy = _legacy_default_workspace(home)
    target = _target_default_workspace(home)
    normalized = _normalized_path_text(path)
    legacy_normalized = _normalized_path_text(legacy)
    target_normalized = _normalized_path_text(target)
    if normalized == legacy_normalized:
        return "legacy_default"
    if normalized == target_normalized:
        return "row_bot_default"
    try:
        if legacy_normalized in {
            _normalized_path_text(parent)
            for parent in path.resolve(strict=False).parents
        }:
            return "custom_inside_legacy_default"
    except OSError:
        pass
    return "custom"


def _workspace_exists(value: str, home: Path) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        return _workspace_path_from_config(text, home).exists()
    except OSError:
        return False


def _rewrite_filesystem_workspace_config(data: dict[str, Any], report: dict[str, Any], *, home: Path) -> None:
    workspace = _filesystem_workspace_config(data)
    summary = _ensure_workspace_report(report, home)
    if workspace is None:
        return

    before = str(workspace.get("workspace_root") or "").strip()
    kind = _workspace_kind(before, home)
    after = before
    action = "preserved_custom_workspace"
    guidance = "Your custom filesystem workspace was kept as configured. No workspace files were moved."

    if kind == "unconfigured":
        action = "default_will_use_row_bot"
        guidance = (
            f"The filesystem workspace was not configured. Row-Bot will use "
            f"{_target_default_workspace(home)} when the workspace is first used."
        )
    elif kind == "legacy_default":
        after = str(_target_default_workspace(home))
        workspace["workspace_root"] = after
        action = "rewritten_to_row_bot_default"
        guidance = (
            f"The old default workspace setting was changed to {after}. "
            f"Files in {_legacy_default_workspace(home)} were not moved."
        )
    elif kind == "row_bot_default":
        action = "already_row_bot_default"
        guidance = f"The filesystem workspace already points at {after}."
    elif kind == "custom_inside_legacy_default":
        action = "preserved_custom_inside_legacy_default"
        guidance = (
            "Your configured workspace is inside the legacy default workspace tree, "
            "so Row-Bot kept it unchanged. Review it before deleting legacy workspace folders."
        )

    summary.update(
        {
            "configured_workspace_before": before,
            "configured_workspace_after": after,
            "configured_workspace_kind": kind,
            "configured_workspace_exists": _workspace_exists(after, home),
            "action": action,
            "user_guidance": guidance,
        }
    )


def _warn_old_workspace(report: dict[str, Any], home: Path) -> None:
    old_workspace = _legacy_default_workspace(home)
    if old_workspace.exists():
        report["warnings"].append(
            f"Legacy workspace exists at {old_workspace}; it was not moved. "
            f"Use Documents/{DEFAULT_WORKSPACE_DIR_NAME} for new Row-Bot exports."
        )


def _base_report(started_at: str, source: Path | None, target: Path) -> dict[str, Any]:
    return {
        "migration_id": MIGRATION_ID,
        "migration_version": MIGRATION_VERSION,
        "policy_option": POLICY_OPTION,
        "status": "started",
        "source": str(source) if source else "",
        "target": str(target),
        "started_at": started_at,
        "completed_at": "",
        "target_preexisting": False,
        "source_fingerprint": {},
        "files_copied": [],
        "files_copied_count": 0,
        "files_skipped": [],
        "files_skipped_count": 0,
        "files_rewritten": [],
        "files_rewritten_count": 0,
        "conflicts": [],
        "warnings": [],
        "blocking_errors": [],
        "secret_migration": {},
    }


def _record(report: dict[str, Any], key: str, value: str, limit: int = 1000) -> None:
    items = report.setdefault(key, [])
    if len(items) < limit:
        items.append(value)


def _rel_to_report(path: Path, target: Path) -> str:
    try:
        return path.relative_to(target).as_posix()
    except ValueError:
        return str(path)


def _find_report_root(path: Path) -> Path:
    current = path
    for parent in path.parents:
        if parent.name == "designer":
            return parent.parent
        current = parent
    return current.parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
