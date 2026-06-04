"""LobeHub agent profile conversion source."""

from __future__ import annotations

import urllib.parse
from typing import Any

import yaml

from .models import SourceResult, SkillBundle, SkillHubEntry
from .search_index import search_entries
from .sources import SkillSource, bundle_from_single_file, fetch_json, normalize_skill_name, slugify

LOBEHUB_INDEX_URL = "https://chat-agents.lobehub.com/index.json"
LOBEHUB_AGENT_DETAIL_URL = "https://chat-agents.lobehub.com/{identifier}.json"
LOBEHUB_AGENT_BLOB_URL = "https://github.com/lobehub/lobe-chat-agents/blob/main/src/{identifier}.json"


class LobeHubSource(SkillSource):
    id = "lobehub"
    display_name = "LobeHub"
    trust_default = "community"
    supports_browse = True
    supports_search = True
    supports_import = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        entries = self._index()
        return SourceResult(entries[:limit], self.id, "live" if entries else "empty")

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        return search_entries(self._index(), query, limit=limit)

    def _index(self) -> list[SkillHubEntry]:
        return parse_lobehub_index(fetch_json(LOBEHUB_INDEX_URL))

    def can_resolve(self, value: str) -> bool:
        host = urllib.parse.urlparse(value or "").netloc.lower()
        return host in {"chat-agents.lobehub.com", "lobehub.com", "www.lobehub.com"}

    def resolve(self, value: str) -> SourceResult:
        parsed = urllib.parse.urlparse(value)
        slug = parsed.path.strip("/").split("/")[-1]
        entries = self.search(slug, limit=10) if slug else []
        return SourceResult(entries[:1], self.id, "live" if entries else "empty")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        agent_id = install_ref.removeprefix("lobehub:").strip()
        entries = parse_lobehub_index(fetch_json(LOBEHUB_INDEX_URL))
        for entry in entries:
            if entry.install_ref == install_ref or str(entry.metadata.get("agent_id") or "") == agent_id:
                return _bundle_from_lobehub_entry(load_lobehub_agent_detail(entry))
        raise ValueError(f"LobeHub agent not found: {agent_id}")


def parse_lobehub_index(data: Any) -> list[SkillHubEntry]:
    if isinstance(data, dict):
        raw_items = data.get("agents") or data.get("items") or data.get("data") or data.get("results") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []
    entries: list[SkillHubEntry] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entry = _entry_from_agent(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_from_agent(raw: dict[str, Any]) -> SkillHubEntry | None:
    name = str(raw.get("title") or raw.get("name") or raw.get("id") or raw.get("identifier") or "").strip()
    if not name:
        return None
    agent_id = str(raw.get("id") or raw.get("identifier") or raw.get("slug") or slugify(name)).strip()
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else raw.get("category") if isinstance(raw.get("category"), list) else []
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    description = str(raw.get("description") or raw.get("summary") or meta.get("description") or "")
    prompt = extract_lobehub_prompt(raw)
    detail_url = LOBEHUB_AGENT_DETAIL_URL.format(identifier=urllib.parse.quote(agent_id, safe=""))
    url = str(raw.get("url") or detail_url)
    return SkillHubEntry(
        id=f"lobehub:{agent_id}",
        name=name,
        description=description or "Converted from a LobeHub agent profile",
        source="lobehub",
        source_id="chat-agents.lobehub.com",
        install_ref=f"lobehub:{agent_id}",
        url=url,
        author=str(raw.get("author") or raw.get("creator") or ""),
        tags=[str(tag) for tag in tags],
        trust_level="community",
        metadata={
            "agent_id": agent_id,
            "converted_from": "lobehub_agent",
            "agent": raw,
            "prompt": prompt,
            "author_url": raw.get("homepage") or "",
            "raw_url": detail_url,
            "source_url": LOBEHUB_AGENT_BLOB_URL.format(identifier=urllib.parse.quote(agent_id, safe="")),
            "preview_status": "unknown",
            "trust_level": "community",
        },
    )


def load_lobehub_agent_detail(entry: SkillHubEntry) -> SkillHubEntry:
    prompt = str(entry.metadata.get("prompt") or "")
    if prompt.strip():
        return entry
    agent_id = str(entry.metadata.get("agent_id") or entry.install_ref.removeprefix("lobehub:")).strip()
    raw_url = LOBEHUB_AGENT_DETAIL_URL.format(identifier=urllib.parse.quote(agent_id, safe=""))
    data = fetch_json(raw_url)
    if not isinstance(data, dict):
        raise ValueError(f"LobeHub agent detail is not JSON: {agent_id}")
    merged = {**data}
    meta = merged.get("meta") if isinstance(merged.get("meta"), dict) else {}
    name = str(meta.get("title") or merged.get("title") or entry.name)
    description = str(meta.get("description") or merged.get("description") or entry.description)
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else entry.tags
    prompt = extract_lobehub_prompt(merged)
    return SkillHubEntry(
        id=entry.id,
        name=name,
        description=description,
        source=entry.source,
        source_id=entry.source_id,
        install_ref=entry.install_ref,
        url=raw_url,
        author=str(merged.get("author") or entry.author),
        tags=[str(tag) for tag in tags],
        trust_level=entry.trust_level,
        metadata={
            **dict(entry.metadata),
            "agent": merged,
            "prompt": prompt,
            "raw_url": raw_url,
            "source_url": LOBEHUB_AGENT_BLOB_URL.format(identifier=urllib.parse.quote(agent_id, safe="")),
            "author_url": merged.get("homepage") or entry.metadata.get("author_url") or "",
        },
    )


def _bundle_from_lobehub_entry(entry: SkillHubEntry) -> SkillBundle:
    raw = entry.metadata.get("agent") if isinstance(entry.metadata.get("agent"), dict) else {}
    prompt = str(entry.metadata.get("prompt") or extract_lobehub_prompt(raw))
    if len(prompt.strip()) < 40:
        raise ValueError("LobeHub entry does not expose substantial agent instructions.")
    name = normalize_skill_name(entry.name)
    meta = {
        "name": name,
        "display_name": entry.name,
        "description": entry.description,
        "version": "1.0",
        "author": entry.author or "LobeHub",
        "tags": [*entry.tags, "converted", "lobehub"],
    }
    body = [
        f"# {entry.name}",
        "",
        "Converted from a public LobeHub agent profile. Review before making it available.",
        "",
        "## Instructions",
        "",
        prompt or entry.description or "Follow the converted agent profile guidance.",
    ]
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + "\n".join(body).strip() + "\n"
    return bundle_from_single_file(
        source="lobehub",
        install_ref=entry.install_ref,
        root_name=name,
        text=text,
        metadata={
            "url": entry.url,
            "trust_level": "community",
            "converted_from": "lobehub_agent",
            "agent_id": entry.metadata.get("agent_id"),
            "raw_url": entry.metadata.get("raw_url"),
            "source_url": entry.metadata.get("source_url"),
            "author_url": entry.metadata.get("author_url"),
        },
    )


def extract_lobehub_prompt(raw: dict[str, Any]) -> str:
    candidates = [
        raw.get("prompt"),
        raw.get("systemRole"),
        raw.get("system_role"),
        raw.get("instructions"),
    ]
    config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
    candidates.extend([
        config.get("systemRole"),
        config.get("system_role"),
        config.get("prompt"),
        config.get("instructions"),
    ])
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
