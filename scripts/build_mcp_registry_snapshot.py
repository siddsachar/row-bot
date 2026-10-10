"""Build the shipped MCP Registry snapshot (a developer step).

  --sync            read every latest record from the public Registry (read-only,
                    paginated, polite, with backoff); falls back to --input when
                    the network is unavailable and a saved capture exists
  --since SNAPSHOT  incremental: only records updated since that snapshot's watermark
  --input FILE      offline: a saved Registry envelope ({"servers": [...]})
  --save-capture FILE  with --sync: also save every record read, so --input FILE
                    --captured-at rebuilds the same bytes offline

The output is reproducible: the same records and capture time give the same bytes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from row_bot.mcp_client import registry_snapshot  # noqa: E402
from row_bot.mcp_client.marketplace import registry_entries  # noqa: E402

CAPTURE = ROOT / ".local" / "integration-reach-audit" / "registry.json"


def _from_capture(path: Path, captured_at: str) -> tuple[list, float, str, bool]:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise SystemExit("input exceeds 64 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("servers")
    if not isinstance(records, list):
        raise SystemExit("expected a Registry {\"servers\": [...]} envelope")
    stamp = captured_at or data.get("fetched_at") or ""
    captured = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
    if captured is None or captured.tzinfo is None:
        raise SystemExit("--captured-at must be a UTC ISO timestamp with a timezone")
    entries, watermark = {}, ""
    for envelope in records:
        official = ((envelope.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {})
        watermark = max(watermark, registry_snapshot.instant(official.get("updatedAt")))
        for entry in registry_entries({"servers": [envelope]}):
            entries[entry.metadata["canonical_name"]] = entry
    return list(entries.values()), captured.timestamp(), watermark, data.get("complete") is True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--input", type=Path)
    parser.add_argument("--since", type=Path, help="an existing snapshot to update incrementally")
    parser.add_argument("--captured-at", default="", help="UTC ISO time of an --input capture")
    parser.add_argument("--output", type=Path, default=registry_snapshot.SHIPPED)
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between Registry pages")
    parser.add_argument("--save-capture", type=Path, help="with --sync, save the records read to this file")
    args = parser.parse_args()
    if args.input:
        entries, captured_at, watermark, complete = _from_capture(args.input, args.captured_at)
    else:
        base = registry_snapshot.read_snapshot(args.since) if args.since else None
        started, read = time.time(), []

        def get(url: str, headers: dict, meta: dict) -> bytes:
            body = registry_snapshot._get(url, headers, meta)
            read.extend(json.loads(body).get("servers") or [])
            return body
        try:
            result = registry_snapshot.sync(since=base["watermark"] if base else "", pause=args.pause,
                                            deadline=3600, max_pages=2000, get=get)
        except Exception as exc:  # The network is unavailable: build from the saved capture instead.
            if not CAPTURE.exists():
                raise
            print(f"Registry sync failed ({type(exc).__name__}: {exc}); using {CAPTURE}", file=sys.stderr)
            entries, captured_at, watermark, complete = _from_capture(CAPTURE, args.captured_at)
        else:
            merged = {e.metadata["canonical_name"]: e for e in (base["entries"] if base else [])}
            merged.update({e.metadata["canonical_name"]: e for e in result["entries"]})
            for name in result["deleted"]:
                merged.pop(name, None)
            entries, captured_at, watermark, complete = list(merged.values()), started, result["watermark"], True
            if args.save_capture and not base:
                stamp = datetime.fromtimestamp(round(started, 3), timezone.utc).isoformat()
                args.save_capture.write_text(json.dumps({"fetched_at": stamp, "complete": True, "servers": read}),
                                             encoding="utf-8")
    data = registry_snapshot.build_snapshot(entries, captured_at=round(captured_at, 3), watermark=watermark, complete=complete)
    args.output.write_bytes(data)
    header = registry_snapshot.read_header(args.output)
    print(json.dumps({"output": str(args.output), "bytes": len(data), **header}, indent=2))


if __name__ == "__main__":
    main()
