"""Fail-safe MCP marketplace/directory discovery adapters."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover - optional dependency fallback
    requests = None

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency fallback
    BeautifulSoup = None

from mcp_client.config import DATA_DIR
from mcp_client.conflicts import conflicts_for_entry
from mcp_client.logging import log_event

CACHE_PATH = DATA_DIR / "mcp_marketplace_cache.json"
CATALOG_PATH = Path(__file__).with_name("recommended_servers.json")
DEFAULT_TIMEOUT = 3
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Thoth-MCP-Client/1.0",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class MarketplaceEntry:
    id: str
    name: str
    description: str
    source: str
    url: str = ""
    publisher: str = ""
    classification: str = ""
    transport: str = ""
    requires_auth: bool = False
    install: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    category: str = ""
    trust_tier: str = ""
    risk_level: str = ""
    action_scope: str = ""
    capabilities: list[str] = field(default_factory=list)
    overlaps_native: list[str] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    recommended: bool = False
    last_reviewed: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class MarketplaceSearchResult:
    entries: list[MarketplaceEntry]
    mode: str
    query: str = ""
    source_counts: dict[str, int] = field(default_factory=dict)


def _count_sources(entries: list[MarketplaceEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
    return counts


def _dedupe_entries(entries: list[MarketplaceEntry]) -> list[MarketplaceEntry]:
    dedup: dict[str, MarketplaceEntry] = {}
    seen_names: set[str] = set()
    for entry in entries:
        if not _is_useful_entry(entry):
            continue
        name_key = re.sub(r"[^a-z0-9]+", " ", entry.name.lower()).strip()
        if name_key:
            name_key = f"{entry.source}:{name_key}"
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
        dedup.setdefault(f"{entry.source}:{entry.id}", entry)
    return list(dedup.values())


def _is_useful_entry(entry: MarketplaceEntry) -> bool:
    if not entry.name.strip():
        return False
    return not (entry.name == "MCP Server" and not entry.description.strip())


def _entry_search_text(entry: MarketplaceEntry) -> str:
    return " ".join([
        entry.id,
        entry.name,
        entry.description,
        entry.publisher,
        entry.classification,
        entry.transport,
    ]).lower()


def _filter_relevant(entries: list[MarketplaceEntry], query: str) -> list[MarketplaceEntry]:
    tokens = [token for token in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(token) > 1]
    if not tokens:
        return entries
    return [entry for entry in entries if all(token in _entry_search_text(entry) for token in tokens)]


def _entry_from_mapping(item: dict[str, Any]) -> MarketplaceEntry | None:
    try:
        allowed = set(MarketplaceEntry.__dataclass_fields__)
        values = {key: value for key, value in item.items() if key in allowed}
        return MarketplaceEntry(**values)
    except Exception as exc:
        log_event("mcp.catalog.entry_invalid", level=30, error=str(exc))
        return None


def _load_curated_catalog() -> list[MarketplaceEntry]:
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:
        log_event("mcp.catalog.load_failed", level=30, path=str(CATALOG_PATH), error=str(exc))
        return []
    if not isinstance(raw, list):
        log_event("mcp.catalog.invalid", level=30, path=str(CATALOG_PATH), error="catalog root must be a list")
        return []
    entries = [_entry_from_mapping(item) for item in raw if isinstance(item, dict)]
    return [entry for entry in entries if entry is not None]


CURATED_STARTER_CATALOG: list[MarketplaceEntry] = _load_curated_catalog()


def _fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "Thoth-MCP-Client/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-triggered directory fetch
        return json.loads(response.read().decode("utf-8"))


def _fetch_text(url: str, timeout: int = DEFAULT_TIMEOUT, *, prefer_urllib: bool = False) -> str:
    if requests is not None and not prefer_urllib:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
        if response.status_code < 200 or response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}")
        return response.text
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-triggered directory fetch
        return response.read().decode("utf-8", errors="replace")


def _clean_text(value: str, *, max_len: int = 800) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:max_len].rstrip()


def _title_from_slug(slug: str) -> str:
    tail = slug.strip("/").split("/")[-1]
    text = re.sub(r"[-_]+", " ", tail).strip()
    return text.title() if text else "MCP Server"


def _parse_directory_html(
    html: str,
    *,
    source: str,
    base_url: str,
    path_prefix: str,
    limit: int,
) -> list[MarketplaceEntry]:
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries: list[MarketplaceEntry] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        absolute = urllib.parse.urljoin(base_url, str(link.get("href") or ""))
        parsed = urllib.parse.urlparse(absolute)
        if not parsed.path.startswith(path_prefix):
            continue
        slug = parsed.path.removeprefix(path_prefix).strip("/")
        if not slug or slug in seen or slug.startswith("#"):
            continue
        seen.add(slug)
        link_text = _clean_text(link.get_text(" ", strip=True), max_len=160)
        container = link.find_parent(["article", "li"]) or link.find_parent("div") or link
        heading = container.find(["h1", "h2", "h3", "h4"]) if hasattr(container, "find") else None
        heading_text = _clean_text(heading.get_text(" ", strip=True), max_len=120) if heading else ""
        description = _clean_text(container.get_text(" ", strip=True), max_len=700)
        if not description:
            description = link_text
        name = heading_text or (link_text if 0 < len(link_text) <= 80 and "CLASSIFICATION" not in link_text else _title_from_slug(slug))
        if name.lower() in {"servers", "next", "previous", "go to next page", "go to previous page"}:
            continue
        entries.append(MarketplaceEntry(
            id=slug,
            name=name,
            description=description,
            source=source,
            url=absolute,
            classification="directory-page",
            metadata={"page_fallback": True, "source_url": base_url},
        ))
        if len(entries) >= limit:
            break
    return entries


def _load_cache() -> list[MarketplaceEntry]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return [MarketplaceEntry(**item) for item in raw.get("entries", [])]
    except Exception:
        return []


def _save_cache(entries: list[MarketplaceEntry]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump({"saved_at": time.time(), "entries": [asdict(e) for e in entries]}, handle, indent=2)
    except Exception as exc:
        log_event("mcp.marketplace.cache_failed", level=30, error=str(exc))


def _official_registry_search(query: str, limit: int) -> list[MarketplaceEntry]:
    # Generic Registry API endpoints can evolve; try conservative paths and
    # degrade silently to the next source/cache if unavailable.
    encoded = urllib.parse.urlencode({"search": query, "limit": str(limit)})
    candidates = [
        f"https://registry.modelcontextprotocol.io/v0/servers?{encoded}",
        f"https://registry.modelcontextprotocol.io/api/servers?{encoded}",
    ]
    for url in candidates:
        try:
            data = _fetch_json(url)
        except Exception as exc:
            log_event("mcp.marketplace.official_failed", level=10, url=url, error=str(exc))
            continue
        items = data.get("servers", data if isinstance(data, list) else data.get("items", [])) if isinstance(data, (dict, list)) else []
        entries: list[MarketplaceEntry] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            entries.append(MarketplaceEntry(
                id=str(item.get("id") or item.get("name") or name),
                name=name,
                description=str(item.get("description") or ""),
                source="official",
                url=str(item.get("homepage") or item.get("repository") or item.get("url") or "https://registry.modelcontextprotocol.io/"),
                publisher=str(item.get("publisher") or ""),
                classification="official-registry",
                metadata=item,
            ))
        if entries:
            return entries
    return []


def _pulsemcp_search(query: str, limit: int) -> list[MarketplaceEntry]:
    encoded = urllib.parse.urlencode({"q": query, "limit": str(limit)})
    candidates = [
        f"https://www.pulsemcp.com/api/v0.1/servers?{encoded}",
        f"https://www.pulsemcp.com/api/servers?{encoded}",
    ]
    for url in candidates:
        try:
            data = _fetch_json(url)
        except Exception as exc:
            log_event("mcp.marketplace.pulsemcp_failed", level=10, url=url, error=str(exc))
            continue
        items = data.get("servers", data.get("items", [])) if isinstance(data, dict) else data if isinstance(data, list) else []
        entries: list[MarketplaceEntry] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("displayName") or "").strip()
            if not name:
                continue
            entries.append(MarketplaceEntry(
                id=str(item.get("id") or item.get("name") or name),
                name=name,
                description=str(item.get("description") or ""),
                source="pulsemcp",
                url=str(item.get("url") or item.get("homepage") or "https://www.pulsemcp.com/servers"),
                publisher=str(item.get("publisher") or item.get("owner") or ""),
                classification=str(item.get("classification") or ""),
                metadata=item,
            ))
        if entries:
            return entries
    page_url = f"https://www.pulsemcp.com/servers?{urllib.parse.urlencode({'query': query})}"
    try:
        html = _fetch_text(page_url, prefer_urllib=True)
        entries = _parse_directory_html(
            html,
            source="pulsemcp",
            base_url=page_url,
            path_prefix="/servers/",
            limit=limit * 3,
        )
        if entries:
            return entries
    except Exception as exc:
        log_event("mcp.marketplace.pulsemcp_page_failed", level=10, url=page_url, error=str(exc))
    return []


def _smithery_search(query: str, limit: int) -> list[MarketplaceEntry]:
    encoded = urllib.parse.urlencode({"q": query, "query": query, "limit": str(limit)})
    candidates = [
        f"https://smithery.ai/api/servers?{encoded}",
        f"https://server.smithery.ai/api/servers?{encoded}",
    ]
    for url in candidates:
        try:
            data = _fetch_json(url)
        except Exception as exc:
            log_event("mcp.marketplace.smithery_failed", level=10, url=url, error=str(exc))
            continue
        items = data.get("servers", data.get("items", data.get("data", []))) if isinstance(data, dict) else data if isinstance(data, list) else []
        entries: list[MarketplaceEntry] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("displayName") or item.get("qualifiedName") or "").strip()
            if not name:
                continue
            entries.append(MarketplaceEntry(
                id=str(item.get("id") or item.get("qualifiedName") or item.get("name") or name),
                name=name,
                description=str(item.get("description") or item.get("summary") or ""),
                source="smithery",
                url=str(item.get("url") or item.get("homepage") or item.get("repository") or f"https://smithery.ai/server/{urllib.parse.quote(name)}"),
                publisher=str(item.get("publisher") or item.get("author") or ""),
                classification="hosted" if item.get("isHosted") else str(item.get("classification") or ""),
                transport="streamable_http" if item.get("isHosted") else "",
                requires_auth=bool(item.get("requiresAuth") or item.get("security")),
                metadata=item,
            ))
        if entries:
            return entries
    page_url = f"https://smithery.ai/servers?{urllib.parse.urlencode({'q': query})}"
    try:
        html = _fetch_text(page_url)
        entries = _parse_directory_html(
            html,
            source="smithery",
            base_url=page_url,
            path_prefix="/servers/",
            limit=limit * 3,
        )
        if entries:
            return entries
    except Exception as exc:
        log_event("mcp.marketplace.smithery_page_failed", level=10, url=page_url, error=str(exc))
    return []


def _glama_search(query: str, limit: int) -> list[MarketplaceEntry]:
    encoded = urllib.parse.urlencode({"q": query, "search": query, "limit": str(limit)})
    candidates = [
        f"https://glama.ai/api/mcp/servers?{encoded}",
        f"https://glama.ai/api/mcp/v1/servers?{encoded}",
    ]
    for url in candidates:
        try:
            data = _fetch_json(url)
        except Exception as exc:
            log_event("mcp.marketplace.glama_failed", level=10, url=url, error=str(exc))
            continue
        items = data.get("servers", data.get("items", data.get("data", []))) if isinstance(data, dict) else data if isinstance(data, list) else []
        entries: list[MarketplaceEntry] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("slug") or "").strip()
            if not name:
                continue
            quality = item.get("quality") or item.get("score") or item.get("grade") or ""
            official = item.get("official") or item.get("isOfficial")
            entries.append(MarketplaceEntry(
                id=str(item.get("id") or item.get("slug") or item.get("name") or name),
                name=name,
                description=str(item.get("description") or item.get("summary") or ""),
                source="glama",
                url=str(item.get("url") or item.get("homepage") or f"https://glama.ai/mcp/servers/{urllib.parse.quote(name)}"),
                publisher=str(item.get("publisher") or item.get("owner") or ""),
                classification="official" if official else str(quality or ""),
                transport=str(item.get("transport") or ""),
                metadata=item,
            ))
        if entries:
            return entries
    page_url = f"https://glama.ai/mcp/servers?{urllib.parse.urlencode({'query': query})}"
    try:
        html = _fetch_text(page_url)
        entries = _parse_directory_html(
            html,
            source="glama",
            base_url=page_url,
            path_prefix="/mcp/servers/",
            limit=limit * 3,
        )
        if entries:
            return entries
    except Exception as exc:
        log_event("mcp.marketplace.glama_page_failed", level=10, url=page_url, error=str(exc))
    return []


def search_marketplace_with_status(query: str = "", *, sources: list[str] | None = None, limit: int = 24) -> MarketplaceSearchResult:
    """Search MCP directories and report whether results are live or fallback."""
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        result = [entry for entry in CURATED_STARTER_CATALOG if entry.recommended][:limit]
        return MarketplaceSearchResult(
            entries=result,
            mode="curated",
            query=normalized_query,
            source_counts=_count_sources(result),
        )
    selected = sources or ["official", "pulsemcp", "smithery", "glama"]
    curated_matches = _filter_relevant(CURATED_STARTER_CATALOG, normalized_query)
    entries: list[MarketplaceEntry] = []
    for source in selected:
        try:
            if source == "official":
                entries.extend(_official_registry_search(normalized_query, limit))
            elif source == "pulsemcp":
                entries.extend(_pulsemcp_search(normalized_query, limit))
            elif source == "smithery":
                entries.extend(_smithery_search(normalized_query, limit))
            elif source == "glama":
                entries.extend(_glama_search(normalized_query, limit))
        except Exception as exc:
            log_event("mcp.marketplace.source_failed", level=30, source=source, error=str(exc))
    if entries:
        live_matches = _filter_relevant(_dedupe_entries(entries), normalized_query)
        if live_matches:
            result = _dedupe_entries(curated_matches + live_matches)[:limit]
            _save_cache(result)
            return MarketplaceSearchResult(
                entries=result,
                mode="live",
                query=normalized_query,
                source_counts=_count_sources(result),
            )
    cached = _load_cache()
    cached_matches = _filter_relevant(cached, normalized_query)
    if cached_matches:
        result = _dedupe_entries(curated_matches + cached_matches)[:limit]
        return MarketplaceSearchResult(
            entries=result,
            mode="cache",
            query=normalized_query,
            source_counts=_count_sources(result),
        )
    if curated_matches:
        result = _dedupe_entries(curated_matches)[:limit]
        return MarketplaceSearchResult(
            entries=result,
            mode="curated",
            query=normalized_query,
            source_counts=_count_sources(result),
        )
    mode = "cache" if cached_matches else "curated"
    fallback = _dedupe_entries(curated_matches + cached_matches) if cached_matches else curated_matches
    result = fallback[:limit]
    return MarketplaceSearchResult(
        entries=result,
        mode=mode,
        query=normalized_query,
        source_counts=_count_sources(result),
    )


def search_marketplace(query: str = "", *, sources: list[str] | None = None, limit: int = 24) -> list[MarketplaceEntry]:
    """Search MCP directories with cache/curated fallback."""
    return search_marketplace_with_status(query, sources=sources, limit=limit).entries


def entry_to_server_config(entry: MarketplaceEntry) -> dict[str, Any]:
    """Return a disabled, review-required server config template."""
    install = dict(entry.install or {})
    conflicts = [conflict.as_dict() for conflict in conflicts_for_entry(entry)]
    return {
        "enabled": False,
        "transport": install.get("transport") or entry.transport or "stdio",
        "command": install.get("command", ""),
        "args": install.get("args", []),
        "url": install.get("url", ""),
        "headers": install.get("headers", {}),
        "env": install.get("env", {}),
        "requirements": list(entry.requirements or []),
        "trust_level": entry.trust_tier or "standard",
        "source": {
            "marketplace": entry.source,
            "id": entry.id,
            "name": entry.name,
            "url": entry.url,
            "publisher": entry.publisher,
            "classification": entry.classification,
            "category": entry.category,
            "trust_tier": entry.trust_tier,
            "risk_level": entry.risk_level,
            "action_scope": entry.action_scope,
            "requires_auth": entry.requires_auth,
            "recommended": entry.recommended,
            "capabilities": list(entry.capabilities or []),
            "overlaps_native": list(entry.overlaps_native or []),
            "requirements": list(entry.requirements or []),
            "conflicts": conflicts,
            "not_verified_by_thoth": True,
        },
    }