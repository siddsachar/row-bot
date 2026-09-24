"""Designer advanced effects use exact binding, staged bytes and reviewed commands."""
# ruff: noqa: F811 -- reused isolated fixtures.
import hashlib
import io
from uuid import uuid4
from zipfile import ZipFile

from PIL import Image
import pytest

from row_bot.designer import storage, brand, importer, ai_content
from row_bot.designer.state import DesignerPage
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service, _create  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_artifact_edit_commands import target
from tests.subsystem.client_platform.test_workspace_setup_integrity import _completed

pytestmark = pytest.mark.subsystem


def current(client, headers, created):
    project = storage.load_project(created['resource_id'])
    return target(client, headers, created, project.updated_at)


@pytest.mark.parametrize('mode', ['deck', 'document', 'landing', 'app_mockup', 'storyboard'])
def test_brand_control_and_exact_receipt_replay_all_modes(artifact_service, mode):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, mode))
        payload = {'target': current(client, headers, created), 'operation': 'brand',
                   'parameters': {'primary_color': '#654321'}}
        identifier = str(uuid4())
        args = {'target': created['conversation_id'], 'revision': created['revision'],
                'command_id': identifier, 'key': identifier}
        response = _command(client, headers, 'artifact.design.control', payload, **args)
        assert response.status_code == 200, response.text
        assert response.json()['artifact_design']['status'] == 'saved'
        saved = storage.load_project(created['resource_id']).to_dict()
        assert saved['brand']['primary_color'] == '#654321'
        assert _command(client, headers, 'artifact.design.control', payload, **args).json() == response.json()
        assert storage.load_project(created['resource_id']).to_dict() == saved
        receipt = client.get('/api/v1/commands/' + identifier, headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert '_artifact_design' not in receipt.text and '#654321' not in receipt.text


def test_curated_block_catalog_and_exact_insert_command(artifact_service):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, 'deck'))
        project = storage.load_project(created['resource_id'])
        base = f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}"
        catalog = client.get(base + '/design-controls?section=blocks', headers=headers)
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()['items'][0]['available'] is True
        assert storage.load_project(project.id).to_dict() == project.to_dict()
        component = catalog.json()['items'][0]['id']
        payload = {'target': current(client, headers, created), 'operation': 'block_insert',
                   'parameters': {'component_name': component}, 'page_id': project.pages[0].route_id}
        identifier = str(uuid4())
        args = {'target': created['conversation_id'], 'revision': created['revision'],
                'command_id': identifier, 'key': identifier}
        saved = _command(client, headers, 'artifact.design.control', payload, **args)
        assert saved.status_code == 200, saved.text
        assert saved.json()['artifact_design']['status'] == 'saved'
        assert f'data-row-bot-component="{component}"' in storage.load_project(project.id).pages[0].html
        assert _command(client, headers, 'artifact.design.control', payload, **args).json() == saved.json()
        assert storage.load_project(project.id).pages[0].html.count(f'data-row-bot-component="{component}"') == 1


def test_asset_staging_matches_session_conversation_name_hash_and_command(artifact_service):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, 'deck'))
        data = io.BytesIO()
        Image.new('RGB', (8, 8), '#abc123').save(data, format='PNG')
        content = data.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        response = client.post('/api/v1/uploads/sessions', headers=headers, json={
            'conversation_id': created['conversation_id'], 'batch_id': str(uuid4()), 'name': 'synthetic.png',
            'size_bytes': len(content), 'sha256': digest})
        assert response.status_code == 200, response.text
        upload = response.json()['upload_id']
        uploaded = client.put(f'/api/v1/uploads/{upload}/chunks?offset=0', headers=headers, content=content)
        assert uploaded.status_code == 200, uploaded.text
        payload = {'target': current(client, headers, created), 'upload_id': upload,
                   'filename': 'wrong.png', 'size_bytes': len(content), 'sha256': digest}
        response = _command(client, headers, 'artifact.asset.upload', payload,
            target=created['conversation_id'], revision=created['revision'])
        assert response.status_code in {404, 409}, response.text
        assert storage.load_project(created['resource_id']).assets == []
        payload['filename'] = 'synthetic.png'
        identity = str(uuid4())
        args = {'target': created['conversation_id'], 'revision': created['revision'], 'command_id': identity, 'key': identity}
        response = _command(client, headers, 'artifact.asset.upload', payload, **args)
        assert response.status_code == 200, response.text
        assert response.json()['artifact_design']['status'] == 'saved'
        assert _command(client, headers, 'artifact.asset.upload', payload, **args).json() == response.json()
        assert len(storage.load_project(created['resource_id']).assets) == 1


