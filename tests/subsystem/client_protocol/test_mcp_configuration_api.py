"""Authenticated MCP saved settings, with isolated files and no live servers."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.mcp_client import config
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.mcp.test_capability_configuration_controls import owner  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/settings/mcp'


def review(client, headers, intent=None):
    page = client.get(BASE + '/configuration', headers=headers)
    assert page.status_code == 200, page.text
    payload = {'configuration_revision': page.json()['revision'], 'intent': intent or {
        'operation': 'add', 'fields': {'name': 'Synthetic MCP', 'transport': 'stdio',
            'command': 'synthetic-command', 'args': ['--synthetic-private-argument'], 'env': {'SYNTHETIC': 'private-value'}}}}
    response = client.post(BASE + '/configuration/review', headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert 'private-value' not in response.text
    return {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'], 'type': 'mcp.configuration.save',
            'expected_revision': '0', 'payload': {**payload, 'nonce': response.json()['nonce']}}


def send(client, headers, body, path=BASE + '/commands'):
    return client.post(path, headers={**headers, 'Idempotency-Key': body['command_id']}, json=body)


def client_for(service, security=None):
    return TestClient(create_client_platform_app(service, security=security, choices=lambda: {'models': [], 'capabilities': []}),
        base_url='http://localhost', client=('127.0.0.1', 12345))


def test_saved_configuration_add_edit_rename_import_preserves_private_launch_fields(service, owner):
    with client_for(service) as client:
        _, headers = bootstrap(client)
        body = review(client, headers)
        assert not config.CONFIG_PATH.exists()
        response = send(client, headers, body)
        assert response.status_code == 200, response.text
        assert response.json()['mcp_configuration']['saved_disabled']
        page = client.get(BASE + '/configuration', headers=headers).json()
        assert page['items'][0]['configured_fields'] == ['command', 'args', 'env']
        assert 'private-value' not in json.dumps(page) + response.text
        identity = page['items'][0]['server_id']
        for intent in ({'operation': 'edit', 'server_id': identity, 'fields': {'output_limit': 500}},
                       {'operation': 'rename', 'server_id': identity, 'fields': {'name': 'Renamed MCP'}},
                       {'operation': 'import', 'import_json': '{"mcpServers":{"Imported":{"command":"synthetic-other"}}}'}):
            command = review(client, headers, intent)
            result = send(client, headers, command)
            assert result.status_code == 200, result.text
        saved = config.read_saved_configuration().document['servers']
        assert saved['Renamed MCP']['env'] == {'SYNTHETIC': 'private-value'}
        assert not saved['Renamed MCP']['enabled'] and not saved['Imported']['enabled']
        receipt = client.get('/api/v1/commands/' + body['command_id'], headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert '_mcp_configuration' not in receipt.text and 'private-value' not in receipt.text


def test_review_tamper_or_foreign_namespace_cannot_claim_original(service, owner):
    with client_for(service) as client:
        _, headers = bootstrap(client)
        body = review(client, headers)
        bad = copy.deepcopy(body)
        bad['payload']['intent']['fields']['command'] = 'different-synthetic'
        response = send(client, headers, bad)
        assert response.status_code == 409 and response.json()['code'] == 'approval_expired'
        assert not config.CONFIG_PATH.exists()
        assert send(client, headers, body, '/api/v1/settings/providers/commands').status_code == 422
        assert send(client, headers, body).status_code == 200


def test_original_lost_ack_reconciles_without_new_approval_or_file_publication(service, owner, monkeypatch):
    security = ClientSecurity(service.instance_id)
    with client_for(service, security) as client:
        _, headers = bootstrap(client)
        body = review(client, headers)
        complete = admissions.complete_command
        monkeypatch.setattr(admissions, 'complete_command', lambda *a, **k: (_ for _ in ()).throw(OSError('Synthetic lost receipt')))
        response = send(client, headers, body)
        assert response.status_code == 503, response.text
        before = config.CONFIG_PATH.read_bytes()
        monkeypatch.setattr(admissions, 'complete_command', complete)
        security._nonces.clear()
        result = send(client, headers, body)
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'completed'
        assert config.CONFIG_PATH.read_bytes() == before
        bad = copy.deepcopy(body)
        bad['payload']['intent']['fields']['command'] = 'different-synthetic'
        assert send(client, headers, bad).status_code == 409
        assert config.CONFIG_PATH.read_bytes() == before
