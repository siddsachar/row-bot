from __future__ import annotations

import io
import json
import zipfile
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.designer import client_exports as client, client_service, export, storage
from row_bot.designer.state import DESIGNER_MODES, DesignerPage
from tests.subsystem.designer.test_client_artifact import isolated as _isolated

isolated = _isolated
pytestmark = pytest.mark.subsystem


@pytest.fixture
def project(isolated, monkeypatch):
    import row_bot.designer.fonts as fonts

    monkeypatch.setattr(storage, 'DESIGNER_DIR', isolated / 'designer')
    monkeypatch.setattr(fonts, 'get_font_css_embedded', lambda _family: '')
    result = client_service.create_deck('exportable', client_service.DeckSetup())
    result.pages = [DesignerPage(route_id='first', title='First', html='<h1>One</h1>'),
                    DesignerPage(route_id='second', title='Second', html='<p>Two</p>', notes='Saved notes')]
    storage.save_project(result)
    return result


@pytest.fixture
def renderer(monkeypatch):
    from PIL import Image
    from pypdf import PdfWriter
    import playwright.sync_api

    output = io.BytesIO()
    Image.new('RGB', (3, 3), 'white').save(output, format='PNG')
    png = output.getvalue()
    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)
    calls = {'contexts': [], 'html': [], 'routes': [], 'launches': 0}

    class Page:
        def set_content(self, html, **_kwargs):
            calls['html'].append(html)

        def pdf(self, **_kwargs):
            return buffer.getvalue()

        def screenshot(self, **_kwargs):
            return png

        def evaluate(self, _script):
            return {'backgroundColor': 'white', 'items': [
                {'kind': 'text', 'text': 'Editable title', 'x': 0, 'y': 0, 'width': 300, 'height': 50},
            ]}

        def set_viewport_size(self, *_args):
            pass

        def close(self):
            pass

    class Context:
        def new_page(self):
            return Page()

        def route(self, pattern, callback):
            calls['routes'].append(pattern)
            denied = []
            callback(SimpleNamespace(abort=lambda: denied.append(True)))
            assert denied == [True]

        def set_default_timeout(self, timeout):
            assert timeout == 10000

        set_default_navigation_timeout = set_default_timeout

        def close(self):
            pass

    class Browser:
        def new_context(self, **kwargs):
            calls['contexts'].append(kwargs)
            return Context()

        def close(self):
            pass

    def launch(_playwright):
        calls['launches'] += 1
        return Browser()

    @contextmanager
    def fake_playwright():
        yield object()

    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', fake_playwright)
    monkeypatch.setattr(export, '_launch_playwright_browser', launch)
    return calls


def create(project, **kwargs):
    options = {'expected_revision': project.updated_at, 'export_id': str(uuid4()),
               'binding_id': 'binding-a', 'format': 'html', 'validate': lambda: None}
    options.update(kwargs)
    return client.create_export(project.id, **options)


@pytest.mark.parametrize('mode', list(DESIGNER_MODES))
@pytest.mark.parametrize(('format', 'pptx_mode'), [('html', None), ('pdf', None), ('png', None),
                                                  ('pptx', 'screenshot'), ('pptx', 'structured')])
def test_all_modes_use_real_export_owners_with_fake_offline_browser(project, renderer, mode, format, pptx_mode):
    project.mode = mode
    storage.save_project(project)
    before = (storage.PROJECTS_DIR / f'{project.id}.json').read_bytes()
    result = create(project, format=format, pptx_mode=pptx_mode)
    descriptor, payload = client.read_export_payload(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)
    assert descriptor == result and result.status == 'ready' and result.page_count == 2
    assert result.size_bytes == len(payload) and result.resource_revision == project.updated_at
    assert result.filename and '/' not in result.filename and '\\' not in result.filename
    assert (storage.PROJECTS_DIR / f'{project.id}.json').read_bytes() == before
    if format == 'html':
        assert b'Page 1: First' in payload and b'Page 2: Second' in payload
        assert b"connect-src 'none'" in payload or b'connect-src &#x27;none&#x27;' in payload
    elif format == 'pdf':
        from pypdf import PdfReader
        assert len(PdfReader(io.BytesIO(payload)).pages) == 2
    elif format == 'png':
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            assert len(bundle.namelist()) == 2
    else:
        from pptx import Presentation
        assert len(Presentation(io.BytesIO(payload)).slides) == 2
    for options in renderer['contexts']:
        assert options['java_script_enabled'] is False and options['service_workers'] == 'block'
        assert options['accept_downloads'] is False
    assert len(renderer['contexts']) == len(renderer['routes'])


def test_replay_and_single_page_png_do_not_repeat_render_or_change_scope(project, renderer):
    identity = str(uuid4())
    first = create(project, export_id=identity, format='png', pages='2')
    count = renderer['launches']
    assert create(project, export_id=identity, format='png', pages='2') == first
    assert renderer['launches'] == count and first.media_type == 'image/png'
    with pytest.raises(client_service.ArtifactError, match='export_conflict'):
        create(project, export_id=identity, format='png', pages='1')
    with pytest.raises(client_service.ArtifactError, match='export_unavailable'):
        client.read_export(project.id, identity, binding_id='binding-b', validate=lambda: None)


