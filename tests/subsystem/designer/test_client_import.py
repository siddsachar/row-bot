"""Synthetic document import previews and exact Designer save recovery."""

from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from row_bot.designer import client_import, history, importer, storage
from row_bot.designer.client_service import ArtifactError
from row_bot.designer.state import DesignerPage
from tests.subsystem.designer.test_client_exports import project as _project, isolated as _isolated

project, isolated = _project, _isolated
pytestmark = pytest.mark.subsystem


def document_bytes() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", "<p>Synthetic</p>")
    return stream.getvalue()


def test_preview_is_passive_and_append_has_exact_recovery(project, monkeypatch):
    project.mode = "document"
    storage.save_project(project)
    source = document_bytes()
    monkeypatch.setattr(importer, "import_docx", lambda data: [
        DesignerPage(title="Synthetic page", html="<h1>Synthetic</h1>", notes="Speaker note")
    ])
    with monkeypatch.context() as patch:
        patch.setattr(storage, "save_project", lambda *_: pytest.fail("preview saved"))
        patch.setattr(history, "snapshot", lambda *_a, **_k: pytest.fail("preview snapshot"))
        preview = client_import.preview_document(
            project.id, expected_revision=project.updated_at, filename="synthetic.docx", data=source)
    assert preview.page_count == 1 and preview.pages[0].has_notes
    assert preview.replacing_page_count == 2
    command_id = str(uuid4())
    saved = client_import.import_document(
        project.id, expected_revision=project.updated_at, filename="synthetic.docx",
        pages=client_import._parse("synthetic.docx", source), command_id=command_id,
        replace=False, validate=lambda: None)
    assert len(saved.pages) == 3
    assert saved.pages[-1].route_id == f"import-{command_id.replace('-', '')}-1"
    assert saved.pages[0].html == project.pages[0].html
    assert client_import.imported_pages_present(project.id, command_id, 1) == saved.updated_at
    assert client_import.imported_pages_present(project.id, command_id, 2) is None
    assert list((history.HISTORY_DIR / project.id).glob("*.json"))
    with pytest.raises(ArtifactError, match="resource_revision_conflict"):
        client_import.import_document(
            project.id, expected_revision=project.updated_at, filename="synthetic.docx",
            pages=client_import._parse("synthetic.docx", source), command_id=str(uuid4()),
            replace=False, validate=lambda: None)


def test_replace_keeps_history_and_invalid_archive_never_writes(project, monkeypatch):
    project.mode = "deck"
    storage.save_project(project)
    before = project.to_dict()
    with pytest.raises(ArtifactError, match="document_import_unavailable"):
        client_import.preview_document(
            project.id, expected_revision=project.updated_at,
            filename="broken.pptx", data=b"not a zip")
    assert storage.load_project(project.id).to_dict() == before
    monkeypatch.setattr(importer, "import_pptx", lambda data: [
        DesignerPage(title="Imported slide", html="<h1>Slide</h1>")
    ])
    saved = client_import.import_document(
        project.id, expected_revision=project.updated_at, filename="synthetic.pptx",
        pages=client_import._parse("synthetic.pptx", document_bytes()),
        command_id=str(uuid4()), replace=True, validate=lambda: None)
    assert len(saved.pages) == 1 and saved.pages[0].title == "Imported slide"
    assert list((history.HISTORY_DIR / project.id).glob("*.json"))


def test_import_preview_refuses_unsupported_mode_and_stale_revision(project):
    project.mode = 'landing'
    storage.save_project(project)
    with pytest.raises(ArtifactError, match='artifact_type_unavailable'):
        client_import.preview_document(
            project.id, expected_revision=project.updated_at,
            filename='synthetic.docx', data=document_bytes())
    project.mode = 'document'
    storage.save_project(project)
    with pytest.raises(ArtifactError, match='resource_revision_conflict'):
        client_import.preview_document(
            project.id, expected_revision='stale', filename='synthetic.docx',
            data=document_bytes())
