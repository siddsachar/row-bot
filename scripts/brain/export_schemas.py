"""Write the brain records' JSON Schemas to contracts/brain/v1/, generated from row_bot.brain's types.

--check writes nothing and fails when a schema file is missing, differs or is no longer generated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from row_bot.brain import SCHEMA_TYPES, json_schema  # noqa: E402 -- load checkout source after path bootstrap

DESTINATION = ROOT / "contracts" / "brain" / "v1"


def outputs() -> dict[Path, str]:
    return {DESTINATION / f"{cls.__name__}.schema.json": json.dumps(json_schema(cls), indent=2, sort_keys=True) + "\n"
            for cls in SCHEMA_TYPES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = outputs()
    stale = sorted(path for path in DESTINATION.glob("*.schema.json") if path not in expected)
    if args.check:
        changed = [path for path, content in expected.items()
                   if not path.exists() or path.read_text(encoding="utf-8") != content]
        if changed or stale:
            names = ", ".join(path.relative_to(ROOT).as_posix() for path in [*changed, *stale])
            print(f"Brain schemas out of date ({names}); run python scripts/brain/export_schemas.py")
            return 1
        print("Brain schemas verified")
        return 0
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for path, content in expected.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
    for path in stale:
        path.unlink()
    print("Brain schemas generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
