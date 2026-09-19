"""Designer advanced effects use exact binding, staged bytes and reviewed commands."""
# ruff: noqa: F811 -- reused isolated fixtures.
import hashlib
import io
from uuid import uuid4

from PIL import Image
import pytest

from row_bot.designer import storage, brand
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
