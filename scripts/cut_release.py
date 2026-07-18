"""Prepare Row-Bot release version bumps.

Usage:
    python scripts/cut_release.py 3.19.0
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Expected exactly one match in {path}: {pattern}")
    path.write_text(new_text, encoding="utf-8")


def replace_at_least_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count < 1:
        raise SystemExit(f"Expected at least one match in {path}: {pattern}")
    path.write_text(new_text, encoding="utf-8")


def validate_version(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", version):
        raise SystemExit("Version must look like 3.19.0 or 3.19.0-beta.1")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump Row-Bot release version files.")
    parser.add_argument("version", help="New version, e.g. 3.19.0")
    args = parser.parse_args()
    validate_version(args.version)

    version = args.version
    replace_once(ROOT / "src" / "row_bot" / "version.py", r'__version__ = "[^"]+"', f'__version__ = "{version}"')
    replace_once(
        ROOT / "installer" / "row_bot_setup.iss",
        r'#define MyAppVersion\s+"[^"]+"',
        f'#define MyAppVersion   "{version}"',
    )
    replace_once(
        ROOT / "installer" / "row_bot_setup.iss",
        r'; Row-Bot v[^\r\n]+Inno Setup Script',
        f'; Row-Bot v{version} - Inno Setup Script',
    )
    replace_at_least_once(
        ROOT / "installer" / "install_deps.bat",
        r"Row-Bot v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?",
        f"Row-Bot v{version}",
    )
    replace_once(
        ROOT / "Start Row-Bot.command",
        r'^    ROW_BOT_VERSION="\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?"$',
        f'    ROW_BOT_VERSION="{version}"',
    )
    replace_once(
        ROOT / ".github" / "workflows" / "release.yml",
        r'default: "\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?"',
        f'default: "{version}"',
    )
    replace_at_least_once(
        ROOT / ".github" / "workflows" / "release.yml",
        r"inputs\.version \|\| '\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?'",
        f"inputs.version || '{version}'",
    )
    for plist in [ROOT / "installer" / "Row-Bot.app" / "Contents" / "Info.plist"]:
        text = plist.read_text(encoding="utf-8")
        text = re.sub(r'<key>CFBundleVersion</key>\s*<string>[^<]+</string>', f'<key>CFBundleVersion</key>\n    <string>{version}</string>', text, count=1)
        text = re.sub(r'<key>CFBundleShortVersionString</key>\s*<string>[^<]+</string>', f'<key>CFBundleShortVersionString</key>\n    <string>{version}</string>', text, count=1)
        plist.write_text(text, encoding="utf-8")
    replace_once(
        ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        r'placeholder: v\d+\.\d+\.\d+',
        f'placeholder: v{version}',
    )
    replace_once(
        ROOT / "tests" / "test_brand_constants.py",
        r'assert __version__ == "[^"]+"',
        f'assert __version__ == "{version}"',
    )
    replace_once(
        ROOT / "tests" / "test_brand_constants.py",
        r'assert brand\.APP_USER_AGENT == "Row-Bot/[^"]+"',
        f'assert brand.APP_USER_AGENT == "Row-Bot/{version}"',
    )
    replace_once(
        ROOT / "tests" / "test_brand_constants.py",
        r'assert brand\.UPDATER_USER_AGENT == "Row-Bot-Updater/[^"]+"',
        f'assert brand.UPDATER_USER_AGENT == "Row-Bot-Updater/{version}"',
    )

    print(f"Prepared release version {version}")
    print("Next steps:")
    print("1. Update RELEASE_NOTES.md")
    print("2. Open a release-prep PR")
    print(f"3. After merge: git tag -a v{version} -m \"v{version}\" && git push origin v{version}")


if __name__ == "__main__":
    main()
