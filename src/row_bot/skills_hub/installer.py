"""Install, update, and uninstall public skill bundles."""

from __future__ import annotations

import pathlib
import json
import hashlib
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Literal
from uuid import uuid4

import yaml

from .models import InstallResult, SkillBundle, SkillFile, SkillInstallRecord
from .provenance import append_audit, get_record, now_iso, remove_record, upsert_record
from .scanner import scan_bundle
from .sources import compute_bundle_hash, normalize_skill_name, parse_skill_markdown

ConflictPolicy = Literal["keep_existing", "rename", "replace_with_backup"]
_PUBLICATION_LOCK = threading.RLock()
_VALIDATE = ContextVar("skill_publication_validation", default=lambda: None)


@contextmanager
def publication_authority(validate=None):
    """Serialize provenance and recheck current authority at the file boundary."""
    with _PUBLICATION_LOCK:
        token = _VALIDATE.set(validate or (lambda: None))
        try:
            yield
        finally:
            _VALIDATE.reset(token)


def install_bundle(
    bundle: SkillBundle,
    *,
    enabled: bool = False,
    conflict_policy: ConflictPolicy = "keep_existing",
) -> InstallResult:
    import row_bot.skills as skills

    skills.load_skills()
    scan = scan_bundle(bundle)
    if scan.blocked:
        append_audit("install_blocked", source=bundle.source, install_ref=bundle.install_ref, scan=scan.as_dict())
        return InstallResult(
            success=False,
            message="The safety scan blocked this skill, so it wasn't installed.",
            skill_name="",
            warnings=scan.warnings,
        )

    local_name, taken = install_name(bundle)
    dest = skills.USER_SKILLS_DIR / local_name
    if taken:
        if conflict_policy == "keep_existing":
            return InstallResult(
                success=False,
                message=f"A skill named {local_name} is already installed.",
                skill_name=local_name,
                warnings=scan.warnings,
            )
        if conflict_policy == "rename":
            local_name = _unique_skill_name(local_name)
            dest = skills.USER_SKILLS_DIR / local_name

    normalized_bundle = _normalized_installed_bundle(bundle, local_name)
    installed_hash = normalized_bundle.content_hash

    operation_id = str(bundle.metadata.get("operation_id") or uuid4().hex)
    _publish_bundle(normalized_bundle, local_name, operation_id=operation_id, replacing=taken)

    skills.load_skills()
    skills.set_enabled(local_name, bool(enabled))
    _clear_agent_cache()

    timestamp = now_iso()
    record = SkillInstallRecord(
        local_name=local_name,
        source=bundle.source,
        source_id=str(bundle.metadata.get("repository") or bundle.metadata.get("url") or bundle.source),
        install_ref=bundle.install_ref,
        installed_at=timestamp,
        updated_at=timestamp,
        content_hash=installed_hash,
        enabled=bool(enabled),
        file_count=len(normalized_bundle.files),
        scan_summary=scan.as_dict(),
        metadata={
            **dict(bundle.metadata or {}),
            "upstream_content_hash": bundle.content_hash,
            "file_list": normalized_bundle.file_tree(),
            "trust_level": bundle.metadata.get("trust_level", "community"),
            "operation_id": operation_id,
        },
    )
    upsert_record(record)
    append_audit("install", local_name=local_name, source=bundle.source, enabled=bool(enabled))
    return InstallResult(
        success=True,
        message=(
            f"Installed {local_name}. It's available in your chats."
            if enabled
            else f"Installed {local_name}. It stays off until you turn it on under Installed."
        ),
        skill_name=local_name,
        record=record,
        warnings=scan.warnings,
    )


def install_from_entry(
    entry,
    *,
    enabled: bool = False,
    conflict_policy: ConflictPolicy = "keep_existing",
) -> InstallResult:
    from .catalog import inspect_entry

    return install_bundle(
        inspect_entry(entry),
        enabled=enabled,
        conflict_policy=conflict_policy,
    )


def check_update(local_name: str) -> InstallResult:
    record = get_record(local_name)
    if record is None:
        return InstallResult(False, f"Skill '{local_name}' is not hub-installed.", skill_name=local_name)
    try:
        bundle = fetch_bundle_for_record(record)
    except Exception as exc:
        return InstallResult(False, f"Update check unavailable for '{local_name}': {exc}", skill_name=local_name, record=record)
    normalized = _normalized_installed_bundle(bundle, record.local_name)
    if normalized.content_hash == record.content_hash:
        return InstallResult(True, f"Skill '{local_name}' is up to date.", skill_name=local_name, record=record)
    return InstallResult(True, f"Update available for '{local_name}'.", skill_name=local_name, record=record)


