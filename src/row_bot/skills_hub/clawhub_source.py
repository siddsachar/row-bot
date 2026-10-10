"""ClawHub public source adapter with high-risk ZIP safety guards."""

from __future__ import annotations

import io
import json
import pathlib
import re
import stat
import urllib.parse
import zipfile
from typing import Any

import httpx

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


class ClawHubSourceBlocked(ValueError):
    """An explicit publisher moderation decision, distinct from a network failure."""


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
        entries = _public_entries(fetch_json(f"{API_ROOT}/skills?{params}"))
        return SourceResult(entries[:limit], self.id, "live" if entries else "empty")

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        params = urllib.parse.urlencode({"q": query or "", "limit": str(limit)})
        entries = _public_entries(fetch_json(f"{API_ROOT}/search?{params}"))
        return search_entries(entries, query, limit=limit)

    def can_resolve(self, value: str) -> bool:
        host = urllib.parse.urlparse(value or "").netloc.lower()
        return host in {"clawhub.ai", "www.clawhub.ai"}

    def resolve(self, value: str) -> SourceResult:
        parsed = urllib.parse.urlparse(value)
        parts = parsed.path.strip("/").split("/")
        slug = parts[-1]
        if not slug:
            return SourceResult([], self.id, "empty", "ClawHub URL does not include a skill slug.")
        owner = parts[0] if len(parts) in {2, 3} and parts[0] != "skills" else ""
        data = _detail(slug, owner)
        raw = dict(data.get("skill", data))
        raw["owner"] = data.get("owner", {})
        entry = _entry_from_clawhub_item(raw)
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
        reference, _, version = install_ref.removeprefix("clawhub:").removeprefix("@").partition("@")
        owner, separator, slug = reference.partition("/")
        if not separator:
            owner, slug = "", owner
        detail = _detail(slug, owner)
        skill = detail.get("skill", detail)
        moderation = detail.get("moderation") or {}
        if not isinstance(moderation, dict):
            raise ValueError("Invalid ClawHub moderation response")
        if (skill.get("deletedAt") or skill.get("moderationStatus") in {"blocked", "removed", "malicious"}
                or moderation.get("isMalwareBlocked") is True or moderation.get("verdict") == "malicious"):
            raise ClawHubSourceBlocked("ClawHub has removed or moderation-blocked this skill. Its local files are retained, but it cannot be turned on.")
        latest = detail.get("latestVersion") or skill.get("latestVersion") or {}
        version = version or (latest.get("version", "") if isinstance(latest, dict) else latest)
        if not isinstance(version, str) or not version or len(version) > 128:
            raise ValueError("ClawHub did not provide an installable version")
        author = _owner(detail) or owner
        pinned = f"clawhub:{author + '/' if author else ''}{slug}@{version}"
        params = {"slug": slug, "version": version}
        if author:
            params["owner"] = author
        url = f"{API_ROOT}/download?" + urllib.parse.urlencode(params)
        data = fetch_bytes(url)
        if data.lstrip().startswith(b"{"):
            bundle = _github_handoff(json.loads(data), install_ref=pinned)
        else:
            bundle = bundle_from_clawhub_zip(data, install_ref=pinned)
        bundle.metadata.update({"version": version, "slug": slug, "author": author,
            "url": f"https://clawhub.ai/{author}/skills/{slug}" if author else f"https://clawhub.ai/skills/{slug}"})
        return bundle


