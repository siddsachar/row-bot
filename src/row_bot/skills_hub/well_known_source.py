"""Well-known skills index source adapter."""

from __future__ import annotations

import urllib.parse
from typing import Any

from .models import SourceResult, SkillBundle, SkillHubEntry
from .sources import SkillSource, fetch_json, slugify
from .url_source import DirectURLSource


class WellKnownSource(SkillSource):
    id = "well_known"
    display_name = "Well-known"
    trust_default = "community"
    supports_browse = False
    supports_search = False
    supports_import = True

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        return []

    def can_resolve(self, value: str) -> bool:
        return bool(normalize_index_url(value))

    def resolve(self, value: str) -> SourceResult:
        index_url = normalize_index_url(value)
        if not index_url:
            return SourceResult([], self.id, "empty", "Input is not a well-known skills index or website URL.")
        data = fetch_json(index_url)
        entries = parse_well_known_index(data, index_url)
        return SourceResult(entries, self.id, "live" if entries else "empty")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        parsed = urllib.parse.urlparse(install_ref)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Well-known index entries must resolve to HTTP(S) SKILL.md URLs")
        return DirectURLSource().fetch(install_ref)


def normalize_index_url(value: str) -> str:
    parsed = urllib.parse.urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    suffix = parsed.path.rsplit("/", 1)[-1].lower()
    if suffix and "." in suffix and not parsed.path.endswith("/.well-known/skills/index.json"):
        return ""
    if parsed.path.endswith("/.well-known/skills/index.json"):
        return urllib.parse.urlunparse(parsed)
    root = parsed._replace(path="/.well-known/skills/index.json", params="", query="", fragment="")
    return urllib.parse.urlunparse(root)


def parse_well_known_index(data: Any, index_url: str) -> list[SkillHubEntry]:
    if isinstance(data, dict):
        raw_entries = data.get("skills") or data.get("entries") or []
        index_meta = data
    elif isinstance(data, list):
        raw_entries = data
        index_meta = {}
    else:
        raw_entries = []
        index_meta = {}
    entries: list[SkillHubEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("url") or raw.get("install_ref") or raw.get("skill_url") or "").strip()
        name = str(raw.get("name") or raw.get("id") or "Public Skill").strip()
        if not target or not name:
            continue
        entry_id = str(raw.get("id") or slugify(name))
        tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
        trust = "verified" if raw.get("verified") or index_meta.get("verified") else "community"
        entries.append(SkillHubEntry(
            id=f"well-known:{index_url}:{entry_id}",
            name=name,
            description=str(raw.get("description") or ""),
            source="well_known",
            source_id=index_url,
            install_ref=target,
            url=str(raw.get("homepage") or target),
            author=str(raw.get("author") or index_meta.get("author") or ""),
            tags=[str(tag) for tag in tags],
            trust_level=trust,
            metadata={"index_url": index_url, "entry": raw},
        ))
    return entries
