"""Generate ignored Settings parity galleries, ledgers, and audit manifests."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import struct
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / ".local/evidence/unified-client-platform/phase-4-settings-deep-parity"
STARTING_HEAD = "37a0193009e67753689be047bbffe1d4be109cf5"

PAGES = (
    (1, "Providers", "providers", "src/row_bot/ui/settings.py::_build_cloud_tab"),
    (2, "Models", "models", "src/row_bot/ui/settings.py::_build_models_tab"),
    (3, "Knowledge", "knowledge", "src/row_bot/ui/settings.py::_build_knowledge_tab"),
    (4, "Buddy", "buddy", "src/row_bot/ui/buddy.py::build_buddy_settings_tab"),
    (5, "Goals", None, "React-only route; NiceGUI goal/profile owners"),
    (6, "Voice", "voice", "src/row_bot/ui/settings.py::_build_voice_tab"),
    (7, "System", "system", "src/row_bot/ui/settings.py::_build_system_access_tab"),
    (8, "Tracker", "tracker", "src/row_bot/ui/settings.py::_build_tracker_tab"),
    (9, "Documents", "documents", "src/row_bot/ui/settings.py::_build_documents_tab"),
    (10, "Tools", "tools", "src/row_bot/ui/settings.py::_build_tools_tab"),
    (11, "Skills", "skills", "src/row_bot/ui/settings.py::_build_skills_tab"),
    (12, "Accounts", "accounts", "src/row_bot/ui/settings.py::_build_accounts_tab"),
    (13, "Channels", "channels", "src/row_bot/ui/settings.py::_build_channels_tab"),
    (14, "Utilities", "utilities", "src/row_bot/ui/settings.py::_build_utilities_tab"),
    (15, "MCP", "mcp", "src/row_bot/ui/mcp_settings.py::build_mcp_settings_tab"),
    (16, "Plugins", "plugins", "src/row_bot/ui/settings.py::_build_plugins_tab"),
    (
        17,
        "Preferences",
        "preferences",
        "src/row_bot/ui/settings.py::_build_preferences_tab",
    ),
)


def _load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _latest_summary(stage: str) -> tuple[Path | None, dict[str, Any]]:
    runs = EVIDENCE / "metrics/runs"
    candidates = sorted(
        runs.glob(f"{stage}-*/summary.json"), key=lambda path: path.parent.name
    )
    if not candidates:
        return None, {}
    path = candidates[-1]
    return path, _load(path, {})


def _image_record(path: Path, decisions: dict[str, Any]) -> dict[str, Any]:
    relative = path.relative_to(EVIDENCE).as_posix()
    digest = _sha256(path)
    dimensions = _png_dimensions(path)
    decision = decisions.get(relative) or decisions.get(digest) or {}
    return {
        "path": relative,
        "sha256": digest,
        "width": dimensions[0] if dimensions else None,
        "height": dimensions[1] if dimensions else None,
        "inspection": decision.get("inspection", "pending original-size inspection"),
        "notes": decision.get("notes", ""),
    }


def _build_gallery(stage: str) -> tuple[str, str]:
    markdown: list[str] = [
        "# Settings paired real-data gallery",
        "",
        f"React stage: `{stage}`. Every available pair uses the same guarded host and unchanged mounted data state. Wiki controls are embedded in Knowledge. Goals is explicitly React-only and therefore has no false NiceGUI peer.",
        "",
    ]
    cards: list[str] = []
    for _, name, nicegui_slug, _owner in PAGES:
        react_slug = name.casefold()
        for viewport in ("desktop", "phone"):
            reference = (
                EVIDENCE / f"reference/nicegui/{nicegui_slug}-{viewport}-full.png"
                if nicegui_slug
                else None
            )
            candidate = EVIDENCE / f"{stage}/react/{react_slug}-{viewport}-full.png"
            reference_link = (
                f"[NiceGUI {viewport}](reference/nicegui/{nicegui_slug}-{viewport}-full.png)"
                if reference and reference.is_file()
                else "No exact NiceGUI Settings owner"
                if nicegui_slug is None
                else "Missing"
            )
            react_link = (
                f"[React {viewport}]({stage}/react/{react_slug}-{viewport}-full.png)"
                if candidate.is_file()
                else "Missing"
            )
            markdown.extend(
                [
                    f"## {name} — {viewport}",
                    "",
                    "| NiceGUI owner | React candidate |",
                    "| --- | --- |",
                    f"| {reference_link} | {react_link} |",
                    "",
                ]
            )
            ref_html = (
                f'<a href="../reference/nicegui/{nicegui_slug}-{viewport}-full.png"><img src="../reference/nicegui/{nicegui_slug}-{viewport}-full.png" alt="{html.escape(name)} NiceGUI {viewport}"></a>'
                if reference and reference.is_file()
                else '<div class="missing">No exact NiceGUI Settings owner</div>'
            )
            react_html = (
                f'<a href="../{stage}/react/{react_slug}-{viewport}-full.png"><img src="../{stage}/react/{react_slug}-{viewport}-full.png" alt="{html.escape(name)} React {viewport}"></a>'
                if candidate.is_file()
                else '<div class="missing">React evidence missing</div>'
            )
            cards.append(
                f"<section><h2>{html.escape(name)} <small>{viewport}</small></h2>"
                f'<div class="pair"><figure><figcaption>NiceGUI</figcaption>{ref_html}</figure>'
                f"<figure><figcaption>React · {html.escape(stage)}</figcaption>{react_html}</figure></div></section>"
            )
    gallery_html = (
        """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Settings paired gallery</title>
