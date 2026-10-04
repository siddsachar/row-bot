"""Dated, bounded MCP Registry metadata; searches never contact the registry."""
from __future__ import annotations

from dataclasses import asdict
from collections.abc import Callable
from typing import TYPE_CHECKING
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import quote, urlencode

from row_bot.data_paths import get_row_bot_data_dir

if TYPE_CHECKING:
    from row_bot.mcp_client.marketplace import MarketplaceEntry

SOURCE = "https://registry.modelcontextprotocol.io/v0.1/servers"
SHIPPED = Path(__file__).with_name("registry_snapshot.json")
MAX_ENTRIES = 500
MAX_BYTES = 2 * 1024 * 1024
MAX_AGE = 7 * 24 * 3600


def build_snapshot(entries: list[MarketplaceEntry], *, captured_at: float, complete: bool = False) -> dict:
    """Reproducible normalized metadata, with no package contents or execution."""
    rows = [asdict(e) for e in entries[:MAX_ENTRIES]]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": 2, "source": SOURCE, "api_version": "v0.1", "captured_at": captured_at,
            "digest": digest, "complete": complete, "limit": MAX_ENTRIES, "entries": rows}


def _read(path: Path) -> dict:
    from row_bot.mcp_client.marketplace import MarketplaceEntry
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("invalid_registry_snapshot")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(doc, dict) or doc.get("schema_version") != 2 or doc.get("source") != SOURCE
            or not isinstance(doc.get("entries"), list) or len(doc["entries"]) > MAX_ENTRIES
            or not isinstance(doc.get("captured_at"), (float, int)) or not math.isfinite(doc["captured_at"])):
        raise ValueError("invalid_registry_snapshot")
    entries = [MarketplaceEntry(**row) for row in doc["entries"]]
    if build_snapshot(entries, captured_at=doc["captured_at"])["digest"] != doc.get("digest"):
        raise ValueError("registry_snapshot_digest_changed")
    return {**doc, "entries": entries}


def read_snapshot() -> dict:
    """Use only intact metadata; never create files during a passive read."""
    for path in (get_row_bot_data_dir(create=False) / "mcp_registry_snapshot.json", SHIPPED):
        try:
            doc = _read(path)
            return {**doc, "status": "stale" if time.time() - doc["captured_at"] > MAX_AGE else "cached"}
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return {"entries": [], "status": "error", "captured_at": None, "digest": "", "api_version": "v0.1", "source": SOURCE}


def refresh_snapshot(*, cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Explicit development refresh: at most five pages, no keyword traffic."""
    from row_bot.mcp_client.marketplace import _fetch_json, registry_entries
    rows, cursor, seen = [], "", set()
    for _ in range(5):
        if cancelled():
            raise ValueError("integration_search_cancelled")
        params = {"limit": "100", "version": "latest"}
        if cursor:
            params["cursor"] = cursor
        data = _fetch_json(SOURCE + "?" + urlencode(params))
        rows.extend(registry_entries(data))
        cursor = data.get("metadata", {}).get("nextCursor", "")
        if not cursor:
            break
        if not isinstance(cursor, str) or len(cursor) > 2048 or cursor in seen:
            raise ValueError("invalid_registry_cursor")
        seen.add(cursor)
    doc = build_snapshot(rows, captured_at=time.time(), complete=not cursor)
    if cancelled():
        raise ValueError("integration_search_cancelled")
    from row_bot.integrations.safe import write_atomic
    write_atomic(get_row_bot_data_dir(create=False) / "mcp_registry_snapshot.json", json.dumps(doc, indent=2), cancelled=cancelled)
    return read_snapshot()


def revalidate_entry(entry: MarketplaceEntry) -> MarketplaceEntry:
    """An old listing cannot authorize a removed or materially changed recipe."""
    from row_bot.mcp_client.marketplace import _fetch_json, registry_entries
    metadata = entry.metadata or {}
    name, version = metadata.get("canonical_name"), metadata.get("version")
    if not name or not version:
        raise ValueError("registry_identity_missing")
    data = _fetch_json(SOURCE + "/" + quote(name, safe="") + "/versions/" + quote(version, safe=""))
    current = registry_entries({"servers": [data]})
    if not current or current[0].id != entry.id or (current[0].metadata or {}).get("status") != "active":
        raise ValueError("registry_entry_removed")
    if (not metadata.get("setup_digest") or metadata["setup_digest"] != (current[0].metadata or {}).get("setup_digest")
            or current[0].install != entry.install or current[0].requires_auth != entry.requires_auth):
        raise ValueError("registry_recipe_changed")
    if not current[0].install:
        raise ValueError("registry_recipe_unsupported")
    return current[0]


def revalidate_configuration(cfg: dict) -> None:
    """Recheck a registry import at the existing configuration publication gate."""
    from row_bot.mcp_client.marketplace import MarketplaceEntry
    source = cfg.get("source", {})
    if not isinstance(source, dict) or source.get("marketplace") != "official":
        return
    install = {key: cfg[key] for key in ("transport", "url", "command", "args", "headers", "env") if cfg.get(key)}
    # Remote normalized configuration includes empty argv but the recipe does not.
    entry = MarketplaceEntry(id=source.get("id", ""), name=source.get("name", ""), description="", source="official",
        install=install, requires_auth=source.get("requires_auth", False),
        metadata={"canonical_name": source.get("registry_name"), "version": source.get("registry_version"),
                  "setup_digest": source.get("registry_setup_digest")})
    revalidate_entry(entry)
