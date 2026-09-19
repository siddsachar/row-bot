"""MCP authorization wire controls retain canonical safety and configuration."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
from uuid import uuid4

import pytest

from row_bot.mcp_client import config
from row_bot.api.v1.security import ClientSecurity, current_policy_snapshot
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for, send
from tests.subsystem.mcp.test_capability_policy_controls import owner, target  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/settings/mcp/policy'


def reviewed(client, headers, intent):
    snapshot = client.get(BASE, headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    payload = {'configuration_revision':snapshot.json()['revision'],'intent':intent}
    result = client.post(BASE + '/review',headers=headers,json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['saved_disabled'] is None
    return {'command_id':str(uuid4()),'client_session_id':headers['X-Client-Session'],'expected_revision':'0',
            'type':'mcp.configuration.control','payload':{**payload,'nonce':result.json()['nonce']}}


def test_global_server_tool_utility_controls_use_exact_review_without_connection_effects(service, owner, monkeypatch):
    from row_bot.mcp_client import runtime
    for name in ('discover_enabled_servers','stop_server','refresh_server','probe_server'):
        monkeypatch.setattr(runtime,name,lambda *_a,**_k:pytest.fail('Saved permissions cannot start connection work'))
    clock = [0.0]
    security = ClientSecurity(service.instance_id, clock=lambda:clock[0], policy=current_policy_snapshot)
    with client_for(service, security) as client:
        _, headers = bootstrap(client)
        page = client.get(BASE,params={'server_id':target()},headers=headers)
        assert page.status_code == 200, page.text
        rows = {item['name']:item for item in page.json()['items']}
        assert rows['delete_record']['approval_locked'] and rows['delete_record']['requires_approval']
        forbidden = {'configuration_revision':page.json()['revision'],'intent':{'operation':'tool_approval',
            'server_id':target(),'tool_id':rows['delete_record']['tool_id'],'enabled':False}}
        denied = client.post(BASE + '/review',headers=headers,json=forbidden)
        assert denied.status_code == 409 and denied.json()['code'] == 'approval_required', denied.text
        intents = [
            {'operation':'global_enabled','enabled':False},
            {'operation':'server_enabled','server_id':target(),'enabled':False},
            {'operation':'tool_enabled','server_id':target(),'tool_id':rows['read']['tool_id'],'enabled':False},
            {'operation':'tool_approval','server_id':target(),'tool_id':rows['read']['tool_id'],'enabled':True},
            {'operation':'utility_enabled','server_id':target(),'utility':'resources','enabled':False},
            {'operation':'utility_enabled','server_id':target(),'utility':'prompts','enabled':False},
        ]
        for intent in intents:
            clock[0] += 2
            command = reviewed(client,headers,intent)
            forged = copy.deepcopy(command)
            forged['payload']['intent']['enabled'] = not intent['enabled']
            assert send(client,headers,forged).status_code == 409
            result = send(client,headers,command)
            assert result.status_code == 200, result.text
            assert result.json()['mcp_configuration']['saved_disabled'] is None
        saved = config.read_saved_configuration().document
        assert saved['future'] == owner['future']
        assert saved['servers']['Synthetic']['env'] == owner['servers']['Synthetic']['env']
        assert saved['servers']['Synthetic']['tools']['enabled']['read'] is False
        assert saved['servers']['Synthetic']['tools']['require_approval'] == ['read']
