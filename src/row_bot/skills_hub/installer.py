"""Install, update, and uninstall public skill bundles."""

from __future__ import annotations

import pathlib
import re
import shutil
import tempfile
from dataclasses import replace
from typing import Literal

import yaml

from .models import InstallResult, SkillBundle, SkillFile, SkillInstallRecord
from .provenance import append_audit, get_record, now_iso, remove_record, upsert_record
from .scanner import scan_bundle
from .sources import compute_bundle_hash, normalize_skill_name, parse_skill_markdown

ConflictPolicy = Literal["keep_existing", "rename", "replace_with_backup"]


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
            message="Install blocked by public skill scanner.",
            skill_name="",
            warnings=scan.warnings,
        )

    local_name = _bundle_local_name(bundle)
    existing = skills.get_skill(local_name)
    dest = skills.USER_SKILLS_DIR / local_name
    if existing is not None or dest.exists():
        if conflict_policy == "keep_existing":
            return InstallResult(
                success=False,
                message=f"Skill already exists: {local_name}",
                skill_name=local_name,
                warnings=scan.warnings,
            )
        if conflict_policy == "rename":
            local_name = _unique_skill_name(local_name)
            dest = skills.USER_SKILLS_DIR / local_name
        elif conflict_policy == "replace_with_backup":
            _backup_existing_skill(local_name, reason="hub-replace")

    normalized_bundle = _normalized_installed_bundle(bundle, local_name)
    installed_hash = normalized_bundle.content_hash

    with tempfile.TemporaryDirectory(prefix="row_bot_skill_hub_") as tmp:
        staged = pathlib.Path(tmp) / local_name
        _write_bundle_to_dir(normalized_bundle, staged)
        _assert_inside(staged, pathlib.Path(tmp))
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(staged, dest)

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
        },
    )
    upsert_record(record)
    append_audit("install", local_name=local_name, source=bundle.source, enabled=bool(enabled))
    return InstallResult(
        success=True,
        message=f"Skill '{local_name}' installed {'and made available' if enabled else 'disabled'}.",
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


def update_skill(local_name: str, *, enabled: bool | None = None) -> InstallResult:
    import row_bot.skills as skills

    record = get_record(local_name)
    if record is None:
        return InstallResult(False, f"Skill '{local_name}' is not hub-installed.", skill_name=local_name)
    try:
        bundle = fetch_bundle_for_record(record)
    except Exception as exc:
        return InstallResult(False, f"Update unavailable for '{local_name}': {exc}", skill_name=local_name, record=record)
    scan = scan_bundle(bundle)
    if scan.blocked:
        append_audit("update_blocked", local_name=local_name, scan=scan.as_dict())
        return InstallResult(False, "Update blocked by public skill scanner.", skill_name=local_name, record=record, warnings=scan.warnings)
    normalized = _normalized_installed_bundle(bundle, record.local_name)
    if normalized.content_hash == record.content_hash:
        return InstallResult(True, f"Skill '{local_name}' is already current.", skill_name=local_name, record=record, warnings=scan.warnings)

    _backup_existing_skill(record.local_name, reason="hub-update")
    dest = skills.USER_SKILLS_DIR / record.local_name
    with tempfile.TemporaryDirectory(prefix="row_bot_skill_hub_update_") as tmp:
        staged = pathlib.Path(tmp) / record.local_name
        _write_bundle_to_dir(normalized, staged)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(staged, dest)

    skills.load_skills()
    next_enabled = skills.is_enabled(record.local_name) if enabled is None else bool(enabled)
    skills.set_enabled(record.local_name, next_enabled)
    _clear_agent_cache()

    updated = replace(
        record,
        updated_at=now_iso(),
        content_hash=normalized.content_hash,
        enabled=next_enabled,
        file_count=len(normalized.files),
        scan_summary=scan.as_dict(),
        metadata={
            **dict(record.metadata or {}),
            **dict(bundle.metadata or {}),
            "upstream_content_hash": bundle.content_hash,
            "file_list": normalized.file_tree(),
        },
    )
    upsert_record(updated)
    append_audit("update", local_name=record.local_name, enabled=next_enabled)
    return InstallResult(True, f"Skill '{record.local_name}' updated.", skill_name=record.local_name, record=updated, warnings=scan.warnings)


def uninstall_skill(local_name: str) -> InstallResult:
    import row_bot.skills as skills

    record = get_record(local_name)
    if record is None:
        return InstallResult(False, f"Skill '{local_name}' is not hub-installed.", skill_name=local_name)
    dest = skills.USER_SKILLS_DIR / record.local_name
    if dest.exists():
        shutil.rmtree(dest)
    remove_record(record.local_name)
    skills.load_skills()
    _clear_agent_cache()
    append_audit("uninstall", local_name=record.local_name)
    return InstallResult(True, f"Skill '{record.local_name}' uninstalled.", skill_name=record.local_name, record=record)


def fetch_bundle_for_record(record: SkillInstallRecord) -> SkillBundle:
    from .catalog import source_for_id

    source = source_for_id(record.source)
    if source is None:
        raise ValueError(f"No source adapter registered for {record.source}")
    return source.fetch(record.install_ref)


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
    root.mkdir(parents=True, exist_ok=True)
    for file in bundle.files:
        rel = pathlib.PurePosixPath(file.path)
        if any(part == ".." for part in rel.parts) or str(rel).startswith("/"):
            raise ValueError(f"Unsafe skill file path: {file.path}")
        target = root / pathlib.Path(*rel.parts)
        _assert_inside(target.parent, root)
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
