"""Authenticated saved-default model review/CAS over the real isolated owner."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
import json
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_provider_configuration_api import send
from tests.subsystem.providers.test_provider_default_model import saved, store  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/settings/providers/default-model'


def review(client, headers):
    before = client.get(BASE, headers=headers)
    assert before.status_code == 200, before.text
    payload = {'settings_revision': before.json()['revision'], 'provider_id': 'openai', 'model_id': 'same'}
    response = client.post(BASE + '/review', headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
        'type': 'provider.default_model.save', 'expected_revision': '0',
        'payload': {**payload, 'nonce': response.json()['nonce']}}


def test_review_is_passive_save_uses_exact_provider_and_preserves_other_settings(service, saved):
    saved.write_text(json.dumps({'context_size': 48000, 'retained': {'keep': True}}))
    with _client(service) as client:
        _, headers = bootstrap(client)
        before = saved.read_bytes()
        command = review(client, headers)
        assert saved.read_bytes() == before
        response = send(client, headers, command)
        assert response.status_code == 200, response.text
        assert response.json()['selection']['selection_ref'] == 'model:openai:same'
        value = json.loads(saved.read_text())
        assert value['retained'] == {'keep': True} and value['context_size'] == 48000
        receipt = client.get(BASE + '/receipts/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()['published'] and receipt.headers['Cache-Control'] == 'no-store'


def test_modified_review_cannot_claim_original_and_stale_settings_do_not_publish(service, saved):
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = review(client, headers)
        modified = copy.deepcopy(body)
        modified['payload']['provider_id'] = 'anthropic'
        response = send(client, headers, modified)
        assert response.status_code == 409 and response.json()['code'] == 'approval_expired'
        assert send(client, headers, body).status_code == 200
        stale = review(client, headers)
        raw = json.loads(saved.read_text())
        saved.write_text(json.dumps({**raw, 'concurrent_setting': True}))
        before = saved.read_bytes()
        response = send(client, headers, stale)
        assert response.status_code == 409 and response.json()['code'] == 'revision_conflict'
        assert saved.read_bytes() == before


def test_lost_ack_has_passive_receipt_without_another_publication(service, saved, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        body = review(client, headers)
        monkeypatch.setattr(admissions, 'complete_command', lambda *a, **k: (_ for _ in ()).throw(OSError('Synthetic lost ack')))
        assert send(client, headers, body).status_code == 503
        before = saved.read_bytes()
        response = client.get(BASE + '/receipts/' + body['command_id'], headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed' and response.json()['published']
        assert saved.read_bytes() == before
