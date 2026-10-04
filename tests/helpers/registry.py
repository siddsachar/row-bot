"""Fixture Registry snapshots: a test's mirror holds exactly the records it lists."""
from __future__ import annotations

from pathlib import Path

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
