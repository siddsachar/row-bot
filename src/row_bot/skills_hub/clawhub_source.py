"""ClawHub public source adapter with high-risk ZIP safety guards."""

from __future__ import annotations

import io
import pathlib
import stat
import urllib.parse
import zipfile
from typing import Any

from .models import SourceResult, SkillBundle, SkillFile, SkillHubEntry
from .search_index import search_entries
from .sources import (
    SkillSource,
    bundle_from_files,
    bundle_from_marketplace_markdown,
    classify_file_kind,
    fetch_bytes,
    fetch_json,
    fetch_text,
    slugify,
)

API_ROOT = "https://clawhub.ai/api/v1"
MAX_ZIP_FILES = 80
MAX_ZIP_TOTAL_BYTES = 5_000_000
MAX_ZIP_FILE_BYTES = 1_000_000


class ClawHubSource(SkillSource):
    id = "clawhub"
    display_name = "ClawHub"
    trust_default = "high-risk community"
    risk = "high"
    supports_browse = True
    supports_search = True
    supports_import = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        params = urllib.parse.urlencode({"limit": str(limit), "cursor": cursor or ""})
        entries = parse_clawhub_payload(fetch_json(f"{API_ROOT}/skills?{params}"))
        return SourceResult(entries[:limit], self.id, "live" if entries else "empty")

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        params = urllib.parse.urlencode({"q": query or "", "limit": str(limit)})
        try:
            entries = parse_clawhub_payload(fetch_json(f"{API_ROOT}/skills/search?{params}"))
        except Exception:
            entries = self.browse(limit=max(limit, 100)).entries
        return search_entries(entries, query, limit=limit)

    def can_resolve(self, value: str) -> bool:
        host = urllib.parse.urlparse(value or "").netloc.lower()
        return host in {"clawhub.ai", "www.clawhub.ai"}

    def resolve(self, value: str) -> SourceResult:
        parsed = urllib.parse.urlparse(value)
        slug = parsed.path.strip("/").split("/")[-1]
        if not slug:
            return SourceResult([], self.id, "empty", "ClawHub URL does not include a skill slug.")
        data = fetch_json(f"{API_ROOT}/skills/{urllib.parse.quote(slug)}")
        entry = _entry_from_clawhub_item(data.get("skill", data) if isinstance(data, dict) else {})
        return SourceResult([entry] if entry else [], self.id, "live" if entry else "empty")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        if install_ref.startswith("http") and install_ref.lower().endswith((".md", "/skill.md", ".markdown")):
            markdown = fetch_text(install_ref)
            bundle = bundle_from_marketplace_markdown(
                source=self.id,
                install_ref=install_ref,
                root_name=slugify(urllib.parse.urlparse(install_ref).path.split("/")[-2] or "clawhub_skill"),
                text=markdown,
                name=urllib.parse.urlparse(install_ref).path.split("/")[-2] or "clawhub_skill",
                metadata={"url": install_ref},
            )
            bundle.metadata.update({"trust_level": "high-risk community", "risk": "high", "source_warning": _warning()})
            return bundle
        if install_ref.startswith("http") and install_ref.lower().endswith(".zip"):
            return bundle_from_clawhub_zip(fetch_bytes(install_ref), install_ref=install_ref)
        slug = install_ref.removeprefix("clawhub:").strip("/")
        raw_url = f"{API_ROOT}/skills/{urllib.parse.quote(slug)}/file?path=SKILL.md"
        try:
            markdown = fetch_text(raw_url)
            if markdown.strip():
                return bundle_from_marketplace_markdown(
                    source=self.id,
                    install_ref=f"clawhub:{slug}",
                    root_name=slugify(slug or "clawhub_skill"),
                    text=markdown,
                    name=slug or "clawhub_skill",
                    metadata={
                        "trust_level": "high-risk community",
                        "risk": "high",
                        "source_warning": _warning(),
                        "raw_url": raw_url,
                    },
                )
        except Exception:
            pass
        zip_url = f"{API_ROOT}/download?slug={urllib.parse.quote(slug)}"
        try:
            return bundle_from_clawhub_zip(fetch_bytes(zip_url), install_ref=zip_url)
        except Exception:
            pass
        data = fetch_json(f"{API_ROOT}/skills/{urllib.parse.quote(slug)}")
        detail = data.get("skill", data) if isinstance(data, dict) else {}
        if not isinstance(detail, dict):
            raise ValueError("ClawHub detail response is not a mapping")
        markdown_url = str(detail.get("skillMdUrl") or detail.get("skill_md_url") or detail.get("rawUrl") or "").strip()
        if markdown_url:
            return self.fetch(markdown_url)
        zip_url = str(detail.get("zipUrl") or detail.get("downloadUrl") or detail.get("archiveUrl") or "").strip()
        if zip_url:
            return self.fetch(zip_url)
        markdown = str(detail.get("skillMd") or detail.get("markdown") or detail.get("content") or "").strip()
        if markdown:
            name = str(detail.get("name") or detail.get("title") or slug or "clawhub_skill")
            return bundle_from_marketplace_markdown(
                source=self.id,
                install_ref=f"clawhub:{slug}",
                root_name=slugify(name),
                text=markdown,
                name=name,
                description=str(detail.get("description") or detail.get("summary") or ""),
                metadata={"trust_level": "high-risk community", "risk": "high", "source_warning": _warning()},
            )
        raise ValueError("ClawHub entry does not expose skill markdown or a ZIP bundle")