<style>
body{margin:0;padding:24px;background:#0d131a;color:#e9f0f7;font:14px system-ui,sans-serif}h1{margin-top:0}
section{margin:0 0 36px;border-top:1px solid #32404f;padding-top:12px}h2{font-size:18px}small{color:#9fb0c1}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}figure{margin:0;min-width:0}figcaption{margin-bottom:6px;color:#9fb0c1}
img{display:block;width:100%;height:auto;border:1px solid #405267;background:#111820}.missing{min-height:180px;display:grid;place-items:center;border:1px dashed #5f7081;color:#9fb0c1}
@media(max-width:800px){.pair{grid-template-columns:1fr}}
</style><body><h1>Settings paired real-data gallery</h1>"""
        + "".join(cards)
        + "</body></html>\n"
    )
    return "\n".join(markdown), gallery_html


def _metric_summary(stage: str, page: str, viewport: str) -> tuple[str, str]:
    slug = page.casefold()
    axe = _load(EVIDENCE / f"metrics/{stage}/react/{slug}-{viewport}-axe.json", {})
    dom = _load(EVIDENCE / f"metrics/{stage}/react/{slug}-{viewport}-dom.json", {})
    if not axe and not dom:
        return "missing", "missing"
    a11y = f"{len(axe.get('violations') or [])} violations; {len(axe.get('incomplete') or [])} incomplete"
    overflow = f"{len(dom.get('overflow') or [])} overflow observations"
    return a11y, overflow


def _build_page_ledger(stage: str, reviews: dict[str, Any]) -> str:
    lines = [
        "# Settings deep parity page ledger",
        "",
        "Generated from evidence. `verified` means the engineering evidence gate was recorded; it never means owner acceptance.",
        "",
        "| Order | Route / source owner | Before evidence | Candidate evidence | Focused tests | A11y / overflow | Visual score | Result |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for order, name, nicegui_slug, owner in PAGES:
        slug = name.casefold()
        before = EVIDENCE / f"before/react/{slug}-desktop-full.png"
        candidate = EVIDENCE / f"candidate/react/{slug}-desktop-full.png"
        before_value = (
            f"`before/react/{slug}-desktop-full.png`" if before.is_file() else "missing"
        )
        candidate_value = (
            f"`candidate/react/{slug}-desktop-full.png`"
            if candidate.is_file()
            else "missing"
        )
        a11y, overflow = _metric_summary(stage, name, "desktop")
        decision = reviews.get(slug, {})
        score = decision.get("visual_score", "pending original-size review")
        result = decision.get("result", "pending")
        tests = decision.get("focused_tests", "pending exact result")
        source = f"`/app-v2/settings/{slug}`; {_cell(owner)}"
        lines.append(
            f"| {order} | {source} | {before_value} | {candidate_value} | {_cell(tests)} | {_cell(a11y)}; {_cell(overflow)} | {_cell(score)} | {_cell(result)} |"
        )
    return "\n".join(lines) + "\n"


def _build_inspection_log(
    stage: str, decisions: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    roots = [
        EVIDENCE / "reference/nicegui",
        EVIDENCE / f"{stage}/react",
        EVIDENCE / "failures",
    ]
    paths = sorted(
        {path for root in roots if root.is_dir() for path in root.rglob("*.png")},
        key=lambda path: path.relative_to(EVIDENCE).as_posix(),
    )
    records = [_image_record(path, decisions) for path in paths]
    counts = Counter(record["inspection"] for record in records)
    lines = [
        "# Settings original-size inspection log",
        "",
        f"Generated image manifest for React stage `{stage}`. Status counts: "
        + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
        + ".",
        "",
        "Automated enumeration is not visual inspection. A reviewer records decisions in `inspection-decisions.json`, keyed by evidence-relative path or SHA-256, before regenerating this log.",
        "",
        "| Image | Pixels | SHA-256 | Inspection | Notes |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for record in records:
        pixels = (
            f"{record['width']}x{record['height']}" if record["width"] else "unknown"
        )
        lines.append(
            f"| `{_cell(record['path'])}` | {pixels} | `{record['sha256']}` | {_cell(record['inspection'])} | {_cell(record['notes'])} |"
        )
    return "\n".join(lines) + "\n", records


def _build_issue_log(
    stage: str, summary_path: Path | None, summary: dict[str, Any]
) -> str:
    failure_count = int(summary.get("failed") or 0)
    meaningful = summary.get("meaningful_data_changes") or []
    run_name = summary_path.parent.name if summary_path else "no retained run"
    return f"""# Settings parity issue log

## Owner finding retained

The owner rejected the previous Settings result: the shell correction did not establish visual or functional parity. The React pages were visually coarse, generic, and materially unlike the current NiceGUI Settings owners. This finding remains open until a new owner review; it is not erased by engineering screenshots or scores.

## Latest `{stage}` evidence run

- Run: `{run_name}`
- Captures: {summary.get("ok", 0)} ok, {failure_count} failed
- Meaningful configured-data changes observed: {len(meaningful)}
- Failure evidence: `failures/{run_name}/`
- Run evidence: `metrics/runs/{run_name}/`

## Retained first-run diagnosis

- The first authorised before run (`before-20260914T150733Z`) retained 38 page-specific failure bundles.
- 36 React captures failed because `Page.add_script_tag` was blocked by the application's CSP. The runner now evaluates axe in the automation world.
- Two NiceGUI Models captures treated the blocked diagnostic `/api/client-error` request as consequential. The runner now records this diagnostic separately while continuing to block it.
- Nine meaningful configured-data paths changed during that first run. Those artifacts and the pre/mid/post manifests remain retained; a replacement paired run must show zero meaningful changes before it is used as parity evidence.
"""


def _build_verification(
    stage: str,
    summary_path: Path | None,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    reviews: dict[str, Any],
) -> str:
    statuses = Counter(record["inspection"] for record in records)
    record_rows = summary.get("records") or []
    external = sum(
        int(row.get("blocked_external_requests") or 0) for row in record_rows
    )
    diagnostics = sum(
        int(row.get("blocked_diagnostic_requests") or 0) for row in record_rows
    )
    observational = sum(int(row.get("observational_posts") or 0) for row in record_rows)
    overflow = sum(int(row.get("overflow_items") or 0) for row in record_rows)
    axe = sum(int(row.get("axe_violations") or 0) for row in record_rows)
    run_name = summary_path.parent.name if summary_path else "missing"
    generated = datetime.now(timezone.utc).isoformat()
    checklist = reviews.get("_checklist", {})
    capture_clean = (
        len(record_rows) == 68
        and int(summary.get("failed") or 0) == 0
        and not (summary.get("meaningful_data_changes") or [])
    )
    inspected = bool(records) and all(
        record["inspection"] != "pending original-size inspection" for record in records
    )
    pages_verified = all(
        reviews.get(name.casefold(), {}).get("result") == "verified"
        for _, name, _, _ in PAGES
    )
    visual_gate = all(
        int(reviews.get(name.casefold(), {}).get("total") or 0) >= 9
        and all(
            int(score) >= 1
            for score in (reviews.get(name.casefold(), {}).get("scores") or {}).values()
        )
        and len((reviews.get(name.casefold(), {}).get("scores") or {})) == 5
        for _, name, _, _ in PAGES
    )
    inventory_path = EVIDENCE / "control-inventory.md"
    inventory_text = (
        inventory_path.read_text(encoding="utf-8") if inventory_path.is_file() else ""
    )
    unreviewed_match = re.search(r"\bunreviewed=(\d+)\b", inventory_text)
    controls_complete = bool(unreviewed_match and int(unreviewed_match.group(1)) == 0)
    final_checks = all(
        bool(checklist.get(name))
        for name in (
            "synthetic_functional",
            "additional_browser",
            "frontend_check",
            "changed_matrix",
            "clean_commits",
        )
    )
    if (
        capture_clean
        and inspected
        and pages_verified
        and visual_gate
        and controls_complete
        and final_checks
    ):
        engineering_result = (
            "**Ready for a new owner Settings review.** This engineering result "
            "does not record owner or Phase 4 acceptance."
        )
    else:
        engineering_result = (
            "Pending all page scores, zero unresolved controls, clean capture "
            "observations, focused checks, changed-source matrix, and original-size "
            "inspection. The final wording may only be **ready for a new owner "
            "Settings review**; this log never records owner or Phase 4 acceptance."
        )
    return f"""# Settings deep parity verification log

Generated: `{generated}`

## Fixed entry gate

- Required branch: `feat/unified-client-platform-phase-4-capability-migration`
- Starting HEAD: `{STARTING_HEAD}`
- Primary data directory: canonical configured directory validated by the guarded runner (not repeated in shareable logs)
- Primary process contract: one loopback process; NiceGUI then React sequentially; no seeding

## Latest `{stage}` capture

- Run: `{run_name}`
- Capture records: {len(record_rows)} total; {summary.get("ok", 0)} ok; {summary.get("failed", 0)} failed
- Meaningful configured-data changes: {len(summary.get("meaningful_data_changes") or [])}
- Blocked external browser requests: {external}
- Blocked diagnostic posts: {diagnostics}
- Allowed observational handshake posts: {observational}
- Overflow observations: {overflow}
- Axe violations: {axe}
- Image inspection statuses: {", ".join(f"{name}={count}" for name, count in sorted(statuses.items())) or "no images"}

## Commands

```powershell
uv run python tests/browser/settings_parity/run_visual.py --authorize-real-data-capture --data-dir '<canonical configured directory>' --engine chromium --channel msedge --viewports desktop,phone --pages all --stage {stage}
uv run python tests/browser/settings_parity/build_inventory.py --react-dom-dir .local/evidence/unified-client-platform/phase-4-settings-deep-parity/metrics/{stage}/react
uv run python tests/browser/settings_parity/build_evidence.py --stage {stage}
```

## Engineering result

{engineering_result}
"""


def _build_verification_checklist(
    stage: str,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    reviews: dict[str, Any],
) -> str:
    capture_clean = (
        len(summary.get("records") or []) == 68
        and int(summary.get("failed") or 0) == 0
        and not (summary.get("meaningful_data_changes") or [])
    )
    inspected = bool(records) and all(
        record["inspection"] != "pending original-size inspection" for record in records
    )
    pages_verified = all(
        reviews.get(name.casefold(), {}).get("result") == "verified"
        for _, name, _, _ in PAGES
    )
    checklist = reviews.get("_checklist", {})
    inventory_text = (
        (EVIDENCE / "control-inventory.md").read_text(encoding="utf-8")
        if (EVIDENCE / "control-inventory.md").is_file()
        else ""
    )
    unreviewed_match = re.search(r"\bunreviewed=(\d+)\b", inventory_text)
    controls_complete = bool(unreviewed_match and int(unreviewed_match.group(1)) == 0)
    pairs_present = capture_clean and all(
        (EVIDENCE / f"{stage}/react/{name.casefold()}-desktop-full.png").is_file()
        and (EVIDENCE / f"{stage}/react/{name.casefold()}-phone-full.png").is_file()
        and (
            nicegui_slug is None
            or (
                (
                    EVIDENCE / f"reference/nicegui/{nicegui_slug}-desktop-full.png"
                ).is_file()
                and (
                    EVIDENCE / f"reference/nicegui/{nicegui_slug}-phone-full.png"
                ).is_file()
            )
        )
        for _, name, nicegui_slug, _ in PAGES
    )

    def item(complete: bool, text: str) -> str:
        return f"- [{'x' if complete else ' '}] {text}"

    return "\n".join(
        [
            "# Settings deep parity verification checklist",
            "",
            item(
                capture_clean,
                "One-host real-data run produced 68 successful captures and zero meaningful configured-data changes.",
            ),
            item(
                pairs_present,
                "Desktop and phone paired evidence exists for all routes and all available NiceGUI owners.",
            ),
            item(
                inspected,
                "Every enumerated image has an original-size inspection decision.",
            ),
            item(
                pages_verified,
                "All 18 page review decisions record focused tests, scores, and engineering result `verified`.",
            ),
            item(
                controls_complete,
                "Control inventory has zero unreviewed controls; every matched, adapted, automatic-owner, or blocked disposition is explicit.",
            ),
            item(
                bool(checklist.get("synthetic_functional")),
                "Synthetic functional coverage is complete for save, validation, review, cancel, stale, receipt, and consequential states.",
            ),
            item(
                bool(checklist.get("additional_browser")),
                "Light / Blue / Compact, Firefox phone smoke, and keyboard-only traversal are recorded.",
            ),
            item(
                bool(checklist.get("frontend_check")),
                "`npm run check` passes on the final source cut.",
            ),
            item(
                bool(checklist.get("changed_matrix")),
                f"Changed-source matrix passes against `{STARTING_HEAD}`.",
            ),
            item(
                bool(checklist.get("clean_commits")),
                "Final tracked commits are coherent and the tracked working tree is clean.",
            ),
            "",
            "Engineering wording after every item is complete: **ready for a new owner Settings review**. This checklist does not establish owner or Phase 4 acceptance.",
            "",
        ]
    )


def build(stage: str, inspection_decisions: Path, page_reviews: Path) -> dict[str, Any]:
    summary_path, summary = _latest_summary(stage)
    inspections = _load(inspection_decisions, {})
    reviews = _load(page_reviews, {})
    gallery_md, gallery_html = _build_gallery(stage)
    inspection_log, image_records = _build_inspection_log(stage, inspections)
    _write(EVIDENCE / "paired-gallery.md", gallery_md)
    _write(EVIDENCE / "pairs/gallery.html", gallery_html)
    _write(EVIDENCE / "inspection-log.md", inspection_log)
    _write_json(EVIDENCE / "image-manifest.json", image_records)
    _write(EVIDENCE / "page-ledger.md", _build_page_ledger(stage, reviews))
    _write(EVIDENCE / "issue-log.md", _build_issue_log(stage, summary_path, summary))
    _write(
        EVIDENCE / "verification-log.md",
        _build_verification(stage, summary_path, summary, image_records, reviews),
    )
    _write(
        EVIDENCE / "verification-checklist.md",
        _build_verification_checklist(stage, summary, image_records, reviews),
    )
    result = {
        "stage": stage,
        "run": summary_path.parent.name if summary_path else None,
        "images": len(image_records),
        "pending_inspections": sum(
            1
            for record in image_records
            if record["inspection"] == "pending original-size inspection"
        ),
        "pages": len(PAGES),
    }
    _write_json(EVIDENCE / "evidence-build-summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("before", "candidate"), default="candidate")
    parser.add_argument(
        "--inspection-decisions",
        type=Path,
        default=EVIDENCE / "inspection-decisions.json",
    )
    parser.add_argument(
        "--page-reviews", type=Path, default=EVIDENCE / "page-review-decisions.json"
    )
    args = parser.parse_args()
    result = build(args.stage, args.inspection_decisions, args.page_reviews)
    print(
        f"built {result['pages']} page rows and {result['images']} image records; "
        f"{result['pending_inspections']} await original-size inspection"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
