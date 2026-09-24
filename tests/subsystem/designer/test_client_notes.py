"""Speaker notes are generated only for an explicit current page command."""

import pytest

from row_bot.designer import ai_content, client_notes, storage
from row_bot.designer.client_service import ArtifactError
from tests.subsystem.designer.test_client_exports import project as _project, isolated as _isolated

project, isolated = _project, _isolated
pytestmark = pytest.mark.subsystem


def test_notes_generation_passes_bounded_page_context_without_saving(project, monkeypatch):
    project.mode = 'deck'
    storage.save_project(project)
    seen = []
    monkeypatch.setattr(ai_content, 'generate_speaker_notes',
                        lambda title, summary, existing, *, strict=False: (seen.append((title, summary, existing, strict)), 'New notes')[1])
    with monkeypatch.context() as patch:
        patch.setattr(storage, 'save_project', lambda *_: pytest.fail('generation saved'))
        notes, unchanged = client_notes.generate_page_notes(
            project.id, expected_revision=project.updated_at,
            page_id=project.pages[0].route_id, validate=lambda: None)
    assert notes == 'New notes' and not unchanged
    assert len(seen) == 1 and 'One' in str(seen[0][1]) and seen[0][3] is True
    assert storage.load_project(project.id).pages[0].notes == project.pages[0].notes


def test_notes_refuse_stale_or_unsupported_without_provider_call(project, monkeypatch):
    monkeypatch.setattr(ai_content, 'generate_speaker_notes',
                        lambda *_a, **_k: pytest.fail('provider called'))
    project.mode = 'document'
    storage.save_project(project)
    with pytest.raises(ArtifactError, match='artifact_type_unavailable'):
        client_notes.generate_page_notes(project.id, expected_revision=project.updated_at,
            page_id=project.pages[0].route_id, validate=lambda: None)
    project.mode = 'deck'
    storage.save_project(project)
    with pytest.raises(ArtifactError, match='resource_revision_conflict'):
        client_notes.generate_page_notes(project.id, expected_revision='stale',
            page_id=project.pages[0].route_id, validate=lambda: None)


def test_provider_failure_is_not_reported_as_saved_notes(project, monkeypatch):
    project.mode = 'deck'
    storage.save_project(project)
    monkeypatch.setattr(ai_content, '_designer_text_llm',
                        lambda: (_ for _ in ()).throw(RuntimeError('synthetic unavailable')))
    with pytest.raises(ArtifactError, match='notes_unavailable'):
        client_notes.generate_page_notes(project.id, expected_revision=project.updated_at,
            page_id=project.pages[0].route_id, validate=lambda: None)
    assert storage.load_project(project.id).pages[0].notes == project.pages[0].notes
