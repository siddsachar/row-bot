from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from row_bot.designer import client_editing as editing, client_service as service, history, storage
from row_bot.designer.state import DESIGNER_MODES, DesignerPage
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem


@pytest.fixture
def project(isolated, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", isolated / "history")
    project = service.create_deck("editable", service.DeckSetup())
    project.pages[0].html = "<html><body><h1>Original title</h1><p>Repeated</p><p>Repeated</p></body></html>"
    storage.save_project(project)
    return project


def test_read_is_passive_and_contains_no_html_or_private_paths(project, monkeypatch):
    import row_bot.designer.session as session

    monkeypatch.setattr(session, "set_active_project", lambda *_args: pytest.fail("read selected visible session"))
    path = storage.PROJECTS_DIR / f"{project.id}.json"
    before = path.read_bytes()
    view = editing.read_editing(project.id)
    assert view.resource_revision == project.updated_at
    assert not history.HISTORY_DIR.exists()
    assert path.read_bytes() == before
    serialized = json.dumps(asdict(view))
    assert "<html>" not in serialized and str(storage.PROJECTS_DIR) not in serialized
    assert view.history_count == 0 and view.element_count == 3


@pytest.mark.parametrize("mode", list(DESIGNER_MODES))
def test_text_edit_uses_exact_page_target_plain_text_and_retained_history(project, mode):
    project.mode = mode
    project.pages.append(DesignerPage(title="Other", route_id="other", html="<p>Repeated</p>"))
    storage.save_project(project)
    view = editing.read_editing(project.id)
    result = editing.apply_edit(project.id, expected_revision=view.resource_revision, operation="text",
                                page_id=view.page_id, element_id=view.elements[2].id,
                                text='<img src="https://example.invalid" onerror="bad()">')
    assert result.mode == mode and result.updated_at != view.resource_revision
    html = BeautifulSoup(result.pages[0].html, "html.parser")
    assert html.find("img") is None
    assert html.find_all("p")[0].get_text() == "Repeated"
    assert html.find_all("p")[1].get_text().startswith("<img ")
    assert result.pages[1].html == "<p>Repeated</p>"
    snapshots = history.list_snapshots(project.id)
    assert len(snapshots) == 1
    saved = history.read_snapshot(project.id, snapshots[0]["id"])
    assert saved["pages"][0]["html"] == project.pages[0].html


def test_parallel_stale_edits_preserve_the_winner(project):
    def edit(name):
        try:
            return editing.apply_edit(project.id, expected_revision=project.updated_at,
                                      operation="project_properties", name=name).name
        except service.ArtifactError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["First edit", "Second edit"]))
    assert results.count("resource_revision_conflict") == 1
    winner = next(item for item in results if item != "resource_revision_conflict")
    assert service.read_artifact(project.id).name == winner
    assert len(history.list_snapshots(project.id)) == 1


@pytest.mark.parametrize("kind", ["stale", "missing_page", "foreign_element", "irrelevant_field"])
def test_invalid_edits_leave_source_and_history_untouched(project, kind):
    view = editing.read_editing(project.id)
    args = dict(expected_revision=project.updated_at, operation="text", page_id=view.page_id,
                element_id=view.elements[0].id, text="Explicit new text")
    if kind == "stale":
        args["expected_revision"] = "old-revision"
    elif kind == "missing_page":
        args["page_id"] = "absent-page"
    elif kind == "foreign_element":
        args["element_id"] = "foreign-element"
    else:
        args["snapshot_id"] = "12.345"
    path = storage.PROJECTS_DIR / f"{project.id}.json"
    before = path.read_bytes()
    with pytest.raises(service.ArtifactError):
        editing.apply_edit(project.id, **args)
    assert path.read_bytes() == before and not history.HISTORY_DIR.exists()


