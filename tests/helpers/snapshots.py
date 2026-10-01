from __future__ import annotations

import os
from pathlib import Path


def assert_matches_snapshot(path: Path, content: str) -> None:
    """Compare with a committed snapshot; ROW_BOT_UPDATE_SNAPSHOTS=1 records it instead."""
    normalized = content.replace("\r\n", "\n")
    if os.environ.get("ROW_BOT_UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8")
        return
    assert path.exists(), f"Snapshot {path} is missing; record it with ROW_BOT_UPDATE_SNAPSHOTS=1"
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == normalized
