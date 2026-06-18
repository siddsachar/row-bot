"""Collect public-docs inventory from Row-Bot source files.

The inventory intentionally avoids importing the full app. It scans stable
source locations and emits deterministic JSON that generated docs can consume.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _first_docstring_summary(path: Path) -> str:
    text = _read_text(path)
    match = re.match(r'\s*"""(.*?)"""', text, flags=re.DOTALL)
    if not match:
        return ""
    summary = " ".join(match.group(1).strip().split())
    return summary.split(". ")[0].strip()


def _frontmatter(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _skill_frontmatter(path: Path) -> dict[str, Any]:
    data = _frontmatter(path)
    text = _read_text(path)
    if "description" not in data:
        for line in text.splitlines():
            if line.lower().startswith("description:"):
                data["description"] = line.split(":", 1)[1].strip()
                break
    return data


def collect_tools() -> list[dict[str, Any]]:
    tools_dir = ROOT / "src" / "row_bot" / "tools"
    tools: list[dict[str, Any]] = []
    for path in sorted(tools_dir.glob("*_tool.py")):
        tool_id = path.stem.removesuffix("_tool")
        tools.append(
            {
                "id": tool_id,
                "source": str(path.relative_to(ROOT)).replace("\\", "/"),
                "summary": _first_docstring_summary(path),
            }
        )
    return tools


def collect_providers() -> list[dict[str, Any]]:
    providers_dir = ROOT / "src" / "row_bot" / "providers"
    providers: list[dict[str, Any]] = []
    skip = {"__init__", "models", "runtime", "routing", "selection", "status", "errors"}
    for path in sorted(providers_dir.glob("*.py")):
        if path.stem in skip or path.stem.startswith("_"):
            continue
        providers.append(
            {
                "id": path.stem,
                "source": str(path.relative_to(ROOT)).replace("\\", "/"),
                "summary": _first_docstring_summary(path),
            }
        )
    return providers


def collect_skills(root_name: str) -> list[dict[str, Any]]:
    skills_root = ROOT / root_name
    skills: list[dict[str, Any]] = []
    if not skills_root.exists():
        return skills
    for path in sorted(skills_root.glob("*/SKILL.md")):
        meta = _skill_frontmatter(path)
        skills.append(
            {
                "id": path.parent.name,
                "kind": root_name,
                "source": str(path.relative_to(ROOT)).replace("\\", "/"),
                "display_name": meta.get("display_name") or meta.get("name") or path.parent.name,
                "description": meta.get("description", ""),
            }
        )
    return skills


def collect_docs_pages() -> list[dict[str, Any]]:
    docs_root = ROOT / "docs-site" / "docs"
    pages: list[dict[str, Any]] = []
    if not docs_root.exists():
        return pages
    for path in sorted(docs_root.rglob("*.mdx")) + sorted(docs_root.rglob("*.md")):
        rel = path.relative_to(docs_root)
        meta = _frontmatter(path)
        pages.append(
            {
                "source": str(path.relative_to(ROOT)).replace("\\", "/"),
                "path": str(rel).replace("\\", "/"),
                "title": meta.get("title") or path.stem.replace("-", " ").title(),
                "description": meta.get("description", ""),
            }
        )
    return pages


def collect_version() -> dict[str, str]:
    version_file = ROOT / "src" / "row_bot" / "version.py"
    text = _read_text(version_file) if version_file.exists() else ""
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return {"version": match.group(1) if match else "unknown"}


def build_inventory() -> dict[str, Any]:
    bundled_skills = collect_skills("bundled_skills")
    tool_guides = collect_skills("tool_guides")
    return {
        "version": collect_version(),
        "tools": collect_tools(),
        "providers": collect_providers(),
        "skills": bundled_skills + tool_guides,
        "docs_pages": collect_docs_pages(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Row-Bot public docs inventory")
    parser.add_argument("--out", default="docs-build/inventory", help="Output directory")
    args = parser.parse_args()

    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    (out_dir / "inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for key, value in inventory.items():
        (out_dir / f"{key}.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"Wrote public docs inventory to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
