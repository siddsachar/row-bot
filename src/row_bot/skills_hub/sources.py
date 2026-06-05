"""Source adapter contracts and shared helpers for public skill sources."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import urllib.parse
import urllib.request
from typing import Any

import yaml

from .models import SourceHealth, SourceResult, SkillBundle, SkillFile, SkillHubEntry

DEFAULT_TIMEOUT = 15
MAX_SOURCE_BYTES = 5_000_000
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
BROWSER_HEADERS = {
    "User-Agent": "Row-Bot-Skills-Hub/1.0",
    "Accept": "text/plain,application/json,text/markdown,*/*;q=0.8",
}


class SkillSource:
    id: str
    display_name: str
    trust_default: str = "community"
    supports_browse: bool = False
    supports_search: bool = False
    supports_import: bool = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        """Return public entries for empty browse."""
        return SourceResult([], self.id, "empty", "Browse is not supported by this source.")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        """Fetch and return the bundle represented by an entry."""
        return self.fetch(entry.install_ref)

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        """Return matching public skill entries."""
        if not self.supports_browse:
            return []
        from .search_index import search_entries

        return search_entries(self.browse(limit=max(limit, 50)).entries, query, limit=limit)

    def search_result(self, query: str, limit: int = 50) -> SourceResult:
        try:
            entries = self.search(query, limit=limit)
            return SourceResult(entries, self.id, "live" if entries else "empty")
        except Exception as exc:
            return SourceResult([], self.id, "error", str(exc))

    def can_resolve(self, value: str) -> bool:
        return False

    def resolve(self, value: str) -> SourceResult:
        return SourceResult([], self.id, "empty", "Input is not supported by this source.")

    def fetch(self, install_ref: str) -> SkillBundle:
        """Fetch a bundle by install reference."""
        raise NotImplementedError(f"{self.display_name} cannot fetch skill bundles")

    def health(self) -> SourceHealth:
        return SourceHealth(source_id=self.id, online=False)


def fetch_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    request_headers = dict(BROWSER_HEADERS)
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-triggered public skill fetch
        data = response.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(f"Source response is larger than {MAX_SOURCE_BYTES} bytes")
    return data


def fetch_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    return fetch_bytes(url, headers=headers, timeout=timeout).decode("utf-8", errors="replace")


def fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
    return json.loads(fetch_text(url, headers=headers, timeout=timeout))


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text or "")
    if not match:
        raise ValueError("SKILL.md is missing YAML frontmatter")
    meta = yaml.safe_load(match.group(1))
    if not isinstance(meta, dict):
        raise ValueError("SKILL.md frontmatter must be a mapping")
    instructions = text[match.end():].strip()
    return meta, instructions


def looks_like_skill_markdown(text: str) -> bool:
    value = text or ""
    if FRONTMATTER_RE.match(value):
        return True
    lower = value.lower()
    return value.lstrip().startswith("#") and any(
        marker in lower
        for marker in ("when to use", "instructions", "workflow", "steps", "skill")
    )


def markdown_with_frontmatter(text: str, *, name: str, description: str = "") -> str:
    if FRONTMATTER_RE.match(text or ""):
        meta, body = parse_skill_markdown(text)
        meta.setdefault("name", normalize_skill_name(name))
        if description:
            meta.setdefault("description", description)
        return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body.strip() + "\n"
    clean_name = normalize_skill_name(name)
    display_name = title_from_slug(clean_name)
    body = (text or "").strip()
    frontmatter = {
        "name": clean_name,
        "display_name": display_name,
        "description": description or f"Imported public skill: {display_name}",
        "version": "1.0",
        "author": "Public Source",
    }
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True) + "---\n\n" + body + "\n"


def normalize_skill_name(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_ -]+", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "imported_skill"


def slugify(value: str, *, fallback: str = "skill") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def title_from_slug(value: str) -> str:
    return re.sub(r"[-_]+", " ", value or "").strip().title() or "Public Skill"


def normalize_bundle_path(path: str) -> str:
    text = str(path or "").replace("\\", "/").lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    return "/".join(parts)


def classify_file_kind(path: str, content: bytes | None = None) -> str:
    suffix = pathlib.PurePosixPath(path.replace("\\", "/")).suffix.lower()
    if suffix in {".md", ".txt", ".rst"}:
        return "markdown" if suffix == ".md" else "text"
    if suffix in {".yml", ".yaml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "asset"
    if suffix in {".docx", ".xlsx", ".pptx", ".pdf", ".csv", ".tsv"}:
        return "template"
    if suffix in {".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".ts"}:
        return "script"
    if content and b"\x00" in content[:2048]:
        return "binary"
    return "text"


def compute_bundle_hash(files: list[SkillFile]) -> str:
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda item: item.path):
        digest.update(file.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.sha256.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def bundle_from_files(
    *,
    source: str,
    install_ref: str,
    root_name: str,
    files: list[SkillFile],
    metadata: dict[str, Any] | None = None,
) -> SkillBundle:
    normalized_files = [
        SkillFile(
            path=normalize_bundle_path(file.path),
            content=file.content,
            kind=file.kind or classify_file_kind(file.path, file.content),
        )
        for file in files
    ]
    skill_paths = [
        file.path for file in normalized_files
        if pathlib.PurePosixPath(file.path).name == "SKILL.md"
    ]
    primary = skill_paths[0] if len(skill_paths) == 1 else "SKILL.md"
    primary_file = next((file for file in normalized_files if file.path == primary), None)
    frontmatter: dict[str, Any] = {}
    instructions = ""
    if primary_file is not None:
        frontmatter, instructions = parse_skill_markdown(primary_file.text)
    return SkillBundle(
        source=source,
        install_ref=install_ref,
        root_name=root_name,
        primary_skill_path=primary,
        files=normalized_files,
        frontmatter=frontmatter,
        instructions=instructions,
        content_hash=compute_bundle_hash(normalized_files),
        metadata=dict(metadata or {}),
    )


def bundle_from_single_file(
    *,
    source: str,
    install_ref: str,
    root_name: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> SkillBundle:
    return bundle_from_files(
        source=source,
        install_ref=install_ref,
        root_name=root_name,
        files=[SkillFile.from_text("SKILL.md", text, kind="markdown")],
        metadata=metadata,
    )


def bundle_from_marketplace_markdown(
    *,
    source: str,
    install_ref: str,
    root_name: str,
    text: str,
    name: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> SkillBundle:
    """Build a single-file public marketplace bundle, inferring frontmatter when needed."""
    inferred_name = name or _heading_from_markdown(text) or root_name
    skill_file = skill_file_from_markdown_text(
        "SKILL.md",
        text,
        name=inferred_name,
        description=description,
    )
    return bundle_from_files(
        source=source,
        install_ref=install_ref,
        root_name=root_name,
        files=[skill_file],
        metadata=metadata,
    )


def skill_file_from_markdown_text(
    path: str,
    text: str,
    *,
    name: str = "",
    description: str = "",
) -> SkillFile:
    if FRONTMATTER_RE.match(text or ""):
        return SkillFile.from_text(path, text, kind="markdown")
    inferred = name or _heading_from_markdown(text) or pathlib.PurePosixPath(path).stem
    return SkillFile.from_text(
        path,
        markdown_with_frontmatter(text, name=inferred, description=description),
        kind="markdown",
    )


def _heading_from_markdown(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return ""


def entry_search_text(entry: SkillHubEntry) -> str:
    from .search_index import entry_search_text as _entry_search_text

    return _entry_search_text(entry).lower()


def score_entry(entry: SkillHubEntry, query: str) -> tuple[int, str]:
    from .search_index import score_entry as _score_entry

    return (-int(_score_entry(entry, query)), entry.name.lower())


def source_url_from_entry(entry: SkillHubEntry) -> str:
    if entry.url:
        return entry.url
    parsed = urllib.parse.urlparse(entry.install_ref)
    if parsed.scheme in {"http", "https"}:
        return entry.install_ref
    return ""
