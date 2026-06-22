"""Build a concise real UI public docs review report."""

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


REPORT = ROOT / "docs-build" / "reports" / "docs-real-ui-review.md"
SCREENSHOT_REPORT = ROOT / "docs-build" / "reports" / "screenshots.json"


def _load_screenshot_report() -> dict[str, Any]:
    if not SCREENSHOT_REPORT.exists():
        return {"total": 0, "captured": 0, "failed": 0, "deferred": 0, "records": []}
    return json.loads(SCREENSHOT_REPORT.read_text(encoding="utf-8"))


def _load_screenshot_manifest() -> dict[str, Any]:
    path = ROOT / "docs-content" / "metadata" / "screenshots.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    screenshots = data.get("screenshots", {})
    return screenshots if isinstance(screenshots, dict) else {}


def build_report() -> str:
    inventory = build_inventory()
    screenshot_report = _load_screenshot_report()
    screenshot_manifest = _load_screenshot_manifest()
    validation_errors = validate()
    generated_counts = {
        key: len(value)
        for key, value in inventory.items()
        if isinstance(value, list)
    }
    deferred = [
        (shot_id, shot.get("reason", ""), shot.get("follow_up", ""))
        for shot_id, shot in screenshot_manifest.items()
        if isinstance(shot, dict) and shot.get("status") == "deferred"
    ]

    review_status_counts: dict[str, int] = {}
    for shot in screenshot_manifest.values():
        if isinstance(shot, dict):
            status = str(shot.get("review_status") or "missing")
            review_status_counts[status] = review_status_counts.get(status, 0) + 1

    lines = [
        "# Row-Bot Public Docs Real UI Review Report",
        "",
        "Generated: 2026-06-22 complete public user guide pass.",
        "",
        "## Completion Summary",
        "",
        "- Curated public user guide pages are in `docs-site/docs`.",
        "- Lookup reference pages are in `docs-site/docs/reference/generated`.",
        "- Pagefind search UI is present on the docs homepage and `/search`.",
        "- LLM docs are generated under `docs-site/static`.",
        "- Screenshot automation opens the real Row-Bot app route and records review status for each image.",
        "- Real-data screenshots are written to `docs-build/reports/real-data-screenshots/` and copied as candidate docs assets.",
        "- Screenshot metadata keeps a final human visual review gate before publishing.",
        "- Current public website guardrails remain validation-only with no Pages deploy behavior.",
        "",
        "## Generated Inventory",
        "",
    ]
    for key in sorted(generated_counts):
        lines.append(f"- {key}: {generated_counts[key]}")
    lines.extend(
        [
            "",
            "## Screenshots",
            "",
            f"- Manifest entries: {len(screenshot_manifest)}",
            f"- Captured/validated: {screenshot_report.get('captured', 0)}",
            f"- Failed: {screenshot_report.get('failed', 0)}",
            f"- Deferred: {screenshot_report.get('deferred', 0)}",
        ]
    )
    if review_status_counts:
        lines.append("- Review status: " + ", ".join(f"{key}={value}" for key, value in sorted(review_status_counts.items())))
    if deferred:
        lines.append("")
        lines.append("Deferred screenshots:")
        for shot_id, reason, follow_up in deferred:
            lines.append(f"- {shot_id}: {reason} Follow-up: {follow_up}")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            "- Status: " + ("passed" if not validation_errors else "failed"),
        ]
    )
    for error in validation_errors:
        lines.append(f"- ERROR: {error}")
    lines.extend(
        [
            "",
            "## Manual Review Checklist",
            "",
            "- Verify provider/model recommendation wording and cost/privacy implications.",
            "- Review privacy and safety claims against current app behavior.",
            "- Inspect every needs-review screenshot for visual accuracy and absence of sensitive data.",
            "- Update screenshot `review_status` to approved, replace, crop, or redact after inspection.",
            "- Verify the docs site visually matches the current public website.",
            "- Confirm every Home tab and every Settings tab page is accurate.",
            "- Confirm troubleshooting steps do not risk data loss or credential exposure.",
            "- Review lookup reference pages for overexposed implementation details.",
            "- Confirm no public-site cutover or deploy behavior is included.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build public docs V1 review report")
    parser.parse_args()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(build_report(), encoding="utf-8")
    print(f"Wrote docs review report to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
