"""Seed deterministic, safe demo data for docs-mode screenshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from row_bot.docs_mode import write_docs_demo_state


SCENARIOS = {
    "first-run",
    "configured",
    "workflows",
    "designer",
    "developer",
    "settings",
    "knowledge",
    "full",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed docs-mode demo data")
    parser.add_argument("--data-dir", required=True, help="Temp Row-Bot data directory")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="full")
    args = parser.parse_args()

    path = write_docs_demo_state(Path(args.data_dir).resolve(), scenario=args.scenario)
    print(f"Wrote docs demo state to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
