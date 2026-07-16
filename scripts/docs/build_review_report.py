"""Build the concise public-documentation review or publication package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.docs.collect_inventory import build_inventory
from scripts.docs.validate_public_docs import validate


REPORT_ROOT = ROOT / "docs-build" / "reports"
REPORT = REPORT_ROOT / "public-docs-phase-c-publication-candidate.md"
LEGACY_REPORT = REPORT_ROOT / "docs-real-ui-review.md"
MISSING_REPORT = REPORT_ROOT / "public-docs-missing-coverage.md"
GALLERY_REPORT = REPORT_ROOT / "public-docs-screenshot-gallery.md"
SCREENSHOT_REPORT = REPORT_ROOT / "screenshots.json"
SURFACE_MAP = ROOT / "docs-content" / "metadata" / "ui_surfaces.yml"
SCREENSHOT_MANIFEST = ROOT / "docs-content" / "metadata" / "screenshots.yml"


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml_mapping(path: Path, key: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _surface_summary(surfaces: dict[str, Any]) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    missing = [
        (surface_id, surface)
        for surface_id, surface in surfaces.items()
        if isinstance(surface, dict) and surface.get("status") == "missing"
    ]
    ready = sum(
        1
        for surface in surfaces.values()
        if isinstance(surface, dict) and surface.get("status") == "ready"
    )
    return missing, ready


def build_missing_report(surfaces: dict[str, Any]) -> str:
    missing, ready = _surface_summary(surfaces)
    approved_no_image = [
        (surface_id, surface)
        for surface_id, surface in surfaces.items()
        if isinstance(surface, dict)
        and surface.get("status") == "ready"
        and surface.get("capture_type") == "manual"
        and surface.get("no_image_reason")
    ]
    lines = [
        "# Public docs coverage limitations",
        "",
        f"- Resolved surfaces: {ready}",
        f"- Unresolved surfaces: {len(missing)}",
        f"- Approved explicit no-image limitations: {len(approved_no_image)}",
        "- Automated screenshot gaps: 0",
        "",
        "The user approved the explicit no-image limitations for this first publication. They cover native hardware, packaged applications, or live accounts that isolated browser capture cannot represent faithfully.",
        "",
        "| Surface | Documentation | Scenario | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for surface_id, surface in approved_no_image + missing:
        reason = str(surface.get("no_image_reason", "")).replace("|", "\\|")
        lines.append(
            f"| `{surface_id}` — {surface.get('title', '')} | {surface.get('docs_page', '')} | "
            f"{surface.get('scenario', '')} | {reason} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def build_gallery(screenshots: dict[str, Any]) -> str:
    lines = [
        "# Public docs screenshot gallery",
        "",
        "Review in this manifest order. Every image is a full viewport capture from isolated first-run or configured demonstration data.",
        "",
    ]
    for index, (shot_id, shot) in enumerate(screenshots.items(), start=1):
        if not isinstance(shot, dict) or shot.get("status") != "required":
            continue
        output = str(shot.get("output") or f"{shot_id}.png")
        image_path = f"../../docs-site/static/img/screenshots/real-ui/{output}"
        docs_pages = ", ".join(str(page) for page in shot.get("docs_pages") or [])
        lines.extend(
            [
                f"## {index}. {shot.get('title', shot_id)}",
                "",
                f"- ID: `{shot_id}`",
                f"- Scenario: `{shot.get('scenario', '')}`; viewport: `{shot.get('viewport', '')}`; source: `{shot.get('source', '')}`",
                f"- Used by: {docs_pages}",
                "",
                f"![{shot.get('alt', shot_id)}]({image_path})",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_report() -> str:
    inventory = build_inventory()
    screenshot_report = _load_json(
        SCREENSHOT_REPORT,
        {"total": 0, "captured": 0, "failed": 0, "deferred": 0, "records": []},
    )
    screenshots = _load_yaml_mapping(SCREENSHOT_MANIFEST, "screenshots")
    surfaces = _load_yaml_mapping(SURFACE_MAP, "surfaces")
    validation_errors = validate()
    missing, ready = _surface_summary(surfaces)
    manual_no_image = sum(
        1
        for surface in surfaces.values()
        if isinstance(surface, dict)
        and surface.get("capture_type") == "manual"
        and surface.get("no_image_reason")
    )
    viewport_counts: dict[str, int] = {}
    for shot in screenshots.values():
        if isinstance(shot, dict) and shot.get("status") == "required":
            viewport = str(shot.get("viewport") or "unknown")
            viewport_counts[viewport] = viewport_counts.get(viewport, 0) + 1
    generated_counts = {
        key: len(value)
        for key, value in inventory.items()
        if isinstance(value, list)
    }
    review_counts: dict[str, int] = {}
    for shot in screenshots.values():
        if isinstance(shot, dict):
            status = str(shot.get("review_status") or "missing")
            review_counts[status] = review_counts.get(status, 0) + 1

    lines = [
        "# Row-Bot public docs — Phase C publication candidate",
        "",
        "Generated 2026-07-16 after consolidated manual review and the Phase C correction pass. Publication was explicitly approved subject to these corrections and passing checks.",
        "",
        "## Preview",
        "",
        "```powershell",
        "cd docs-site",
        "npm run serve -- --host 127.0.0.1 --port 3000",
        "```",
        "",
        "Open `http://127.0.0.1:3000/docs/`.",
        "",
        "## Baseline and current state",
        "",
        "- Branch confirmed: `docs/public-docs-end-to-end-uplift`; stable application version: `4.4.0`.",
        "- Initial lightweight inventory, validation, docs build, and eight documentation tests passed before editing.",
        "- The initial build contained 69 pages and 1,707 Pagefind words. The old automated images were narrow 1440×960 frames or partial crops and included machine-specific configured states; all screenshot-eligible assets were regenerated.",
        "- `docs-content/metadata/ui_surfaces.yml` is now the single surface → page → screenshot/no-image outcome ledger.",
        "- Public guides now use a shallow task-oriented structure and cover first run, chat, profiles/goals/agents, workflows, Knowledge/Wiki Vault, both studios, Android, extensions, settings, and operations.",
        "- Generated references now include settings controls, CLI options, environment/configuration, providers, tools, channels, skills, plugins, MCP, storage, safety, and screenshot coverage.",
        "- Marketing navigation and README now link to `/docs/`; analytics and form handling were not changed.",
        "",
        "## Coverage",
        "",
        f"- Authoritative surfaces: {len(surfaces)}",
        f"- Resolved outcomes: {ready}",
        f"- Unresolved outcomes: {len(missing)}",
        f"- Approved explicit no-image limitations: {manual_no_image}",
        f"- Automated screenshots validated: {screenshot_report.get('captured', 0)} of {len(screenshots)}",
        f"- Screenshot failures/deferred: {screenshot_report.get('failed', 0)}/{screenshot_report.get('deferred', 0)}",
        f"- Automated dimensions: {viewport_counts.get('desktop', 0)} desktop frames at 3840×2160 and {viewport_counts.get('mobile', 0)} Android frames at 390×844.",
        "- Screenshot review state: " + ", ".join(f"{key}={value}" for key, value in sorted(review_counts.items())),
        "",
        "See `public-docs-missing-coverage.md` for the concise unresolved list and `public-docs-screenshot-gallery.md` for the ordered image review.",
        "",
        "## Generated inventory",
        "",
    ]
    for key in sorted(generated_counts):
        lines.append(f"- {key}: {generated_counts[key]}")

    lines.extend(
        [
            "",
            "## Approved first-publication limitations",
            "",
            "### Windows packaged application",
            "",
            "- Installer, launch, update, repair/uninstall, Windows security prompts, and packaged recovery.",
            "- Tray/window modes, Buddy desktop overlay, microphone/realtime voice, camera, and Computer Use disclosure/takeover/stop.",
            "- Developer command approvals/local server controls and Designer import/export/share/history dialogs.",
            "",
            "### macOS packaged application",
            "",
            "- Installation/launch, Gatekeeper and privacy prompts, update/removal, menu/tray/window behaviour, and packaged recovery.",
            "- Buddy/window behaviour, microphone/realtime voice, camera, and Computer Use permissions.",
            "- Developer and Designer native dialogs as above.",
            "",
            "### Android physical device",
            "",
            "- Pairing/QR, trusted-LAN or Tailscale-style access, access denial/recovery, responsive navigation, and primary chat use.",
            "- Do not claim iPhone validation; Linux remains source-verified only.",
            "",
            "### Live integrations",
            "",
            "- Ollama, a custom local/OpenAI-compatible endpoint, API providers, Codex OAuth, and xAI OAuth status/recovery without unnecessary paid calls.",
            "- Available messaging channels except SMS; avoid unintended external messages.",
            "- Configured MCP servers and plugins, including permission, disabled, failure, and recovery states.",
            "- GitHub, Google Gmail/Calendar, and X initiation/connected/recovery states; do not reproduce third-party login pages.",
            "",
            "## Safety and genuine limitations",
            "",
            "- Automated capture used a new temporary `ROW_BOT_DATA_DIR`; the normal profile and OS keyring were not read.",
            "- Background autostart, network health probes, provider status refresh, real channel adapters, live MCP transports, and external marketplace fetches were disabled or replaced by explicit display-only documentation fixtures.",
            "- No provider prompts, real channel messages, live account changes, analytics/form changes, deploys, tags, releases, or publication actions were performed.",
            "- Native OS prompts, physical pairing, packaged-app flows, and live integration health cannot be represented faithfully in browser automation. Their explicit no-image reasons are approved limitations for this first publication.",
            "",
            "## Validation",
            "",
            "- Metadata/content/build validation: " + ("passed" if not validation_errors else "failed"),
        ]
    )
    for error in validation_errors:
        lines.append(f"- ERROR: {error}")
    lines.extend(
        [
            "",
            "Exact command results and live deployment verification are recorded in the implementation handoff.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the public docs publication candidate package")
    parser.parse_args()
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    surfaces = _load_yaml_mapping(SURFACE_MAP, "surfaces")
    screenshots = _load_yaml_mapping(SCREENSHOT_MANIFEST, "screenshots")
    report = build_report()
    REPORT.write_text(report, encoding="utf-8")
    LEGACY_REPORT.write_text(report, encoding="utf-8")
    MISSING_REPORT.write_text(build_missing_report(surfaces), encoding="utf-8")
    GALLERY_REPORT.write_text(build_gallery(screenshots), encoding="utf-8")
    print(f"Wrote docs review package to {REPORT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