def test_restore_preserves_dirty_current_document_as_a_new_snapshot(project):
    original = history.snapshot(project, "Original saved version")
    dirty = editing.apply_edit(project.id, expected_revision=project.updated_at,
                               operation="page_properties", page_id=project.pages[0].route_id,
                               title="Dirty new title", notes="Dirty notes retained")
    restored = editing.apply_edit(project.id, expected_revision=dirty.updated_at,
                                  operation="restore", snapshot_id=original)
    assert restored.pages[0].title == project.pages[0].title
    assert restored.thread_id == project.thread_id and restored.id == project.id
    states = [history.read_snapshot(project.id, row["id"]) for row in history.list_snapshots(project.id)]
    assert any(item["pages"][0]["notes"] == "Dirty notes retained" for item in states)
    with pytest.raises(service.ArtifactError, match="resource_revision_conflict"):
        editing.apply_edit(project.id, expected_revision=dirty.updated_at, operation="restore", snapshot_id=original)


@pytest.mark.parametrize("damage", ["path", "malformed", "unsupported_shape"])
def test_unavailable_history_cannot_restore(project, damage):
    snapshot = history.snapshot(project)
    path = history.HISTORY_DIR / project.id / f"{snapshot}.json"
    if damage == "path":
        snapshot = "../outside"
    elif damage == "malformed":
        path.write_text("corrupt saved history")
    else:
        data = json.loads(path.read_text())
        data["pages"] = [{"html": {"not": "text"}}]
        path.write_text(json.dumps(data))
    if damage != "path":
        assert not editing.read_editing(project.id).history[0].available
    before = (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes()
    with pytest.raises(service.ArtifactError, match="history_unavailable"):
        editing.apply_edit(project.id, expected_revision=project.updated_at, operation="restore", snapshot_id=snapshot)
    assert (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes() == before


def test_snapshot_failure_prevents_document_mutation(project, monkeypatch):
    monkeypatch.setattr(history, "snapshot", lambda *_args, **_kw: "")
    with pytest.raises(service.ArtifactError, match="history_unavailable"):
        editing.apply_edit(project.id, expected_revision=project.updated_at,
                           operation="project_properties", name="Never committed")
    assert service.read_artifact(project.id).name == project.name


def test_authority_is_rechecked_before_snapshot_and_save(project):
    calls = []

    def authority():
        calls.append(True)
        if len(calls) == 3:
            raise PermissionError("Synthetic revocation after snapshot")

    with pytest.raises(PermissionError):
        editing.apply_edit(project.id, expected_revision=project.updated_at,
                           operation="project_properties", name="Must not commit", validate=authority)
    assert len(calls) == 3
    assert service.read_artifact(project.id).name == project.name
    assert len(history.list_snapshots(project.id)) == 1


def test_all_page_element_history_pages_have_real_continuation(project):
    project.pages[0].html = "<html><body>" + "".join(f"<p>Element {i}</p>" for i in range(67)) + "</body></html>"
    project.pages.extend(DesignerPage(title=f"Page {i}", route_id=f"page-extra-{i}") for i in range(66))
    storage.save_project(project)
    directory = history.HISTORY_DIR / project.id
    directory.mkdir(parents=True)
    from dataclasses import asdict
    state = asdict(history.capture_project_state(project))
    for number in range(67):
        identifier = f"123456.{number:06}"
        (directory / f"{identifier}.json").write_text(json.dumps(dict(state, id=identifier, label=f"History {number}")))
    for kind, field, expected in (("page", "pages", 67), ("element", "elements", 67), ("history", "history", 67)):
        identifiers = []
        cursor = None
        while True:
            view = editing.read_editing(project.id, limit=9, **{f"{kind}_cursor": cursor})
            rows = getattr(view, field)
            assert len(rows) <= 9
            identifiers.extend(row.id for row in rows)
            cursor = getattr(view, f"{kind}_next_cursor")
            if cursor is None:
                break
        assert len(identifiers) == len(set(identifiers)) == expected


def test_cursors_are_bound_to_resource_revision_page_and_list_kind(project):
    project.pages.append(DesignerPage(route_id="second", html=project.pages[0].html))
    storage.save_project(project)
    view = editing.read_editing(project.id, limit=1)
    with pytest.raises(service.ArtifactError, match="invalid_cursor"):
        editing.read_editing(project.id, element_cursor=view.page_next_cursor)
    with pytest.raises(service.ArtifactError, match="invalid_cursor"):
        editing.read_editing(project.id, page_id="second", element_cursor=view.element_next_cursor)
    other = service.create_deck("other-resource", service.DeckSetup())
    other.updated_at = project.updated_at
    storage._write_json_atomic(storage.PROJECTS_DIR / f"{other.id}.json", other.to_dict())
    with pytest.raises(service.ArtifactError, match="invalid_cursor"):
        editing.read_editing(other.id, element_cursor=view.element_next_cursor)
    editing.apply_edit(project.id, expected_revision=project.updated_at, operation="project_properties", name="New revision")
    with pytest.raises(service.ArtifactError, match="resource_revision_conflict"):
        editing.read_editing(project.id, element_cursor=view.element_next_cursor)


def test_byte_budget_paginates_complete_text_without_partial_overwrite(project):
    texts = [(str(i) + "é" * 19000) for i in range(12)]
    project.pages[0].html = "<html><body>" + "".join(f"<p>{text}</p>" for text in texts) + "</body></html>"
    storage.save_project(project)
    cursor = None
    actual = []
    while True:
        view = editing.read_editing(project.id, element_cursor=cursor, limit=50)
        assert len(json.dumps(asdict(view), ensure_ascii=False).encode()) <= 240 * 1024
        actual.extend(row.text for row in view.elements)
        cursor = view.element_next_cursor
        if cursor is None:
            break
    assert actual == texts


def test_oversized_element_is_explicitly_uneditable_and_source_preserved(project):
    project.pages[0].html = "<p>" + "x" * 20001 + "</p>"
    storage.save_project(project)
    view = editing.read_editing(project.id)
    assert len(view.elements) == 1 and not view.elements[0].editable and view.elements[0].text == ""
    with pytest.raises(service.ArtifactError, match="element_unavailable"):
        editing.apply_edit(project.id, expected_revision=project.updated_at, operation="text",
                           page_id=view.page_id, element_id=view.elements[0].id, text="Partial replacement")
    assert service.read_artifact(project.id).pages[0].html == project.pages[0].html


@pytest.mark.parametrize("mode", list(DESIGNER_MODES))
def test_explicit_authoring_preview_is_isolated_and_revision_driven(project, mode):
    project.mode = mode
    storage.save_project(project)
    view = editing.read_editing(project.id)
    before = (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes()
    args = dict(authoring=True, preview_id="editor-frame-123456", capability="capability-123456789")
    preview = service.read_preview(project.id, **args)
    assert preview.scripts_allowed and "plainTextEdits = true" in preview.html
    assert view.elements[0].id in preview.html
    assert "connect-src 'none'" in preview.html
    assert service.read_preview(project.id, known_revision=preview.preview_revision, **args).unchanged
    assert service.read_preview(project.id).preview_revision != preview.preview_revision
    assert (storage.PROJECTS_DIR / f"{project.id}.json").read_bytes() == before
    assert not history.HISTORY_DIR.exists()


@pytest.mark.parametrize("identity", [{}, {"preview_id": "short", "capability": "bad"},
                                    {"preview_id": "bad/identity-123456", "capability": "valid-capability-123456"}])
def test_authoring_requires_valid_transient_identity(project, identity):
    with pytest.raises(service.ArtifactError, match="invalid_preview_identity"):
        service.read_preview(project.id, authoring=True, **identity)


def test_exact_element_selection_jumps_to_its_real_descriptor_page(project):
    project.pages[0].html = ''.join(f'<p>Target {index}</p>' for index in range(70))
    storage.save_project(project)
    expected = editing.read_editing(project.id, limit=50).elements[43]
    selected = editing.read_editing(project.id, element_id=expected.id, limit=5)
    assert selected.elements[0] == expected and len(selected.elements) == 5
    assert selected.element_count == 70 and selected.element_next_cursor
    next_page = editing.read_editing(project.id, element_cursor=selected.element_next_cursor, limit=5)
    assert next_page.elements[0].text == 'Target 48'
    with pytest.raises(service.ArtifactError, match='element_unavailable'):
        editing.read_editing(project.id, element_id='absent')


@pytest.mark.parametrize('case', ['html_type', 'html_unicode', 'title_unicode', 'other_title', 'canvas', 'route_id', 'history_name'])
def test_unrepresentable_saved_state_has_explicit_error_without_source_mutation(project, case):
    data = project.to_dict()
    expected = 'resource_state_invalid'
    if case == 'html_type':
        data['pages'][0]['html'] = {'invalid': 'source'}
    elif case == 'html_unicode':
        data['pages'][0]['html'] = '<p>\ud800</p>'
    elif case == 'title_unicode':
        data['pages'][0]['title'] = '\ud800'
    elif case == 'other_title':
        data['pages'].append(DesignerPage(title='x' * 257, route_id='other').to_dict())
        expected = 'editing_record_too_large'
    elif case == 'canvas':
        data['aspect_ratio'] = 'custom'
        data['canvas_width'] = 20000
    elif case == 'route_id':
        data['pages'][0]['route_id'] = 'invalid saved route'
    else:
        directory = history.HISTORY_DIR / project.id
        directory.mkdir(parents=True)
        (directory / 'unknown-owned-history.json').write_text('{}')
        expected = 'history_unavailable'
    path = storage.PROJECTS_DIR / f'{project.id}.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(service.ArtifactError, match=expected):
        editing.read_editing(project.id)
    assert path.read_bytes() == before


@pytest.mark.parametrize('oversized', [False, True])
def test_authoring_bridge_emits_complete_plain_text_or_preserves_oversized_edit(project, oversized):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the deterministic Designer bridge behavior test')
    preview = service.read_preview(project.id, authoring=True, preview_id='editor-frame-123456',
                                   capability='capability-123456789')
    script = r'''
const fs = require('node:fs');
const { JSDOM } = require('./frontend/node_modules/jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const messages = [];
const dom = new JSDOM(input.html, { runScripts: 'dangerously', beforeParse(window) {
  window.TextEncoder = TextEncoder;
  Object.defineProperty(window, 'parent', {value: {postMessage(message) {messages.push(message);}}});
}});
const el = dom.window.document.querySelector('[data-row-bot-element-id]');
el.dispatchEvent(new dom.window.MouseEvent('dblclick', {bubbles: true}));
el.innerHTML = input.replacement;
el.dispatchEvent(new dom.window.Event('blur'));
process.stdout.write(JSON.stringify({messages, text: el.textContent}));
dom.window.close();
'''
    replacement = 'é' * 10000 if oversized else '<b>New &amp; complete text</b>'
    result = subprocess.run([node, '-e', script], input=json.dumps({'html': preview.html, 'replacement': replacement}),
                            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True,
                            encoding='utf-8', timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    messages = actual['messages']
    if oversized:
        assert actual['text'] == 'Original title'
        assert not any(item['type'] == 'text-edit' for item in messages)
        assert messages[-1]['type'] == 'edit-unavailable'
        assert messages[-1]['detail']['code'] == 'text_too_large'
    else:
        proposal = next(item for item in messages if item['type'] == 'text-edit')
        assert proposal['detail']['newText'] == 'New & complete text'
        assert proposal['detail']['oldText'] == 'Original title'
        assert proposal['previewId'] == 'editor-frame-123456'
        assert proposal['revision'] == preview.preview_revision
        assert proposal['detail']['elementInfo']['elementId'] == editing.read_editing(project.id).elements[0].id
    assert service.read_artifact(project.id).pages[0].html == project.pages[0].html
