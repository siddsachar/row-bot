"""Authenticated endpoint configuration over canonical isolated owners."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.providers import config, custom
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.providers.test_provider_configuration_controls import fields
from tests.subsystem.providers.test_provider_settings_controls import store  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/settings/providers/configuration'


def reviewed(client, headers, operation='provider.endpoint.create', values=None):
    response = client.get(BASE, headers=headers)
    assert response.status_code == 200, response.text
    body = {'operation': operation, 'configuration_revision': response.json()['revision'],
            'fields': fields() if values is None else values}
    response = client.post(BASE + '/review', headers=headers, json=body)
    assert response.status_code == 200, response.text
    return {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
            'type': operation, 'expected_revision': '0', 'payload': {
                'configuration_revision': body['configuration_revision'], 'fields': body['fields'],
                'nonce': response.json()['nonce']}}


def send(client, headers, command, path='/api/v1/settings/providers/commands'):
    return client.post(path, headers={**headers, 'Idempotency-Key': command['command_id']}, json=command)


def test_create_edit_delete_review_is_passive_replay_retains_proof(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        before = Path(config.CONFIG_PATH).read_bytes()
        command = reviewed(client, headers)
        assert Path(config.CONFIG_PATH).read_bytes() == before and store.writes == []
        response = send(client, headers, command)
        assert response.status_code == 200, response.text
        saved = Path(config.CONFIG_PATH).read_bytes()
        assert send(client, headers, command).json() == response.json()
        assert Path(config.CONFIG_PATH).read_bytes() == saved and store.writes == []
        receipt = client.get(BASE + '/receipts/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()['status'] == 'completed' and receipt.headers['Cache-Control'] == 'no-store'
        endpoint = custom.get_custom_endpoint('synthetic')
        assert endpoint['base_url'] == fields()['base_url']
        for operation, values in [('provider.endpoint.save', {**fields(), 'enabled': False}),
                                  ('provider.endpoint.delete', {'endpoint_id': 'synthetic'})]:
            changed = reviewed(client, headers, operation, values)
            response = send(client, headers, changed)
            assert response.status_code == 200, response.text
        assert custom.get_custom_endpoint('synthetic') is None and store.writes == []


def test_review_nonce_binds_exact_operation_fields_session_and_endpoint(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        changed = copy.deepcopy(command)
        changed['payload']['fields']['base_url'] = 'http://127.0.0.1:8999/v1'
        response = send(client, headers, changed)
        assert response.status_code == 409 and response.json()['code'] == 'approval_expired'
        assert custom.get_custom_endpoint('synthetic') is None
        changed = copy.deepcopy(command)
        changed['client_session_id'] = str(uuid4())
        assert send(client, headers, changed).status_code == 403
        assert send(client, headers, command, '/api/v1/conversations/commands').status_code == 422
        assert send(client, headers, command).status_code == 200


def test_unconfirmed_published_configuration_has_passive_receipt(service, store, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        monkeypatch.setattr(admissions, 'complete_command', lambda *a, **k: (_ for _ in ()).throw(OSError('synthetic acknowledgement loss')))
        assert send(client, headers, command).status_code == 503
        saved = Path(config.CONFIG_PATH).read_bytes()
        receipt = client.get(BASE + '/receipts/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()['status'] == 'completed'
        assert Path(config.CONFIG_PATH).read_bytes() == saved and store.writes == []


def test_invalid_endpoint_and_stale_configuration_are_rejected_before_mutation(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        cfg = config.load_provider_config(strict=True)
        cfg['retained_unknown'] = {'keep': True}
        config.save_provider_config(cfg)
        before = Path(config.CONFIG_PATH).read_bytes()
        response = send(client, headers, command)
        assert response.status_code == 409 and response.json()['code'] == 'revision_conflict'
        invalid = {'operation': 'provider.endpoint.create', 'configuration_revision': config.provider_config_revision(cfg),
                   'fields': {**fields(), 'base_url': 'https://user:password@example.invalid/v1'}}
        response = client.post(BASE + '/review', headers=headers, json=invalid)
        assert response.status_code == 422, response.text
        assert Path(config.CONFIG_PATH).read_bytes() == before and store.writes == []
