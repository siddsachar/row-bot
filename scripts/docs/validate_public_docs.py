"""Validate the public docs source before building/deploying."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def validate() -> list[str]:
    errors: list[str] = []
    docs_site = ROOT / "docs-site"
    docs_root = docs_site / "docs"
    metadata_root = ROOT / "docs-content" / "metadata"

    required_files = [
        docs_site / "package.json",
        docs_site / "docusaurus.config.ts",
        docs_site / "sidebars.ts",
        docs_site / "static" / "CNAME",
        docs_root / "index.mdx",
        metadata_root / "ui_surfaces.yml",
        metadata_root / "settings.yml",
        metadata_root / "screenshots.yml",
    ]
    for path in required_files:
        if not path.exists():
            errors.append(f"Missing required docs file: {path.relative_to(ROOT)}")

    pages = sorted(docs_root.rglob("*.mdx")) + sorted(docs_root.rglob("*.md"))
    if not pages:
        errors.append("No docs pages found under docs-site/docs")

    try:
        surfaces = _load_yaml(metadata_root / "ui_surfaces.yml").get("surfaces", {})
        screenshots = _load_yaml(metadata_root / "screenshots.yml").get("screenshots", {})
        settings = _load_yaml(metadata_root / "settings.yml").get("tabs", {})
    except Exception as exc:
        errors.append(f"Could not parse docs metadata: {exc}")
        return errors

    if not isinstance(surfaces, dict):
        errors.append("ui_surfaces.yml surfaces must be a mapping")
        surfaces = {}
    if not isinstance(screenshots, dict):
        errors.append("screenshots.yml screenshots must be a mapping")
        screenshots = {}
    if not isinstance(settings, dict):
        errors.append("settings.yml tabs must be a mapping")
        settings = {}

    for surface_id, surface in surfaces.items():
        for screenshot_id in surface.get("screenshot_ids", []) or []:
            if screenshot_id not in screenshots:
                errors.append(f"Surface {surface_id} references unknown screenshot {screenshot_id}")

    for screenshot_id, screenshot in screenshots.items():
        if not screenshot.get("alt"):
            errors.append(f"Screenshot {screenshot_id} is missing alt text")
        surface = screenshot.get("surface")
        if surface and surface not in surfaces:
            errors.append(f"Screenshot {screenshot_id} references unknown surface {surface}")

    expected_settings_tabs = {
        "Providers",
        "Models",
        "Knowledge",
        "Buddy",
        "Voice",
        "System",
        "Tracker",
        "Documents",
        "Search",
        "Skills",
        "Accounts",
        "Channels",
        "Utilities",
        "MCP",
        "Plugins",
    }
    missing_tabs = sorted(expected_settings_tabs - set(settings))
    if missing_tabs:
        errors.append("settings.yml missing tabs: " + ", ".join(missing_tabs))

    cname = docs_site / "static" / "CNAME"
    if cname.exists() and cname.read_text(encoding="utf-8").strip() != "row-bot.ai":
        errors.append("docs-site/static/CNAME must contain row-bot.ai")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate public docs source")
    parser.parse_args()
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Public docs validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
