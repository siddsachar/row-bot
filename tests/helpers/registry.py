"""Fixture Registry snapshots (a test's mirror holds exactly the records it lists) and typed catalog searches."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def use_registry(monkeypatch: pytest.MonkeyPatch, folder: Path, entries: list, *,
                 watermark: str = "2026-10-01T00:00:00Z", captured_at: float = 1790800000.0) -> Path:
    """Ship ``entries`` as the release snapshot and build the local index from it, as start-up does."""
    from row_bot.integrations import index
    from row_bot.mcp_client import registry_snapshot
    path = folder / "registry_snapshot.jsonl.xz"
    path.write_bytes(registry_snapshot.build_snapshot(entries, captured_at=captured_at, watermark=watermark))
    monkeypatch.setattr(registry_snapshot, "SHIPPED", path)
    index.ensure()
    return path


def search_catalog(owner_id: str = "owner", **fields: Any) -> dict:
    """An explicit typed catalog search, with the defaults ``POST /integrations/items/search`` fills in.

    ``cancelled`` and ``validate`` pass through to the search; every other field is a request field."""
    from row_bot.api.v1 import schemas as dto
    from row_bot.application import client_integrations
    request = dto.IntegrationSearchRequest().model_dump(mode="json")
    return client_integrations.search_items(owner_id=owner_id, **{**request, **fields})
