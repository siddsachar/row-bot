"""Single source of truth for files copied into packaged Row-Bot apps."""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
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
    # Row-Bot's own guides, read offline to answer "how do I…" (row_bot.guides).
    "docs-site/docs",
)

ROOT_FILES = (
    "pyproject.toml",
    "uv.lock",
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

# Left out of the payload folders, as the macOS and Linux builds' rsync filters do.
# The React client (src/row_bot/static/client-v2) is staged from its own build.
PAYLOAD_EXCLUDED_NAMES = (
    "__pycache__",
    "*.pyc",
    "node_modules",
    ".pytest_cache",
    "tests",
    "test",
    "test-results",
    "*.test.js",
    "*.spec.js",
    "*.bak",
    "*.bak[0-9]*",
)
PAYLOAD_EXCLUDED_PATHS = ("static/client-v2",)
ASSET_EXCLUDED_NAMES = ("*.pyc",)


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


def _ignore(source_root: Path, names: tuple[str, ...], paths: tuple[str, ...] = ()):
    def ignore(directory: str, entries: list[str]) -> set[str]:
        relative = Path(directory).relative_to(source_root).as_posix()
        ignored = set()
        for entry in entries:
            path = entry if relative == "." else f"{relative}/{entry}"
            if path in paths or any(fnmatch.fnmatchcase(entry, pattern) for pattern in names):
                ignored.add(entry)
        return ignored

    return ignore


def stage_app_payload(project_root: Path | str, destination: Path | str) -> Path:
    """Copy the app payload into a new folder laid out like an installed ``app/``.

    The Windows build stages it this way so the ``.pyc`` it ships can be
    compiled before Inno Setup packs the folder.
    """
    root = Path(project_root)
    target = Path(destination)
    if target.exists():
        raise FileExistsError(f"stage folder already exists: {target}")
    manifest = build_manifest(root)
    target.mkdir(parents=True)
    for key in ("root_python_files", "root_files", "runtime_script_files"):
        for relative in manifest[key]:
            source = root / relative
            if not source.is_file():
                raise FileNotFoundError(f"manifest file is missing: {relative}")
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / relative)
    for key, names, paths in (
        ("payload_dirs", PAYLOAD_EXCLUDED_NAMES, PAYLOAD_EXCLUDED_PATHS),
        ("asset_dirs", ASSET_EXCLUDED_NAMES, ()),
    ):
        for relative in manifest[key]:
            source = root / relative
            if not source.is_dir():
                raise FileNotFoundError(f"manifest directory is missing: {relative}")
            shutil.copytree(source, target / relative, ignore=_ignore(source, names, paths))
    return target


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
    parser.add_argument("--stage", type=Path, help="Copy the app payload into this new folder")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root)
    if args.stage:
        stage_app_payload(project_root, args.stage)
        return 0
    if args.json:
        print(json.dumps(build_manifest(project_root), indent=2, sort_keys=True))
        return 0
    if args.category:
        for value in _category_values(args.category, project_root):
            print(value)
        return 0

    parser.error("choose --json, --category or --stage")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
