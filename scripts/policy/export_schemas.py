"""Write the company policy's JSON Schemas to contracts/policy/v1/, generated from row_bot.policy:
CompanyPolicy.schema.json (also the shape of the effective policy a Row-Bot fetches) and
GroupOverride.schema.json.

--check writes nothing and fails when a schema file is missing, differs or is no longer generated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from row_bot.policy import json_schema  # noqa: E402 -- load checkout source after path bootstrap

DESTINATION = ROOT / "contracts" / "policy" / "v1"


def outputs() -> dict[Path, str]:
    return {DESTINATION / f"{name}.schema.json": json.dumps(json_schema(override=override), indent=2) + "\n"
            for name, override in (("CompanyPolicy", False), ("GroupOverride", True))}


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
            print(f"Policy schemas out of date ({names}); run python scripts/policy/export_schemas.py")
            return 1
        print("Policy schemas verified")
        return 0
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for path, content in expected.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
    for path in stale:
        path.unlink()
    print("Policy schemas generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
