"""Prepare Row-Bot release version bumps.

Usage:
    python scripts/cut_release.py 5.0.0

Rewrites every file that carries the release version. The Linux and macOS build
scripts, the generated docs reference pages and the release gate read the
version from src/row_bot/version.py, so they need no edit here.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?"

# Every file this script rewrites, relative to the repository root.
VERSION_FILES = (
    "src/row_bot/version.py",
    "installer/row_bot_setup.iss",
    "Start Row-Bot.command",
    ".github/workflows/release.yml",
    "installer/Row-Bot.app/Contents/Info.plist",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    "docs-site/src/components/Screenshot.tsx",
)


def _rewrite(path: Path, change: Callable[[str], str]) -> None:
    """Apply ``change`` to the file's text, keeping its line endings."""
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = change(raw.replace("\r\n", "\n"))
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def _sub(path: Path, pattern: str, replacement: str, *, exactly_one: bool) -> None:
    def change(text: str) -> str:
        new_text, count = re.subn(pattern, replacement, text, count=1 if exactly_one else 0, flags=re.MULTILINE)
        if count < 1:
            raise SystemExit(f"Expected {'exactly one' if exactly_one else 'at least one'} match in {path}: {pattern}")
        return new_text

    _rewrite(path, change)


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    _sub(path, pattern, replacement, exactly_one=True)


def replace_at_least_once(path: Path, pattern: str, replacement: str) -> None:
    _sub(path, pattern, replacement, exactly_one=False)


def validate_version(version: str) -> None:
    if not re.fullmatch(VERSION_PATTERN, version):
        raise SystemExit("Version must look like 3.19.0 or 3.19.0-beta.1")


def bump(root: Path, version: str) -> None:
    validate_version(version)
    replace_once(root / "src" / "row_bot" / "version.py", r'__version__ = "[^"]+"', f'__version__ = "{version}"')
    replace_once(
        root / "installer" / "row_bot_setup.iss",
        r'#define MyAppVersion\s+"[^"]+"',
        f'#define MyAppVersion   "{version}"',
    )
    replace_once(
        root / "installer" / "row_bot_setup.iss",
        r'; Row-Bot v[^\r\n]+Inno Setup Script',
        f'; Row-Bot v{version} - Inno Setup Script',
    )
    replace_once(
        root / "Start Row-Bot.command",
        rf'^    ROW_BOT_VERSION="{VERSION_PATTERN}"$',
        f'    ROW_BOT_VERSION="{version}"',
    )
    replace_once(
        root / ".github" / "workflows" / "release.yml",
        rf'default: "{VERSION_PATTERN}"',
        f'default: "{version}"',
    )
    replace_at_least_once(
        root / ".github" / "workflows" / "release.yml",
        rf"inputs\.version \|\| '{VERSION_PATTERN}'",
        f"inputs.version || '{version}'",
    )
    for key in ("CFBundleVersion", "CFBundleShortVersionString"):
        replace_once(
            root / "installer" / "Row-Bot.app" / "Contents" / "Info.plist",
            rf"(<key>{key}</key>\s*<string>)[^<]+(</string>)",
            rf"\g<1>{version}\g<2>",
        )
    replace_once(
        root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        r'placeholder: v\d+\.\d+\.\d+',
        f'placeholder: v{version}',
    )
    # The docs site's screenshot URLs carry the release as a cache-busting query.
    replace_once(
        root / "docs-site" / "src" / "components" / "Screenshot.tsx",
        r"const SCREENSHOT_REVISION = '[^']+';",
        f"const SCREENSHOT_REVISION = '{version}';",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bump Row-Bot release version files.")
    parser.add_argument("version", help="New version, e.g. 5.0.0")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    version = args.version
    bump(args.root, version)

    print(f"Prepared release version {version}")
    print("Next steps (docs/RELEASING.md):")
    print("1. Update RELEASE_NOTES.md")
    print("2. Run `uv run python scripts/run_test_matrix.py pr` and open the release-prep PR")
    print(f"3. After it merges and CI / ci-ok is green on the merge commit, tag it v{version},")
    print(f"   push the tag and run release.yml with version {version}")


if __name__ == "__main__":
    main()