def test_document_import_preview_exact_bytes_append_and_replay(artifact_service, monkeypatch):
    content_buffer = io.BytesIO()
    with ZipFile(content_buffer, 'w') as archive:
        archive.writestr('word/document.xml', '<p>Synthetic</p>')
    content = content_buffer.getvalue()
    monkeypatch.setattr(importer, 'import_docx', lambda data: [
        DesignerPage(title='Synthetic imported page', html='<h1>Synthetic</h1>')])
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, 'document'))
        digest = hashlib.sha256(content).hexdigest()
        command_id = str(uuid4())
        filename = 'synthetic.docx'
        opened = client.post('/api/v1/uploads/sessions', headers=headers, json={
            'conversation_id': created['conversation_id'], 'batch_id': command_id,
            'name': filename, 'size_bytes': len(content), 'sha256': digest})
        assert opened.status_code == 200, opened.text
        upload = opened.json()['upload_id']
        assert client.put(f'/api/v1/uploads/{upload}/chunks?offset=0',
                          headers=headers, content=content).status_code == 200
        base = f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}"
        revision = storage.load_project(created['resource_id']).updated_at
        preview_body = {'expected_revision': revision, 'upload_id': upload, 'filename': filename,
                        'size_bytes': len(content), 'sha256': digest}
        preview = client.post(base + '/document-import-preview', headers=headers, json=preview_body)
        assert preview.status_code == 200, preview.text
        assert preview.json()['page_count'] == 1
        assert storage.load_project(created['resource_id']).updated_at == revision
        changed = client.post(base + '/document-import-preview', headers=headers,
                              json={**preview_body, 'sha256': '0' * 64})
        assert changed.status_code == 409 and changed.json()['code'] == 'upload_identity_conflict'
        payload = {'target': current(client, headers, created), 'upload_id': upload,
                   'filename': filename, 'size_bytes': len(content), 'sha256': digest,
                   'replace': False}
        args = {'target': created['conversation_id'], 'revision': created['revision'],
                'command_id': command_id, 'key': command_id}
        saved = _command(client, headers, 'artifact.document.import', payload, **args)
        assert saved.status_code == 200, saved.text
        assert saved.json()['artifact_design']['operation'] == 'document_import'
        assert len(storage.load_project(created['resource_id']).pages) == 2
        assert _command(client, headers, 'artifact.document.import', payload, **args).json() == saved.json()
        assert len(storage.load_project(created['resource_id']).pages) == 2
        assert 'Synthetic imported page' not in client.get('/api/v1/commands/' + command_id,
                                                            headers=headers).text


def test_speaker_notes_command_is_explicit_once_and_receipt_redacted(artifact_service, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_content, 'generate_speaker_notes',
        lambda title, summary, existing, *, strict=False: (calls.append(title), 'Synthetic generated notes')[1])
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, 'deck'))
        project = storage.load_project(created['resource_id'])
        assert calls == []
        payload = {'target': current(client, headers, created), 'page_id': project.pages[0].route_id}
        command_id = str(uuid4())
        args = {'target': created['conversation_id'], 'revision': created['revision'],
                'command_id': command_id, 'key': command_id}
        saved = _command(client, headers, 'artifact.notes.generate', payload, **args)
        assert saved.status_code == 200, saved.text
        assert saved.json()['artifact_design']['operation'] == 'notes_generate'
        assert storage.load_project(project.id).pages[0].notes == 'Synthetic generated notes'
        assert _command(client, headers, 'artifact.notes.generate', payload, **args).json() == saved.json()
        assert len(calls) == 1
        assert 'Synthetic generated notes' not in client.get('/api/v1/commands/' + command_id,
                                                            headers=headers).text


def test_global_preset_requires_exact_review_and_never_changes_source(artifact_service, monkeypatch, tmp_path):
    monkeypatch.setattr(brand, '_BRAND_DIR', tmp_path / 'isolated-brands')
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, 'deck'))
        before = storage.load_project(created['resource_id']).to_dict()
        command_id = str(uuid4())
        payload = {'target': current(client, headers, created), 'action': 'save', 'name': 'Synthetic reviewed brand'}
        base = f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}"
        response = client.post(base + '/preset-review', headers=headers, json={**payload, 'command_id': command_id})
        assert response.status_code == 200, response.text
        assert not brand._BRAND_DIR.exists()
        payload['nonce'] = response.json()['nonce']
        args = {'target': created['conversation_id'], 'revision': created['revision'], 'command_id': command_id, 'key': command_id}
        changed = _command(client, headers, 'artifact.preset.mutate', {**payload, 'name': 'Changed after review'}, **args)
        assert changed.status_code == 409, changed.text
        # The forged intent must not consume the original key in canonical admissions.
        saved = _command(client, headers, 'artifact.preset.mutate', payload, **args)
        assert saved.status_code == 200, saved.text
        assert saved.json()['artifact_design']['status'] == 'saved'
        assert _command(client, headers, 'artifact.preset.mutate', payload, **args).json() == saved.json()
        assert storage.load_project(created['resource_id']).to_dict() == before
