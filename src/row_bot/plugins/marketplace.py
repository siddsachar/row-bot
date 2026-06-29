"""Plugin marketplace index parsing, cache, and query helpers."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
_CACHE_PATH = DATA_DIR / "marketplace_cache.json"

CACHE_TTL_SECONDS = 3600
MARKETPLACE_SCHEMA_VERSION = 2

DEFAULT_INDEX_URL = os.environ.get(
    "ROW_BOT_PLUGIN_INDEX_URL",
    "https://raw.githubusercontent.com/siddsachar/row-bot-plugins/main/index.json",
)


@dataclass
class MarketplaceEntry:
    """One plugin in the marketplace index."""

    id: str
    name: str
    version: str
    description: str
    icon: str = "extension"
    author_name: str = ""
    author_github: str = ""
    tags: list[str] = field(default_factory=list)
    min_row_bot_version: str = ""
    path: str = ""
    archive_url: str = ""
    checksum: str = ""
    changelog_url: str = ""
    provides: dict[str, int] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    verified: bool = False
    released: str = ""
    updated: str = ""
    index_source: str = ""

    @property
    def native_tool_count(self) -> int:
        return int(self.provides.get("native_tools", 0))

    @property
    def mcp_server_count(self) -> int:
        return int(self.provides.get("mcp_servers", 0))

    @property
    def tool_count(self) -> int:
        return self.native_tool_count

    @property
    def channel_count(self) -> int:
        return int(self.provides.get("channels", 0))

    @property
    def skill_count(self) -> int:
        return int(self.provides.get("skills", 0))


@dataclass
class MarketplaceIndex:
    """Parsed marketplace index."""

    schema_version: int = MARKETPLACE_SCHEMA_VERSION
    generated: str = ""
    source: str = ""
    plugins: list[MarketplaceEntry] = field(default_factory=list)


_cached_index: MarketplaceIndex | None = None
_cache_timestamp: float = 0.0


def fetch_index(force_refresh: bool = False) -> MarketplaceIndex:
    """Fetch the plugin index. Uses cache if within TTL.

    Network and file errors return an empty index or stale cache instead of
    raising, so deterministic tests and offline users do not surprise-call live
    services repeatedly.
    """

    global _cached_index, _cache_timestamp

    now = time.time()
    if not force_refresh and _cached_index is not None:
        if (now - _cache_timestamp) < CACHE_TTL_SECONDS:
            return _cached_index

    if not force_refresh and _CACHE_PATH.exists():
        try:
            disk_data = _read_disk_cache()
            if disk_data and (now - disk_data.get("_fetched_at", 0)) < CACHE_TTL_SECONDS:
                idx = _parse_index(disk_data)
                _cached_index = idx
                _cache_timestamp = disk_data.get("_fetched_at", now)
                return idx
        except Exception:
            logger.debug("Disk cache unusable, will fetch fresh", exc_info=True)

    try:
        raw = _fetch_from_url(DEFAULT_INDEX_URL)
        raw["_fetched_at"] = now
        _write_disk_cache(raw)
        idx = _parse_index(raw)
        _cached_index = idx
        _cache_timestamp = now
        logger.info("Marketplace index fetched: %d plugins", len(idx.plugins))
        return idx
    except Exception as exc:
        logger.warning("Failed to fetch marketplace index: %s", exc)
        if _cached_index is not None:
            return _cached_index
        stale = _read_disk_cache()
        if stale:
            try:
                idx = _parse_index(stale)
                _cached_index = idx
                _cache_timestamp = stale.get("_fetched_at", 0)
                logger.info(
                    "Using stale marketplace cache with %d plugins",
                    len(idx.plugins),
                )
                return idx
            except Exception:
                logger.debug("Stale marketplace cache unusable", exc_info=True)
        return MarketplaceIndex()


def search_plugins(
    query: str = "",
    tag: str = "",
    index: MarketplaceIndex | None = None,
) -> list[MarketplaceEntry]:
    """Search/filter the marketplace."""

    if index is None:
        index = fetch_index()

    results = index.plugins

    if tag:
        tag_lower = tag.lower()
        results = [p for p in results if tag_lower in [t.lower() for t in p.tags]]

    if query:
        q = query.lower()
        results = [
            p for p in results
            if q in p.name.lower()
            or q in p.description.lower()
            or q in p.id.lower()
            or any(q in t.lower() for t in p.tags)
            or any(q in permission.lower() for permission in p.permissions)
        ]

    return results


def get_all_tags(index: MarketplaceIndex | None = None) -> list[str]:
    """Return sorted unique tags from all plugins."""

    if index is None:
        index = fetch_index()
    tags = set()
    for plugin in index.plugins:
        tags.update(plugin.tags)
    return sorted(tags)


def get_entry(plugin_id: str, index: MarketplaceIndex | None = None) -> MarketplaceEntry | None:
    """Look up a single plugin by ID."""

    if index is None:
        index = fetch_index()
    for plugin in index.plugins:
        if plugin.id == plugin_id:
            return plugin
    return None


def check_updates(installed_manifests: list) -> list[dict[str, str]]:
    """Compare installed plugins against the marketplace index."""

    index = fetch_index()
    updates: list[dict[str, str]] = []
    for manifest in installed_manifests:
        entry = get_update_entry(manifest, index=index)
        if entry:
            updates.append({
                "plugin_id": manifest.id,
                "name": manifest.name,
                "installed_version": manifest.version,
                "latest_version": entry.version,
            })
    return updates


def get_cached_index(*, allow_stale: bool = True) -> MarketplaceIndex | None:
    """Return a cached marketplace index without making network calls."""

    global _cached_index, _cache_timestamp

    now = time.time()
    if _cached_index is not None:
        if allow_stale or (now - _cache_timestamp) < CACHE_TTL_SECONDS:
            return _cached_index

    disk_data = _read_disk_cache()
    if not disk_data:
        return None
    fetched_at = disk_data.get("_fetched_at", 0)
    if not allow_stale and (now - fetched_at) >= CACHE_TTL_SECONDS:
        return None
    idx = _parse_index(disk_data)
    _cached_index = idx
    _cache_timestamp = fetched_at or now
    return idx


def get_update_entry(
    manifest: Any,
    *,
    index: MarketplaceIndex | None = None,
) -> MarketplaceEntry | None:
    """Return the marketplace entry when it is newer than an installed manifest."""

    index = index or get_cached_index()
    if index is None:
        return None
    entry = get_entry(manifest.id, index)
    if entry and _is_newer_version(entry.version, manifest.version):
        return entry
    return None


def _fetch_from_url(url: str) -> dict[str, Any]:
    """Fetch JSON from a URL. Raises on error."""

    import urllib.request

    local_path = _local_path_from_ref(url)
    if local_path is not None:
        data = json.loads(local_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        return data

    req = urllib.request.Request(url, headers={"User-Agent": "Row-Bot-Plugin-Client"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


def _local_path_from_ref(ref: str) -> pathlib.Path | None:
    if not ref:
        return None
    candidate = pathlib.Path(ref).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    parsed = urlparse(ref)
    if parsed.scheme != "file":
        return None
    raw_path = url2pathname(parsed.path)
    if parsed.netloc:
        raw_path = f"//{parsed.netloc}{raw_path}"
    path = pathlib.Path(raw_path).expanduser()
    return path.resolve() if path.is_file() else None


def _read_disk_cache() -> dict[str, Any] | None:
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_disk_cache(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        logger.warning("Failed to write marketplace cache", exc_info=True)


def _parse_index(raw: dict[str, Any]) -> MarketplaceIndex:
    """Parse raw marketplace JSON into a MarketplaceIndex."""

    plugins: list[MarketplaceEntry] = []
    for entry in raw.get("plugins", []):
        if not isinstance(entry, dict):
            continue
        author = entry.get("author", {})
        provides = _parse_provides_summary(entry.get("provides", {}))
        plugins.append(MarketplaceEntry(
            id=str(entry.get("id", "")),
            name=str(entry.get("name", "")),
            version=str(entry.get("version", "")),
            description=str(entry.get("description", "")),
            icon=str(entry.get("icon", "extension")),
            author_name=str(author.get("name", "")) if isinstance(author, dict) else "",
            author_github=str(author.get("github", "")) if isinstance(author, dict) else "",
            tags=[str(tag) for tag in entry.get("tags", []) if isinstance(tag, str)],
            min_row_bot_version=str(entry.get("min_row_bot_version", "")),
            path=str(entry.get("path", "")),
            archive_url=str(entry.get("archive_url", "")),
            checksum=str(entry.get("checksum", "")),
            changelog_url=str(entry.get("changelog_url", "")),
            provides=provides,
            permissions=[
                str(permission)
                for permission in entry.get("permissions", [])
                if isinstance(permission, str)
            ],
            verified=bool(entry.get("verified", False)),
            released=str(entry.get("released", "")),
            updated=str(entry.get("updated", "")),
            index_source=str(raw.get("source", "")),
        ))

    return MarketplaceIndex(
        schema_version=int(raw.get("schema_version", MARKETPLACE_SCHEMA_VERSION) or 1),
        generated=str(raw.get("generated", "")),
        source=str(raw.get("source", "")),
        plugins=plugins,
    )


def _parse_provides_summary(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {"native_tools": 0, "mcp_servers": 0, "channels": 0, "skills": 0}
    return {
        "native_tools": _surface_count(raw.get("native_tools", raw.get("tools", 0))),
        "mcp_servers": _surface_count(raw.get("mcp_servers", 0)),
        "channels": _surface_count(raw.get("channels", 0)),
        "skills": _surface_count(raw.get("skills", 0)),
    }


def _surface_count(value: Any) -> int:
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, list):
        return len(value)
    return 0


def _is_newer_version(candidate: str, installed: str) -> bool:
    try:
        return _version_tuple(candidate) > _version_tuple(installed)
    except Exception:
        return False


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(value).replace("-", ".").split("."):
        if part.isdigit():
            parts.append(int(part))
        else:
            break
    return tuple(parts)


def _reset() -> None:
    global _cached_index, _cache_timestamp
    _cached_index = None
    _cache_timestamp = 0.0
