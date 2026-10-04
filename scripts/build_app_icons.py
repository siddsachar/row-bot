"""Build ``integrations/icons.json``: bundled marks for the featured apps (a developer step).

Reads an extracted ``simple-icons`` npm package (CC0-1.0; some marks carry their
own licence, which is recorded) and keeps only the marks ``apps.json`` names.
Apps without a mark use a letter avatar.

  uv run python scripts/build_app_icons.py path/to/simple-icons/package
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "row_bot" / "integrations"


def _slug(title: str) -> str:
    text = title.lower().replace("+", "plus").replace(".", "dot").replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package", type=Path, help="the extracted simple-icons package folder")
    args = parser.parse_args()
    meta = json.loads((args.package / "package.json").read_text(encoding="utf-8"))
    data = json.loads((args.package / "data" / "simple-icons.json").read_text(encoding="utf-8"))
    by_slug = {item.get("slug") or _slug(item["title"]): item for item in data}
    apps = json.loads((PACKAGE / "apps.json").read_text(encoding="utf-8"))["apps"]
    wanted = sorted({app["icon"][3:] for app in apps if app.get("icon", "").startswith("si:")})
    icons = {}
    for slug in wanted:
        item = by_slug[slug]
        svg = (args.package / "icons" / f"{slug}.svg").read_text(encoding="utf-8")
        path = re.search(r'<path d="([^"]+)"', svg).group(1)
        licence = (item.get("license") or {}).get("type") or meta["license"]
        icons[slug] = {"title": item["title"], "hex": item["hex"].upper(), "path": path, "license": licence,
                       "source": f"https://github.com/simple-icons/simple-icons/blob/{meta['version']}/icons/{slug}.svg",
                       **({"guidelines": item["guidelines"]} if str(item.get("guidelines", "")).startswith("https://") else {})}
    document = {"schema_version": 1, "package": f"simple-icons@{meta['version']}",
                "note": "Marks are trademarks of their owners, shown only to identify each service.", "icons": icons}
    (PACKAGE / "icons.json").write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(icons)} marks from simple-icons@{meta['version']}")


if __name__ == "__main__":
    main()
