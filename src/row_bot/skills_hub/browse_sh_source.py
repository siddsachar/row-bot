"""browse.sh public source adapter."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from .models import SourceResult, SkillBundle, SkillHubEntry
from .search_index import search_entries
from .sources import SkillSource, bundle_from_marketplace_markdown, fetch_json, fetch_text, slugify

API_SKILLS_URL = "https://browse.sh/api/skills"


class BrowseShSource(SkillSource):
    id = "browse_sh"
    display_name = "browse.sh"
    trust_default = "community"
    supports_browse = True
    supports_search = True
    supports_import = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        entries = self._catalog()
        return SourceResult(entries[:limit], self.id, "live" if entries else "empty")

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        entries = self._catalog()
        return search_entries(entries, query, limit=limit)

    def _catalog(self) -> list[SkillHubEntry]:
        return parse_browse_sh_catalog(fetch_json(API_SKILLS_URL))

    def can_resolve(self, value: str) -> bool:
        host = urllib.parse.urlparse(value or "").netloc.lower()
        return host in {"browse.sh", "www.browse.sh"}

    def resolve(self, value: str) -> SourceResult:
        parsed = urllib.parse.urlparse(value)
        route = _route_from_browse_sh_url(value)
        if not route:
            return SourceResult([], self.id, "empty", "browse.sh URL does not include a skill slug.")
        detail = self._detail(route)
        entry = _entry_from_browse_sh_item(detail, fallback_slug=route)
        if entry is None:
            return SourceResult([], self.id, "empty", "browse.sh skill was not found.")
        return SourceResult([entry], self.id, "live")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        if install_ref.startswith("http") and install_ref.lower().endswith((".md", "/skill.md", ".markdown")):
            markdown = fetch_text(install_ref)
            name = slugify(urllib.parse.urlparse(install_ref).path.split("/")[-2] if "/" in urllib.parse.urlparse(install_ref).path else "browse_skill")
            return bundle_from_marketplace_markdown(
                source=self.id,
                install_ref=install_ref,
                root_name=name,
                text=markdown,
                name=name,
                metadata={"trust_level": "community", "source_name": "browse.sh", "url": install_ref},
            )
        if install_ref.startswith("http"):
            from .github_source import GitHubSource, parse_github_install_ref

            if parse_github_install_ref(install_ref) is not None:
                bundle = GitHubSource().fetch(install_ref)
                bundle.metadata.update({"source_name": "browse.sh", "trust_level": "community"})
                return bundle
        route = install_ref.removeprefix("browse_sh:").strip("/")
        page_url = f"https://browse.sh/skills/{route}"
        detail = self._detail(route)
        skill_url = str(detail.get("skillMdUrl") or detail.get("skill_md_url") or detail.get("rawUrl") or "").strip()
        if skill_url:
            markdown = fetch_text(skill_url)
            return bundle_from_marketplace_markdown(
                source=self.id,
                install_ref=skill_url,
                root_name=slugify(str(detail.get("name") or detail.get("title") or route or "browse_skill")),
                text=markdown,
                name=str(detail.get("name") or detail.get("title") or route.split("/")[-1]),
                description=str(detail.get("description") or detail.get("summary") or ""),
                metadata={"trust_level": "community", "detail": detail, "source_name": "browse.sh"},
            )
        markdown = str(detail.get("skillMd") or detail.get("markdown") or detail.get("content") or "").strip()
        if markdown:
            name = str(detail.get("name") or detail.get("title") or "")
            return bundle_from_marketplace_markdown(
                source=self.id,
                install_ref=f"browse_sh:{route}",
                root_name=slugify(name or route.split("/")[-1] or "browse_skill"),
                text=markdown,
                name=name,
                description=str(detail.get("description") or detail.get("summary") or ""),
                metadata={"trust_level": "community", "detail": detail},
            )
        source_url = str(detail.get("sourceUrl") or detail.get("source_url") or "").strip()
        raw_url = github_blob_to_raw(source_url)
        if raw_url:
            try:
                markdown = fetch_text(raw_url)
                return bundle_from_marketplace_markdown(
                    source=self.id,
                    install_ref=raw_url,
                    root_name=slugify(str(detail.get("name") or detail.get("title") or route or "browse_skill")),
                    text=markdown,
                    name=str(detail.get("name") or detail.get("title") or route.split("/")[-1]),
                    description=str(detail.get("description") or detail.get("summary") or ""),
                    metadata={"trust_level": "community", "detail": detail, "source_name": "browse.sh", "source_url": source_url},
                )
            except Exception:
                pass
        try:
            html = fetch_text(page_url)
            markdown = extract_browse_sh_markdown_from_html(html)
            if markdown:
                return bundle_from_marketplace_markdown(
                    source=self.id,
                    install_ref=f"browse_sh:{route}",
                    root_name=slugify(route.split("/")[-1] or "browse_skill"),
                    text=markdown,
                    name="",
                    description="Public skill listed on browse.sh",
                    metadata={
                        "trust_level": "community",
                        "source_name": "browse.sh",
                        "detail_url": page_url,
                    },
                )
        except Exception:
            pass
        raise ValueError("browse.sh entry does not expose skill markdown")

    def _detail(self, route: str) -> dict[str, Any]:
        candidates = [route]
        final_slug = route.strip("/").split("/")[-1]
        if final_slug and final_slug not in candidates:
            candidates.append(final_slug)
        for candidate in candidates:
            try:
                data = fetch_json(f"{API_SKILLS_URL}/{urllib.parse.quote(candidate, safe='/')}")
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("skill"), dict):
                detail = data["skill"]
            elif isinstance(data, dict):
                detail = data
            else:
                detail = {}
            if isinstance(detail, dict) and detail:
                detail.setdefault("url", f"https://browse.sh/skills/{route}")
                detail.setdefault("slug", route)
                return detail
        try:
            html = fetch_text(f"https://browse.sh/skills/{route}")
        except Exception:
            return {}
        markdown = extract_browse_sh_markdown_from_html(html)
        if markdown:
            return {"slug": route, "url": f"https://browse.sh/skills/{route}", "skillMd": markdown}
        return {}


def parse_browse_sh_catalog(data: Any) -> list[SkillHubEntry]:
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
        entry = _entry_from_browse_sh_item(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_from_browse_sh_item(raw: dict[str, Any], *, fallback_slug: str = "") -> SkillHubEntry | None:
    name = str(raw.get("name") or raw.get("title") or raw.get("id") or fallback_slug).strip()
    if not name:
        return None
    detail_url = str(raw.get("url") or raw.get("detailUrl") or "").strip()
    if detail_url and detail_url.startswith("/"):
        detail_url = urllib.parse.urljoin("https://browse.sh", detail_url)
    route = _route_from_browse_sh_url(detail_url)
    slug = str(route or raw.get("slug") or raw.get("id") or fallback_slug or slugify(name)).strip().strip("/")
    skill_url = str(raw.get("skillMdUrl") or raw.get("skill_md_url") or raw.get("rawUrl") or "").strip()
    install_ref = skill_url or f"browse_sh:{slug}"
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return SkillHubEntry(
        id=f"browse_sh:{slug}",
        name=name,
        description=str(raw.get("description") or raw.get("summary") or ""),
        source="browse_sh",
        source_id="browse.sh",
        install_ref=install_ref,
        url=detail_url or f"https://browse.sh/skills/{slug}",
        author=str(raw.get("author") or raw.get("publisher") or ""),
        tags=[str(tag) for tag in tags],
        trust_level="community",
        metadata={
            "slug": slug,
            "detail_url": detail_url,
            "skill_url": skill_url,
            "source_url": raw.get("sourceUrl") or raw.get("source_url") or "",
            "source_name": "browse.sh",
            "install_count": raw.get("installCount") or raw.get("installs") or raw.get("downloads") or 0,
            "preview_status": "unknown",
        },
    )


def _route_from_browse_sh_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value or "")
    path = parsed.path.strip("/")
    if path.startswith("skills/"):
        return path.removeprefix("skills/").strip("/")
    return path


def extract_browse_sh_markdown_from_html(text: str) -> str:
    html = text or ""
    for key in ("skillMd", "skill_md", "markdown", "content"):
        match = re.search(rf'"{re.escape(key)}"\s*:\s*("(?:\\.|[^"\\])*")', html, re.DOTALL)
        if match:
            try:
                value = json.loads(match.group(1))
            except Exception:
                value = ""
            if isinstance(value, str) and value.strip():
                return value
    code_match = re.search(r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>\s*</pre>", html, re.DOTALL | re.IGNORECASE)
    if code_match:
        value = re.sub(r"<[^>]+>", "", code_match.group(1))
        value = value.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        if "SKILL.md" in html or "When to use" in value or "Instructions" in value:
            return value.strip()
    return ""


def github_blob_to_raw(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 5 or parts[2] != "blob":
        return ""
    owner, repo, ref = parts[0], parts[1], parts[3]
    path = "/".join(parts[4:])
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
