"""skills.sh public source adapter."""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from .models import SourceResult, SkillBundle, SkillHubEntry
from .search_index import search_entries
from .sources import SkillSource, bundle_from_marketplace_markdown, fetch_json, fetch_text, slugify, title_from_slug

API_SEARCH_URL = "https://skills.sh/api/search"
SITEMAP_URL = "https://www.skills.sh/sitemap.xml"
SITEMAP_SKILL_RE = re.compile(
    r"^https?://(?:www\.)?skills\.sh/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<skill>[^/?#]+)/?$",
    re.IGNORECASE,
)


class SkillsShSource(SkillSource):
    id = "skills_sh"
    display_name = "skills.sh"
    trust_default = "community"
    supports_browse = True
    supports_search = True
    supports_import = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        try:
            entries = fetch_skills_sh_sitemap_catalog(limit=limit)
            if entries:
                return SourceResult(entries, self.id, "live", f"Loaded {len(entries)} skills from skills.sh sitemap.")
        except Exception:
            pass
        try:
            entries = self.search("", limit=limit)
            return SourceResult(entries, self.id, "live" if entries else "empty")
        except Exception as exc:
            return SourceResult([], self.id, "error", str(exc))

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        params = urllib.parse.urlencode({"q": query or "", "query": query or "", "limit": str(limit)})
        data = fetch_json(f"{API_SEARCH_URL}?{params}")
        entries = parse_skills_sh_payload(data)
        return search_entries(entries, query, limit=limit) if query else entries[:limit]

    def can_resolve(self, value: str) -> bool:
        host = urllib.parse.urlparse(value or "").netloc.lower()
        return host in {"skills.sh", "www.skills.sh"}

    def resolve(self, value: str) -> SourceResult:
        parsed = urllib.parse.urlparse(value)
        slug = parsed.path.strip("/").split("/")[-1]
        if not slug:
            return SourceResult([], self.id, "empty", "skills.sh URL does not include a skill slug.")
        entries = self.search(slug, limit=10)
        direct = [entry for entry in entries if slug.lower() in {slugify(entry.name), str(entry.metadata.get("slug", "")).lower()}]
        return SourceResult(direct or entries[:1], self.id, "live" if entries else "empty")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        if install_ref.startswith("skills_sh:"):
            source, skill = _parse_skills_sh_ref(install_ref)
            if source and skill:
                return _bundle_from_skills_sh_source(source, skill)
        if install_ref.startswith("http"):
            if install_ref.lower().endswith((".md", "/skill.md", ".markdown")):
                return _bundle_from_skills_sh_markdown_url(install_ref)
            from .github_source import GitHubSource, parse_github_install_ref

            if parse_github_install_ref(install_ref) is not None:
                bundle = GitHubSource().fetch(install_ref)
                bundle.metadata.update({"source_name": "skills.sh", "trust_level": "community"})
                return bundle
            detail_html = fetch_text(install_ref)
            resolved = resolve_skills_sh_detail_page(detail_html)
            if resolved:
                if resolved.lower().endswith((".md", "/skill.md", ".markdown")):
                    bundle = _bundle_from_skills_sh_markdown_url(resolved)
                elif resolved.startswith("skills_sh:"):
                    bundle = self.fetch(resolved)
                else:
                    bundle = GitHubSource().fetch(resolved)
                bundle.metadata.update({
                    "source_name": "skills.sh",
                    "trust_level": "community",
                    "detail_url": install_ref,
                })
                return bundle
        raise ValueError("skills.sh entry does not expose a direct SKILL.md URL")


