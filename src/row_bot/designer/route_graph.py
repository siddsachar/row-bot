"""Phase 2.2.K — Route graph helpers for interactive designer modes.

Produces a lightweight graph description of a designer project's
interactive navigation so the editor can render a mini-map:

- ``build_route_graph(project)`` returns a dict with ``nodes`` and
  ``edges`` keyed off ``route_id``.
- ``route_graph_summary(project)`` returns a human-readable summary
  (e.g. counts of orphans, reachability from the entry route).

Nothing in this module imports NiceGUI; it is safe to call from
tests and from non-UI contexts.
"""

from __future__ import annotations

from typing import Any

from row_bot.designer.state import DesignerProject

INTERACTIVE_MODES = {"landing", "app_mockup", "storyboard"}


def _entry_route(project: DesignerProject) -> str:
    """Return the entry-point route_id for a project (first page)."""
    for page in project.pages:
        rid = (getattr(page, "route_id", "") or "").strip()
        if rid:
            return rid
    return ""


def build_route_graph(project: DesignerProject) -> dict[str, Any]:
    """Build a directed graph of routes from ``project.interactions``.

    Returns::

        {
          "mode": str,
          "entry": str,            # route_id of the first page
          "nodes": [                # stable page-order preserving
             {"route_id": str, "title": str, "index": int,
              "is_entry": bool, "out_degree": int, "in_degree": int,
              "reachable": bool}
          ],
          "edges": [                # navigate interactions only
             {"source": str, "target": str, "selector": str,
              "event": str, "transition": str}
          ],
          "orphans": [str],        # route_ids unreachable from entry
        }

    Non-interactive projects return ``mode="deck"`` with empty nodes/
    edges — callers can skip rendering the mini-map in that case.
    """

    mode = getattr(project, "mode", "deck") or "deck"
    if mode not in INTERACTIVE_MODES:
        return {
            "mode": mode,
            "entry": "",
            "nodes": [],
            "edges": [],
            "orphans": [],
        }

    entry = _entry_route(project)

    # Build node list in page order.
    route_ids: list[str] = []
    titles: dict[str, str] = {}
    indices: dict[str, int] = {}
    for idx, page in enumerate(project.pages):
        rid = (getattr(page, "route_id", "") or "").strip()
        if not rid:
            continue
        if rid not in indices:
            route_ids.append(rid)
            titles[rid] = (getattr(page, "title", "") or rid).strip() or rid
            indices[rid] = idx

    known = set(route_ids)

    # Collect navigate edges. Ignore interactions whose source/target
    # reference routes that no longer exist (e.g. after a page delete).
    edges: list[dict[str, Any]] = []
    out_degree: dict[str, int] = {rid: 0 for rid in route_ids}
    in_degree: dict[str, int] = {rid: 0 for rid in route_ids}
    for interaction in getattr(project, "interactions", []) or []:
        if (getattr(interaction, "action", "") or "") != "navigate":
            continue
        src = (getattr(interaction, "source_route", "") or "").strip()
        tgt = (getattr(interaction, "target", "") or "").strip()
        if not src or not tgt:
            continue
        if src not in known or tgt not in known:
            continue
        edges.append({
            "source": src,
            "target": tgt,
            "selector": (getattr(interaction, "selector", "") or "").strip(),
            "event": (getattr(interaction, "event", "") or "click").strip() or "click",
            "transition": (getattr(interaction, "transition", "") or "fade").strip() or "fade",
        })
        out_degree[src] = out_degree.get(src, 0) + 1
        in_degree[tgt] = in_degree.get(tgt, 0) + 1

    # Reachability BFS from entry.
    reachable: set[str] = set()
    if entry and entry in known:
        adjacency: dict[str, list[str]] = {rid: [] for rid in route_ids}
        for edge in edges:
            adjacency[edge["source"]].append(edge["target"])
        stack = [entry]
        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            stack.extend(adjacency.get(node, []))

    nodes = [
        {
            "route_id": rid,
            "title": titles[rid],
            "index": indices[rid],
            "is_entry": rid == entry,
            "out_degree": out_degree.get(rid, 0),
            "in_degree": in_degree.get(rid, 0),
            "reachable": rid in reachable,
        }
        for rid in route_ids
    ]

    orphans = [rid for rid in route_ids if rid not in reachable and rid != entry]

    return {
        "mode": mode,
        "entry": entry,
        "nodes": nodes,
        "edges": edges,
        "orphans": orphans,
    }


def route_graph_summary(project: DesignerProject) -> str:
    """Return a short human-readable summary of the graph."""
    graph = build_route_graph(project)
    if not graph["nodes"]:
        return "No interactive screens."
    total = len(graph["nodes"])
    edges = len(graph["edges"])
    orphans = len(graph["orphans"])
    entry = graph["entry"] or "(none)"
    parts = [
        f"{total} screens",
        f"{edges} links",
        f"entry: {entry}",
    ]
    if orphans:
        parts.append(f"{orphans} orphan{'s' if orphans != 1 else ''}")
    return " · ".join(parts)
