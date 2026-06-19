"""Compatibility wrapper for the real docs demo-data seeder."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main() -> int:
    from scripts.docs.seed_real_app_demo_data import main as real_main

    return real_main()


if __name__ == "__main__":
    raise SystemExit(main())
