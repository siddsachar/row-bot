"""Unified Review orchestrator — merges critique (layout/readability) and
brand-lint (brand policy) findings into a single report, and routes fixes
to the matching deterministic repairer or an agent request.

Pure logic (no NiceGUI). The dialog lives in ``designer.review_dialog``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass, asdict
from typing import Any, Callable, Iterable

from row_bot.designer.critique import critique_page_html, apply_page_repairs
from row_bot.designer.brand_lint import (
    lint_page,
    apply_brand_repairs_to_html,
    _BRAND_AUTO_CATEGORIES,
)
from row_bot.designer.session import prepare_project_mutation
from row_bot.designer.storage import save_project


_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}

# Categories that are auto-fixable per source.
_CRITIQUE_AUTO = {"hierarchy", "overflow", "contrast", "readability", "spacing"}


@dataclass
class ReviewFinding:
    id: str
    source: str          # "critique" | "brand_lint"
    category: str
    severity: str        # low | medium | high
    message: str
    suggested_fix: str
    page_index: int
    element_ref: str = ""
    selector_hint: str = ""
    excerpt: str = ""
    auto_fixable: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _finding_id(source: str, category: str, page_index: int,
                element_ref: str, message: str) -> str:
    key = f"{source}|{category}|{page_index}|{element_ref}|{message}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _summary(findings: list[ReviewFinding], cat_counts: dict[str, int]) -> str:
    if not findings:
        return "No issues detected."
    parts = ", ".join(f"{cat_counts[k]} {k}" for k in sorted(cat_counts))
    return f"{len(findings)} issue(s): {parts}"


def build_review_report(
    project,
    *,
    scope: str = "page",
    page_index: int | None = None,
    dismissed: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run critique + brand-lint over the requested scope and return a merged
    report. ``scope`` is ``"page"`` (single page) or ``"project"`` (all pages).
    """
    dismissed_set = set(dismissed or [])
    pages = list(getattr(project, "pages", None) or [])
    if not pages:
        return {
            "findings": [], "summary": "No pages to review.",
            "severity_counts": {"low": 0, "medium": 0, "high": 0},
            "category_counts": {}, "score": 100, "scope": scope,
            "page_indices": [],
        }

    if scope == "page":
        target = project.active_page if page_index is None else page_index
        indices = [max(0, min(int(target), len(pages) - 1))]
    else:
        indices = list(range(len(pages)))

    brand = getattr(project, "brand", None)
    canvas_w = int(getattr(project, "canvas_width", 1920) or 1920)
    canvas_h = int(getattr(project, "canvas_height", 1080) or 1080)

    findings: list[ReviewFinding] = []
    critique_scores: list[int] = []

    for idx in indices:
        page = pages[idx]
        html = getattr(page, "html", "") or ""

        # Critique findings
        try:
            report = critique_page_html(html, canvas_w, canvas_h)
            critique_scores.append(int(report.get("score", 0)))
            for f in report.get("findings", []):
                element_ref = f.get("element_ref", "") or ""
                fid = _finding_id("critique", f["category"], idx, element_ref, f["message"])
                if fid in dismissed_set:
                    continue
                findings.append(ReviewFinding(
                    id=fid, source="critique",
                    category=f["category"], severity=f.get("severity", "low"),
                    message=f.get("message", ""),
                    suggested_fix=f.get("suggested_fix", ""),
                    page_index=idx,
                    element_ref=element_ref,
                    selector_hint=f.get("selector_hint", ""),
                    excerpt=f.get("excerpt", ""),
                    auto_fixable=(
                        f["category"] in _CRITIQUE_AUTO
                        and f.get("auto_fixable", True)
                    ),
                ))
        except Exception:
            pass

        # Brand-lint findings
        try:
            for lf in lint_page(html, brand=brand, page_index=idx):
                fid = _finding_id("brand_lint", lf.category, idx,
                                  lf.element_ref, lf.message)
                if fid in dismissed_set:
                    continue
                findings.append(ReviewFinding(
                    id=fid, source="brand_lint",
                    category=lf.category, severity=lf.severity,
                    message=lf.message, suggested_fix=lf.suggested_fix,
                    page_index=idx,
                    element_ref=lf.element_ref,
                    selector_hint=lf.selector_hint,
                    excerpt=lf.excerpt,
                    auto_fixable=(lf.category in _BRAND_AUTO_CATEGORIES),
                ))
        except Exception:
            pass

    # Dedup: same (category, page, element_ref, message) keeps higher severity.
    by_key: dict[tuple, ReviewFinding] = {}
    for f in findings:
        key = (f.category, f.page_index, f.element_ref, f.message)
        prev = by_key.get(key)
        if prev is None or _SEVERITY_RANK.get(f.severity, 0) > _SEVERITY_RANK.get(prev.severity, 0):
            by_key[key] = f
    deduped = list(by_key.values())

    # Stable ordering: severity desc, page asc, category.
    deduped.sort(key=lambda f: (
        -_SEVERITY_RANK.get(f.severity, 0), f.page_index, f.source, f.category
    ))

    sev_counts = {"low": 0, "medium": 0, "high": 0}
    cat_counts: dict[str, int] = {}
    for f in deduped:
        sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1
        cat_counts[f.category] = cat_counts.get(f.category, 0) + 1

    avg_score = (int(sum(critique_scores) / len(critique_scores))
                 if critique_scores else 100)

    return {
        "findings": [f.to_dict() for f in deduped],
        "summary": _summary(deduped, cat_counts),
        "severity_counts": sev_counts,
        "category_counts": cat_counts,
        "score": avg_score,
        "scope": scope,
        "page_indices": indices,
    }


