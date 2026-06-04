"""Run live Skills Hub preview/install/uninstall checks against public sources."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _path in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


DEFAULT_QUERIES = {
    "github": "python",
    "skills_sh": "python",
    "browse_sh": "trails",
    "lobehub": "academic",
    "clawhub": "",
}


@dataclass
class MatrixRow:
    source: str
    status: str
    entry_name: str = ""
    entry_url: str = ""
    install_ref: str = ""
    bundle_source: str = ""
    bundle_hash: str = ""
    file_count: int = 0
    installed_skill: str = ""
    installed_path: str = ""
    enabled_after_install: bool | None = None
    instructions_match: bool | None = None
    blocked: bool | None = None
    warnings: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class VisibleCheckRow:
    source: str
    mode: str
    query: str = ""
    checked: int = 0
    previewable: int = 0
    unavailable: int = 0
    errors: list[str] = field(default_factory=list)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=".tmp/skills_hub_live_import_matrix")
    parser.add_argument("--output", default=".tmp/skills_hub_live_import_matrix/report.json")
    parser.add_argument("--sources", default="github,skills_sh,browse_sh,lobehub,clawhub")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--visible-limit", type=int, default=8)
    parser.add_argument("--skip-visible-checks", action="store_true")
    parser.add_argument("--keep-installed", action="store_true")
    args = parser.parse_args()

    data_dir = pathlib.Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ROW_BOT_DATA_DIR"] = str(data_dir)

    from row_bot.skills_hub.installer import install_bundle, uninstall_skill
    from row_bot.skills_hub.provenance import get_record
    from row_bot.skills_hub.scanner import scan_bundle
    from row_bot.skills_hub.source_registry import SkillSourceRegistry
    from row_bot.skills_hub.sources import parse_skill_markdown
    import row_bot.skills as skills

    registry = SkillSourceRegistry()
    rows: list[MatrixRow] = []
    visible_rows: list[VisibleCheckRow] = []
    for source_id in [item.strip() for item in args.sources.split(",") if item.strip()]:
        source = registry.source(source_id)
        if source is None:
            rows.append(MatrixRow(source_id, "fail", error="Source adapter not registered."))
            continue
        if not args.skip_visible_checks:
            visible_rows.extend(_visible_checks(source, source_id, DEFAULT_QUERIES.get(source_id, ""), args.visible_limit))
        row = MatrixRow(source_id, "fail")
        try:
            entries = _candidate_entries(source, source_id, DEFAULT_QUERIES.get(source_id, ""), args.limit)
            if not entries:
                row.error = "No browse/search entries returned."
                rows.append(row)
                continue
            last_error = ""
            for entry in entries:
                row.entry_name = entry.name
                row.entry_url = entry.url
                row.install_ref = entry.install_ref
                try:
                    bundle = source.inspect(entry)
                    scan = scan_bundle(bundle)
                    result = install_bundle(bundle, enabled=False, conflict_policy="rename")
                    if not result.success:
                        last_error = result.message
                        continue
                    skill_name = result.skill_name
                    skills.load_skills()
                    installed_path = skills.USER_SKILLS_DIR / skill_name / "SKILL.md"
                    installed_text = installed_path.read_text(encoding="utf-8") if installed_path.exists() else ""
                    _installed_meta, installed_instructions = parse_skill_markdown(installed_text)
                    _bundle_meta, bundle_instructions = parse_skill_markdown(bundle.primary_file().text if bundle.primary_file() else "")
                    record = get_record(skill_name)
                    row.status = "pass"
                    row.bundle_source = bundle.source
                    row.bundle_hash = bundle.content_hash
                    row.file_count = len(bundle.files)
                    row.installed_skill = skill_name
                    row.installed_path = str(installed_path)
                    row.enabled_after_install = skills.is_enabled(skill_name)
                    row.instructions_match = installed_instructions.strip() == bundle_instructions.strip()
                    row.blocked = scan.blocked
                    row.warnings = scan.warnings
                    if record is None:
                        row.status = "fail"
                        row.error = "Install succeeded but provenance record was not written."
                    elif row.enabled_after_install:
                        row.status = "fail"
                        row.error = "Installed skill was enabled; public imports must default Off."
                    elif not row.instructions_match:
                        row.status = "fail"
                        row.error = "Installed instructions differ from preview bundle instructions."
                    if not args.keep_installed:
                        uninstall_skill(skill_name)
                    break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue
            if row.status != "pass" and not row.error:
                row.error = last_error or "No previewable/installable entry found."
        except Exception as exc:
            row.error = f"{type(exc).__name__}: {exc}"
            row.warnings = traceback.format_exc().splitlines()[-6:]
        rows.append(row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "rows": [asdict(row) for row in rows],
        "visible_checks": [asdict(row) for row in visible_rows],
    }
    output = pathlib.Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text(_markdown_report(report), encoding="utf-8")
    print(json.dumps(report, indent=2))
    visible_ok = all(row.unavailable == 0 for row in visible_rows)
    return 0 if all(row.status == "pass" for row in rows) and visible_ok else 1


def _candidate_entries(source: object, source_id: str, query: str, limit: int):
    if query and callable(getattr(source, "search", None)):
        entries = list(source.search(query, limit=limit))
    elif callable(getattr(source, "browse", None)):
        result = source.browse(limit=limit)
        entries = list(result.entries)
    else:
        entries = []
    if not entries and callable(getattr(source, "browse", None)):
        result = source.browse(limit=limit)
        entries = list(result.entries)
    entries.sort(key=lambda entry: int(entry.metadata.get("install_count") or 0), reverse=True)
    return entries[:limit]


def _visible_checks(source: object, source_id: str, query: str, limit: int) -> list[VisibleCheckRow]:
    rows: list[VisibleCheckRow] = []
    for mode, active_query in (("browse", ""), ("search", query)):
        if mode == "search" and not active_query:
            continue
        row = VisibleCheckRow(source_id, mode, active_query)
        try:
            if mode == "browse" and callable(getattr(source, "browse", None)):
                entries = list(source.browse(limit=limit).entries)
            elif callable(getattr(source, "search", None)):
                entries = list(source.search(active_query, limit=limit))
            else:
                entries = []
            for entry in entries[:limit]:
                row.checked += 1
                try:
                    bundle = source.inspect(entry)
                    primary = bundle.primary_file()
                    if primary is not None and primary.text.strip():
                        row.previewable += 1
                    else:
                        row.unavailable += 1
                        row.errors.append(f"{entry.name}: empty preview")
                except Exception as exc:
                    row.unavailable += 1
                    row.errors.append(f"{entry.name}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            row.errors.append(f"list failed: {type(exc).__name__}: {exc}")
        rows.append(row)
    return rows


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Skills Hub Live Import Matrix",
        "",
        f"Generated: {report['generated_at']}",
        f"Data dir: `{report['data_dir']}`",
        "",
        "| Source | Status | Entry | Files | Default Off | Instructions Match | Error |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            "| {source} | {status} | {entry} | {files} | {off} | {match} | {error} |".format(
                source=row["source"],
                status=row["status"],
                entry=(row["entry_name"] or "").replace("|", "\\|"),
                files=row["file_count"],
                off=row["enabled_after_install"] is False,
                match=row["instructions_match"],
                error=(row["error"] or "").replace("|", "\\|"),
            )
        )
    if report.get("visible_checks"):
        lines.extend([
            "",
            "## Visible Preview Checks",
            "",
            "| Source | Mode | Query | Checked | Previewable | Unavailable |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ])
        for row in report["visible_checks"]:
            lines.append(
                "| {source} | {mode} | {query} | {checked} | {previewable} | {unavailable} |".format(
                    source=row["source"],
                    mode=row["mode"],
                    query=(row["query"] or "").replace("|", "\\|"),
                    checked=row["checked"],
                    previewable=row["previewable"],
                    unavailable=row["unavailable"],
                )
            )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
