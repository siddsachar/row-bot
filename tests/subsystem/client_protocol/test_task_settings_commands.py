"""Reviewed task settings and private webhook download through the real API."""
# ruff: noqa: F811 -- reused fixtures.
from uuid import uuid4

import pytest

from row_bot.application import task_commands
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_task_edit_commands import task_api, fields, body, send  # noqa: F401

pytestmark = pytest.mark.subsystem


def reviewed_settings(client, headers, fields):
    saved = send(client, headers, body(headers, fields)).json()
    path = f"/api/v1/tasks/{saved['task_id']}"
    response = client.get(path + '/settings', headers=headers)
    assert response.status_code == 200, response.text
    proposed = {**response.json()['fields'], 'trigger_type': 'webhook', 'persistent_enabled': True,
                'concurrency_group': 'synthetic-group'}
    reviewed = client.post(path + '/settings-review', json=proposed, headers=headers)
    assert reviewed.status_code == 200, reviewed.text
    value = reviewed.json()
    command = {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
               'type': 'task.settings.update', 'expected_revision': '0', 'payload': {
                   'task_id': saved['task_id'], 'task_revision': value['revision'],
                   'profile_revision': value['profile_revision'], 'fields': value['fields']}}
    return path, command


def test_review_save_private_download_rotation_and_no_execution(task_api, fields):
    owner, tasks = task_api
    with _client(owner) as client:
        _, headers = bootstrap(client)
        path, command = reviewed_settings(client, headers, fields)
        task_id = command['payload']['task_id']
        assert tasks.get_task(task_id)['trigger'] is None
        saved = send(client, headers, command)
        assert saved.status_code == 200, saved.text
        assert saved.json()['task_saved']
        assert send(client, headers, command).json() == saved.json()
        secret = tasks.get_task(task_id)['trigger']['secret']
        settings = client.get(path + '/settings', headers=headers).json()
        assert settings['webhook_configured'] and settings['conversation_id'].startswith('pt_')
        assert secret not in str(settings) and secret not in saved.text
        downloaded = client.get(path + '/webhook-configuration', params={'revision': settings['revision']}, headers=headers)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers['Cache-Control'] == 'no-store'
        assert downloaded.headers['Content-Disposition'].startswith('attachment;')
        assert secret in downloaded.json()['relative_url']
        rotate = {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
                  'type': 'task.webhook.rotate', 'expected_revision': '0',
                  'payload': {'task_id': task_id, 'task_revision': settings['revision']}}
        rotated = send(client, headers, rotate)
        assert rotated.status_code == 200, rotated.text
        newer = tasks.get_task(task_id)['trigger']['secret']
        assert newer != secret
        assert send(client, headers, rotate).json() == rotated.json()
        assert tasks.get_task(task_id)['trigger']['secret'] == newer
        stale = client.get(path + '/webhook-configuration', params={'revision': settings['revision']}, headers=headers)
        assert stale.status_code == 409
        with admissions.transaction() as conn:
            receipts = str([tuple(row) for row in conn.execute('SELECT * FROM client_commands')])
        assert secret not in receipts and newer not in receipts
        assert tasks.get_recent_runs() == [] and owner.list_conversations()['items'] == []


def test_lost_settings_result_retains_secret_and_concurrent_edit(task_api, fields, monkeypatch):
    owner, tasks = task_api
    with _client(owner) as client:
        _, headers = bootstrap(client)
        path, command = reviewed_settings(client, headers, fields)
        original = task_commands.update_saved_task_settings
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Synthetic lost committed result')
        monkeypatch.setattr(task_commands, 'update_saved_task_settings', lost)
        result = send(client, headers, command)
        assert result.status_code == 200 and result.json()['status'] == 'partial', result.text
        task_id = command['payload']['task_id']
        before = tasks.get_task(task_id)['trigger']['secret']
        tasks.update_task(task_id, name='Concurrent settings editor')
        monkeypatch.setattr(task_commands, 'update_saved_task_settings', lambda *a, **k: pytest.fail('Repeated settings write'))
        replay = send(client, headers, command)
        assert replay.status_code == 200 and replay.json()['status'] == 'completed', replay.text
        assert tasks.get_task(task_id)['trigger']['secret'] == before
        assert tasks.get_task(task_id)['name'] == 'Concurrent settings editor'
