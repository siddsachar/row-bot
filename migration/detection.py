"""Read-only source detection for migration wizard providers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from migration.core import MigrationCategory, MigrationProvider, MigrationSensitivity, MigrationSource, normalize_provider
from migration.redaction import redact_mapping

try:
    import yaml
except Exception:  # pragma: no cover - optional parser, detection remains useful without it
    yaml = None


class MigrationScanKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class MigrationScanEntry:
    relative_path: str
    category: MigrationCategory
    kind: MigrationScanKind
    sensitivity: MigrationSensitivity = MigrationSensitivity.NORMAL
    archive_only: bool = False
    size_bytes: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "category": self.category.value,
            "kind": self.kind.value,
            "sensitivity": self.sensitivity.value,
            "archive_only": self.archive_only,
            "size_bytes": self.size_bytes,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MigrationScanSummary:
    total: int = 0
    files: int = 0
    directories: int = 0
    archive_only: int = 0
    sensitive: int = 0
    risky: int = 0
    total_size_bytes: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "files": self.files,
            "directories": self.directories,
            "archive_only": self.archive_only,
            "sensitive": self.sensitive,
            "risky": self.risky,
            "total_size_bytes": self.total_size_bytes,
        }


@dataclass(frozen=True)
class MigrationScan:
    source: MigrationSource
    entries: tuple[MigrationScanEntry, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True

    @property
    def summary(self) -> MigrationScanSummary:
        files = sum(1 for entry in self.entries if entry.kind == MigrationScanKind.FILE)
        directories = sum(1 for entry in self.entries if entry.kind == MigrationScanKind.DIRECTORY)
        archive_only = sum(1 for entry in self.entries if entry.archive_only)
        sensitive = sum(
            1 for entry in self.entries
            if entry.sensitivity in {MigrationSensitivity.SENSITIVE, MigrationSensitivity.SECRET}
        )
        risky = sum(1 for entry in self.entries if entry.sensitivity == MigrationSensitivity.RISKY)
        total_size = sum(entry.size_bytes for entry in self.entries)
        return MigrationScanSummary(
            total=len(self.entries),
            files=files,
            directories=directories,
            archive_only=archive_only,
            sensitive=sensitive,
            risky=risky,
            total_size_bytes=total_size,
        )

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        metadata = redact_mapping(self.metadata) if redact else dict(self.metadata)
        return {
            "read_only": self.read_only,
            "source": self.source.to_dict(),
            "summary": self.summary.to_dict(),
            "metadata": metadata,
            "entries": [entry.to_dict() for entry in self.entries],
        }


HERMES_ARCHIVE_DIRS = ("plugins", "sessions", "logs", "cron", "mcp-tokens")
HERMES_ARCHIVE_FILES = ("auth.json", "state.db")
HERMES_SIGNATURE_PATHS = ("config.yaml", "SOUL.md", "AGENTS.md", "memories/MEMORY.md", "memories/USER.md")
HERMES_MARKERS: tuple[tuple[str, MigrationCategory, MigrationSensitivity, bool, str], ...] = (
    ("config.yaml", MigrationCategory.SETTINGS, MigrationSensitivity.SENSITIVE, False, "Hermes configuration"),
    (".env", MigrationCategory.API_KEYS, MigrationSensitivity.SECRET, False, "Hermes environment secrets"),
    ("SOUL.md", MigrationCategory.IDENTITY, MigrationSensitivity.NORMAL, False, "Hermes persona"),
    ("AGENTS.md", MigrationCategory.IDENTITY, MigrationSensitivity.NORMAL, False, "Hermes agent instructions"),
    ("memories/MEMORY.md", MigrationCategory.MEMORIES, MigrationSensitivity.NORMAL, False, "Hermes memory"),
    ("memories/USER.md", MigrationCategory.MEMORIES, MigrationSensitivity.NORMAL, False, "Hermes user profile"),
    ("skills", MigrationCategory.SKILLS, MigrationSensitivity.NORMAL, False, "Hermes skills"),
)

OPENCLAW_ARCHIVE_DIRS = (
    "sessions",
    "logs",
    "plugins",
    "extensions",
    "oauth",
    "cache",
    "workspace/.agents",
)
OPENCLAW_ARCHIVE_FILES = (
    "cron-store.json",
    "hooks.json",
    "state.db",
    "credentials/telegram-default-allowFrom.json",
)
OPENCLAW_SIGNATURE_PATHS = (
    "openclaw.json",
    "exec-approvals.json",
    "workspace/AGENTS.md",
    "workspace/MEMORY.md",
    "workspace/USER.md",
)
OPENCLAW_MARKERS: tuple[tuple[str, MigrationCategory, MigrationSensitivity, bool, str], ...] = (
    ("openclaw.json", MigrationCategory.SETTINGS, MigrationSensitivity.SENSITIVE, False, "OpenClaw config"),
    (".env", MigrationCategory.API_KEYS, MigrationSensitivity.SECRET, False, "OpenClaw environment secrets"),
    ("exec-approvals.json", MigrationCategory.SETTINGS, MigrationSensitivity.RISKY, True, "OpenClaw command allowlist"),
    ("workspace/AGENTS.md", MigrationCategory.IDENTITY, MigrationSensitivity.NORMAL, False, "OpenClaw workspace instructions"),
    ("workspace/MEMORY.md", MigrationCategory.MEMORIES, MigrationSensitivity.NORMAL, False, "OpenClaw memory"),
    ("workspace/USER.md", MigrationCategory.MEMORIES, MigrationSensitivity.NORMAL, False, "OpenClaw user profile"),
    ("workspace/skills", MigrationCategory.SKILLS, MigrationSensitivity.NORMAL, False, "OpenClaw workspace skills"),
    ("skills", MigrationCategory.SKILLS, MigrationSensitivity.NORMAL, False, "OpenClaw shared skills"),
    ("workspace/memory", MigrationCategory.MEMORIES, MigrationSensitivity.NORMAL, False, "OpenClaw daily memory"),
)


def detect_source(provider: str | MigrationProvider, root: str | Path | None = None) -> MigrationScan:
    normalized = normalize_provider(provider)
    if normalized == MigrationProvider.HERMES:
        return detect_hermes_source(root)
    if normalized == MigrationProvider.OPENCLAW:
        return detect_openclaw_source(root)
    source = MigrationSource.from_path(MigrationProvider.UNKNOWN, root or "", label="Unknown", found=False)
    return MigrationScan(source=source, metadata={"error": "unsupported provider"})


def detect_hermes_source(root: str | Path | None = None) -> MigrationScan:
    source_root = _resolve_root(root, ".hermes")
    if _looks_like_openclaw(source_root) and not _looks_like_hermes(source_root):
        return _provider_mismatch_scan(
            MigrationProvider.HERMES,
            source_root,
            "This looks like an OpenClaw folder. Choose OpenClaw as the migration source.",
            detected_provider=MigrationProvider.OPENCLAW,
        )
    entries = _collect_entries(source_root, HERMES_MARKERS)
    entries.extend(
        _collect_archive_entries(
            source_root,
            dirs=HERMES_ARCHIVE_DIRS,
            files=HERMES_ARCHIVE_FILES,
            reason="Hermes state archived for manual review",
        )
    )
    entries = sorted(entries, key=lambda entry: entry.relative_path)
    found = bool(entries)
    warnings = _warnings_for_scan(source_root, entries)
    metadata = {
        "config_keys": _yaml_top_level_keys(source_root / "config.yaml"),
        "env_keys": _env_keys(source_root / ".env"),
        "skill_count": _skill_count(source_root / "skills"),
    }
    source = MigrationSource.from_path(
        MigrationProvider.HERMES,
        source_root,
        confidence="high" if found else "low",
        label="Hermes",
        found=found,
        discovered_files=[entry.relative_path for entry in entries],
        warnings=warnings,
    )
    return MigrationScan(source=source, entries=tuple(entries), metadata=metadata)


def detect_openclaw_source(root: str | Path | None = None) -> MigrationScan:
    source_root = _resolve_openclaw_root(root)
    if _looks_like_hermes(source_root) and not _looks_like_openclaw(source_root):
        return _provider_mismatch_scan(
            MigrationProvider.OPENCLAW,
            source_root,
            "This looks like a Hermes folder. Choose Hermes Agent as the migration source.",
            detected_provider=MigrationProvider.HERMES,
        )
    entries = _collect_entries(source_root, OPENCLAW_MARKERS)
    entries.extend(
        _collect_archive_entries(
            source_root,
            dirs=OPENCLAW_ARCHIVE_DIRS,
            files=OPENCLAW_ARCHIVE_FILES,
            reason="OpenClaw state archived for manual review",
        )
    )
    entries = sorted(entries, key=lambda entry: entry.relative_path)
    found = bool(entries)
    warnings = list(_warnings_for_scan(source_root, entries))
    if source_root.name in {".clawdbot", ".moltbot"}:
        warnings.append(f"legacy OpenClaw directory name detected: {source_root.name}")
    metadata = {
        "config_keys": _json_top_level_keys(source_root / "openclaw.json"),
        "env_keys": _env_keys(source_root / ".env"),
        "workspace_skill_count": _skill_count(source_root / "workspace" / "skills"),
        "shared_skill_count": _skill_count(source_root / "skills"),
    }
    source = MigrationSource.from_path(
        MigrationProvider.OPENCLAW,
        source_root,
        confidence="high" if found else "low",
        label="OpenClaw",
        found=found,
        discovered_files=[entry.relative_path for entry in entries],
        warnings=warnings,
    )
    return MigrationScan(source=source, entries=tuple(entries), metadata=metadata)


def _resolve_root(root: str | Path | None, default_dir: str) -> Path:
    return Path(root).expanduser() if root is not None else Path.home() / default_dir


def _resolve_openclaw_root(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    for candidate_name in (".openclaw", ".clawdbot", ".moltbot"):
        candidate = Path.home() / candidate_name
        if candidate.is_dir():
            return candidate
    return Path.home() / ".openclaw"


def _looks_like_hermes(root: Path) -> bool:
    return root.exists() and any((root / relative_path).exists() for relative_path in HERMES_SIGNATURE_PATHS)


def _looks_like_openclaw(root: Path) -> bool:
    return root.exists() and any((root / relative_path).exists() for relative_path in OPENCLAW_SIGNATURE_PATHS)


def _provider_mismatch_scan(
    provider: MigrationProvider,
    root: Path,
    warning: str,
    *,
    detected_provider: MigrationProvider,
) -> MigrationScan:
    source = MigrationSource.from_path(
        provider,
        root,
        confidence="low",
        label=provider.value.title(),
        found=False,
        warnings=(warning,),
    )
    return MigrationScan(source=source, metadata={"error": "provider_mismatch", "detected_provider": detected_provider.value})


def _collect_entries(
    root: Path,
    markers: tuple[tuple[str, MigrationCategory, MigrationSensitivity, bool, str], ...],
) -> list[MigrationScanEntry]:
    entries: list[MigrationScanEntry] = []
    for relative_path, category, sensitivity, archive_only, reason in markers:
        candidate = root / relative_path
        entry = _entry_for_path(candidate, relative_path, category, sensitivity, archive_only, reason)
        if entry is not None:
            entries.append(entry)
    return entries


def _collect_archive_entries(
    root: Path,
    *,
    dirs: tuple[str, ...],
    files: tuple[str, ...],
    reason: str,
) -> list[MigrationScanEntry]:
    entries: list[MigrationScanEntry] = []
    for relative_path in dirs:
        entry = _entry_for_path(
            root / relative_path,
            relative_path,
            MigrationCategory.ARCHIVE,
            MigrationSensitivity.RISKY,
            True,
            reason,
        )
        if entry is not None:
            entries.append(entry)
    for relative_path in files:
        entry = _entry_for_path(
            root / relative_path,
            relative_path,
            MigrationCategory.ARCHIVE,
            MigrationSensitivity.RISKY,
            True,
            reason,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_for_path(
    path: Path,
    relative_path: str,
    category: MigrationCategory,
    sensitivity: MigrationSensitivity,
    archive_only: bool,
    reason: str,
) -> MigrationScanEntry | None:
    if path.is_file():
        return MigrationScanEntry(
            relative_path=relative_path.replace("\\", "/"),
            category=category,
            kind=MigrationScanKind.FILE,
            sensitivity=sensitivity,
            archive_only=archive_only,
            size_bytes=_safe_file_size(path),
            reason=reason,
        )
    if path.is_dir():
        return MigrationScanEntry(
            relative_path=relative_path.replace("\\", "/"),
            category=category,
            kind=MigrationScanKind.DIRECTORY,
            sensitivity=sensitivity,
            archive_only=archive_only,
            size_bytes=_safe_tree_size(path),
            reason=reason,
        )
    return None


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_tree_size(root: Path) -> int:
    total = 0
    try:
        for child in root.rglob("*"):
            if child.is_file():
                total += _safe_file_size(child)
    except OSError:
        return total
    return total


def _warnings_for_scan(root: Path, entries: list[MigrationScanEntry]) -> tuple[str, ...]:
    warnings: list[str] = []
    if not root.exists():
        warnings.append("source directory does not exist")
    if any(entry.sensitivity == MigrationSensitivity.SECRET for entry in entries):
        warnings.append("secrets detected; import must be opt-in")
    if any(entry.archive_only for entry in entries):
        warnings.append("archive-only state detected for manual review")
    return tuple(warnings)


def _env_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    keys: list[str] = []
    for line in _safe_read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key:
            keys.append(key)
    return sorted(dict.fromkeys(keys))


def _json_top_level_keys(path: Path) -> list[str]:
    try:
        data = json.loads(_safe_read_text(path))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    return sorted(str(key) for key in data)


def _yaml_top_level_keys(path: Path) -> list[str]:
    if yaml is None or not path.is_file():
        return []
    try:
        data = yaml.safe_load(_safe_read_text(path))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    return sorted(str(key) for key in data)


def _skill_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    count = 0
    for candidate in root.iterdir():
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            count += 1
    return count


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
