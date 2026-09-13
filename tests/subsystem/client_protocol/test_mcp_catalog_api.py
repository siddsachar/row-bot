"""Saved MCP Test catalogs require exact review and never launch a second Test."""
# ruff: noqa: F811 -- shared isolated fixtures.
from uuid import uuid4

import pytest

from row_bot.application import capability_runtime_controls as lifecycle
from row_bot.mcp_client import config
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for, send
from tests.subsystem.mcp.test_capability_catalog_controls import owner, test_request  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/settings/mcp/catalog'


def prepared(service):
    command = test_request()
    result = lifecycle.execute_mcp_runtime_command(owner_id=service.instance_id, key=command['command_id'],
        command=command, validate=lambda: None, validate_review=lambda _: None)
    assert result['mcp_runtime']['state'] == 'tested'
    return command


def test_tested_catalog_wire_approvals_current_configuration_and_read_only_replay(service, owner, monkeypatch):
    tested = prepared(service)
    monkeypatch.setattr(owner.runtime, 'launch_server_owned', lambda *_a, **_k: pytest.fail('Catalog acceptance cannot retest'))
    with client_for(service) as client:
        _, headers = bootstrap(client)
        query = {'server_id':tested['payload']['server_id'], 'test_command_id':tested['command_id']}
        page = client.get(BASE, params=query, headers=headers)
        assert page.status_code == 200 and page.json()['total'] == 3, page.text
        payload = {**query, 'configuration_revision':page.json()['configuration_revision']}
        review = client.post(BASE + '/review', headers=headers, json=payload)
        assert review.status_code == 200 and review.json()['tool_count'] == 3, review.text
        command = {'command_id':str(uuid4()), 'client_session_id':headers['X-Client-Session'], 'expected_revision':'0',
            'type':'mcp.catalog.accept', 'payload':{**payload,'nonce':'forged'}}
        assert send(client, headers, command).status_code == 409
        assert admissions.read_command_metadata(service.instance_id, command['command_id']) is None
        command['payload']['nonce'] = review.json()['nonce']
        result = send(client, headers, command)
        assert result.status_code == 200 and result.json()['mcp_configuration']['status'] == 'saved', result.text
        saved = config.CONFIG_PATH.read_bytes()
        assert send(client, headers, command).json() == result.json()
        assert config.CONFIG_PATH.read_bytes() == saved
        receipt = client.get('/api/v1/commands/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200 and '_mcp_configuration' not in receipt.text, receipt.text
        assert 'synthetic-secret' not in page.text + review.text + result.text + receipt.text
        stale = client.get(BASE, params=query, headers=headers)
        assert stale.status_code == 200 and stale.json()['availability'] == 'stale', stale.text
        assert owner.calls == ['connect', 'list_tools']


def test_foreign_test_catalog_and_changed_test_id_do_not_admit_acceptance(service, owner):
    tested = prepared(service)
    with client_for(service) as client:
        _, headers = bootstrap(client)
        query = {'server_id':tested['payload']['server_id'], 'test_command_id':str(uuid4())}
        page = client.get(BASE, params=query, headers=headers)
        assert page.status_code == 200 and page.json()['availability'] == 'unavailable', page.text
        payload = {**query, 'configuration_revision':page.json()['configuration_revision']}
        result = client.post(BASE + '/review', headers=headers, json=payload)
        assert result.status_code == 409 and result.json()['code'] == 'mcp_catalog_unavailable', result.text
        assert owner.calls == ['connect', 'list_tools']
