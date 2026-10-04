"""Fail-safe MCP marketplace/directory discovery adapters."""

from __future__ import annotations

import hashlib
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

from row_bot.mcp_client.config import DATA_DIR
from row_bot.mcp_client.conflicts import conflicts_for_entry
from row_bot.mcp_client.logging import log_event

CACHE_PATH = DATA_DIR / "mcp_marketplace_cache.json"
CATALOG_PATH = Path(__file__).with_name("recommended_servers.json")
DEFAULT_TIMEOUT = 3
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Row-Bot-MCP-Client/1.0",
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
    for entry in entries:
        if not _is_useful_entry(entry):
            continue
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
    import httpx
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "registry.modelcontextprotocol.io":
        raise ValueError("registry_source_not_supported")
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "Row-Bot-Catalog"}) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("registry_response_too_large")
            return json.loads(data)


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


def _registry_setup_digest(item: dict) -> str:
    # Bind all delivery declarations, including unknown extensions. Display-only
    # metadata does not identify a deployment. Never persist raw declaration values:
    # even a public catalog may accidentally contain a secret header/default.
    display = {"$schema", "name", "version", "title", "description", "repository", "websiteUrl", "icons"}
    setup = {key: value for key, value in item.items() if key not in display}
    try:
        encoded = json.dumps(setup, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid_registry_setup") from exc
    if len(encoded) > 65536:
        raise ValueError("registry_setup_too_large")
    return hashlib.sha256(encoded).hexdigest()


def registry_entries(data: dict) -> list[MarketplaceEntry]:
    """Parse bounded v0.1 metadata; unsupported declarations never become recipes."""
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("invalid_registry_response")
    entries = []
    known = {"$schema", "name", "version", "title", "description", "repository", "websiteUrl", "icons", "remotes", "packages"}
    for envelope in data["servers"][:1000]:
        if not isinstance(envelope, dict) or not isinstance(envelope.get("server"), dict):
            continue
        item = envelope["server"]
        official = envelope.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
        name, version = item.get("name"), item.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not name or len(name) > 200 or len(version) > 128:
            continue
        setup_digest = _registry_setup_digest(item)
        status = official.get("status", "unknown")
        install, notes, requires_auth = None, [], False
        remotes, packages = item.get("remotes", []), item.get("packages", [])
        if not isinstance(remotes, list) or not isinstance(packages, list) or len(remotes) > 16 or len(packages) > 16:
            raise ValueError("invalid_registry_declarations")
        if status != "active":
            notes.append("Registry status: " + str(status)[:80] + ". New installation is unavailable.")
        elif set(item) - known:
            notes.append("Additional server setup or authentication declarations are unsupported by catalog import.")
        else:
            for remote in remotes:
                if not isinstance(remote, dict):
                    continue
                if remote.get("headers") or set(remote) - {"type", "url", "headers"}:
                    notes.append("Remote header, authentication or variable declarations require setup that catalog import cannot safely express.")
                    requires_auth = requires_auth or bool(remote.get("headers"))
                    continue
                url = str(remote.get("url", ""))
                parsed = urllib.parse.urlsplit(url)
                if (remote.get("type") not in {"streamable-http", "sse"} or parsed.scheme != "https"
                        or not parsed.hostname or parsed.username or parsed.password or any(c in url for c in "{}")):
                    continue
                install = {"transport": remote["type"].replace("-", "_"), "url": url}
                requires_auth = False
                break
            if install is None:
                for package in packages:
                    if not isinstance(package, dict):
                        continue
                    allowed = {"registryType", "identifier", "version", "transport", "registryBaseUrl", "runtimeHint",
                               "runtimeArguments", "packageArguments", "environmentVariables"}
                    if (set(package) - allowed or package.get("environmentVariables") or package.get("runtimeArguments")
                            or package.get("packageArguments") or package.get("runtimeHint", "npx") != "npx"
                            or package.get("registryBaseUrl", "https://registry.npmjs.org").rstrip("/") != "https://registry.npmjs.org"
                            or package.get("transport") != {"type": "stdio"}):
                        notes.append("Package environment, runtime, argument, integrity or registry declarations are unsupported by catalog import.")
                        requires_auth = requires_auth or bool(package.get("environmentVariables"))
                        continue
                    identifier, package_version = package.get("identifier", ""), package.get("version", "")
                    if (package.get("registryType") != "npm" or not isinstance(identifier, str) or not isinstance(package_version, str)
                            or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", identifier)
                            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", package_version)):
                        continue
                    install = {"transport": "stdio", "command": "npx", "args": [identifier + "@" + package_version]}
                    requires_auth = False
                    notes.append("Prepare reviewed dependencies first. Only self-contained npm archives or complete npm shrinkwraps without install scripts are supported.")
                    break
        repository = item.get("repository", {})
        entries.append(MarketplaceEntry(id=name + "@" + version, name=str(item.get("title") or name)[:128],
            description=str(item.get("description", ""))[:800], source="official", publisher=name.split("/", 1)[0],
            url=str(repository.get("url", "")) if isinstance(repository, dict) else "", classification="official-registry",
            transport=install.get("transport", "") if install else "", requires_auth=requires_auth,
            install=install, notes=list(dict.fromkeys(notes)), metadata={"version": version, "status": status,
                "canonical_name": name, "setup_digest": setup_digest}))
    return entries


def _official_registry_search(query: str, limit: int) -> list[MarketplaceEntry]:
    """Normalize the v0.1 envelope, bounded pagination and explicit recipes."""
    entries, cursor, seen = [], "", set()
    for _ in range(4):
        params = {"search": query, "limit": str(min(50, limit)), "version": "latest"}
        if cursor:
            params["cursor"] = cursor
        data = _fetch_json("https://registry.modelcontextprotocol.io/v0.1/servers?" + urllib.parse.urlencode(params))
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            raise ValueError("invalid_registry_response")
        entries.extend(registry_entries(data))
        if len(entries) >= limit:
            return entries[:limit]
        cursor = data.get("metadata", {}).get("nextCursor", "")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
            break
        seen.add(cursor)
    return entries


def _pulsemcp_search(query: str, limit: int) -> list[MarketplaceEntry]:
    """B2B access requires a separately approved tenant/key integration."""
    return []


def _smithery_search(query: str, limit: int) -> list[MarketplaceEntry]:
    """Desktop catalog access is unresolved; never guess alternate endpoints."""
    return []


def _glama_search(query: str, limit: int) -> list[MarketplaceEntry]:
    """Key/licensing integration is not implemented; no anonymous scraping."""
    return []


def search_marketplace_with_status(query: str = "", *, sources: list[str] | None = None, limit: int = 24, cached_only: bool = False) -> MarketplaceSearchResult:
    """Search shipped/saved metadata locally. Refresh is a separate action."""
    from row_bot.mcp_client.registry_snapshot import read_snapshot
    normalized = (query or "").strip().lower()
    selected = sources or ["official"]
    saved = read_snapshot() if "official" in selected else {"entries": []}
    entries = _dedupe_entries(CURATED_STARTER_CATALOG + saved["entries"] + _load_cache())
    entries = [entry for entry in entries if entry.source == "curated" or entry.source in selected]
    entries = _filter_relevant(entries, normalized)
    entries.sort(key=lambda entry: (not entry.recommended, entry.name.casefold(), entry.source, entry.id))
    result = entries[:limit]
    mode = "cache" if any(entry.source != "curated" for entry in result) else "curated"
    return MarketplaceSearchResult(result, mode, normalized, _count_sources(result))


def search_marketplace(query: str = "", *, sources: list[str] | None = None, limit: int = 24) -> list[MarketplaceEntry]:
    """Search MCP directories with cache/curated fallback."""
    return search_marketplace_with_status(query, sources=sources, limit=limit).entries


def entry_to_server_config(entry: MarketplaceEntry) -> dict[str, Any]:
    """Return a disabled, review-required server config template."""
    if entry.source == "official" and (not entry.install or not (entry.metadata or {}).get("setup_digest")):
        raise ValueError("registry_recipe_unsupported")
    install = dict(entry.install or {})
    conflicts = [conflict.as_dict() for conflict in conflicts_for_entry(entry)]
    return {
        "enabled": False,
        "environment_mode": "minimal",
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
            **{key: (entry.metadata or {})[key] for key in ("auth_mode", "auth_bindings", "account_requirements", "cost", "evidence") if key in (entry.metadata or {})},
            "recommended": entry.recommended,
            "capabilities": list(entry.capabilities or []),
            "overlaps_native": list(entry.overlaps_native or []),
            "requirements": list(entry.requirements or []),
            "conflicts": conflicts,
            "not_verified_by_row_bot": True,
            **({"registry_name": (entry.metadata or {}).get("canonical_name", ""),
                "registry_version": (entry.metadata or {}).get("version", ""),
                "registry_setup_digest": (entry.metadata or {}).get("setup_digest", "")}
               if entry.source == "official" else {}),
        },
    }
