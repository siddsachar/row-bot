"""Explicit, bounded speaker-note generation for one saved Designer page."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from row_bot.designer.client_service import ArtifactError, read_artifact


def generate_page_notes(project_id: str, *, expected_revision: str, page_id: str,
                        validate: Callable[[], None]) -> tuple[str, bool]:
    """Call the shared provider only after the owner validates an explicit command."""
    validate()
    project = read_artifact(project_id)
    if project.updated_at != expected_revision:
        raise ArtifactError('resource_revision_conflict', project.updated_at)
    if project.mode not in {'deck', 'storyboard'}:
        raise ArtifactError('artifact_type_unavailable')
    page = next((item for item in project.pages if item.route_id == page_id), None)
    if page is None:
        raise ArtifactError('page_unavailable')
    if not page.html.strip() or len(page.html.encode('utf-8')) > 2 * 1024 * 1024:
        raise ArtifactError('page_unavailable')
    from row_bot.designer.ai_content import generate_speaker_notes
    from row_bot.designer.html_ops import summarize_page_html

    summary = summarize_page_html(page.html)
    if len(json.dumps(summary, ensure_ascii=False).encode('utf-8')) > 8192:
        summary = {
            'headings': [str(item)[:500] for item in summary.get('headings', [])[:6]],
            'text_preview': [str(item)[:500] for item in summary.get('text_preview', [])[:6]],
        }
    validate()
    try:
        notes = (generate_speaker_notes(
            page.title[:200], summary, page.notes[:20000], strict=True) or '').strip()
    except Exception:
        raise ArtifactError('notes_unavailable') from None
    if not notes or len(notes) > 20000:
        raise ArtifactError('notes_unavailable')
    return notes, notes == page.notes.strip()


def saved_notes_revision(project_id: str, page_id: str, digest: str) -> str | None:
    project = read_artifact(project_id)
    page = next((item for item in project.pages if item.route_id == page_id), None)
    if page is None or hashlib.sha256(page.notes.encode('utf-8')).hexdigest() != digest:
        return None
    return project.updated_at