@pytest.mark.parametrize('pages', ['0', '3', '2-1', '2-99', 'all,1', '', '1,,2', '../x'])
def test_invalid_range_never_falls_back_to_all_or_creates_output(project, pages):
    with pytest.raises(client_service.ArtifactError, match='invalid_page_range'):
        create(project, pages=pages)
    assert not client._root().exists()


def test_revision_and_revocation_prevent_effects_and_preserve_source(project, monkeypatch):
    monkeypatch.setattr(export, 'export_html', lambda *_args, **_kwargs: pytest.fail('render admitted'))
    with pytest.raises(client_service.ArtifactError, match='resource_revision_conflict'):
        create(project, expected_revision='old')
    def denied():
        raise client_service.ArtifactError('capability_revoked')
    with pytest.raises(client_service.ArtifactError, match='capability_revoked'):
        create(project, validate=denied)
    assert not client._root().exists()


def test_revocation_after_render_retains_incomplete_attempt_without_ready_download(project, monkeypatch):
    original = export.export_html
    revoked = False
    def rendered(*args, **kwargs):
        nonlocal revoked
        result = original(*args, **kwargs)
        revoked = True
        return result
    def validate():
        if revoked:
            raise client_service.ArtifactError('capability_revoked')
    monkeypatch.setattr(export, 'export_html', rendered)
    identity = str(uuid4())
    with pytest.raises(client_service.ArtifactError, match='capability_revoked'):
        create(project, export_id=identity, validate=validate)
    directory = client._directory(identity)
    assert list(directory.glob('*.html'))
    with pytest.raises(client_service.ArtifactError, match='export_incomplete'):
        create(project, export_id=identity)
    assert client_service.read_artifact(project.id).updated_at == project.updated_at


def test_external_assets_are_blocked_with_explicit_warning(project):
    project.pages[0].html = '<img src="https://example.invalid/private.png"><style>@import url(https://example.invalid/font.css);</style><h1>Saved</h1>'
    storage.save_project(project)
    result = create(project)
    assert result.warnings == ('external_assets_unavailable',)
    _descriptor, payload = client.read_export_payload(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)
    assert b'example.invalid' not in payload


def test_embedded_local_asset_is_preserved_without_external_warning(project):
    project.brand = None
    html = '<html><body><img src="data:image/png;base64,AAAA"><div style="background:url(\'data:image/png;base64,AAAA\')">Local</div></body></html>'
    with export.strict_export(lambda: None) as context:
        offline = export._offline_export_html(html, project)
    assert context.warnings == set()
    assert offline.count('data:image/png;base64,AAAA') == 2


def test_corrupt_payload_and_expired_output_never_download_or_delete_bytes(project, monkeypatch):
    result = create(project)
    directory = client._directory(result.export_id)
    monkeypatch.setattr(client.time, 'time', lambda: result.expires_at + 1)
    with pytest.raises(client_service.ArtifactError, match='export_expired'):
        client.read_export(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert client.expire_exports(before=result.expires_at + 1) == {'removed': 0, 'retained': 1}
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    (directory / 'payload').write_bytes(b'changed copy')
    with pytest.raises(client_service.ArtifactError, match='export_unavailable'):
        client.read_export(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)


def test_capacity_and_strict_context_failure_do_not_weaken_legacy_defaults(project, monkeypatch):
    create(project)
    monkeypatch.setattr(client, 'MAX_EXPORTS', 1)
    with pytest.raises(client_service.ArtifactError, match='export_capacity_reached'):
        create(project)
    with pytest.raises(RuntimeError, match='export_size_limit'):
        with export.strict_export(lambda: None):
            export._export_collection_size([b'x' * (export.MAX_STRICT_EXPORT_BYTES + 1)])
    assert export._STRICT_EXPORT.get() is None


def test_source_edit_during_render_never_publishes_stale_success(project, monkeypatch):
    original = export.export_html
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        project.name = 'Concurrent source'
        storage.save_project(project)
        return result
    monkeypatch.setattr(export, 'export_html', changed)
    identity = str(uuid4())
    with pytest.raises(client_service.ArtifactError, match='resource_revision_conflict'):
        create(project, export_id=identity)
    assert json.loads((client._directory(identity) / 'manifest.json').read_text())['status'] == 'incomplete'


def test_concurrent_response_loss_replay_renders_once(project, monkeypatch):
    original, calls = export.export_html, []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(export, 'export_html', counted)
    identity = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(project, export_id=identity), range(2)))
    assert results[0] == results[1] and calls == [1]