def revalidate_bundle(bundle: SkillBundle) -> None:
    """Check current moderation and availability of the exact reviewed version."""
    if bundle.source != "clawhub" or not bundle.install_ref.startswith("clawhub:"):
        return
    reference, separator, version = bundle.install_ref.removeprefix("clawhub:").partition("@")
    if not separator:
        raise ValueError("ClawHub version must be pinned before installation")
    owner, slash, slug = reference.partition("/")
    if not slash:
        owner, slug = "", owner
    detail = _detail(slug, owner)
    skill, moderation = detail.get("skill", detail), detail.get("moderation") or {}
    if (skill.get("deletedAt") or skill.get("moderationStatus") in {"blocked", "removed", "malicious"}
            or moderation.get("isMalwareBlocked") is True or moderation.get("verdict") == "malicious"):
        raise ClawHubSourceBlocked("ClawHub has removed or moderation-blocked this skill.")
    url = f"{API_ROOT}/skills/{urllib.parse.quote(slug, safe='')}/versions/{urllib.parse.quote(version, safe='')}"
    if owner:
        url += "?" + urllib.parse.urlencode({"owner": owner})
    data = fetch_json(url)
    current = data.get("version") if isinstance(data, dict) else None
    if (not isinstance(current, dict) or current.get("version") != version
            or current.get("deletedAt") or current.get("status") in {"deleted", "removed", "revoked"}):
        raise ValueError("ClawHub reviewed version is unavailable")


def _owner(raw: dict[str, Any]) -> str:
    publisher = raw.get("owner") or raw.get("publisher") or {}
    value = raw.get("ownerHandle") or (publisher.get("handle", "") if isinstance(publisher, dict) else "")
    if not isinstance(value, str) or value and not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", value):
        raise ValueError("Invalid ClawHub publisher")
    return value.lower()


def _detail(slug: str, owner: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", slug) or owner and not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", owner):
        raise ValueError("Invalid ClawHub reference")
    url = f"{API_ROOT}/skills/{urllib.parse.quote(slug, safe='')}"
    if owner:
        url += "?" + urllib.parse.urlencode({"owner": owner})
    try:
        value = fetch_json(url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise ValueError("This ClawHub slug has multiple publishers. Use its publisher-qualified ClawHub URL.") from None
        raise
    if not isinstance(value, dict) or not isinstance(value.get("skill", value), dict):
        raise ValueError("ClawHub detail response is not a mapping")
    if owner and _owner(value) != owner.lower():
        raise ValueError("ClawHub returned a different publisher")
    return value


def _github_handoff(value: Any, *, install_ref: str) -> SkillBundle:
    """Accept only the documented public, immutable GitHub source descriptor."""
    from row_bot.package_files import relative_package_path

    if not isinstance(value, dict) or value.get("sourceRef") != "public-github":
        raise ValueError("Invalid ClawHub GitHub handoff")
    repo, commit, path = (value.get(key, "") for key in ("repo", "commit", "path"))
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid ClawHub GitHub repository")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", commit):
        raise ValueError("ClawHub GitHub handoff is not pinned")
    if path:
        relative_package_path(path)
    canonical = f"https://codeload.github.com/{repo}/zip/{commit}"
    if value.get("archiveUrl") not in {canonical, f"https://github.com/{repo}/archive/{commit}.zip"}:
        raise ValueError("ClawHub GitHub archive does not match its pin")
    bundle = bundle_from_clawhub_zip(fetch_bytes(canonical), install_ref=install_ref, subdirectory=path)
    bundle.metadata.update({"repository": repo, "ref": commit, "path": path,
        "upstream_content_hash": str(value.get("contentHash", ""))[:256]})
    return bundle


def _public_entries(data: Any) -> list[SkillHubEntry]:
    if not isinstance(data, dict) or not any(isinstance(data.get(key), list) for key in ("results", "skills", "items")):
        raise ValueError("Invalid ClawHub catalog response")
    return parse_clawhub_payload(data)


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


