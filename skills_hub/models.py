"""Data models for public skill browsing, scanning, and installation."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ScanSeverity = Literal["block", "warn", "info"]
SourceStatusMode = Literal["live", "cached", "stale", "partial", "error", "empty"]


@dataclass
class SkillHubEntry:
    id: str
    name: str
    description: str
    source: str
    source_id: str
    install_ref: str
    url: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    trust_level: str = "community"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillHubEntry":
        allowed = set(cls.__dataclass_fields__)
        values = {key: value for key, value in data.items() if key in allowed}
        values.setdefault("tags", [])
        values.setdefault("metadata", {})
        return cls(**values)


@dataclass
class SkillFile:
    path: str
    content: bytes
    size_bytes: int = 0
    sha256: str = ""
    kind: str = "text"

    def __post_init__(self) -> None:
        if isinstance(self.content, str):
            self.content = self.content.encode("utf-8")
        if not self.size_bytes:
            self.size_bytes = len(self.content)
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.content).hexdigest()
        self.path = self.path.replace("\\", "/").lstrip("/")

    @classmethod
    def from_text(cls, path: str, text: str, *, kind: str = "text") -> "SkillFile":
        return cls(path=path, content=(text or "").encode("utf-8"), kind=kind)

    @classmethod
    def from_bytes(cls, path: str, data: bytes, *, kind: str = "asset") -> "SkillFile":
        return cls(path=path, content=data or b"", kind=kind)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def content_b64(self) -> str:
        return base64.b64encode(self.content).decode("ascii")

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "kind": self.kind,
        }


@dataclass
class SkillBundle:
    source: str
    install_ref: str
    root_name: str
    primary_skill_path: str
    files: list[SkillFile]
    frontmatter: dict[str, Any]
    instructions: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def primary_file(self) -> SkillFile | None:
        target = self.primary_skill_path.replace("\\", "/")
        for file in self.files:
            if file.path == target:
                return file
        return None

    def file_tree(self) -> list[str]:
        return sorted(file.path for file in self.files)


@dataclass
class SkillScanFinding:
    severity: ScanSeverity
    code: str
    message: str
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillScanFinding":
        allowed = set(cls.__dataclass_fields__)
        values = {key: value for key, value in data.items() if key in allowed}
        values.setdefault("details", {})
        return cls(**values)


@dataclass
class SkillScanResult:
    ok: bool
    blocked: bool
    findings: list[SkillScanFinding]
    summary: dict[str, Any]
    token_estimate: int

    @property
    def warnings(self) -> list[SkillScanFinding]:
        return [finding for finding in self.findings if finding.severity == "warn"]

    @property
    def blocks(self) -> list[SkillScanFinding]:
        return [finding for finding in self.findings if finding.severity == "block"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "findings": [finding.as_dict() for finding in self.findings],
            "summary": dict(self.summary),
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillScanResult":
        findings = [
            SkillScanFinding.from_dict(item)
            for item in data.get("findings", [])
            if isinstance(item, dict)
        ]
        return cls(
            ok=bool(data.get("ok")),
            blocked=bool(data.get("blocked")),
            findings=findings,
            summary=dict(data.get("summary") or {}),
            token_estimate=int(data.get("token_estimate") or 0),
        )


@dataclass
class SkillInstallRecord:
    local_name: str
    source: str
    source_id: str
    install_ref: str
    installed_at: str
    updated_at: str
    content_hash: str
    enabled: bool
    file_count: int
    scan_summary: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillInstallRecord":
        allowed = set(cls.__dataclass_fields__)
        values = {key: value for key, value in data.items() if key in allowed}
        values.setdefault("scan_summary", {})
        values.setdefault("metadata", {})
        values.setdefault("enabled", False)
        return cls(**values)


@dataclass
class InstallResult:
    success: bool
    message: str
    skill_name: str = ""
    record: SkillInstallRecord | None = None
    warnings: list[SkillScanFinding] = field(default_factory=list)


@dataclass
class SourceResult:
    entries: list[SkillHubEntry]
    source_id: str
    status: SourceStatusMode
    message: str = ""
    next_cursor: str = ""
    fetched_at: float = 0.0
    duration_ms: int = 0
    from_cache: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.as_dict() for entry in self.entries],
            "source_id": self.source_id,
            "status": self.status,
            "message": self.message,
            "next_cursor": self.next_cursor,
            "fetched_at": self.fetched_at,
            "duration_ms": self.duration_ms,
            "from_cache": self.from_cache,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceResult":
        entries = [
            SkillHubEntry.from_dict(item)
            for item in data.get("entries", [])
            if isinstance(item, dict)
        ]
        return cls(
            entries=entries,
            source_id=str(data.get("source_id") or ""),
            status=str(data.get("status") or "empty"),  # type: ignore[arg-type]
            message=str(data.get("message") or ""),
            next_cursor=str(data.get("next_cursor") or ""),
            fetched_at=float(data.get("fetched_at") or 0.0),
            duration_ms=int(data.get("duration_ms") or 0),
            from_cache=bool(data.get("from_cache")),
        )


@dataclass
class SourceHealth:
    source_id: str
    online: bool = False
    last_success: float = 0.0
    last_error: str = ""
    rate_limited: bool = False
    cache_age_seconds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DetectedSourceInput:
    kind: str
    value: str
    normalized: str = ""
    source_id: str = ""
    confidence: float = 1.0
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_import_like(self) -> bool:
        return self.kind not in {"empty", "keyword"}


@dataclass
class CatalogSearchResult:
    entries: list[SkillHubEntry]
    mode: str
    query: str = ""
    source_counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
    source_statuses: list[SourceResult] = field(default_factory=list)
    detected_input: DetectedSourceInput | None = None
