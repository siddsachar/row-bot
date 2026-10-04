"""Normalize an already acquired Registry v0.1 sample; never fetch the network."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from row_bot.mcp_client.marketplace import registry_entries
from row_bot.mcp_client.registry_snapshot import MAX_ENTRIES, build_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--captured-at", required=True, help="UTC ISO timestamp of the metadata acquisition")
    args = parser.parse_args()
    if args.input.stat().st_size > 32 * 1024 * 1024:
        parser.error("input exceeds 32 MiB")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    records = data.get("servers", data.get("entries"))
    if not isinstance(records, list):
        parser.error("expected a Registry servers/entries list")
    captured = datetime.fromisoformat(args.captured_at.replace("Z", "+00:00"))
    if captured.tzinfo is None:
        parser.error("captured-at must include timezone")
    doc = build_snapshot(registry_entries({"servers": records[:MAX_ENTRIES]}), captured_at=captured.timestamp(),
        complete=data.get("complete") is True and len(records) <= MAX_ENTRIES)
    args.output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
