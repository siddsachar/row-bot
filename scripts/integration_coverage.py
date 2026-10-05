"""Coverage KPI: how much of the catalog Row-Bot can connect today, and what blocks the rest.

Runs ``plans.compute`` over every local catalog entry (curated recipes, the whole
shipped Registry mirror, the featured skills library, the examples and, when a saved
Hermes catalog is given, its packages) in a throwaway data folder. Nothing is fetched
and no user data is read. Writes ``coverage.json`` (per entry: complete, or the exact
reason) and ``coverage.md`` (counts per source and per featured app).

  uv run python scripts/integration_coverage.py --output .local/integrations-phases/phase-2/coverage
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
HERMES = ROOT / ".local" / "integration-reach-audit" / "hermes.json"
# What each pinned Hermes package is, read from its manifest only (as a catalog update does).
MANIFESTS = HERMES.with_name("hermes-manifests.json")
# Complete, but a hosted server that declares no sign-in: the first test shows whether it needs one.
UNDECLARED = "complete; hosted, sign-in not declared"


def _entries(hermes: Path | None):
    """Every local catalog entry as (row, reference), from the same adapters search uses."""
    from row_bot.integrations import index, sources
    from row_bot.mcp_client.marketplace import CURATED_STARTER_CATALOG
    for entry in CURATED_STARTER_CATALOG:
        yield sources.SOURCES["recommended"].row(entry)
    for entry in index.rows():
        yield sources.SOURCES["official"].row(entry)
    for skill in sources.featured_skills().values():
        yield sources.SOURCES["featured_skills"].row(skill, set())
    for example in sources._EXAMPLES:
        yield sources.SOURCES["examples"].row(*example)
    if hermes:
        from row_bot.plugins import hermes_catalog
        for entry in hermes_catalog.read_catalog()["entries"]:
            yield sources.SOURCES["hermes"].row(entry)


def _outcome(row: dict, reference: dict) -> str:
    """'' for a complete plan, else the exact reason the plan (or the listing) gives."""
    from row_bot.integrations import facts, plans
    facts.finish(row)
    notes = [b["message"] for b in row["blockers"] if b["message"]]
    if row["compatibility"] == "unsupported":  # Listed with a reason; a plan is never offered.
        return notes[-1] if notes else "unsupported"
    try:
        plan = plans.compute(row, reference)
    except Exception as exc:
        return str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    if plan is None:
        return "nothing to do"
    if plan["supported"] and plan["consent"]["destinations"] and not any(s["type"] in {"sign_in", "inputs"} for s in plan["steps"]):
        return "" if row["auth_requirement"] == "none" else UNDECLARED
    return "" if plan["supported"] else plan["unsupported_reason"] or "unsupported"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp" / "integration-coverage")
    parser.add_argument("--hermes", type=Path, default=HERMES if HERMES.exists() else None,
                        help="a saved Hermes catalog document (entries/removed) to include")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="row-bot-coverage-") as data:
        os.environ["ROW_BOT_DATA_DIR"] = data
        if args.hermes:
            shutil.copyfile(args.hermes, Path(data) / "hermes_catalog_cache.json")
        if args.hermes and MANIFESTS.exists():
            from row_bot.plugins.portable import SCHEMA
            classified = {}
            for entry in json.loads(MANIFESTS.read_text(encoding="utf-8"))["entries"]:
                manifest = entry.get("manifest") or {}
                portable = isinstance(manifest.get("data"), dict) and manifest["data"].get("$schema") == SCHEMA
                if manifest.get("status") in {200, 404}:
                    classified[entry["sha"] + ":" + entry.get("subdir", "")] = "portable" if portable else "native"
            (Path(data) / "hermes_classification.json").write_text(json.dumps(classified), encoding="utf-8")
        from row_bot.integrations import apps, index
        mirror = index.ensure()
        reasons: Counter = Counter()
        by_source: dict = defaultdict(Counter)
        by_app: dict = defaultdict(Counter)
        rows = []
        for row, reference in _entries(args.hermes):
            reason = _outcome(row, reference)
            reasons[reason] += 1
            by_source[row["source"]][reason] += 1
            if row["app"]:
                by_app[row["app"]["id"]][reason] += 1
            rows.append([row["id"], row["app"]["id"] if row["app"] else "", reason])
    table = sorted(reasons, key=lambda reason: (reason != "", -reasons[reason], reason))
    known = apps.catalog()[0]
    featured = sorted((app for app in known.values() if app.featured_rank), key=lambda app: app.featured_rank)
    complete = reasons[""] + reasons[UNDECLARED]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "registry": {key: mirror[key] for key in ("count", "digest", "captured_at", "watermark")},
        "totals": {"entries": len(rows), "complete": complete, "blocked": len(rows) - complete},
        "reasons": {reason or "complete": reasons[reason] for reason in table},
        "by_source": {source: {reason or "complete": n for reason, n in counts.most_common()} for source, counts in sorted(by_source.items())},
        "featured_apps": {app.id: {"rank": app.featured_rank, "complete": by_app[app.id][""] + by_app[app.id][UNDECLARED],
                                   "entries": sum(by_app[app.id].values()),
                                   "reasons": {r: n for r, n in by_app[app.id].most_common() if r}} for app in featured},
        "columns": ["id", "app", "reason (empty: complete)"],
        "entries": rows,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    connectable = [app for app in featured if by_app[app.id][""] or by_app[app.id][UNDECLARED]]
    placeholders = [app for app in featured if app.placeholder and not sum(by_app[app.id].values())]
    lines = [
        "# Integration coverage", "",
        f"Generated {report['generated_at']} from the shipped Registry mirror ({mirror['count']:,} records, watermark "
        f"{mirror['watermark']}), {len(rows):,} catalog entries in all.", "",
        f"- **Complete plans:** {complete:,} of {len(rows):,} entries ({complete / max(len(rows), 1):.1%}), of which "
        f"{reasons[UNDECLARED]:,} are hosted servers that declare no sign-in (their first test shows whether they need one).",
        f"- **Featured apps with at least one complete plan:** {len(connectable)} of {len(featured)} "
        f"({len(placeholders)} more are accounts or channels that join Apps in Phase 5).", "",
        "## By source", "", "| Source | Entries | Complete | Top blocker |", "| --- | ---: | ---: | --- |",
    ]
    for source, counts in sorted(by_source.items(), key=lambda item: -sum(item[1].values())):
        blocker = next((f"{r} ({n:,})" for r, n in counts.most_common() if r and r != UNDECLARED), "")
        lines.append(f"| {source} | {sum(counts.values()):,} | {counts[''] + counts[UNDECLARED]:,} | {blocker} |")
    lines += ["", "## Every reason", "", "| Reason | Entries |", "| --- | ---: |"]
    lines += [f"| {reason or 'complete'} | {reasons[reason]:,} |" for reason in table]
    lines += ["", "## Featured apps without a complete plan", "", "| Rank | App | Entries | Reasons |", "| ---: | --- | ---: | --- |"]
    for app in featured:
        if app not in connectable:
            why = "; ".join(f"{r} ({n})" for r, n in by_app[app.id].most_common()) or "no catalog entry yet (account or channel)"
            lines.append(f"| {app.featured_rank} | {app.name} | {sum(by_app[app.id].values())} | {why} |")
    (args.output / "coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{complete:,} of {len(rows):,} entries complete; {len(connectable)} of {len(featured)} featured apps -> {args.output}")


if __name__ == "__main__":
    main()
