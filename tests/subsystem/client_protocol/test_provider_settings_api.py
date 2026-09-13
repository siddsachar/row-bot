"""Actual authenticated credential review/publication with isolated secret storage."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.providers import auth_store, config
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.providers.test_provider_settings_controls import store  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/settings/providers/openai'


def reviewed(client, headers, operation='save', value='synthetic-api-replacement'):
    response = client.get(BASE + '/credential', headers=headers)
    assert response.status_code == 200, response.text
    before = response.json()
    body = {'provider_revision': before['revision'], 'operation': operation,
            **({'value': value} if operation == 'save' else {})}
    response = client.post(BASE + '/credential-review', headers=headers, json=body)
    assert response.status_code == 200, response.text
    review = response.json()
    assert value not in response.text
    return {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
            'type': 'provider.credential.' + operation, 'expected_revision': '0', 'payload': {
                'provider_id': 'openai', 'provider_revision': before['revision'], 'nonce': review['nonce'],
                **({'value': value} if operation == 'save' else {})}}


def send(client, headers, command, path='/api/v1/settings/providers/commands'):
    return client.post(path, headers={**headers, 'Idempotency-Key': command['command_id']}, json=command)


def test_review_is_passive_save_clear_restore_and_private_receipt(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        before = Path(config.CONFIG_PATH).read_bytes()
        command = reviewed(client, headers)
        assert Path(config.CONFIG_PATH).read_bytes() == before and store.writes == []
        result = send(client, headers, command)
        assert result.status_code == 200, result.text
        assert result.json()['credential']['configured']
        writes = list(store.writes)
        assert send(client, headers, command).json() == result.json()
        assert store.writes == writes
        receipt = client.get(BASE + '/receipts/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()['published'] and receipt.json()['credential']['display_name'] == 'OpenAI API'
        assert receipt.headers['Cache-Control'] == 'no-store'
        assert 'synthetic-api-replacement' not in receipt.text + result.text
        assert client.get(BASE.replace('openai', 'anthropic') + '/receipts/' + command['command_id'], headers=headers).status_code == 404
        for operation in ('clear', 'restore'):
            value = reviewed(client, headers, operation)
            assert send(client, headers, value).status_code == 200
        assert auth_store.get_provider_secret('openai') == 'synthetic-api-replacement'
        with admissions.transaction() as conn:
            stored = str([tuple(row) for row in conn.execute('SELECT * FROM client_commands')])
        assert 'synthetic-api-replacement' not in stored and command['payload']['nonce'] not in stored


def test_exact_review_nonce_and_endpoint_reject_modified_or_foreign_commands(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = reviewed(client, headers)
        modified = copy.deepcopy(body)
        modified['payload']['value'] += 'different'
        response = send(client, headers, modified)
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'approval_expired'
        assert not store.writes
        response = send(client, headers, body, '/api/v1/conversations/commands')
        assert response.status_code == 422
        foreign = copy.deepcopy(body)
        foreign['client_session_id'] = str(uuid4())
        assert send(client, headers, foreign).status_code == 403
        response = client.post(BASE + '/credential-review', headers=headers, json={
            'provider_revision': body['payload']['provider_revision'], 'operation': 'clear', 'value': 'not permitted'})
        assert response.status_code == 422 and not store.writes


def test_lost_publication_ack_is_read_only_recoverable(service, store, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = reviewed(client, headers)
        monkeypatch.setattr(admissions, 'complete_command', lambda *a, **k: (_ for _ in ()).throw(OSError('Synthetic completion loss')))
        assert send(client, headers, body).status_code == 503
        before = Path(config.CONFIG_PATH).read_bytes()
        writes = list(store.writes)
        response = client.get(BASE + '/receipts/' + body['command_id'], headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed' and response.json()['published']
        assert store.writes == writes and Path(config.CONFIG_PATH).read_bytes() == before


def test_failed_secret_write_preserves_effective_value_and_uncertain_identity(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = reviewed(client, headers)
        store.failure = True
        response = send(client, headers, body)
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'provider_credential_unconfirmed'
        assert auth_store.get_provider_secret('openai') == 'old-synthetic-secret'
        receipt = client.get(BASE + '/receipts/' + body['command_id'], headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()['status'] == 'uncertain' and not receipt.json()['published']