def _apply_to_page(project, idx: int, source: str, categories: list[str]) -> tuple[bool, list[dict]]:
    page = project.pages[idx]
    html = page.html
    canvas_w = int(getattr(project, "canvas_width", 1920) or 1920)
    canvas_h = int(getattr(project, "canvas_height", 1080) or 1080)
    if source == "critique":
        new_html, changes = apply_page_repairs(html, canvas_w, canvas_h, categories)
    else:
        new_html, changes = apply_brand_repairs_to_html(
            html, getattr(project, "brand", None), categories,
            page_title=getattr(page, "title", "") or "",
        )
    if not changes or new_html == html:
        return False, []
    page.html = new_html
    page.thumbnail_b64 = None
    return True, changes


def apply_fix(project, finding: dict) -> dict[str, Any]:
    """Apply a safe fix for a single finding. Note: deterministic repairers
    operate per-category on the full page, so this fixes every instance of
    that category on that page, not only the one finding."""
    if not finding.get("auto_fixable"):
        return {"applied": False, "changes": [], "reason": "not auto-fixable"}
    idx = int(finding["page_index"])
    pages = getattr(project, "pages", None) or []
    if not (0 <= idx < len(pages)):
        return {"applied": False, "changes": [], "reason": "invalid page"}
    source = finding["source"]
    category = finding["category"]
    prepare_project_mutation(project, f"review_fix_{source}_{category}_p{idx}")
    ok, changes = _apply_to_page(project, idx, source, [category])
    if not ok:
        return {"applied": False, "changes": []}
    project.manual_edits.append(
        f"Review: applied {source}/{category} fix on page {idx + 1} "
        f"({len(changes)} change(s))."
    )
    save_project(project)
    return {"applied": True, "changes": changes}


def apply_fixes_bulk(project, findings: list[dict]) -> dict[str, Any]:
    """Apply all auto-fixable findings grouped by (page, source)."""
    auto = [f for f in findings if f.get("auto_fixable")]
    if not auto:
        return {"applied": 0, "changes": [], "pages_touched": 0}

    # group
    by_page: dict[int, dict[str, set[str]]] = {}
    for f in auto:
        idx = int(f["page_index"])
        src = f["source"]
        by_page.setdefault(idx, {"critique": set(), "brand_lint": set()})[src].add(f["category"])

    prepare_project_mutation(project, "review_fix_bulk")
    total_changes: list[dict] = []
    pages_touched = 0
    pages = getattr(project, "pages", None) or []

    for idx, by_src in by_page.items():
        if not (0 <= idx < len(pages)):
            continue
        page_changed = False
        for source in ("critique", "brand_lint"):
            cats = sorted(by_src.get(source) or [])
            if not cats:
                continue
            ok, changes = _apply_to_page(project, idx, source, cats)
            if ok:
                total_changes.extend(changes)
                page_changed = True
        if page_changed:
            pages_touched += 1

    if total_changes:
        project.manual_edits.append(
            f"Review: bulk-applied {len(total_changes)} safe fix(es) "
            f"across {pages_touched} page(s)."
        )
        save_project(project)

    return {
        "applied": len(total_changes),
        "changes": total_changes,
        "pages_touched": pages_touched,
    }


def build_ai_fix_request(finding: dict) -> str:
    """Craft a focused instruction the agent can act on."""
    page_num = int(finding.get("page_index", 0)) + 1
    category = finding.get("category", "issue")
    msg = finding.get("message", "")
    suggested = finding.get("suggested_fix", "")
    selector = finding.get("selector_hint", "")
    excerpt = finding.get("excerpt", "")
    parts = [f"Fix this {category} issue on page {page_num}: {msg}"]
    if selector:
        parts.append(f"Element: {selector}.")
    if excerpt:
        parts.append(f'Text: "{excerpt}".')
    if suggested:
        parts.append(f"Guidance: {suggested}")
    parts.append("Use designer_update_page or designer_restyle_element as appropriate.")
    return " ".join(parts)


def request_ai_fix(finding: dict, send_agent_message: Callable) -> str:
    """Dispatch a focused AI fix request. ``send_agent_message`` may be
    sync or async; in the async case we schedule it on the running loop.
    Returns the message that was sent (or queued)."""
    request = build_ai_fix_request(finding)
    try:
        result = send_agent_message(request)
        if inspect.iscoroutine(result):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(result)
                else:
                    loop.run_until_complete(result)
            except RuntimeError:
                # No loop — fire and forget in a new one
                asyncio.run(result)
    except Exception:
        pass
    return request
