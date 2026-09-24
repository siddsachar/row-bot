"""Explicit, bounded MCP directory discovery for authenticated clients."""

from __future__ import annotations

from collections.abc import Callable
import json
import re
from typing import Any

from row_bot.mcp_client import config, marketplace
from row_bot.mcp_client.conflicts import unique_server_name


def search_directory(query: str, *, validate: Callable[[], None]) -> dict[str, Any]:
    validate()
    if not isinstance(query, str) or len(query) > 128 or "\0" in query:
        raise ValueError("invalid_query")
    result = marketplace.search_marketplace_with_status(query.strip(), limit=24)
    validate()
    names = set(config.read_saved_configuration().document.get("servers", {}))
    entries = []
    for entry in result.entries[:24]:
        base = (
            re.sub(r"[^a-zA-Z0-9_-]+", "-", entry.name.strip().lower()).strip("-")
            or entry.id
        )
        name = unique_server_name(base[:64], names)
        names.add(name)
        payload = json.dumps(
            {"mcpServers": {name: marketplace.entry_to_server_config(entry)}},
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(payload.encode()) > 8192:
            continue
        entries.append(
            {
                "id": str(entry.id)[:128],
                "name": str(entry.name)[:128],
                "description": str(entry.description)[:800],
                "source": str(entry.source)[:32],
                "publisher": str(entry.publisher)[:128],
                "transport": str(
                    entry.transport or (entry.install or {}).get("transport") or "stdio"
                )[:32],
                "risk_level": str(entry.risk_level)[:32],
                "requires_auth": bool(entry.requires_auth),
                "recommended": bool(entry.recommended),
                "import_json": payload,
            }
        )
    validate()
    return {
        "schema_version": 1,
        "mode": result.mode
        if result.mode in {"live", "cache", "curated"}
        else "curated",
        "items": entries,
    }