def parse_skills_sh_payload(data: Any) -> list[SkillHubEntry]:
    if isinstance(data, dict):
        raw_items = data.get("skills") or data.get("results") or data.get("items") or data.get("data") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []
    entries: list[SkillHubEntry] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entry = _entry_from_skills_sh_item(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def parse_skills_sh_sitemap(text: str, *, limit: int = 50) -> list[SkillHubEntry]:
    entries: list[SkillHubEntry] = []
    try:
        root = ET.fromstring(text)
        urls = [node.text or "" for node in root.iter() if node.tag.endswith("loc")]
    except Exception:
        urls = re.findall(r"https?://[^\s<]+", text or "")
    for url in urls:
        match = SITEMAP_SKILL_RE.match(url.strip())
        if not match:
            continue
        owner = match.group("owner")
        repo = match.group("repo")
        slug = match.group("skill")
        source_ref = f"{owner}/{repo}"
        canonical = f"{source_ref}/{slug}"
        entries.append(SkillHubEntry(
            id=f"skills_sh:{canonical}",
            name=title_from_slug(slug),
            description=f"Indexed by skills.sh from {source_ref}",
            source="skills_sh",
            source_id="skills.sh",
            install_ref=f"skills_sh:{canonical}",
            url=url,
            trust_level="community",
            metadata={
                "slug": slug,
                "detail_url": url,
                "source": source_ref,
                "skill_id": slug,
                "repository": source_ref,
                "preview_status": "unknown",
                "source_name": "skills.sh",
            },
        ))
        if len(entries) >= limit:
            break
    return entries


def parse_skills_sh_sitemap_index(text: str) -> list[str]:
    try:
        root = ET.fromstring(text)
        urls = [node.text or "" for node in root.iter() if node.tag.endswith("loc")]
    except Exception:
        urls = re.findall(r"https?://[^\s<]+", text or "")
    return [
        url.strip()
        for url in urls
        if "sitemap-skills" in url.lower() and url.strip().startswith("http")
    ]


def fetch_skills_sh_sitemap_catalog(*, limit: int = 50) -> list[SkillHubEntry]:
    index_text = fetch_text(SITEMAP_URL, headers={"Accept-Encoding": "identity"})
    child_urls = parse_skills_sh_sitemap_index(index_text)
    if not child_urls:
        return parse_skills_sh_sitemap(index_text, limit=limit)
    entries: list[SkillHubEntry] = []
    for url in child_urls:
        remaining = limit - len(entries)
        if remaining <= 0:
            break
        child_text = fetch_text(url, headers={"Accept-Encoding": "identity"})
        entries.extend(parse_skills_sh_sitemap(child_text, limit=remaining))
        if len(entries) >= limit:
            break
    return entries[:limit]


def _entry_from_skills_sh_item(raw: dict[str, Any]) -> SkillHubEntry | None:
    raw_id = str(raw.get("id") or "").strip()
    source_ref = str(raw.get("source") or "").strip()
    skill_id = str(raw.get("skillId") or raw.get("skill_id") or raw.get("slug") or "").strip()
    if raw_id.count("/") >= 2:
        parts = [part for part in raw_id.strip("/").split("/") if part]
        source_ref = source_ref or "/".join(parts[:2])
        skill_id = skill_id or "/".join(parts[2:])
    name = str(raw.get("name") or raw.get("title") or skill_id or raw.get("slug") or raw_id).strip()
    if not name:
        return None
    slug = str(raw.get("slug") or skill_id or slugify(name)).strip()
    detail_url = str(raw.get("url") or raw.get("detailUrl") or raw.get("homepage") or "").strip()
    if detail_url and detail_url.startswith("/"):
        detail_url = urllib.parse.urljoin("https://www.skills.sh", detail_url)
    if not detail_url and source_ref and skill_id:
        detail_url = f"https://www.skills.sh/{source_ref.strip('/')}/{skill_id}"
    skill_url = str(
        raw.get("skillMdUrl")
        or raw.get("skill_md_url")
        or raw.get("skillUrl")
        or raw.get("rawUrl")
        or raw.get("install_url")
        or raw.get("url")
        or ""
    ).strip()
    repo_url = str(raw.get("repoUrl") or raw.get("repositoryUrl") or raw.get("github") or "").strip()
    if source_ref and skill_id:
        install_ref = f"skills_sh:{source_ref.strip('/')}/{skill_id}"
    else:
        install_ref = skill_url if skill_url.lower().endswith((".md", "/skill.md")) else repo_url or skill_url or detail_url
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else raw.get("categories") if isinstance(raw.get("categories"), list) else []
    metadata = {
        "slug": slug,
        "detail_url": detail_url,
        "skill_url": skill_url,
        "repository": repo_url,
        "source": source_ref,
        "skill_id": skill_id,
        "preview_status": "unknown",
        "install_count": raw.get("installCount") or raw.get("installs") or raw.get("downloads") or 0,
        "source_name": "skills.sh",
    }
    return SkillHubEntry(
        id=f"skills_sh:{raw.get('id') or slug}",
        name=name,
        description=str(raw.get("description") or raw.get("summary") or ""),
        source="skills_sh",
        source_id="skills.sh",
        install_ref=install_ref,
        url=detail_url or skill_url or repo_url,
        author=str(raw.get("author") or raw.get("publisher") or raw.get("owner") or ""),
        tags=[str(tag) for tag in tags],
        trust_level="community",
        metadata=metadata,
    )


def resolve_skills_sh_detail_page(text: str) -> str:
    direct = _first_direct_skill_url(text)
    if direct:
        return direct
    command = parse_skills_sh_install_command(text)
    if command:
        source, skill = command
        return _github_ref_from_skills_command(source, skill)
    github_url = _first_github_url(text)
    return github_url or ""


def _bundle_from_skills_sh_markdown_url(url: str) -> SkillBundle:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    name = parts[-2] if len(parts) >= 2 and parts[-1].lower().endswith(".md") else pathlib_stem(parts[-1] if parts else "skills_sh_skill")
    return bundle_from_marketplace_markdown(
        source="skills_sh",
        install_ref=url,
        root_name=slugify(name, fallback="skills_sh_skill"),
        text=fetch_text(url),
        name=name,
        metadata={"url": url, "source_name": "skills.sh", "trust_level": "community"},
    )


def _parse_skills_sh_ref(install_ref: str) -> tuple[str, str]:
    body = install_ref.removeprefix("skills_sh:").strip("/")
    parts = [part for part in body.split("/") if part]
    if len(parts) < 3:
        return "", ""
    return "/".join(parts[:-1]), parts[-1]


def _bundle_from_skills_sh_source(source: str, skill: str) -> SkillBundle:
    from .github_source import GitHubSource, parse_github_install_ref, parsed_for_path

    parsed = parse_github_install_ref(source)
    if parsed is None:
        raise ValueError(f"skills.sh source is not a GitHub repository: {source}")
    github = GitHubSource()
    base_path = parsed.path.strip("/")
    candidates = [
        skill,
        f"skills/{skill}",
        f".agents/skills/{skill}",
        f".claude/skills/{skill}",
        f".github/skills/{skill}",
    ]
    for path in candidates:
        candidate_path = f"{base_path}/{path}".strip("/") if base_path else path
        try:
            bundle = github.fetch(parsed_for_path(parsed, candidate_path).format())
            bundle.metadata.update({
                "source_name": "skills.sh",
                "trust_level": "community",
                "skills_sh_source": source,
                "skills_sh_skill": skill,
            })
            return bundle
        except Exception:
            continue
    for path in candidates:
        candidate_path = f"{base_path}/{path}".strip("/") if base_path else path
        try:
            bundle = _bundle_from_skills_sh_raw_github_candidate(parsed, candidate_path, source=source, skill=skill)
            if bundle is not None:
                return bundle
        except Exception:
            continue
    entry = github.find_skill_by_name(parsed, skill)
    if entry is not None:
        bundle = github.inspect(entry)
        bundle.metadata.update({
            "source_name": "skills.sh",
            "trust_level": "community",
            "skills_sh_source": source,
            "skills_sh_skill": skill,
            "detail_url": f"https://www.skills.sh/{source.strip('/')}/{skill}",
        })
        return bundle
    detail_html = fetch_text(f"https://www.skills.sh/{source.strip('/')}/{skill}")
    resolved = resolve_skills_sh_detail_page(detail_html)
    current_ref = f"skills_sh:{source.strip('/')}/{skill}"
    if resolved and resolved not in {source, current_ref}:
        return SkillsShSource().fetch(resolved)
    markdown = extract_skills_sh_preview_markdown(detail_html, name=skill)
    if markdown:
        return bundle_from_marketplace_markdown(
            source="skills_sh",
            install_ref=current_ref,
            root_name=slugify(skill, fallback="skills_sh_skill"),
            text=markdown,
            name=skill,
            metadata={
                "source_name": "skills.sh",
                "trust_level": "community",
                "skills_sh_source": source,
                "skills_sh_skill": skill,
                "detail_url": f"https://www.skills.sh/{source.strip('/')}/{skill}",
            },
        )
    raise ValueError("skills.sh entry did not resolve to a SKILL.md file or GitHub skill folder")


def _bundle_from_skills_sh_raw_github_candidate(parsed: Any, candidate_path: str, *, source: str, skill: str) -> SkillBundle | None:
    refs = [parsed.ref] if parsed.ref else ["main", "master"]
    for ref in refs:
        if not ref:
            continue
        skill_path = candidate_path.strip("/")
        if not skill_path.lower().endswith("skill.md"):
            skill_path = f"{skill_path}/SKILL.md"
        url = (
            f"https://raw.githubusercontent.com/{parsed.owner}/{parsed.repo}/"
            f"{urllib.parse.quote(ref)}/"
            + "/".join(urllib.parse.quote(part) for part in skill_path.split("/") if part)
        )
        try:
            bundle = _bundle_from_skills_sh_markdown_url(url)
        except Exception:
            continue
        bundle.metadata.update({
            "source_name": "skills.sh",
            "trust_level": "community",
            "skills_sh_source": source,
            "skills_sh_skill": skill,
            "repository": f"{parsed.owner}/{parsed.repo}",
            "path": candidate_path.strip("/"),
            "ref": ref,
            "source_warning": "Fetched SKILL.md directly because GitHub folder listing was unavailable.",
        })
        return bundle
    return None


def pathlib_stem(value: str) -> str:
    return value.rsplit(".", 1)[0] if "." in value else value


def parse_skills_sh_install_command(text: str) -> tuple[str, str]:
    match = re.search(
        r"\b(?:npx\s+)?skills\s+add\s+(?P<source>[^\s<>\"'`]+)(?:\s+--skill\s+(?P<skill>[^\s<>\"'`]+))?",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return "", ""
    return match.group("source").strip(), (match.group("skill") or "").strip()


def extract_skills_sh_preview_markdown(text: str, *, name: str = "") -> str:
    source_text = text or ""
    raw_value = ""
    match = re.search(r'"previewHtml"\s*:\s*"((?:\\.|[^"\\])*)"', source_text, re.DOTALL)
    if match:
        raw_value = match.group(1)
    elif '\\"previewHtml\\":\\"' in source_text:
        marker = '\\"previewHtml\\":\\"'
        start = source_text.find(marker) + len(marker)
        end = source_text.find('\\",\\"restHtml\\"', start)
        if end == -1:
            end = source_text.find('\\",\\"proseClassName\\"', start)
        if end != -1:
            raw_value = source_text[start:end]
    if not raw_value:
        return ""
    try:
        import json

        html = json.loads('"' + raw_value + '"')
    except Exception:
        return ""
    plain = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1\n\n", html, flags=re.IGNORECASE | re.DOTALL)
    plain = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n\n## \1\n\n", plain, flags=re.IGNORECASE | re.DOTALL)
    plain = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n\n### \1\n\n", plain, flags=re.IGNORECASE | re.DOTALL)
    plain = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", plain, flags=re.IGNORECASE | re.DOTALL)
    plain = re.sub(r"<br\s*/?>", "\n", plain, flags=re.IGNORECASE)
    plain = re.sub(r"</p>", "\n\n", plain, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = plain.replace("\\u003c", "<").replace("\\u003e", ">")
    plain = plain.replace("\\n", "\n").replace('\\"', '"')
    import html as html_lib

    plain = html_lib.unescape(plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    if not plain:
        return ""
    title = name or _heading_from_plain(plain) or "skills_sh_skill"
    return f"# {title}\n\n{plain}" if not plain.startswith("#") else plain


def _heading_from_plain(text: str) -> str:
    for line in (text or "").splitlines():
        clean = line.strip("# ").strip()
        if clean:
            return clean
    return ""


def _github_ref_from_skills_command(source: str, skill: str) -> str:
    from .github_source import parse_github_install_ref

    parsed = parse_github_install_ref(source)
    if parsed is None:
        return source
    if skill:
        base_path = parsed.path.strip("/")
        source_ref = f"{parsed.owner}/{parsed.repo}"
        if base_path:
            source_ref = f"{source_ref}/{base_path}"
        return f"skills_sh:{source_ref}/{skill}"
    return parsed.format()


def _first_direct_skill_url(text: str) -> str:
    for url in re.findall(r"https?://[^\s<>\"'`]+", text or ""):
        clean = url.rstrip(").,;]")
        if clean.lower().endswith((".md", "/skill.md", ".markdown")):
            return clean
    return ""


def _first_github_url(text: str) -> str:
    from .github_source import parse_github_install_ref

    for url in re.findall(r"https?://github\.com/[^\s<>\"'`]+", text or ""):
        clean = url.rstrip(").,;]")
        if parse_github_install_ref(clean) is not None:
            return clean
    return ""
