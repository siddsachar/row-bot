from __future__ import annotations

import argparse
import json
import sys

from row_bot.plugins.devtools import build_index, write_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Row-Bot plugin marketplace index.")
    parser.add_argument("root", help="Repository root containing plugins/")
    parser.add_argument("--source", default="")
    parser.add_argument("--check", action="store_true", help="Print the index instead of writing index.json")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(build_index(args.root, source=args.source), indent=2))
    else:
        path = write_index(args.root, source=args.source)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