def bundle_from_clawhub_zip(data: bytes, *, install_ref: str, subdirectory: str = "") -> SkillBundle:
    from row_bot.package_files import relative_package_path

    files: list[SkillFile] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        all_members = archive.infolist()
        if len(all_members) > 8192 or sum(m.file_size for m in all_members) > 64 * 1024 * 1024:
            raise ValueError("ClawHub archive exceeds the extraction limit")
        seen: set[str] = set()
        for member in all_members:
            # zipfile normalizes backslashes on Windows; inspect the original
            # central-directory spelling before platform normalization.
            relative_package_path(member.orig_filename.rstrip("/"))
            name = relative_package_path(member.filename.rstrip("/"))
            if name.casefold() in seen or _zip_member_is_symlink(member):
                raise ValueError("ClawHub archive contains a link or duplicate path")
            seen.add(name.casefold())
        members = [member for member in all_members if not member.is_dir()]
        root_prefix = _common_root_prefix([member.filename for member in members])
        if subdirectory:
            root_prefix += relative_package_path(subdirectory) + "/"
            members = [member for member in members if member.filename.startswith(root_prefix)]
        if len(members) > MAX_ZIP_FILES:
            raise ValueError(f"ClawHub ZIP has too many files ({len(members)} > {MAX_ZIP_FILES})")
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
    name = str(raw.get("displayName") or raw.get("name") or raw.get("title") or raw.get("slug") or raw.get("id") or "").strip()
    if not name:
        return None
    slug = str(raw.get("slug") or raw.get("id") or slugify(name)).strip()
    author = _owner(raw)
    reference = author + "/" + slug if author else slug
    canonical = raw.get("canonicalUrl") or raw.get("url") or raw.get("detailUrl")
    detail_url = urllib.parse.urljoin("https://clawhub.ai/", str(canonical or (f"/{author}/skills/{slug}" if author else f"/skills/{slug}")))
    skill_url = str(raw.get("skillMdUrl") or raw.get("skill_md_url") or raw.get("rawUrl") or "").strip()
    zip_url = str(raw.get("zipUrl") or raw.get("downloadUrl") or raw.get("archiveUrl") or "").strip()
    install_ref = f"clawhub:{reference}"
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    native = raw.get("native") if isinstance(raw.get("native"), dict) else {}
    skill = native.get("skill") if isinstance(native.get("skill"), dict) else {}
    stats = skill.get("stats") if isinstance(skill.get("stats"), dict) else {}
    publisher = raw.get("publisher") if isinstance(raw.get("publisher"), dict) else {}
    return SkillHubEntry(
        id=install_ref,
        name=name,
        description=str(raw.get("description") or raw.get("summary") or ""),
        source="clawhub",
        source_id="clawhub.ai",
        install_ref=install_ref,
        url=detail_url,
        author=author or str(raw.get("author") or ""),
        tags=[str(tag) for tag in tags],
        trust_level="high-risk community",
        metadata={
            "slug": slug,
            "owner": author,
            "detail_url": detail_url,
            "skill_url": skill_url,
            "zip_url": zip_url,
            "source_warning": _warning(),
            "risk": "high",
            "trust_level": "high-risk community",
            # Publisher signals ClawHub provides. (Its upstream repository names no folder, so it is
            # not an identity: one repository holds many skills.)
            "downloads": _count(raw.get("downloads") if raw.get("downloads") is not None else stats.get("downloads")),
            "stars": _count(stats.get("stars")),
            "official": raw.get("official") is True or publisher.get("official") is True,
        },
    )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**12 else None


def _safe_zip_member_path(filename: str, *, root_prefix: str) -> str:
    from row_bot.package_files import relative_package_path

    clean = relative_package_path(filename)
    if root_prefix and clean.startswith(root_prefix):
        clean = clean[len(root_prefix):]
    return relative_package_path(clean)


def _zip_member_is_symlink(member: zipfile.ZipInfo) -> bool:
    mode = (member.external_attr >> 16) & 0o777777
    return stat.S_ISLNK(mode)


def _common_root_prefix(paths: list[str]) -> str:
    first_parts = [path.replace("\\", "/").split("/", 1)[0] for path in paths if "/" in path.replace("\\", "/")]
    if not first_parts:
        return ""
    first = first_parts[0]
    if first and len(first_parts) == len(paths) and all(part == first for part in first_parts):
        return first + "/"
    return ""


def _warning() -> str:
    return "Community high-risk source. Review scan findings carefully before making available."
