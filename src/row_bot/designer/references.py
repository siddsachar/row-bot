"""Designer project reference helpers."""

from __future__ import annotations

from row_bot.designer.state import DesignerProject, DesignerReference


def find_project_reference(project: DesignerProject, reference_ref: str) -> DesignerReference | None:
    """Find a reference by id, exact name, partial name, or recency aliases."""
    ref = (reference_ref or "").strip().lower()
    if not ref:
        return None
    if ref in {"last", "latest", "most recent"} and project.references:
        return project.references[-1]

    for reference in project.references:
        if reference.id.lower() == ref:
            return reference
    for reference in project.references:
        if reference.name.lower() == ref:
            return reference
    for reference in project.references:
        if ref in reference.name.lower():
            return reference
    return None

