"""Designer project reference helpers."""

from __future__ import annotations

import hashlib
import mimetypes
import pathlib

from row_bot.designer.state import DesignerProject, DesignerReference
from row_bot.designer.storage import delete_reference_bytes, save_reference_bytes
from row_bot.ui.constants import DATA_EXTENSIONS, IMAGE_EXTENSIONS, TEXT_EXTENSIONS
from row_bot.ui.helpers import process_attached_files

SUMMARY_LIMIT = 320
EXCERPT_LIMIT = 4000


def _reference_kind(name: str) -> str:
    suffix = pathlib.Path(name).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in DATA_EXTENSIONS:
        return "data"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "file"


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _build_reference_summary(context_text: str, warnings: list[str]) -> tuple[str, str]:
    excerpt = (context_text or "").strip()
    if len(excerpt) > EXCERPT_LIMIT:
        excerpt = excerpt[:EXCERPT_LIMIT].rstrip() + "\n[Excerpt truncated]"

    body = excerpt.split("\n", 1)[1].strip() if "\n" in excerpt else excerpt
    summary_source = body or excerpt or (warnings[0] if warnings else "")
    summary = _collapse_whitespace(summary_source)
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[: SUMMARY_LIMIT - 3].rstrip() + "..."
    return summary, excerpt


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


def persist_project_references(
    project: DesignerProject,
    files: list[dict],
    vision_svc,
    attached_data_cache: dict[str, bytes],
    model_name: str | None = None,
) -> list[DesignerReference]:
    """Persist attached files as project references and return newly added items."""
    added: list[DesignerReference] = []
    for file_info in files:
        name = file_info.get("name", "")
        data = file_info.get("data", b"")
        if not name or not isinstance(data, (bytes, bytearray)):
            continue

        digest = hashlib.sha256(bytes(data)).hexdigest()
        existing = next((ref for ref in project.references if ref.sha256 == digest), None)
        if existing is not None:
            continue

        context_text, _, warnings = process_attached_files(
            [{"name": name, "data": bytes(data)}],
            vision_svc,
            attached_data_cache,
            model_name,
        )
        summary, excerpt = _build_reference_summary(context_text, warnings)
        reference = DesignerReference(
            name=name,
            kind=_reference_kind(name),
            mime_type=mimetypes.guess_type(name)[0] or "",
            suffix=pathlib.Path(name).suffix.lower(),
            size_bytes=len(data),
            sha256=digest,
            summary=summary,
            content_excerpt=excerpt,
            warnings=list(warnings),
        )
        reference.stored_name = save_reference_bytes(project.id, reference.id, name, bytes(data))
        project.references.append(reference)
        added.append(reference)
    return added


def delete_project_reference(project: DesignerProject, reference_ref: str) -> DesignerReference | None:
    """Delete one project reference by id or name."""
    reference = find_project_reference(project, reference_ref)
    if reference is None:
        return None

    delete_reference_bytes(project.id, reference.stored_name)
    project.references = [item for item in project.references if item.id != reference.id]
    return reference