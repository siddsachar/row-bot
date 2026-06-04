"""Phase 2.2.L — Agent-mutation diff helpers.

Utilities for surfacing the most recent agent-initiated change to the
editor as a reviewable diff. All helpers are pure and do not touch
NiceGUI; the editor wires them into a dialog.

- ``compute_html_diff(before, after, *, context_lines=3)`` returns a
  unified diff string (or an empty string when the two inputs are
  identical).
- ``find_last_agent_snapshot(project)`` returns the newest snapshot
  summary with ``author == "agent"`` or ``None``.
- ``diff_last_agent_change(project)`` returns a structured dict
  describing per-page HTML deltas between the last agent snapshot
  and the current project state.
"""

from __future__ import annotations

import difflib
from typing import Any, Optional

from row_bot.designer.history import list_snapshots, read_snapshot
from row_bot.designer.state import DesignerProject


def compute_html_diff(
    before: str,
    after: str,
    *,
    context_lines: int = 3,
    before_label: str = "before",
    after_label: str = "after",
) -> str:
    """Return a unified diff of ``before`` vs ``after``.

    Empty string when the inputs are identical. Both values are split
    on newlines preserving line endings.
    """
    if before == after:
        return ""
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=before_label,
        tofile=after_label,
        n=max(0, int(context_lines)),
    )
    return "".join(diff)


def _count_line_changes(unified_diff: str) -> tuple[int, int]:
    """Return ``(added, removed)`` line counts from a unified diff."""
    added = 0
    removed = 0
    for line in (unified_diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def find_last_agent_snapshot(project: DesignerProject) -> Optional[dict]:
    """Return the newest snapshot summary authored by the agent, or None."""
    for entry in list_snapshots(project.id):
        if (entry.get("author") or "user") == "agent":
            return entry
    return None


def diff_last_agent_change(project: DesignerProject) -> dict[str, Any]:
    """Compare the current project to the last agent snapshot.

    Returns a dict with::

        {
          "available": bool,
          "reason": str,
          "snapshot_id": str,
          "label": str,
          "timestamp": float,
          "pages": [
             {
               "page_index": int,
               "title": str,
               "route_id": str,
               "change": "added" | "removed" | "modified" | "unchanged",
               "lines_added": int,
               "lines_removed": int,
               "unified_diff": str,
             }
          ],
          "total_added": int,
          "total_removed": int,
        }
    """

    latest = find_last_agent_snapshot(project)
    if latest is None:
        return {
            "available": False,
            "reason": "No recorded agent changes yet.",
            "pages": [],
            "total_added": 0,
            "total_removed": 0,
        }

    snap = read_snapshot(project.id, latest["id"])
    if snap is None:
        return {
            "available": False,
            "reason": "Agent snapshot could not be loaded.",
            "pages": [],
            "total_added": 0,
            "total_removed": 0,
        }

    before_pages = snap.get("pages") or []
    after_pages = project.pages

    page_entries: list[dict[str, Any]] = []
    total_added = 0
    total_removed = 0

    # Pair pages by index. Extra pages on either side become added /
    # removed entries so the editor can call them out.
    max_len = max(len(before_pages), len(after_pages))
    for idx in range(max_len):
        before = before_pages[idx] if idx < len(before_pages) else None
        after = after_pages[idx] if idx < len(after_pages) else None

        if before is None and after is not None:
            after_html = getattr(after, "html", "") or ""
            diff = compute_html_diff("", after_html,
                                     before_label="(none)",
                                     after_label=f"page-{idx + 1}")
            added, removed = _count_line_changes(diff)
            page_entries.append({
                "page_index": idx,
                "title": getattr(after, "title", "") or f"Page {idx + 1}",
                "route_id": getattr(after, "route_id", "") or "",
                "change": "added",
                "lines_added": added,
                "lines_removed": removed,
                "unified_diff": diff,
            })
            total_added += added
            total_removed += removed
            continue

        if after is None and before is not None:
            before_html = before.get("html", "") or ""
            diff = compute_html_diff(before_html, "",
                                     before_label=f"page-{idx + 1}",
                                     after_label="(deleted)")
            added, removed = _count_line_changes(diff)
            page_entries.append({
                "page_index": idx,
                "title": before.get("title", "") or f"Page {idx + 1}",
                "route_id": before.get("route_id", "") or "",
                "change": "removed",
                "lines_added": added,
                "lines_removed": removed,
                "unified_diff": diff,
            })
            total_added += added
            total_removed += removed
            continue

        before_html = (before or {}).get("html", "") or ""
        after_html = getattr(after, "html", "") or ""
        if before_html == after_html:
            page_entries.append({
                "page_index": idx,
                "title": getattr(after, "title", "") or f"Page {idx + 1}",
                "route_id": getattr(after, "route_id", "") or "",
                "change": "unchanged",
                "lines_added": 0,
                "lines_removed": 0,
                "unified_diff": "",
            })
            continue

        diff = compute_html_diff(
            before_html,
            after_html,
            before_label=f"page-{idx + 1} (before)",
            after_label=f"page-{idx + 1} (after)",
        )
        added, removed = _count_line_changes(diff)
        page_entries.append({
            "page_index": idx,
            "title": getattr(after, "title", "") or f"Page {idx + 1}",
            "route_id": getattr(after, "route_id", "") or "",
            "change": "modified",
            "lines_added": added,
            "lines_removed": removed,
            "unified_diff": diff,
        })
        total_added += added
        total_removed += removed

    return {
        "available": True,
        "reason": "",
        "snapshot_id": latest["id"],
        "label": latest.get("label", ""),
        "timestamp": float(latest.get("timestamp", 0.0) or 0.0),
        "pages": page_entries,
        "total_added": total_added,
        "total_removed": total_removed,
    }
