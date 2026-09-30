"""Bounded React access to the existing public skill catalog and installer."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import replace
from typing import Any, Callable
from uuid import uuid4

from row_bot.skills_hub import catalog, installer
from row_bot.skills_hub.models import SkillBundle, SkillHubEntry
from row_bot.skills_hub import provenance
from row_bot.skills_hub.models import SkillInstallRecord
from row_bot.skills_hub.scanner import scan_bundle
from row_bot.skills_hub.source_registry import SkillSourceTimeout

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
# Per owner, the last few result lists by revision: a skill opened from a list
# still previews after "Load more" or a late source replaced that list.
_CATALOGS: dict[str, dict[str, tuple[float, dict[str, SkillHubEntry]]]] = {}
_CATALOG_REVISIONS = 4
_PREVIEWS: dict[tuple[str, str], tuple[float, SkillBundle, dict[str, Any]]] = {}
_COMMANDS: dict[tuple[str, str], tuple[str, dict[str, Any] | None]] = {}
_MAINTENANCE: dict[tuple[str, str], tuple[str, dict[str, Any] | None]] = {}
_TTL = 20 * 60
MAX_RESULTS = 96


class SkillHubCommandError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _entry_public(entry: SkillHubEntry) -> dict[str, Any]:
    return {
        "id": entry.id[:256],
        "name": entry.name[:160],
        "description": entry.description[:1000],
        "source": entry.source[:80],
        "author": entry.author[:160],
        "trust_level": entry.trust_level[:80],
        "tags": [str(tag)[:80] for tag in entry.tags[:8]],
        "installed": bool(entry.metadata.get("installed")),
    }


def _catalog_revision(entries: list[SkillHubEntry]) -> str:
    data = [(entry.id, entry.source, entry.install_ref) for entry in entries]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def search_public_skills(
    *,
    owner_id: str,
    query: str,
    source: str = "all",
    refresh: bool = False,
    limit: int = 24,
) -> dict[str, Any]:
    if (
        len(query) > 2000
        or not 1 <= limit <= MAX_RESULTS
        or source
        not in {
            "all",
            "github",
            "skills_sh",
            "browse_sh",
            "clawhub",
            "lobehub",
        }
    ):
        raise SkillHubCommandError("invalid_skill_query")
    try:
        # One more than shown says whether "Load more" has anything to add.
        result = catalog.search_skills(
            query.strip(), source=source, limit=limit + 1, force_refresh=refresh
        )
    except SkillSourceTimeout as exc:
        raise SkillHubCommandError("skill_source_timeout") from exc
    found = [entry for entry in result.entries if 0 < len(entry.id) <= 256]
    entries = found[:limit]
    revision = _catalog_revision(entries)
    with _LOCK:
        if len(_CATALOGS) >= 32 and owner_id not in _CATALOGS:
            _CATALOGS.pop(next(iter(_CATALOGS)))
        lists = _CATALOGS.setdefault(owner_id, {})
        lists.pop(revision, None)
        lists[revision] = (time.monotonic(), {entry.id: entry for entry in entries})
        while len(lists) > _CATALOG_REVISIONS:
            lists.pop(next(iter(lists)))
    return {
        "schema_version": 1,
        "revision": revision,
        "mode": result.mode[:40],
        "query": result.query[:2000],
        "entries": [_entry_public(entry) for entry in entries],
        "has_more": len(found) > limit,
        "source_statuses": [
            {
                "source_id": status.source_id[:80],
                "status": status.status,
                "message": status.message[:300],
            }
            for status in result.source_statuses[:12]
        ],
        "error": result.error[:500],
    }


def preview_public_skill(
    *,
    owner_id: str,
    revision: str,
    entry_id: str,
) -> dict[str, Any]:
    with _LOCK:
        now = time.monotonic()
        lists = _CATALOGS.get(owner_id, {})
        if not any(now - stamp <= _TTL for stamp, _entries in lists.values()):
            raise SkillHubCommandError("skill_catalog_expired")
        listed = lists.get(revision)
        if listed is None or now - listed[0] > _TTL or entry_id not in listed[1]:
            raise SkillHubCommandError("skill_catalog_changed")
        entry = listed[1][entry_id]
    try:
        bundle = catalog.inspect_entry(entry)
    except SkillSourceTimeout as exc:
        raise SkillHubCommandError("skill_source_timeout") from exc
    except Exception as exc:
        logger.warning("Skill preview from %s failed: %s", entry.source, type(exc).__name__)
        raise SkillHubCommandError("skill_preview_unavailable") from exc
    # The install record keeps the listing it came from, so search can mark it
    # installed even when the files came from another address.
    bundle = replace(bundle, metadata={**bundle.metadata, "hub_entry_ref": entry.install_ref})
    scan = scan_bundle(bundle)
    local_name, taken = installer.install_name(bundle)
    shown = _entry_public(entry)
    shown["installed"] = shown["installed"] or taken
    preview_id = uuid4().hex
    primary = bundle.primary_file()
    summary = {
        "schema_version": 1,
        "preview_id": preview_id,
        "content_hash": bundle.content_hash,
        "entry": shown,
        "skill_name": local_name[:160],
        "primary_text": (primary.text if primary else "")[:6000],
        "files": bundle.file_tree()[:100],
        "scan": {
            "blocked": scan.blocked,
            "findings": [
                {
                    "severity": finding.severity,
                    "code": finding.code[:80],
                    "message": finding.message[:500],
                    "path": finding.path[:256],
                }
                for finding in scan.findings[:50]
            ],
            "token_estimate": scan.token_estimate,
        },
    }
    with _LOCK:
        if len(_PREVIEWS) >= 64:
            _PREVIEWS.pop(next(iter(_PREVIEWS)))
        _PREVIEWS[(owner_id, preview_id)] = (time.monotonic(), bundle, summary)
    return summary


def install_previewed_skill(
    *,
    owner_id: str,
    command_id: str,
    preview_id: str,
    content_hash: str,
    make_available: bool,
    validate: Callable[[], None] | None = None,
) -> dict[str, Any]:
    identity = (preview_id, content_hash, make_available)
    digest = hashlib.sha256(repr(identity).encode()).hexdigest()
    key = (owner_id, command_id)
    with _LOCK:
        prior = _COMMANDS.get(key)
        if prior is not None:
            if prior[0] != digest:
                raise SkillHubCommandError("skill_command_conflict")
            if prior[1] is None:
                raise SkillHubCommandError("skill_install_pending")
            return prior[1]
        preview = _PREVIEWS.get((owner_id, preview_id))
        if preview is None or time.monotonic() - preview[0] > _TTL:
            raise SkillHubCommandError("skill_preview_expired")
        bundle = preview[1]
        if bundle.content_hash != content_hash or preview[2]["scan"]["blocked"]:
            raise SkillHubCommandError("skill_preview_changed")
        if len(_COMMANDS) >= 128:
            completed = next(
                (item for item, value in _COMMANDS.items() if value[1] is not None),
                None,
            )
            if completed is None:
                raise SkillHubCommandError("skill_install_pending")
            _COMMANDS.pop(completed)
        _COMMANDS[key] = (digest, None)
    if validate is not None:
        try:
            validate()
        except BaseException:
            with _LOCK:
                _COMMANDS.pop(key, None)
            raise
    try:
        result = installer.install_bundle(bundle, enabled=make_available)
        receipt = {
            "schema_version": 1,
            "command_id": command_id,
            "success": result.success,
            "message": result.message[:500],
            "skill_name": result.skill_name[:160],
        }
    except Exception:
        receipt = {
            "schema_version": 1,
            "command_id": command_id,
            "success": False,
            "message": "Installation outcome is uncertain. Inspect the local skill library before retrying.",
            "skill_name": "",
        }
    with _LOCK:
        _COMMANDS[key] = (digest, receipt)
    return receipt


def read_skill_install_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    with _LOCK:
        item = _COMMANDS.get((owner_id, command_id))
        if item is None:
            raise SkillHubCommandError("skill_receipt_missing")
        if item[1] is None:
            raise SkillHubCommandError("skill_install_pending")
        return item[1]


def _record_revision(record: SkillInstallRecord) -> str:
    data = (record.local_name, record.content_hash, record.updated_at, record.enabled)
    return hashlib.sha256(repr(data).encode()).hexdigest()


def _record_public(record: SkillInstallRecord) -> dict[str, Any]:
    return {
        "name": record.local_name[:160],
        "source": record.source[:80],
        "enabled": record.enabled,
        "installed_at": record.installed_at[:64],
        "updated_at": record.updated_at[:64],
        "file_count": record.file_count,
        "revision": _record_revision(record),
    }


def read_installed_public_skills() -> dict[str, Any]:
    records = list(provenance.load_records().values())[:200]
    return {
        "schema_version": 1,
        "items": [_record_public(record) for record in records],
    }


def execute_public_skill_maintenance(
    *,
    owner_id: str,
    command_id: str,
    name: str,
    expected_revision: str,
    action: str,
    confirmed: bool = False,
    validate: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if action not in {"check", "update", "uninstall"} or not name or len(name) > 160:
        raise SkillHubCommandError("invalid_skill_action")
    if action == "uninstall" and not confirmed:
        raise SkillHubCommandError("skill_confirmation_required")
    digest = hashlib.sha256(
        repr((name, expected_revision, action, confirmed)).encode()
    ).hexdigest()
    key = (owner_id, command_id)
    with _LOCK:
        prior = _MAINTENANCE.get(key)
        if prior is not None:
            if prior[0] != digest:
                raise SkillHubCommandError("skill_command_conflict")
            if prior[1] is None:
                raise SkillHubCommandError("skill_install_pending")
            return prior[1]
        record = provenance.get_record(name)
        if record is None:
            raise SkillHubCommandError("skill_not_installed")
        if _record_revision(record) != expected_revision:
            raise SkillHubCommandError("skill_record_changed")
        if len(_MAINTENANCE) >= 128:
            completed = next(
                (item for item, value in _MAINTENANCE.items() if value[1] is not None),
                None,
            )
            if completed is None:
                raise SkillHubCommandError("skill_install_pending")
            _MAINTENANCE.pop(completed)
        _MAINTENANCE[key] = (digest, None)
    if validate is not None:
        try:
            validate()
        except BaseException:
            with _LOCK:
                _MAINTENANCE.pop(key, None)
            raise
    try:
        if action == "check":
            result = installer.check_update(name)
        elif action == "update":
            result = installer.update_skill(name, expected_record=record)
        else:
            result = installer.uninstall_skill(name, expected_record=record)
        latest = provenance.get_record(name)
        receipt = {
            "schema_version": 1,
            "command_id": command_id,
            "action": action,
            "success": result.success,
            "message": (
                result.message[:300]
                if result.success
                else "Action did not complete. Check the Skill Library and source before retrying."
            ),
            "record": _record_public(latest) if latest else None,
        }
    except Exception:
        receipt = {
            "schema_version": 1,
            "command_id": command_id,
            "action": action,
            "success": False,
            "message": "Outcome uncertain. Inspect the Skill Library before retrying.",
            "record": None,
        }
    with _LOCK:
        _MAINTENANCE[key] = (digest, receipt)
    return receipt


def read_skill_maintenance_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    with _LOCK:
        item = _MAINTENANCE.get((owner_id, command_id))
        if item is None:
            raise SkillHubCommandError("skill_receipt_missing")
        if item[1] is None:
            raise SkillHubCommandError("skill_install_pending")
        return item[1]