def parse_clawhub_payload(data: Any) -> list[SkillHubEntry]:
    if isinstance(data, dict):
        raw_items = data.get("skills") or data.get("items") or data.get("data") or data.get("results") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []
    entries: list[SkillHubEntry] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entry = _entry_from_clawhub_item(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def bundle_from_clawhub_zip(data: bytes, *, install_ref: str) -> SkillBundle:
    files: list[SkillFile] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) > MAX_ZIP_FILES:
            raise ValueError(f"ClawHub ZIP has too many files ({len(members)} > {MAX_ZIP_FILES})")
        root_prefix = _common_root_prefix([member.filename for member in members])
        for member in members:
            rel_path = _safe_zip_member_path(member.filename, root_prefix=root_prefix)
            if not rel_path:
                continue
            if _zip_member_is_symlink(member):
                files.append(SkillFile.from_text(rel_path, "", kind="symlink"))
                continue
            if member.file_size > MAX_ZIP_FILE_BYTES:
                raise ValueError(f"ClawHub ZIP member is too large: {rel_path}")
            total += member.file_size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("ClawHub ZIP exceeds total size cap")
            content = archive.read(member)
            files.append(SkillFile.from_bytes(rel_path, content, kind=classify_file_kind(rel_path, content)))
    return bundle_from_files(
        source="clawhub",
        install_ref=install_ref,
        root_name=pathlib.PurePosixPath(urllib.parse.urlparse(install_ref).path).stem or "clawhub_skill",
        files=files,
        metadata={"trust_level": "high-risk community", "risk": "high", "source_warning": _warning()},
    )


def _entry_from_clawhub_item(raw: dict[str, Any]) -> SkillHubEntry | None:
    name = str(raw.get("name") or raw.get("title") or raw.get("id") or raw.get("slug") or "").strip()
    if not name:
        return None
    slug = str(raw.get("slug") or raw.get("id") or slugify(name)).strip()
    detail_url = str(raw.get("url") or raw.get("detailUrl") or f"https://clawhub.ai/skills/{slug}")
    skill_url = str(raw.get("skillMdUrl") or raw.get("skill_md_url") or raw.get("rawUrl") or "").strip()
    zip_url = str(raw.get("zipUrl") or raw.get("downloadUrl") or raw.get("archiveUrl") or "").strip()
    install_ref = skill_url or zip_url or f"clawhub:{slug}"
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return SkillHubEntry(
        id=f"clawhub:{slug}",
        name=name,
        description=str(raw.get("description") or raw.get("summary") or ""),
        source="clawhub",
        source_id="clawhub.ai",
        install_ref=install_ref,
        url=detail_url,
        author=str(raw.get("author") or raw.get("publisher") or ""),
        tags=[str(tag) for tag in tags],
        trust_level="high-risk community",
        metadata={
            "slug": slug,
            "detail_url": detail_url,
            "skill_url": skill_url,
            "zip_url": zip_url,
            "source_warning": _warning(),
            "risk": "high",
            "trust_level": "high-risk community",
        },
    )


def _safe_zip_member_path(filename: str, *, root_prefix: str) -> str:
    clean = str(filename or "").replace("\\", "/").lstrip("/")
    if root_prefix and clean.startswith(root_prefix):
        clean = clean[len(root_prefix):]
    pure = pathlib.PurePosixPath(clean)
    if not clean or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"Unsafe ClawHub ZIP path: {filename}")
    return str(pure)


def _zip_member_is_symlink(member: zipfile.ZipInfo) -> bool:
    mode = (member.external_attr >> 16) & 0o777777
    return stat.S_ISLNK(mode)


def _common_root_prefix(paths: list[str]) -> str:
    first_parts = [path.replace("\\", "/").split("/", 1)[0] for path in paths if "/" in path.replace("\\", "/")]
    if not first_parts:
        return ""
    first = first_parts[0]
    if first and all(part == first for part in first_parts):
        return first + "/"
    return ""


def _warning() -> str:
    return "Community high-risk source. Review scan findings carefully before making available."