def update_skill(
    local_name: str,
    *,
    enabled: bool | None = None,
    expected_record: SkillInstallRecord | None = None,
    reviewed_bundle: SkillBundle | None = None,
    operation_id: str = "",
    recovery_hash: str = "",
) -> InstallResult:
    import row_bot.skills as skills

    record = get_record(local_name)
    if record is None:
        return InstallResult(False, f"Skill '{local_name}' is not hub-installed.", skill_name=local_name)
    try:
        bundle = reviewed_bundle or fetch_bundle_for_record(record)
    except Exception as exc:
        return InstallResult(False, f"Update unavailable for '{local_name}': {exc}", skill_name=local_name, record=record)
    scan = scan_bundle(bundle)
    if scan.blocked:
        append_audit("update_blocked", local_name=local_name, scan=scan.as_dict())
        return InstallResult(False, "Update blocked by public skill scanner.", skill_name=local_name, record=record, warnings=scan.warnings)
    normalized = _normalized_installed_bundle(bundle, record.local_name)
    active_hash = installed_content_hash(record)
    if normalized.content_hash == record.content_hash and active_hash == record.content_hash:
        return InstallResult(True, f"Skill '{local_name}' is already current.", skill_name=local_name, record=record, warnings=scan.warnings)

    if expected_record is not None and get_record(local_name) != expected_record:
        return InstallResult(False, "Public skill changed before update; inspect it again.", skill_name=local_name)

    if active_hash != record.content_hash and (not recovery_hash or active_hash != recovery_hash):
        return InstallResult(False, "Local edits found. Keep an editable copy before updating.", skill_name=local_name)
    operation_id = operation_id or uuid4().hex
    _publish_bundle(normalized, record.local_name, operation_id=operation_id, replacing=True)

    skills.load_skills()
    next_enabled = False if record.metadata.get("source_blocked") else skills.is_enabled(record.local_name) if enabled is None else bool(enabled)
    skills.set_enabled(record.local_name, next_enabled)
    _clear_agent_cache()

    updated = replace(
        record,
        updated_at=now_iso(),
        content_hash=normalized.content_hash,
        install_ref=bundle.install_ref,
        enabled=next_enabled,
        file_count=len(normalized.files),
        scan_summary=scan.as_dict(),
        metadata={
            **dict(record.metadata or {}),
            **dict(bundle.metadata or {}),
            "upstream_content_hash": bundle.content_hash,
            "file_list": normalized.file_tree(),
            "operation_id": operation_id,
        },
    )
    upsert_record(updated)
    append_audit("update", local_name=record.local_name, enabled=next_enabled)
    return InstallResult(True, f"Skill '{record.local_name}' updated.", skill_name=record.local_name, record=updated, warnings=scan.warnings)


def uninstall_skill(
    local_name: str, *, expected_record: SkillInstallRecord | None = None, operation_id: str = "",
) -> InstallResult:
    import row_bot.skills as skills

    record = get_record(local_name)
    if record is None:
        return InstallResult(False, f"Skill '{local_name}' is not hub-installed.", skill_name=local_name)
    if expected_record is not None and record != expected_record:
        return InstallResult(False, "Public skill changed before uninstall; inspect it again.", skill_name=local_name)
    from row_bot.package_files import contained_path
    dest = contained_path(skills.USER_SKILLS_DIR, record.local_name)
    _VALIDATE.get()()
    skills.set_enabled(record.local_name, False)
    if dest.exists():
        shutil.rmtree(dest)
    remove_record(record.local_name)
    if operation_id:
        _save_publication(operation_id, {"name": local_name, "action": "uninstall", "complete": True})
    skills.load_skills()
    _clear_agent_cache()
    append_audit("uninstall", local_name=record.local_name)
    return InstallResult(True, f"Skill '{record.local_name}' uninstalled.", skill_name=record.local_name, record=record)


