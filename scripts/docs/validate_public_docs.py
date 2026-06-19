"""Validate the public docs source before building or publishing."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.docs.capture_real_ui_screenshots import DOM_ROOT, OUTPUT_ROOT, _validate_image
from scripts.docs.collect_inventory import build_inventory
from scripts.docs.generate_mdx import check_pages, render_pages
from scripts.docs.schemas import public_route_for_doc


DOCS_SITE = ROOT / "docs-site"
DOCS_ROOT = DOCS_SITE / "docs"
METADATA_ROOT = ROOT / "docs-content" / "metadata"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY"),
    re.compile(r"C:\\Users\\"),
    re.compile(r"/Users/[^/\s]+"),
]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    data: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, text[end + 4 :]


def _doc_pages() -> list[Path]:
    return sorted(DOCS_ROOT.rglob("*.md")) + sorted(DOCS_ROOT.rglob("*.mdx"))


def _doc_routes() -> dict[str, Path]:
    return {public_route_for_doc(path, DOCS_ROOT): path for path in _doc_pages()}


def _route_exists(route: str, routes: dict[str, Path]) -> bool:
    normalized = str(route or "").strip()
    if not normalized:
        return False
    variants = {normalized, normalized.rstrip("/"), normalized.rstrip("/") + "/"}
    return any(variant in routes for variant in variants)


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _scan_text(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    rel = _relative(path)
    if ".local/" in text.replace("\\", "/"):
        errors.append(f"{rel} references .local")
    lowered = text.lower()
    normalized = text.replace("\\", "/")
    if "/docs-mode/" in lowered or "docs-mode screenshot" in lowered or "docs mode screenshot" in lowered:
        errors.append(f"{rel} references forbidden docs-mode screenshot text")
    if "img/screenshots/generated" in normalized or "screenshots/generated" in normalized:
        errors.append(f"{rel} references the fake generated screenshot directory")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            errors.append(f"{rel} contains blocked secret/path pattern: {pattern.pattern}")
    return errors


def _scan_files(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if path.exists() and path.is_file():
            errors.extend(_scan_text(path, path.read_text(encoding="utf-8", errors="replace")))
    return errors


def _validate_required_files(errors: list[str]) -> None:
    required_files = [
        DOCS_SITE / "package.json",
        DOCS_SITE / "docusaurus.config.ts",
        DOCS_SITE / "sidebars.ts",
        DOCS_SITE / "static" / "CNAME",
        DOCS_ROOT / "index.mdx",
        METADATA_ROOT / "ui_surfaces.yml",
        METADATA_ROOT / "real_ui_surfaces.yml",
        METADATA_ROOT / "settings.yml",
        METADATA_ROOT / "settings_tabs.yml",
        METADATA_ROOT / "home_tabs.yml",
        METADATA_ROOT / "dialogs.yml",
        METADATA_ROOT / "docs_routes.yml",
        METADATA_ROOT / "screenshots.yml",
        METADATA_ROOT / "how_to_guides.yml",
    ]
    for path in required_files:
        if not path.exists():
            errors.append(f"Missing required docs file: {_relative(path)}")
    if not _doc_pages():
        errors.append("No docs pages found under docs-site/docs")
    cname = DOCS_SITE / "static" / "CNAME"
    if cname.exists() and cname.read_text(encoding="utf-8").strip() != "row-bot.ai":
        errors.append("docs-site/static/CNAME must contain row-bot.ai")


def _validate_generated_pages(errors: list[str]) -> None:
    inventory = build_inventory()
    errors.extend(check_pages(render_pages(inventory)))


def _validate_metadata(errors: list[str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    surfaces = _load_yaml(METADATA_ROOT / "ui_surfaces.yml").get("surfaces", {})
    screenshots = _load_yaml(METADATA_ROOT / "screenshots.yml").get("screenshots", {})
    settings = _load_yaml(METADATA_ROOT / "settings.yml").get("tabs", {})
    home_tabs = _load_yaml(METADATA_ROOT / "home_tabs.yml").get("tabs", {})
    guides = _load_yaml(METADATA_ROOT / "how_to_guides.yml").get("guides", {})
    if not isinstance(surfaces, dict):
        errors.append("ui_surfaces.yml surfaces must be a mapping")
        surfaces = {}
    if not isinstance(screenshots, dict):
        errors.append("screenshots.yml screenshots must be a mapping")
        screenshots = {}
    if not isinstance(settings, dict):
        errors.append("settings.yml tabs must be a mapping")
        settings = {}
    if not isinstance(home_tabs, dict):
        errors.append("home_tabs.yml tabs must be a mapping")
        home_tabs = {}
    if not isinstance(guides, dict):
        errors.append("how_to_guides.yml guides must be a mapping")
        guides = {}
    return surfaces, screenshots, settings, home_tabs, guides


def _validate_routes(errors: list[str], settings: dict[str, Any], home_tabs: dict[str, Any], guides: dict[str, Any]) -> None:
    routes = _doc_routes()
    for guide_id, guide in guides.items():
        route = str((guide or {}).get("route") or "")
        if not _route_exists(route, routes):
            errors.append(f"Guide {guide_id} route does not exist: {route}")
    for tab, meta in settings.items():
        route = str((meta or {}).get("docs_route") or "")
        if route and not _route_exists(route, routes):
            errors.append(f"Settings tab {tab} route does not exist: {route}")
    for tab, meta in home_tabs.items():
        route = str((meta or {}).get("docs_route") or "")
        if route and not _route_exists(route, routes):
            errors.append(f"Home tab {tab} route does not exist: {route}")
    for path in _doc_pages():
        meta, _body = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if not meta.get("title"):
            errors.append(f"{_relative(path)} missing frontmatter title")
        if not meta.get("description"):
            errors.append(f"{_relative(path)} missing frontmatter description")


def _source_and_dom_text() -> str:
    parts: list[str] = []
    source_roots = [
        ROOT / "src" / "row_bot",
        ROOT / "scripts" / "docs",
        ROOT / "docs-content" / "metadata",
    ]
    for root in source_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yml", ".yaml", ".json"}:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
    if DOM_ROOT.exists():
        for path in sorted(DOM_ROOT.glob("*.json")):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _validate_screenshots(errors: list[str], surfaces: dict[str, Any], screenshots: dict[str, Any]) -> None:
    referenced = set()
    for surface_id, surface in surfaces.items():
        for screenshot_id in (surface or {}).get("screenshot_ids", []) or []:
            referenced.add(screenshot_id)
            if screenshot_id not in screenshots:
                errors.append(f"Surface {surface_id} references unknown screenshot {screenshot_id}")

    docs_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in _doc_pages())
    component_ids = set(re.findall(r"<Screenshot\s+[^>]*id=\"([^\"]+)\"", docs_text, flags=re.DOTALL))

    source_text = _source_and_dom_text()
    for screenshot_id, screenshot in screenshots.items():
        if not isinstance(screenshot, dict):
            errors.append(f"Screenshot {screenshot_id} must be a mapping")
            continue
        if not screenshot.get("alt"):
            errors.append(f"Screenshot {screenshot_id} is missing alt text")
        route = str(screenshot.get("route") or "")
        if "/docs-mode/" in route or "/docs_mode/" in route:
            errors.append(f"Screenshot {screenshot_id} uses forbidden fake docs route: {route}")
        if "docs-site/static/img/screenshots/generated" in json.dumps(screenshot):
            errors.append(f"Screenshot {screenshot_id} references fake generated screenshot directory")
        surface = screenshot.get("surface")
        if surface and surface not in surfaces:
            errors.append(f"Screenshot {screenshot_id} references unknown surface {surface}")
        status = str(screenshot.get("status") or "")
        if status not in {"required", "deferred"}:
            errors.append(f"Screenshot {screenshot_id} status must be required or deferred")
        selector = str(screenshot.get("capture_selector") or screenshot.get("wait_for") or "")
        for docs_id in re.findall(r'data-docs-id=\"([^\"]+)\"', selector):
            if docs_id not in source_text:
                errors.append(f"Screenshot {screenshot_id} references data-docs-id {docs_id} not present in source or real DOM snapshots")
        if status == "required" and not screenshot.get("expected_text"):
            errors.append(f"Screenshot {screenshot_id} is missing expected_text")
        output = OUTPUT_ROOT / str(screenshot.get("output") or f"{screenshot_id}.png")
        if status == "deferred":
            if not screenshot.get("reason"):
                errors.append(f"Deferred screenshot {screenshot_id} is missing reason")
            if not screenshot.get("follow_up"):
                errors.append(f"Deferred screenshot {screenshot_id} is missing follow_up")
            if screenshot_id in component_ids:
                errors.append(f"Deferred screenshot {screenshot_id} is referenced by user-facing docs")
            continue
        errors.extend(f"Screenshot {screenshot_id}: {error}" for error in _validate_image(output, {"id": screenshot_id, **screenshot}))
        if screenshot_id not in referenced:
            errors.append(f"Screenshot {screenshot_id} is not referenced by any UI surface")
        if not (screenshot.get("docs_pages") or screenshot_id in component_ids):
            errors.append(f"Screenshot {screenshot_id} is not used by docs pages or manifest docs_pages")
    for screenshot_id in component_ids:
        if screenshot_id not in screenshots:
            errors.append(f"Docs page references unknown Screenshot id {screenshot_id}")


def _validate_reference_links(errors: list[str]) -> None:
    reference_index = DOCS_ROOT / "reference" / "index.mdx"
    text = reference_index.read_text(encoding="utf-8", errors="replace") if reference_index.exists() else ""
    expected = [
        "tools",
        "providers",
        "settings",
        "home-tabs",
        "channels",
        "skills",
        "mcp",
        "plugins",
        "data-storage",
        "safety-approvals",
        "environment-and-config",
        "screenshots",
    ]
    for slug in expected:
        if f"/docs/reference/generated/{slug}" not in text:
            errors.append(f"reference/index.mdx does not link generated category {slug}")


def _validate_llms(errors: list[str]) -> None:
    llms = DOCS_SITE / "static" / "llms.txt"
    full = DOCS_SITE / "static" / "llms-full.txt"
    for path in (llms, full, DOCS_SITE / "static" / "docs" / "llms.txt", DOCS_SITE / "static" / "docs" / "llms-full.txt"):
        if not path.exists() or path.stat().st_size < 100:
            errors.append(f"Missing or empty generated LLM docs file: {_relative(path)}")
    if llms.exists():
        text = llms.read_text(encoding="utf-8", errors="replace")
        for route in _doc_routes():
            if route not in text:
                errors.append(f"llms.txt missing route {route}")


def _validate_public_guardrails(errors: list[str]) -> None:
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", "main", "--", "docs", "README.md"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        changed = [line for line in completed.stdout.splitlines() if line.strip()]
        if changed:
            errors.append("Current public website/README changed: " + ", ".join(changed))
    except Exception as exc:
        errors.append(f"Could not check public-site git diff: {exc}")
    readme = ROOT / "README.md"
    if readme.exists():
        readme_text = readme.read_text(encoding="utf-8", errors="replace").lower()
        if "docs-site" in readme_text or "row-bot.ai/docs" in readme_text:
            errors.append("README.md links to docs-site or row-bot.ai/docs")


def _validate_workflow(errors: list[str]) -> None:
    workflow = ROOT / ".github" / "workflows" / "docs.yml"
    text = workflow.read_text(encoding="utf-8", errors="replace") if workflow.exists() else ""
    lowered = text.lower()
    blocked = [
        "actions/deploy-pages",
        "peaceiris/actions-gh-pages",
        "github-pages",
        "pages: write",
        "id-token: write",
    ]
    for pattern in blocked:
        if pattern in lowered:
            errors.append(f"docs workflow contains deploy/publish behavior: {pattern}")


def _validate_pagefind(errors: list[str]) -> None:
    build_dir = DOCS_SITE / "build"
    if build_dir.exists() and not (build_dir / "pagefind").exists():
        errors.append("docs-site/build exists but Pagefind output is missing")


def _validate_secret_scans(errors: list[str]) -> None:
    paths = _doc_pages()
    paths.extend([METADATA_ROOT / "screenshots.yml", ROOT / "docs-content" / "review-status.md"])
    paths.extend([DOCS_SITE / "static" / "llms.txt", DOCS_SITE / "static" / "llms-full.txt"])
    report = ROOT / "docs-build" / "reports" / "docs-real-ui-review.md"
    if report.exists():
        paths.append(report)
    errors.extend(_scan_files(paths))


def validate() -> list[str]:
    errors: list[str] = []
    _validate_required_files(errors)
    if errors:
        return errors

    try:
        surfaces, screenshots, settings, home_tabs, guides = _validate_metadata(errors)
    except Exception as exc:
        errors.append(f"Could not parse docs metadata: {exc}")
        return errors

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
        "Preferences",
    }
    missing_tabs = sorted(expected_settings_tabs - set(settings))
    if missing_tabs:
        errors.append("settings.yml missing tabs: " + ", ".join(missing_tabs))
    expected_home_tabs = {"Workflows", "Designer", "Developer", "Knowledge", "Monitor"}
    missing_home = sorted(expected_home_tabs - set(home_tabs))
    if missing_home:
        errors.append("home_tabs.yml missing tabs: " + ", ".join(missing_home))

    _validate_generated_pages(errors)
    _validate_routes(errors, settings, home_tabs, guides)
    _validate_screenshots(errors, surfaces, screenshots)
    _validate_reference_links(errors)
    _validate_llms(errors)
    _validate_public_guardrails(errors)
    _validate_workflow(errors)
    _validate_pagefind(errors)
    _validate_secret_scans(errors)
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
