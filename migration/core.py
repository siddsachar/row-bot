"""Pure migration models used by detection, planning, apply, reports, and UI."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from migration.redaction import redact_mapping, redact_value


class MigrationProvider(StrEnum):
    HERMES = "hermes"
    OPENCLAW = "openclaw"
    UNKNOWN = "unknown"


class MigrationCategory(StrEnum):
    IDENTITY = "identity"
    MEMORIES = "memories"
    SKILLS = "skills"
    MODEL = "model"
    API_KEYS = "api_keys"
    MCP = "mcp"
    CHANNELS = "channels"
    TASKS = "tasks"
    DOCUMENTS = "documents"
    ARCHIVE = "archive"
    SETTINGS = "settings"


class MigrationAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    APPEND = "append"
    COPY = "copy"
    SKIP = "skip"
    ARCHIVE = "archive"
    MANUAL_REVIEW = "manual_review"


class MigrationStatus(StrEnum):
    PLANNED = "planned"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    SENSITIVE = "sensitive"
    ARCHIVE_ONLY = "archive_only"
    MIGRATED = "migrated"
    ERROR = "error"


class MigrationSensitivity(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"
    RISKY = "risky"


class ConflictPolicy(StrEnum):
    REFUSE = "refuse"
    SKIP = "skip"
    RENAME = "rename"
    OVERWRITE = "overwrite"


SOURCE_DEFAULTS: dict[MigrationProvider, tuple[str, ...]] = {
    MigrationProvider.HERMES: (".hermes",),
    MigrationProvider.OPENCLAW: (".openclaw", ".clawdbot", ".moltbot"),
    MigrationProvider.UNKNOWN: (),
}

_SAFE_ID_RE = re.compile(r"[^a-z0-9_.:-]+")


def normalize_provider(value: str | MigrationProvider | None) -> MigrationProvider:
    """Normalize loose provider labels into a supported provider enum."""
    if isinstance(value, MigrationProvider):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"hermes", "hermes_agent", "nous_hermes"}:
        return MigrationProvider.HERMES
    if normalized in {"openclaw", "clawdbot", "moltbot"}:
        return MigrationProvider.OPENCLAW
    return MigrationProvider.UNKNOWN


def make_item_id(category: str | MigrationCategory, label: str) -> str:
    """Build a stable, report-safe item id from a category and label."""
    category_value = category.value if isinstance(category, MigrationCategory) else str(category)
    raw_label = str(label or "item").strip().lower().replace(" ", "-")
    safe_label = _SAFE_ID_RE.sub("-", raw_label).strip("-_.:") or "item"
    return f"{category_value}:{safe_label}"


def _path_to_text(value: str | Path | None) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True)
class MigrationSource:
    """Description of a detected source installation."""

    provider: MigrationProvider
    root: Path
    confidence: str = "low"
    label: str = ""
    found: bool = False
    discovered_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_path(
        cls,
        provider: str | MigrationProvider,
        root: str | Path,
        *,
        confidence: str = "low",
        label: str = "",
        found: bool = False,
        discovered_files: list[str] | tuple[str, ...] | None = None,
        warnings: list[str] | tuple[str, ...] | None = None,
    ) -> "MigrationSource":
        return cls(
            provider=normalize_provider(provider),
            root=Path(root).expanduser(),
            confidence=confidence,
            label=label,
            found=found,
            discovered_files=tuple(discovered_files or ()),
            warnings=tuple(warnings or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "root": str(self.root),
            "confidence": self.confidence,
            "label": self.label or self.provider.value.title(),
            "found": self.found,
            "discovered_files": list(self.discovered_files),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MigrationItem:
    """One planned migration operation."""

    id: str
    category: MigrationCategory
    action: MigrationAction
    status: MigrationStatus = MigrationStatus.PLANNED
    source: str | Path | None = None
    target: str | Path | None = None
    label: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    sensitivity: MigrationSensitivity = MigrationSensitivity.NORMAL
    conflict_policy: ConflictPolicy = ConflictPolicy.REFUSE
    selected: bool = True
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if self.status in {MigrationStatus.ARCHIVE_ONLY, MigrationStatus.SKIPPED, MigrationStatus.BLOCKED} and self.selected:
            object.__setattr__(self, "selected", False)
        if self.sensitivity in {MigrationSensitivity.SECRET, MigrationSensitivity.RISKY} and not self.requires_confirmation:
            object.__setattr__(self, "requires_confirmation", True)
        if self.sensitivity == MigrationSensitivity.SECRET and self.status == MigrationStatus.PLANNED:
            object.__setattr__(self, "status", MigrationStatus.SENSITIVE)
            object.__setattr__(self, "selected", False)

    @property
    def is_apply_candidate(self) -> bool:
        return self.selected and self.status in {MigrationStatus.PLANNED, MigrationStatus.SENSITIVE}

    @property
    def is_archive_only(self) -> bool:
        return self.status == MigrationStatus.ARCHIVE_ONLY or self.action == MigrationAction.ARCHIVE

    def with_status(self, status: MigrationStatus, reason: str = "") -> "MigrationItem":
        return MigrationItem(
            id=self.id,
            category=self.category,
            action=self.action,
            status=status,
            source=self.source,
            target=self.target,
            label=self.label,
            reason=reason or self.reason,
            details=dict(self.details),
            sensitivity=self.sensitivity,
            conflict_policy=self.conflict_policy,
            selected=self.selected,
            requires_confirmation=self.requires_confirmation,
        )

    def with_selection(self, selected: bool) -> "MigrationItem":
        return MigrationItem(
            id=self.id,
            category=self.category,
            action=self.action,
            status=self.status,
            source=self.source,
            target=self.target,
            label=self.label,
            reason=self.reason,
            details=dict(self.details),
            sensitivity=self.sensitivity,
            conflict_policy=self.conflict_policy,
            selected=bool(selected),
            requires_confirmation=self.requires_confirmation,
        )

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        details = redact_mapping(self.details) if redact else dict(self.details)
        source = redact_value(_path_to_text(self.source), key="source") if redact else _path_to_text(self.source)
        target = redact_value(_path_to_text(self.target), key="target") if redact else _path_to_text(self.target)
        return {
            "id": self.id,
            "category": self.category.value,
            "action": self.action.value,
            "status": self.status.value,
            "source": source,
            "target": target,
            "label": self.label,
            "reason": self.reason,
            "details": details,
            "sensitivity": self.sensitivity.value,
            "conflict_policy": self.conflict_policy.value,
            "selected": self.selected,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass(frozen=True)
class PlanSummary:
    total: int = 0
    selected: int = 0
    ready: int = 0
    migrated: int = 0
    conflicts: int = 0
    sensitive: int = 0
    archive_only: int = 0
    skipped: int = 0
    blocked: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "selected": self.selected,
            "ready": self.ready,
            "migrated": self.migrated,
            "conflicts": self.conflicts,
            "sensitive": self.sensitive,
            "archive_only": self.archive_only,
            "skipped": self.skipped,
            "blocked": self.blocked,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class MigrationPlan:
    """Preview-first migration plan."""

    source: MigrationSource
    items: tuple[MigrationItem, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, source: MigrationSource, warning: str = "") -> "MigrationPlan":
        warnings = (warning,) if warning else ()
        return cls(source=source, warnings=warnings)

    def add_item(self, item: MigrationItem) -> "MigrationPlan":
        return MigrationPlan(
            source=self.source,
            items=self.items + (item,),
            warnings=self.warnings,
            metadata=dict(self.metadata),
        )

    @property
    def summary(self) -> PlanSummary:
        status_counts = Counter(item.status for item in self.items)
        selected = sum(1 for item in self.items if item.is_apply_candidate)
        sensitive = sum(
            1 for item in self.items
            if item.sensitivity in {MigrationSensitivity.SENSITIVE, MigrationSensitivity.SECRET}
            or item.status == MigrationStatus.SENSITIVE
        )
        return PlanSummary(
            total=len(self.items),
            selected=selected,
            ready=status_counts[MigrationStatus.PLANNED],
            migrated=status_counts[MigrationStatus.MIGRATED],
            conflicts=status_counts[MigrationStatus.CONFLICT],
            sensitive=sensitive,
            archive_only=status_counts[MigrationStatus.ARCHIVE_ONLY],
            skipped=status_counts[MigrationStatus.SKIPPED],
            blocked=status_counts[MigrationStatus.BLOCKED],
            errors=status_counts[MigrationStatus.ERROR],
        )

    @property
    def has_blocking_conflicts(self) -> bool:
        return any(
            item.status == MigrationStatus.CONFLICT and item.conflict_policy == ConflictPolicy.REFUSE
            for item in self.items
        )

    @property
    def apply_candidates(self) -> tuple[MigrationItem, ...]:
        return tuple(item for item in self.items if item.is_apply_candidate)

    def items_by_category(self) -> dict[str, list[MigrationItem]]:
        grouped: dict[str, list[MigrationItem]] = {}
        for item in self.items:
            grouped.setdefault(item.category.value, []).append(item)
        return grouped

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        metadata = redact_mapping(self.metadata) if redact else dict(self.metadata)
        return {
            "source": self.source.to_dict(),
            "summary": self.summary.to_dict(),
            "warnings": list(self.warnings),
            "metadata": metadata,
            "items": [item.to_dict(redact=redact) for item in self.items],
        }
