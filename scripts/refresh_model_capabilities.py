"""Refresh the model capability catalogue bundled with Row-Bot (a release-prep step).

Downloads models.dev's api.json (or reads a saved copy with --input), keeps the
fields and providers Row-Bot reads (row_bot.providers.capability_catalog.trim)
and writes src/row_bot/providers/model_capabilities.json. scripts/cut_release.py
runs it with the docs publish.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from row_bot.providers import capability_catalog  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, help="a saved models.dev api.json instead of downloading it")
    parser.add_argument("--output", type=Path, default=capability_catalog.BUNDLED_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    raw = args.input.read_bytes() if args.input else capability_catalog._download()
    providers = capability_catalog.trim(json.loads(raw))
    if not providers:
        raise SystemExit("models.dev's catalogue lists none of Row-Bot's providers")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.output.write_text(capability_catalog.document(providers, fetched_at) + "\n", encoding="utf-8")
    count = sum(len(models) for models in providers.values())
    print(f"Wrote {count} models from {len(providers)} providers to {args.output}")


if __name__ == "__main__":
    main()
