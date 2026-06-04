"""Single source of truth for files copied into packaged Row-Bot apps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable


ROOT_PYTHON_EXCLUDES = frozenset(
    {
        "debug_tools.py",
        "seed_knowledge_graph.py",
        "workflows.py",
    }
)

PAYLOAD_DIRS = (
    "src/row_bot",
)

ASSET_DIRS = (
    "static",
    "sounds",
    "bundled_skills",
    "tool_guides",
)

ROOT_FILES = (
    "requirements.txt",
    "row-bot.ico",
)

RUNTIME_SCRIPT_FILES = (
    "scripts/verify_runtime_dependencies.py",
)

LINUX_ICON_CANDIDATES = (
    "docs/row_bot_glyph_256.png",
    "docs/row_bot_glyph.png",
)

MAC_ICON_SOURCE_CANDIDATES = (
    "docs/row_bot_glyph.png",
)


def _is_runtime_root_python_file(path: Path) -> bool:
    name = path.name
    if not path.is_file() or path.suffix != ".py":
        return False
    if name in ROOT_PYTHON_EXCLUDES:
        return False
    if name.startswith("_") or name.startswith("test_"):
        return False
    if name.endswith("_test.py") or name.endswith("_harness.py"):
        return False
    return True


def root_python_files(project_root: Path | str = ".") -> tuple[str, ...]:
    root = Path(project_root)
    return tuple(sorted(path.name for path in root.glob("*.py") if _is_runtime_root_python_file(path)))


def build_manifest(project_root: Path | str = ".") -> dict[str, list[str]]:
    root = Path(project_root)
    return {
        "root_python_files": list(root_python_files(root)),
        "root_files": list(ROOT_FILES),
        "runtime_script_files": list(RUNTIME_SCRIPT_FILES),
        "payload_dirs": list(PAYLOAD_DIRS),
        "asset_dirs": list(ASSET_DIRS),
        "linux_icon_candidates": list(LINUX_ICON_CANDIDATES),
        "mac_icon_source_candidates": list(MAC_ICON_SOURCE_CANDIDATES),
    }


def app_payload_paths(project_root: Path | str = ".") -> tuple[str, ...]:
    manifest = build_manifest(project_root)
    paths: list[str] = []
    for key in ("root_python_files", "root_files", "runtime_script_files", "payload_dirs", "asset_dirs"):
        paths.extend(manifest[key])
    return tuple(paths)


def _category_values(category: str, project_root: Path) -> Iterable[str]:
    manifest = build_manifest(project_root)
    if category == "app-payload-paths":
        return app_payload_paths(project_root)
    return manifest[category]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Row-Bot app payload manifest entries")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Repository root used for dynamic root Python file discovery",
    )
    parser.add_argument(
        "--category",
        choices=(
            "root_python_files",
            "root_files",
            "runtime_script_files",
            "payload_dirs",
            "asset_dirs",
            "linux_icon_candidates",
            "mac_icon_source_candidates",
            "app-payload-paths",
        ),
        help="Print one manifest category as newline-delimited paths",
    )
    parser.add_argument("--json", action="store_true", help="Print the whole manifest as JSON")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root)
    if args.json:
        print(json.dumps(build_manifest(project_root), indent=2, sort_keys=True))
        return 0
    if args.category:
        for value in _category_values(args.category, project_root):
            print(value)
        return 0

    parser.error("choose --json or --category")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