def fetch_bundle_for_record(record: SkillInstallRecord) -> SkillBundle:
    from .catalog import source_for_id

    source = source_for_id(record.source)
    if source is None:
        raise ValueError(f"No source adapter registered for {record.source}")
    reference = record.install_ref
    if record.source == "clawhub" and reference.startswith("clawhub:"):
        reference = reference.split("@", 1)[0]
        owner = record.metadata.get("author")
        if "/" not in reference and isinstance(owner, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", owner):
            reference = "clawhub:" + owner.lower() + "/" + reference.removeprefix("clawhub:")
    from .clawhub_source import ClawHubSourceBlocked
    try:
        return source.fetch(reference)
    except ClawHubSourceBlocked as error:
        _VALIDATE.get()()
        # Record only an explicit source verdict, never a timeout or absence of search results.
        upsert_record(replace(record, metadata={**record.metadata, "source_blocked": str(error)}))
        raise


def install_name(bundle: SkillBundle) -> tuple[str, bool]:
    """The skill name a bundle installs as, and whether a skill already has it."""
    import row_bot.skills as skills

    local_name = _bundle_local_name(bundle)
    taken = skills.get_skill(local_name) is not None or (skills.USER_SKILLS_DIR / local_name).exists()
    return local_name, taken


def _bundle_local_name(bundle: SkillBundle) -> str:
    name = str(bundle.frontmatter.get("name") or bundle.root_name or "imported_skill")
    return normalize_skill_name(name)


def _unique_skill_name(base: str) -> str:
    import row_bot.skills as skills

    existing = {skill.name for skill in skills.get_all_skills()}
    existing.update(path.name for path in skills.USER_SKILLS_DIR.iterdir() if path.is_dir())
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


def _normalized_installed_bundle(bundle: SkillBundle, local_name: str) -> SkillBundle:
    files: list[SkillFile] = []
    for file in bundle.files:
        if file.path == bundle.primary_skill_path:
            meta, instructions = parse_skill_markdown(file.text)
            meta["name"] = local_name
            meta["enabled_by_default"] = False
            text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + instructions.strip() + "\n"
            files.append(SkillFile.from_text(file.path, text, kind=file.kind))
        else:
            files.append(SkillFile(path=file.path, content=file.content, kind=file.kind))
    content_hash = compute_bundle_hash(files)
    frontmatter = dict(bundle.frontmatter)
    frontmatter["name"] = local_name
    frontmatter["enabled_by_default"] = False
    return replace(bundle, files=files, frontmatter=frontmatter, content_hash=content_hash)


def _write_bundle_to_dir(bundle: SkillBundle, root: pathlib.Path) -> None:
    from row_bot.package_files import contained_path

    root.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for file in bundle.files:
        if file.path.casefold() in seen or file.path == ".row-bot-publication.json" or file.kind == "symlink":
            raise ValueError("Unsafe or duplicate skill file")
        seen.add(file.path.casefold())
        target = contained_path(root, file.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.content)


def _backup_existing_skill(local_name: str, *, reason: str) -> pathlib.Path | None:
    import row_bot.skills as skills

    dest = skills.USER_SKILLS_DIR / local_name
    if not dest.exists():
        return None
    root = skills.DATA_DIR / "skill_versions" / local_name
    root.mkdir(parents=True, exist_ok=True)
    safe_ts = re.sub(r"[^0-9A-Za-z_-]+", "-", now_iso()).strip("-")
    backup = root / f"{reason}-{safe_ts}"
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(dest, backup)
    return backup


def installed_content_hash(record: SkillInstallRecord) -> str:
    """Hash installed bytes, including unexpected edits, without following links."""
    from row_bot.package_files import check_package_tree, contained_path
    import row_bot.skills as skills

    root = contained_path(skills.USER_SKILLS_DIR, record.local_name)
    check_package_tree(root, max_files=200, max_bytes=5_000_000)
    files = [SkillFile.from_bytes(path.relative_to(root).as_posix(), path.read_bytes())
             for path in root.rglob("*") if path.is_file() and path.name != ".row-bot-publication.json"]
    return compute_bundle_hash(files)


def _publication_path(operation_id: str) -> pathlib.Path:
    from .provenance import hub_dir
    return hub_dir(create=False) / "operations" / (hashlib.sha256(operation_id.encode()).hexdigest() + ".json")


def _save_publication(operation_id: str, value: dict) -> None:
    path = _publication_path(operation_id)
    path.parent.mkdir(exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({**value, "operation_id": operation_id}), encoding="utf-8")
    temp.replace(path)


def publication_result(operation_id: str, name: str, action: str) -> bool:
    """Observe publication proof; never replay files, downloads or enablement."""
    import row_bot.skills as skills
    from row_bot.package_files import contained_path

    try:
        if action == "uninstall":
            value = json.loads(_publication_path(operation_id).read_text(encoding="utf-8"))
            return value == {"name": name, "action": action, "complete": True, "operation_id": operation_id} and get_record(name) is None and not (skills.USER_SKILLS_DIR / name).exists()
        record = get_record(name)
        if record is None or record.metadata.get("operation_id") != operation_id:
            return False
        marker = contained_path(skills.USER_SKILLS_DIR, name + "/.row-bot-publication.json")
        value = json.loads(marker.read_text(encoding="utf-8"))
        return value == {"operation_id": operation_id, "content_hash": record.content_hash} and installed_content_hash(record) == record.content_hash and skills.is_enabled(name) == record.enabled
    except (OSError, ValueError):
        return False


def _publish_bundle(bundle: SkillBundle, name: str, *, operation_id: str, replacing: bool) -> None:
    import row_bot.skills as skills
    from row_bot.package_files import contained_path
    from .provenance import hub_dir

    root = hub_dir() / "staging"
    root.mkdir(exist_ok=True)
    dest = contained_path(skills.USER_SKILLS_DIR, name)
    previous = contained_path(skills.DATA_DIR, "skill_versions/" + name + "/previous")
    with tempfile.TemporaryDirectory(prefix="publish-", dir=root) as tmp:
        staged = pathlib.Path(tmp) / "package"
        _write_bundle_to_dir(bundle, staged)
        (staged / ".row-bot-publication.json").write_text(json.dumps({"operation_id": operation_id, "content_hash": bundle.content_hash}), encoding="utf-8")
        if dest.exists() and not replacing:
            raise ValueError("skill_name_conflict")
        old = get_record(name)
        _VALIDATE.get()()
        if dest.exists():
            if previous.exists():
                shutil.rmtree(previous)
            previous.parent.mkdir(parents=True, exist_ok=True)
            if old:
                (previous.parent / "record.json").write_text(json.dumps(old.as_dict()), encoding="utf-8")
        _save_publication(operation_id, {"name": name, "action": "update" if replacing else "install", "complete": False, "content_hash": bundle.content_hash})
        if dest.exists():
            dest.rename(previous)
        try:
            staged.rename(dest)
        except BaseException:
            if previous.exists() and not dest.exists():
                previous.rename(dest)
            raise


def restore_skill(local_name: str, *, expected_record: SkillInstallRecord, operation_id: str) -> InstallResult:
    """Explicitly restore one prior managed revision, retaining current enablement."""
    import row_bot.skills as skills
    from row_bot.package_files import check_package_tree, contained_path

    if get_record(local_name) != expected_record:
        return InstallResult(False, "Skill changed; review recovery again.", skill_name=local_name)
    previous = contained_path(skills.DATA_DIR, "skill_versions/" + local_name + "/previous")
    check_package_tree(previous, max_files=200, max_bytes=5_000_000)
    old = SkillInstallRecord.from_dict(json.loads((previous.parent / "record.json").read_text(encoding="utf-8")))
    from .sources import bundle_from_files
    bundle = bundle_from_files(source=old.source, install_ref=old.install_ref, root_name=local_name,
        files=[SkillFile.from_bytes(p.relative_to(previous).as_posix(), p.read_bytes()) for p in previous.rglob("*") if p.is_file() and p.name != ".row-bot-publication.json"], metadata=old.metadata)
    recovery_hash = ""
    try:
        marker_path = contained_path(skills.USER_SKILLS_DIR, local_name + "/.row-bot-publication.json")
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        journal = json.loads(_publication_path(marker["operation_id"]).read_text(encoding="utf-8"))
        if (journal.get("name") == local_name and journal.get("operation_id") == marker["operation_id"]
                and journal.get("content_hash") == marker.get("content_hash") and old.content_hash == expected_record.content_hash):
            recovery_hash = marker["content_hash"]
    except (OSError, ValueError, KeyError):
        pass
    return update_skill(local_name, expected_record=expected_record, reviewed_bundle=bundle,
        operation_id=operation_id, recovery_hash=recovery_hash)


def _assert_inside(path: pathlib.Path, root: pathlib.Path) -> None:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Path escapes staging root: {path}") from exc


def _clear_agent_cache() -> None:
    try:
        from row_bot.agent import clear_agent_cache

        clear_agent_cache()
    except Exception:
        pass
