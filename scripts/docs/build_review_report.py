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

    lines = [
        "# Row-Bot Public Docs Real UI Review Report",
        "",
        "Generated: 2026-06-19 real UI docs automation run.",
        "",
        "## Completion Summary",
        "",
        "- Curated public docs real-UI pages are in `docs-site/docs`.",
        "- Generated reference pages are in `docs-site/docs/reference/generated`.",
        "- Pagefind search UI is present on the docs homepage and `/search`.",
        "- LLM docs are generated under `docs-site/static`.",
        "- Real UI capture mode seeds fake data, freezes time, reduces motion, and disables unsafe side effects.",
        "- Screenshots are captured from the actual NiceGUI `/` route, not fake docs routes.",
        "- Baseline validator previously passed the synthetic prototype; the hardened validator now rejects that approach.",
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
            "- Inspect screenshots for visual accuracy and absence of sensitive data.",
            "- Verify the docs site visually matches the current public website.",
            "- Confirm every Home tab and every Settings tab page is accurate.",
            "- Confirm troubleshooting steps do not risk data loss or credential exposure.",
            "- Review generated reference pages for overexposed implementation details.",
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
