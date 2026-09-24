"""Bounded preview and revision-bound PPTX/DOCX import for Designer clients."""

from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable
from uuid import UUID
from zipfile import ZipFile

from row_bot.designer import history, storage
from row_bot.designer.client_service import ArtifactError, read_artifact
from row_bot.designer.state import DesignerPage


@dataclass(frozen=True)
class ImportPagePreview:
    title: str
    has_notes: bool


@dataclass(frozen=True)
class DocumentImportPreview:
    resource_id: str
    resource_revision: str
    filename: str
    source_sha256: str
    page_count: int
    pages: tuple[ImportPagePreview, ...]
    replacing_page_count: int


def _parse(filename: str, data: bytes) -> list[DesignerPage]:
    suffix = filename.lower().rsplit('.', 1)[-1]
    if suffix not in {'pptx', 'docx'} or not 0 < len(data) <= 25 * 1024 * 1024:
        raise ArtifactError('invalid_document_import')
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (len(entries) > 4096 or sum(item.file_size for item in entries) > 96 * 1024 * 1024
                    or any(item.flag_bits & 1 for item in entries)):
                raise ArtifactError('document_import_too_large')
        from row_bot.designer.importer import import_docx, import_pptx
        pages = import_pptx(data) if suffix == 'pptx' else import_docx(data)
    except ArtifactError:
        raise
    except Exception:
        raise ArtifactError('document_import_unavailable') from None
    if (not 1 <= len(pages) <= 100 or any(not isinstance(page, DesignerPage) for page in pages)
            or sum(len(page.html.encode('utf-8')) + len(page.notes.encode('utf-8')) for page in pages)
            > 16 * 1024 * 1024):
        raise ArtifactError('document_import_too_large')
    return pages


def preview_document(project_id: str, *, expected_revision: str, filename: str,
                     data: bytes) -> DocumentImportPreview:
    project = read_artifact(project_id)
    if project.updated_at != expected_revision:
        raise ArtifactError('resource_revision_conflict', project.updated_at)
    if project.mode not in {'deck', 'document'}:
        raise ArtifactError('artifact_type_unavailable')
    pages = _parse(filename, data)
    return DocumentImportPreview(
        project.id, project.updated_at, filename, hashlib.sha256(data).hexdigest(),
        len(pages), tuple(ImportPagePreview(page.title[:200], bool(page.notes.strip())) for page in pages),
        len(project.pages),
    )


def import_document(project_id: str, *, expected_revision: str, filename: str,
                    pages: list[DesignerPage], command_id: str, replace: bool,
                    validate: Callable[[], None]) -> object:
    """Append or replace only the selected project under the shared save owner."""
    marker = UUID(command_id).hex
    with storage._project_save_lock(project_id):
        validate()
        project = read_artifact(project_id)
        if project.updated_at != expected_revision:
            raise ArtifactError('resource_revision_conflict', project.updated_at)
        if project.mode not in {'deck', 'document'} or not pages:
            raise ArtifactError('artifact_type_unavailable')
        updated = deepcopy(project)
        updated._row_bot_persisted_updated_at = project.updated_at
        imported = deepcopy(pages)
        for index, page in enumerate(imported):
            page.route_id = f'import-{marker}-{index + 1}'
        if replace:
            updated.pages = imported
            updated.active_page = 0
        else:
            updated.pages.extend(imported)
        if len(json.dumps(updated.to_dict(), ensure_ascii=False).encode('utf-8')) > 32 * 1024 * 1024:
            raise ArtifactError('resource_too_large')
        validate()
        if not history.snapshot(project, label='Before document import', author='user'):
            raise ArtifactError('history_unavailable')
        from row_bot.designer.render_assets import normalize_inline_image_sources
        for page in imported:
            page.html, _ = normalize_inline_image_sources(
                page.html, updated, default_asset_kind='imported-image')
        updated.manual_edits.append(
            f'User imported {len(imported)} pages from {filename} ({"replace" if replace else "append"}).')
        validate()
        try:
            storage.save_project(updated)
        except storage.StaleDesignerProjectError:
            raise ArtifactError('resource_revision_conflict', read_artifact(project_id).updated_at) from None
        return updated


def imported_pages_present(project_id: str, command_id: str, count: int) -> str | None:
    """Reconcile an interrupted import without replaying the write."""
    if not 1 <= count <= 100:
        return None
    project = read_artifact(project_id)
    marker = UUID(command_id).hex
    expected = {f'import-{marker}-{index + 1}' for index in range(count)}
    found = {page.route_id for page in project.pages if page.route_id in expected}
    return project.updated_at if found == expected else None
