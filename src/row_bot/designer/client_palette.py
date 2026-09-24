"""Bounded Designer command palette for a currently bound artifact."""

from __future__ import annotations

from row_bot.designer.client_service import ArtifactError, read_artifact
from row_bot.designer.command_palette import (
    build_palette_items,
    filter_items,
    tool_prefill,
)
from row_bot.designer.preview import _ensure_page_route_ids


def read_palette(
    resource_id: str, *, expected_revision: str, query: str = ""
) -> dict[str, object]:
    """Search the shared tool/page/asset catalog on an explicit palette open."""

    if len(query) > 128:
        raise ArtifactError("invalid_query")
    project = read_artifact(resource_id)
    if project.updated_at != expected_revision:
        raise ArtifactError("resource_revision_conflict")
    try:
        from row_bot.designer.tool import DesignerTool

        tool_names = [item.name for item in DesignerTool().as_langchain_tools()]
        tools_available = True
    except Exception:
        tool_names = []
        tools_available = False
    items = build_palette_items(project, tool_names=tool_names)
    selected = filter_items(items, query, limit=len(items))
    route_ids = _ensure_page_route_ids(project)
    rows = []
    for item in selected:
        if item.category == "page":
            identity = route_ids[int(item.payload)]
            prefill = ""
        elif item.category == "tool":
            identity = str(item.payload)
            prefill = tool_prefill(identity)
        else:
            identity = str(item.payload)
            prefill = f"Reuse asset {identity} on the current page: "
        if not identity or len(identity) > 128:
            continue
        rows.append(
            {
                "category": item.category,
                "label": item.label[:256],
                "hint": item.hint[:128],
                "identity": identity,
                "prefill": prefill[:256],
            }
        )
        if len(rows) > 60:
            break
    return {
        "resource_id": project.id,
        "resource_revision": project.updated_at,
        "tools_available": tools_available,
        "items": rows[:60],
        "has_more_matches": len(rows) > 60,
    }
