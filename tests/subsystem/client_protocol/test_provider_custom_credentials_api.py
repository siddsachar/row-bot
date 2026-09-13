"""Custom endpoint credential UI API with real isolated canonical publication."""
# ruff: noqa: F811 -- shared isolated fixtures.
from uuid import uuid4
from pathlib import Path
import copy

import pytest

from row_bot.providers import auth_store, config
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_provider_configuration_api import reviewed as endpoint_review, send
from tests.subsystem.providers.test_provider_settings_controls import store  # noqa: F401

pytestmark = pytest.mark.subsystem
PROVIDER = 'custom_openai_synthetic'
BASE = '/api/v1/settings/providers/' + PROVIDER


def review(client, headers, operation='save'):
    snapshot = client.get(BASE + '/credential', headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    fields = {'provider_revision': snapshot.json()['revision'], 'operation': operation,
              **({'value': 'synthetic-custom-value'} if operation == 'save' else {})}
    value = client.post(BASE + '/credential-review', headers=headers, json=fields)
    assert value.status_code == 200, value.text
    assert 'synthetic-custom-value' not in value.text
    return {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
            'type': 'provider.custom_credential.' + operation, 'expected_revision': '0', 'payload': {
                'provider_id': PROVIDER, 'provider_revision': fields['provider_revision'], 'nonce': value.json()['nonce'],
                **({'value': fields['value']} if operation == 'save' else {})}}


def test_custom_save_clear_restore_and_receipt_retain_endpoint_identity(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        assert send(client, headers, endpoint_review(client, headers)).status_code == 200
        for operation in ('save', 'clear', 'restore'):
            before = Path(config.CONFIG_PATH).read_bytes()
            body = review(client, headers, operation)
            assert Path(config.CONFIG_PATH).read_bytes() == before
            saved = send(client, headers, body)
            assert saved.status_code == 200, saved.text
            assert saved.json()['credential']['configured'] == (operation != 'clear')
            writes = list(store.writes)
            receipt = client.get(BASE + '/receipts/' + body['command_id'], headers=headers)
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()['status'] == 'completed'
            assert receipt.json()['credential']['provider_id'] == PROVIDER
            assert receipt.json()['credential']['display_name'] == 'Synthetic endpoint'
            assert store.writes == writes
            assert 'synthetic-custom-value' not in receipt.text + saved.text
        assert auth_store.get_provider_secret(PROVIDER) == 'synthetic-custom-value'


def test_custom_review_tamper_cannot_claim_original_or_cross_endpoint_namespace(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        assert send(client, headers, endpoint_review(client, headers)).status_code == 200
        body = review(client, headers)
        bad = copy.deepcopy(body)
        bad['payload']['value'] = 'different-synthetic'
        response = send(client, headers, bad)
        assert response.status_code == 409 and response.json()['code'] == 'approval_expired'
        assert not store.writes
        assert send(client, headers, body).status_code == 200
        assert client.get(BASE.replace(PROVIDER, 'custom_openai_other') + '/receipts/' + body['command_id'], headers=headers).status_code == 404