def test_strict_partial_pdf_merge_and_pptx_item_failures_never_claim_ready(project, renderer, monkeypatch, tmp_path):
    import builtins
    real_import = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'pypdf':
            raise ImportError('synthetic unavailable merge')
        return real_import(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', unavailable)
        with pytest.raises(client_service.ArtifactError, match='export_incomplete'):
            create(project, format='pdf')
        # Compatibility behavior belongs to the retained owner; strict policy
        # does not leak after failure into existing NiceGUI callers.
        assert export.export_pdf(project, directory=tmp_path)
    def fail_item(*_args):
        raise ValueError('synthetic item conversion failure')
    monkeypatch.setattr(export, '_add_rendered_item_to_slide', fail_item)
    with pytest.raises(client_service.ArtifactError, match='export_incomplete'):
        create(project, format='pptx', pptx_mode='structured')
    assert export.export_pptx_structured(project, directory=tmp_path)


def test_metadata_cannot_supply_download_headers_or_other_resource_identity(project):
    result = create(project)
    path = client._directory(result.export_id) / 'manifest.json'
    original = json.loads(path.read_text())
    for field, value in [('filename', 'bad\r\nInjected: true.html'), ('resource_id', 'other'), ('media_type', 'text/javascript')]:
        altered = json.loads(json.dumps(original))
        altered['descriptor'][field] = value
        path.write_text(json.dumps(altered))
        with pytest.raises(client_service.ArtifactError, match='export_unavailable'):
            client.read_export(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)


def test_download_rejects_payload_hardlink_without_reading_foreign_copy(project, tmp_path):
    import os
    result = create(project)
    path = client._directory(result.export_id) / 'payload'
    foreign = tmp_path / 'foreign-copy'
    foreign.write_bytes(path.read_bytes())
    path.unlink()
    os.link(foreign, path)
    with pytest.raises(client_service.ArtifactError, match='export_unavailable'):
        client.read_export_payload(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)
    assert foreign.is_file()


def test_render_page_and_item_budgets_fail_before_unbounded_work(project, renderer, monkeypatch):
    project.canvas_width, project.canvas_height = 16000, 16000
    project.aspect_ratio = 'custom'
    storage.save_project(project)
    with pytest.raises(client_service.ArtifactError, match='export_incomplete'):
        create(project, format='pdf')
    assert renderer['launches'] == 0
    assert export._STRICT_EXPORT.get() is None


@pytest.mark.parametrize('swap', ['leaf', 'parent'])
def test_download_rejects_path_replacement_during_open(project, monkeypatch, swap):
    import os
    result = create(project)
    directory = client._directory(result.export_id)
    payload = directory / 'payload'
    original_open = os.open
    switched = False
    def replaced(path, *args, **kwargs):
        nonlocal switched
        if Path(path) == payload and not switched:
            switched = True
            if swap == 'leaf':
                payload.rename(directory / 'retained-original')
                payload.write_bytes(b'foreign replacement')
            else:
                directory.rename(directory.with_name(directory.name + '-retained'))
                directory.mkdir()
                payload.write_bytes(b'foreign replacement')
        return original_open(path, *args, **kwargs)
    from pathlib import Path
    monkeypatch.setattr(os, 'open', replaced)
    with pytest.raises(client_service.ArtifactError, match='export_unavailable'):
        client.read_export_payload(project.id, result.export_id, binding_id='binding-a', validate=lambda: None)
    assert payload.read_bytes() == b'foreign replacement'


def test_capacity_stops_enumeration_before_unbounded_unknown_entries(project, monkeypatch):
    from pathlib import Path
    root = client._root()
    root.mkdir(parents=True)
    original_iterdir = Path.iterdir
    yielded = []
    def bounded(path):
        if path == root:
            for index in range(client.MAX_EXPORTS + 2):
                yielded.append(index)
                if index > client.MAX_EXPORTS:
                    pytest.fail('enumerated beyond admission budget')
                yield root / str(index)
        else:
            yield from original_iterdir(path)
    monkeypatch.setattr(Path, 'iterdir', bounded)
    with pytest.raises(client_service.ArtifactError, match='export_capacity_reached'):
        create(project)
    assert len(yielded) == client.MAX_EXPORTS + 1


def test_application_validator_exception_is_preserved_after_partial_render(project, monkeypatch):
    class AuthorityRejected(Exception):
        pass
    rejected = AuthorityRejected('synthetic current authority rejection')
    original = export.export_html
    rendered = False
    def output(*args, **kwargs):
        nonlocal rendered
        result = original(*args, **kwargs)
        rendered = True
        return result
    def validate():
        if rendered:
            raise rejected
    monkeypatch.setattr(export, 'export_html', output)
    identity = str(uuid4())
    with pytest.raises(AuthorityRejected) as caught:
        create(project, export_id=identity, validate=validate)
    assert caught.value is rejected
    directory = client._directory(identity)
    assert list(directory.glob('*.html'))
    assert json.loads((directory / 'manifest.json').read_text())['status'] == 'incomplete'
