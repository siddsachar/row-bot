"""Explicit local-owner preview of the existing selective migration planner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from row_bot.application.client_platform import ClientPlatformError
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.migration import (
    MigrationApplyOptions, MigrationPlan, MigrationStatus,
    apply_migration_plan, build_migration_plan,
)
from row_bot.migration.redaction import redact_value

_LOCK = RLock()
_PLANS: dict[str, dict[str, Any]] = {}
_RECEIPTS: dict[str, dict[str, Any]] = {}
_ACTIVE: set[str] = set()


def _journal_path(command_id: str) -> Path:
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_migration_command") from None
    return get_row_bot_data_dir(create=False) / "migration-commands" / f"{command_id}.json"


def _interrupted_receipt(command_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "command_id": command_id, "status": "interrupted",
        "summary": dict.fromkeys(("total", "selected", "ready", "migrated", "conflicts", "sensitive", "archive_only", "skipped", "blocked", "errors"), 0),
        "report": "", "warnings": ["The migration was interrupted. Inspect the target and backups, then scan again."],
        "failed_items": [],
    }


def _read_journal(command_id: str) -> dict[str, Any] | None:
    path = _journal_path(command_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("command_id") == command_id:
            return data
    except (OSError, ValueError):
        pass
    return {"command_id": command_id, "receipt": _interrupted_receipt(command_id)}


def _reserve_journal(command_id: str, plan_id: str, digest: str) -> dict[str, Any] | None:
    path = _journal_path(command_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_journal(command_id)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"command_id": command_id, "plan_id": plan_id, "review_digest": digest,
                   "receipt": _interrupted_receipt(command_id)}, output)
        output.flush()
        os.fsync(output.fileno())
    return None


def _finish_journal(command_id: str, plan_id: str, digest: str, receipt: dict[str, Any]) -> None:
    path = _journal_path(command_id)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump({"command_id": command_id, "plan_id": plan_id, "review_digest": digest,
                   "receipt": receipt}, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _roots(provider: str, source: str, target: str) -> tuple[Path, Path]:
    if provider not in {"hermes", "openclaw"} or not source or len(source) > 2048 or len(target) > 2048:
        raise ClientPlatformError("invalid_migration_selection")
    source_root = Path(source).expanduser()
    target_root = Path(target).expanduser() if target else get_row_bot_data_dir(create=False)
    if not source_root.is_absolute() or not target_root.is_absolute():
        raise ClientPlatformError("invalid_migration_selection")
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    if not source_root.is_dir() or source_root == target_root or source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ClientPlatformError("invalid_migration_selection")
    return source_root, target_root


def _fingerprint(plan: MigrationPlan) -> str:
    """Fence a preview to source/target file metadata and the dry-run plan."""
    paths: set[Path] = {plan.source.root}
    for item in plan.items:
        if item.source:
            paths.add(Path(str(item.source).split("#", 1)[0]))
        if item.target:
            paths.add(Path(str(item.target).split("#", 1)[0]))
    if len(paths) > 4096:
        raise ClientPlatformError("migration_plan_too_large")
    stamps = []
    for path in sorted(paths, key=str):
        try:
            stat = path.stat()
            stamps.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_mode))
        except OSError:
            stamps.append((str(path), None))
    payload = {"plan": plan.to_dict(redact=True), "stamps": stamps}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _target_label(target: str | Path | None, root: Path) -> str:
    if not target:
        return ""
    raw, separator, fragment = str(target).partition("#")
    try:
        relative = Path(raw).resolve().relative_to(root)
    except ValueError:
        return "Outside target folder"
    return str(relative) + (f"#{fragment}" if separator else "")


def _safe_note(value: str, source: Path, target: Path) -> str:
    text = str(value)
    for root in (source, target):
        text = text.replace(str(root), "[folder]")
    return str(redact_value(text))[:512]


def _preview(plan_id: str, plan: MigrationPlan, target_root: Path, revision: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "revision": revision,
        "provider": plan.source.provider.value,
        "source_found": plan.source.found,
        "source_label": plan.source.label[:128],
        "summary": plan.summary.to_dict(),
        "warnings": [_safe_note(value, plan.source.root, target_root) for value in plan.warnings[:64]],
        "items": [
            {
                "id": item.id[:128],
                "category": item.category.value,
                "action": item.action.value,
                "status": item.status.value,
                "label": item.label[:256],
                "reason": _safe_note(item.reason, plan.source.root, target_root),
                "target": _target_label(item.target, target_root)[:256],
                "sensitivity": item.sensitivity.value,
                "selected": item.selected,
                "requires_confirmation": item.requires_confirmation,
            }
            for item in plan.items[:4096]
        ],
    }


def scan_migration(
    *, owner_id: str, provider: str, source: str, target: str = "", include_secrets: bool = False,
) -> dict[str, Any]:
    """Build a read-only plan only after the owner requests a scan."""
    source_root, target_root = _roots(provider, source, target)
    plan = build_migration_plan(provider, source_root, target_root=target_root, include_secrets=include_secrets)
    if len(plan.items) > 4096:
        raise ClientPlatformError("migration_plan_too_large")
    revision = _fingerprint(plan)
    plan_id = str(uuid4())
    with _LOCK:
        if len(_PLANS) >= 16:
            _PLANS.pop(next(iter(_PLANS)))
        _PLANS[plan_id] = {
            "owner_id": owner_id,
            "source": source_root,
            "target": target_root,
            "include_secrets": include_secrets,
            "revision": revision,
            "plan": plan,
        }
    return _preview(plan_id, plan, target_root, revision)


def _selected_plan(
    *, owner_id: str, plan_id: str, revision: str,
    selected_ids: list[str], overwrite: bool,
) -> tuple[MigrationPlan, dict[str, Any]]:
    with _LOCK:
        saved = _PLANS.get(plan_id)
        if not saved or saved["owner_id"] != owner_id:
            raise ClientPlatformError("migration_plan_missing")
    if revision != saved["revision"] or len(selected_ids) > 4096 or len(set(selected_ids)) != len(selected_ids):
        raise ClientPlatformError("migration_changed")
    plan = build_migration_plan(
        saved["plan"].source.provider,
        saved["source"],
        target_root=saved["target"],
        include_secrets=saved["include_secrets"],
    )
    if _fingerprint(plan) != revision:
        raise ClientPlatformError("migration_changed")
    selected = set(selected_ids)
    choices = {item.id: item for item in plan.items}
    if len(choices) != len(plan.items) or not selected.issubset(choices):
        raise ClientPlatformError("invalid_migration_selection")
    for item_id in selected:
        item = choices[item_id]
        if item.action.value == "manual_review" or item.status not in (
            {MigrationStatus.PLANNED, MigrationStatus.SENSITIVE, MigrationStatus.CONFLICT}
            if overwrite else {MigrationStatus.PLANNED, MigrationStatus.SENSITIVE}
        ):
            raise ClientPlatformError("invalid_migration_selection")
    for item in plan.items:
        if item.source:
            try:
                Path(str(item.source).split("#", 1)[0]).resolve().relative_to(saved["source"])
            except ValueError:
                raise ClientPlatformError("migration_path_escaped") from None
        if item.target:
            try:
                Path(str(item.target).split("#", 1)[0]).resolve().relative_to(saved["target"])
            except ValueError:
                raise ClientPlatformError("migration_path_escaped") from None
    if not selected:
        raise ClientPlatformError("invalid_migration_selection")
    chosen = MigrationPlan(
        source=plan.source,
        # The legacy apply engine archives archive-only rows regardless of
        # selected, so pass only the exact rows the owner chose.
        items=tuple(item.with_selection(True) for item in plan.items if item.id in selected),
        warnings=plan.warnings,
        metadata=dict(plan.metadata),
    )
    return chosen, saved


def _review_digest(plan_id: str, revision: str, selected_ids: list[str], overwrite: bool) -> str:
    return hashlib.sha256(json.dumps({
        "plan_id": plan_id, "revision": revision,
        "selected_ids": sorted(selected_ids), "overwrite": overwrite,
    }, sort_keys=True).encode()).hexdigest()


def review_migration_apply(
    *, owner_id: str, plan_id: str, revision: str,
    selected_ids: list[str], overwrite: bool,
) -> dict[str, Any]:
    """Rebuild the exact plan and disclose what a later apply will change."""
    plan, _saved = _selected_plan(
        owner_id=owner_id, plan_id=plan_id, revision=revision,
        selected_ids=selected_ids, overwrite=overwrite,
    )
    selected = [item for item in plan.items if item.selected]
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "revision": revision,
        "review_digest": _review_digest(plan_id, revision, selected_ids, overwrite),
        "selected": len(selected),
        "conflicts": sum(item.status == MigrationStatus.CONFLICT for item in selected),
        "sensitive": sum(item.requires_confirmation for item in selected),
        "overwrite": overwrite,
        "backup_required": True,
    }


def apply_selected_migration(
    *, owner_id: str, command_id: str, plan_id: str, revision: str,
    review_digest: str, selected_ids: list[str], overwrite: bool,
    confirmed: bool,
) -> dict[str, Any]:
    """Apply one reviewed selection with mandatory backups and a redacted result."""
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_migration_command") from None
    receipt_key = f"{owner_id}:{command_id}"
    digest = _review_digest(plan_id, revision, selected_ids, overwrite)
    with _LOCK:
        prior = _RECEIPTS.get(receipt_key)
        if prior:
            if prior["review_digest"] != digest or prior["plan_id"] != plan_id:
                raise ClientPlatformError("migration_command_conflict")
            return prior["receipt"]
        if _ACTIVE:
            raise ClientPlatformError("migration_apply_busy")
        if not confirmed or review_digest != digest:
            raise ClientPlatformError("migration_confirmation_required")
        _ACTIVE.add(receipt_key)
    try:
        plan, saved = _selected_plan(
            owner_id=owner_id, plan_id=plan_id, revision=revision,
            selected_ids=selected_ids, overwrite=overwrite,
        )
        journal = _reserve_journal(command_id, plan_id, digest)
        if journal:
            if journal.get("plan_id") != plan_id or journal.get("review_digest") != digest:
                raise ClientPlatformError("migration_command_conflict")
            return journal["receipt"]
        result = apply_migration_plan(
            plan,
            MigrationApplyOptions(require_backup=True, overwrite=overwrite),
        )
        report_root = saved["target"]
        report = ""
        if result.report_dir:
            try:
                report = str(result.report_dir.resolve().relative_to(report_root))[:256]
            except ValueError:
                report = "Migration report"
        receipt = {
            "schema_version": 1,
            "command_id": command_id,
            "status": "completed" if result.summary.errors == 0 else "partial",
            "summary": result.summary.to_dict(),
            "report": report,
            "warnings": [_safe_note(value, saved["source"], saved["target"]) for value in result.warnings[:64]],
            "failed_items": [
                {"id": item.id[:128], "status": item.status.value,
                 "reason": _safe_note(item.reason, saved["source"], saved["target"])}
                for item in result.items if item.status in {MigrationStatus.ERROR, MigrationStatus.BLOCKED}
            ][:4096],
        }
        with _LOCK:
            if len(_RECEIPTS) >= 32:
                _RECEIPTS.pop(next(iter(_RECEIPTS)))
            _RECEIPTS[receipt_key] = {"plan_id": plan_id, "review_digest": digest, "receipt": receipt}
        _finish_journal(command_id, plan_id, digest, receipt)
        return receipt
    finally:
        with _LOCK:
            _ACTIVE.discard(receipt_key)


def read_migration_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    """Recover a completed command in the current server epoch."""
    with _LOCK:
        saved = _RECEIPTS.get(f"{owner_id}:{command_id}")
        if saved:
            return saved["receipt"]
        if f"{owner_id}:{command_id}" in _ACTIVE:
            raise ClientPlatformError("migration_apply_busy")
    journal = _read_journal(command_id)
    if journal:
        return journal["receipt"]
    raise ClientPlatformError("migration_receipt_missing")
