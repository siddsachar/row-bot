"""Bounded React access to the existing public skill catalog and installer."""

from __future__ import annotations

import hashlib
import json
import logging
import os

import psutil
import threading
import time
from dataclasses import replace
from typing import Any, Callable
from uuid import uuid4

from row_bot.runtime import admissions
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
_ACTIVE: set[tuple[str, str]] = set()
_TTL = 20 * 60
MAX_RESULTS = 96


class SkillHubCommandError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _entry_public(entry: SkillHubEntry) -> dict[str, Any]:
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(entry.url)
        url = entry.url[:2048] if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        url = ""
    return {
        "url": url,
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
    cached_only: bool = False,
    cancelled: Callable[[], bool] | None = None,
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
            query.strip(), source=source, limit=limit + 1, force_refresh=refresh, cached_only=cached_only, cancelled=cancelled
        )
    except SkillSourceTimeout as exc:
        raise SkillHubCommandError("skill_source_timeout") from exc
    if cancelled is not None and cancelled():
        raise SkillHubCommandError("integration_search_cancelled")
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
                "fetched_at": status.fetched_at or None,
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
    return _save_preview(owner_id, bundle, entry)


def _save_preview(owner_id: str, bundle: SkillBundle, entry: SkillHubEntry) -> dict[str, Any]:
    scan = scan_bundle(bundle)
    local_name, taken = installer.install_name(bundle)
    shown = _entry_public(entry)
    shown["installed"] = shown["installed"] or taken
    preview_id = uuid4().hex
    primary = bundle.primary_file()
    from pathlib import PurePosixPath
    from row_bot.skills_hub.scanner import EXECUTABLE_EXTENSIONS, MAX_FILE_BYTES, MAX_TOTAL_BYTES
    review_files = []
    bounded = sum(len(f.content) for f in bundle.files) <= MAX_TOTAL_BYTES
    for file in bundle.files[:100]:
        text, reason = None, ""
        if not bounded or len(file.content) > MAX_FILE_BYTES:
            reason = "File exceeds the review limit; installation is blocked."
        else:
            try:
                text = file.content.decode("utf-8")
                if "\0" in text:
                    text, reason = None, "Binary asset; content cannot be rendered as instructions."
            except UnicodeDecodeError:
                reason = "Binary asset; content cannot be rendered as instructions."
        review_files.append({"path": file.path, "text": text, "size_bytes": len(file.content), "sha256": file.sha256,
            "executable": PurePosixPath(file.path).suffix.lower() in EXECUTABLE_EXTENSIONS,
            "unavailable_reason": reason})
    summary = {
        "schema_version": 1,
        "preview_id": preview_id,
        "content_hash": bundle.content_hash,
        "entry": shown,
        "skill_name": local_name[:160],
        "primary_text": (primary.text if primary else "")[:6000],
        "files": bundle.file_tree()[:100],
        "review_files": review_files,
        "version": str(bundle.metadata.get("version") or bundle.frontmatter.get("version") or bundle.metadata.get("commit") or bundle.content_hash)[:256],
        "requirements": [str(bundle.frontmatter[key])[:512] for key in ("allowed-tools", "compatibility", "requirements") if bundle.frontmatter.get(key)],
        "provenance": [f"{key}: {str(value)[:480]}" for key, value in bundle.metadata.items() if key in {"version", "commit", "audit", "audits", "moderation", "source_url", "repository", "revision"}][:32],
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
    wire = {"command_id": command_id, "type": "skill.hub.install", "preview_id": preview_id,
            "content_hash": content_hash, "make_available": make_available}
    if validate is not None:
        validate()
    prior = _replay(owner_id, command_id, wire)
    if prior is not None:
        return prior
    with _LOCK:
        preview = _PREVIEWS.get((owner_id, preview_id))
        if preview is None or time.monotonic() - preview[0] > _TTL:
            raise SkillHubCommandError("skill_preview_expired")
        bundle = preview[1]
        if bundle.content_hash != content_hash or preview[2]["scan"]["blocked"]:
            raise SkillHubCommandError("skill_preview_changed")
        from row_bot.skills_hub.clawhub_source import revalidate_bundle
        revalidate_bundle(bundle)
        name, _taken = installer.install_name(bundle)
        _claim(owner_id, command_id, wire, name, "install")
        _ACTIVE.add((owner_id, command_id))
    try:
        if validate is not None:
            validate()
        bundle = replace(bundle, metadata={**bundle.metadata, "operation_id": command_id})
        with installer.publication_authority(validate):
            result = installer.install_bundle(bundle, enabled=make_available)
        receipt = {"schema_version": 1, "command_id": command_id, "success": result.success,
                   "message": result.message[:500], "skill_name": result.skill_name[:160]}
        return admissions.complete_command(owner_id, command_id, {"receipt": receipt})["receipt"]
    except Exception:
        logger.warning("Skill publication outcome is unconfirmed", exc_info=False)
        return _read_receipt(owner_id, command_id, "install")
    finally:
        _ACTIVE.discard((owner_id, command_id))


def _claim(owner_id: str, command_id: str, wire: dict, name: str, action: str) -> None:
    try:
        admissions.claim_command(owner_id, command_id, wire, "settings:skill:" + name.casefold(),
            exclusive_target=True, initial_result={"name": name, "action": action, "_process": {"pid": os.getpid(), "birth": psutil.Process().create_time()}})
    except admissions.AdmissionError as exc:
        raise SkillHubCommandError("skill_command_conflict" if str(exc) == "idempotency_mismatch" else "skill_install_pending") from None


def _replay(owner_id: str, command_id: str, wire: dict) -> dict | None:
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None:
        return None
    if metadata["type"] != wire["type"]:
        raise SkillHubCommandError("skill_command_conflict")
    try:
        saved = admissions.claim_command(owner_id, command_id, wire, metadata["target"])
        return saved["receipt"] if saved else None
    except admissions.AdmissionError as exc:
        if str(exc) == "idempotency_mismatch":
            raise SkillHubCommandError("skill_command_conflict") from None
        if str(exc) != "operation_uncertain" or _original_alive(owner_id, command_id):
            raise SkillHubCommandError("skill_install_pending") from None
        # An explicit retry reconciles; it never replays publication or networking.
        action = "install" if wire["type"] == "skill.hub.install" else "maintenance"
        receipt = _read_receipt(owner_id, command_id, action)
        admissions.complete_command(owner_id, command_id, {"receipt": receipt})
        return receipt


def _read_receipt(owner_id: str, command_id: str, kind: str) -> dict:
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None or metadata["type"] != "skill.hub." + kind:
        raise SkillHubCommandError("skill_receipt_missing")
    saved = admissions.read_command_receipt(owner_id, command_id) or {}
    if "receipt" in saved:
        return saved["receipt"]
    name, action = str(saved.get("name", "")), str(saved.get("action", ""))
    proven = bool(name) and installer.publication_result(command_id, name, action)
    receipt = {"schema_version": 1, "command_id": command_id, "success": proven,
        "message": "Publication recovered from its operation identity." if proven else
        "Outcome uncertain. Inspect the installed skill and request a fresh review; nothing was replayed."}
    if kind == "install":
        receipt["skill_name"] = name
    else:
        record = provenance.get_record(name)
        receipt.update(action=action, record=_record_public(record) if record else None)
    return receipt


def read_skill_install_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    return _read_receipt(owner_id, command_id, "install")


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
    preview_id: str = "",
    content_hash: str = "",
    validate: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if action not in {"check", "review_update", "update", "restore", "uninstall"} or not name or len(name) > 160:
        raise SkillHubCommandError("invalid_skill_action")
    if action in {"uninstall", "restore"} and not confirmed:
        raise SkillHubCommandError("skill_confirmation_required")
    wire = {"command_id": command_id, "type": "skill.hub.maintenance", "name": name,
            "revision": expected_revision, "action": action, "confirmed": confirmed,
            "preview_id": preview_id, "content_hash": content_hash}
    if validate is not None:
        validate()
    prior = _replay(owner_id, command_id, wire)
    if prior is not None:
        return prior
    with _LOCK:
        record = provenance.get_record(name)
        if record is None:
            raise SkillHubCommandError("skill_not_installed")
        if _record_revision(record) != expected_revision:
            raise SkillHubCommandError("skill_record_changed")
        reviewed_bundle = None
        if action == "update":
            preview = _PREVIEWS.get((owner_id, preview_id))
            if preview is None or time.monotonic() - preview[0] > _TTL:
                raise SkillHubCommandError("skill_preview_expired")
            reviewed_bundle = preview[1]
            if (reviewed_bundle.content_hash != content_hash or preview[2]["scan"]["blocked"]
                    or reviewed_bundle.metadata.get("update_name") != name
                    or reviewed_bundle.metadata.get("update_revision") != expected_revision):
                raise SkillHubCommandError("skill_preview_changed")
        _claim(owner_id, command_id, wire, name, action)
        _ACTIVE.add((owner_id, command_id))
    try:
        if validate is not None:
            validate()
        update_preview = None
        with installer.publication_authority(validate):
            if action == "check":
                result = installer.check_update(name)
            elif action == "review_update":
                bundle = installer.fetch_bundle_for_record(record)
                bundle = replace(bundle, metadata={**bundle.metadata, "update_name": name, "update_revision": expected_revision})
                entry = SkillHubEntry(id="installed:" + name, name=name, description="Review the complete update before replacing local files.", source=record.source,
                    source_id=record.source_id, install_ref=bundle.install_ref)
                update_preview = _save_preview(owner_id, bundle, entry)
                from row_bot.package_files import check_package_tree, contained_path
                from row_bot import skills
                import hashlib
                root = contained_path(skills.USER_SKILLS_DIR, name)
                check_package_tree(root, max_files=200, max_bytes=5_000_000)
                before = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in root.rglob("*") if path.is_file() and path.name != ".row-bot-publication.json"}
                after = {file.path: file.sha256 for file in installer._normalized_installed_bundle(bundle, name).files}
                update_preview["changes"] = [f"{('Added' if path not in before else 'Removed' if path not in after else 'Changed')}: {path}"
                    for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]
                result = installer.InstallResult(True, "Review the update's files and scan findings.", skill_name=name)
            elif action == "update":
                result = installer.update_skill(name, expected_record=record, reviewed_bundle=reviewed_bundle, operation_id=command_id)
            elif action == "restore":
                result = installer.restore_skill(name, expected_record=record, operation_id=command_id)
            else:
                result = installer.uninstall_skill(name, expected_record=record, operation_id=command_id)
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
        if update_preview is not None:
            # Package bodies stay in the expiring preview cache, never admissions.
            admissions.complete_command(owner_id, command_id, {"receipt": receipt})
            _ACTIVE.discard((owner_id, command_id))
            return {**receipt, "update_preview": update_preview}
    except Exception:
        _ACTIVE.discard((owner_id, command_id))
        return _read_receipt(owner_id, command_id, "maintenance")
    try:
        return admissions.complete_command(owner_id, command_id, {"receipt": receipt})["receipt"]
    finally:
        _ACTIVE.discard((owner_id, command_id))


def read_skill_maintenance_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    return _read_receipt(owner_id, command_id, "maintenance")


def reconcile_skill_hub_operation(*, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict:
    """Settle a stopped owner without repeating downloads or publication."""
    validate()
    with _LOCK:
        if _original_alive(owner_id, command_id):
            raise SkillHubCommandError("skill_install_pending")
        metadata = admissions.read_command_metadata(owner_id, command_id)
        if not metadata or metadata["type"] not in {"skill.hub.install", "skill.hub.maintenance"}:
            raise SkillHubCommandError("skill_receipt_missing")
        kind = metadata["type"].rsplit(".", 1)[1]
        result = _read_receipt(owner_id, command_id, kind)
        validate()
        admissions.complete_command(owner_id, command_id, {"receipt": result})
        return {"command_id": command_id, "settled": True, "message": result["message"]}


def _original_alive(owner_id: str, command_id: str) -> bool:
    if (owner_id, command_id) in _ACTIVE:
        return True
    process = (admissions.read_command_receipt(owner_id, command_id) or {}).get("_process", {})
    if process.get("pid") == os.getpid():
        return False
    try:
        return psutil.Process(process["pid"]).create_time() == process["birth"]
    except (KeyError, TypeError, psutil.Error):
        return False
