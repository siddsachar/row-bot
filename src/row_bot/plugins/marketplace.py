"""Plugin marketplace — fetch and cache the plugin index from GitHub.

The index is a JSON file (``index.json``) stored in the
``row-bot-plugins`` monorepo on GitHub.  This module fetches it via the
raw GitHub URL, caches it locally with a TTL, and provides query helpers.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
_CACHE_PATH = DATA_DIR / "marketplace_cache.json"

# Default TTL: 1 hour
CACHE_TTL_SECONDS = 3600

# The raw GitHub URL — set via ROW_BOT_PLUGIN_INDEX_URL env var or default.
# Users/CI can override this for testing.
DEFAULT_INDEX_URL = os.environ.get(
    "ROW_BOT_PLUGIN_INDEX_URL",
) or os.environ.get(
    "ROW_BOT_PLUGIN_INDEX_URL",
) or (
    "https://raw.githubusercontent.com/siddsachar/row-bot-plugins/main/index.json"
)


# ── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class MarketplaceEntry:
    """One plugin in the marketplace index."""
    id: str
    name: str
    version: str
    description: str
    icon: str = "🔌"
    author_name: str = ""
    author_github: str = ""
    tags: list[str] = field(default_factory=list)
    min_row_bot_version: str = ""
    tool_count: int = 0
    skill_count: int = 0
    verified: bool = False
    released: str = ""
    updated: str = ""


@dataclass
class MarketplaceIndex:
    """Parsed marketplace index."""
    schema_version: int = 1
    generated: str = ""
    source: str = ""
    plugins: list[MarketplaceEntry] = field(default_factory=list)


# ── In-memory cache ─────────────────────────────────────────────────────────
_cached_index: MarketplaceIndex | None = None
_cache_timestamp: float = 0.0


# ── Public API ───────────────────────────────────────────────────────────────
def fetch_index(force_refresh: bool = False) -> MarketplaceIndex:
    """Fetch the plugin index. Uses cache if within TTL.

    *force_refresh* bypasses the cache and re-fetches from GitHub.
    Returns an empty index on network errors (never raises).
    """
    global _cached_index, _cache_timestamp

    now = time.time()

    # Try in-memory cache first
    if not force_refresh and _cached_index is not None:
        if (now - _cache_timestamp) < CACHE_TTL_SECONDS:
            return _cached_index

    # Try disk cache
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

    # Fetch from network
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
        # Fall back to stale cache
        if _cached_index is not None:
            return _cached_index
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
        ]

    return results


def get_all_tags(index: MarketplaceIndex | None = None) -> list[str]:
    """Return sorted unique tags from all plugins."""
    if index is None:
        index = fetch_index()
    tags = set()
    for p in index.plugins:
        tags.update(p.tags)
    return sorted(tags)


def get_entry(plugin_id: str, index: MarketplaceIndex | None = None) -> MarketplaceEntry | None:
    """Look up a single plugin by ID."""
    if index is None:
        index = fetch_index()
    for p in index.plugins:
        if p.id == plugin_id:
            return p
    return None


def check_updates(installed_manifests: list) -> list[dict]:
    """Compare installed plugins against the marketplace index.

    Returns a list of dicts: {plugin_id, installed_version, latest_version}
    for plugins with available updates.
    """
    index = fetch_index()
    updates = []
    for manifest in installed_manifests:
        entry = get_entry(manifest.id, index)
        if entry and entry.version != manifest.version:
            from row_bot.plugins.loader import _version_tuple
            if _version_tuple(entry.version) > _version_tuple(manifest.version):
                updates.append({
                    "plugin_id": manifest.id,
                    "name": manifest.name,
                    "installed_version": manifest.version,
                    "latest_version": entry.version,
                })
    return updates


# ── Network ──────────────────────────────────────────────────────────────────
def _fetch_from_url(url: str) -> dict:
    """Fetch JSON from a URL. Raises on error."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, headers={"User-Agent": "Row-Bot-Plugin-Client"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


# ── Disk cache ───────────────────────────────────────────────────────────────
def _read_disk_cache() -> dict | None:
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_disk_cache(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        logger.warning("Failed to write marketplace cache", exc_info=True)


# ── Parsing ──────────────────────────────────────────────────────────────────
def _parse_index(raw: dict) -> MarketplaceIndex:
    """Parse raw JSON into a MarketplaceIndex."""
    plugins = []
    for entry in raw.get("plugins", []):
        if not isinstance(entry, dict):
            continue
        provides = entry.get("provides", {})
        author = entry.get("author", {})
        plugins.append(MarketplaceEntry(
            id=entry.get("id", ""),
            name=entry.get("name", ""),
            version=entry.get("version", ""),
            description=entry.get("description", ""),
            icon=entry.get("icon", "🔌"),
            author_name=author.get("name", "") if isinstance(author, dict) else "",
            author_github=author.get("github", "") if isinstance(author, dict) else "",
            tags=entry.get("tags", []),
            min_row_bot_version=entry.get("min_row_bot_version", ""),
            tool_count=provides.get("tools", 0) if isinstance(provides, int) is False else 0,
            skill_count=provides.get("skills", 0) if isinstance(provides, int) is False else 0,
            verified=entry.get("verified", False),
            released=entry.get("released", ""),
            updated=entry.get("updated", ""),
        ))

    return MarketplaceIndex(
        schema_version=raw.get("schema_version", 1),
        generated=raw.get("generated", ""),
        source=raw.get("source", ""),
        plugins=plugins,
    )


# ── Reset (for testing) ─────────────────────────────────────────────────────
def _reset():
    global _cached_index, _cache_timestamp
    _cached_index = None
    _cache_timestamp = 0.0
