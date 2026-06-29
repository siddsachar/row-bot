from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from row_bot.plugins.devtools import validate_plugin_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Row-Bot plugin directory.")
    parser.add_argument("path")
    args = parser.parse_args(argv)
    result = validate_plugin_path(args.path)
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
