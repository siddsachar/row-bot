"""Relation controls reach the canonical graph with exact reviewed authority."""
# ruff: noqa: F811 -- shared isolated fixtures.
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_knowledge_commands_api import BASE, review, send
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.knowledge_graph.test_knowledge_commands import client as knowledge_env, fields  # noqa: F401
from tests.subsystem.knowledge_graph.test_knowledge_projection_recovery import projection_stack  # noqa: F401

pytestmark = pytest.mark.subsystem
RELATIONS = '/api/v1/knowledge/relations'


def relation_command(client, headers, kind, payload):
    result = client.post(RELATIONS + '/review', headers=headers, json={'action': kind, 'payload': payload})
    assert result.status_code == 200, result.text
    return {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'], 'expected_revision': '0',
            'type': kind, 'payload': {**payload, 'review_id': result.json()['review_id']}}


def relation_send(client, headers, command):
    return client.post(RELATIONS + '/commands', headers=headers | {'Idempotency-Key': command['command_id']}, json=command)


def test_reviewed_add_remove_and_paired_supersession_with_original_recovery(service, knowledge_env, monkeypatch):
    _, kg, _ = knowledge_env
    now = [100.0]
    security = ClientSecurity(instance_id=service.instance_id, clock=lambda: now[0])
    app = create_client_platform_app(service, security=security, choices=lambda: {'models': [], 'capabilities': []})
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12345)) as client:
        _, headers = bootstrap(client)
        entities = []
        for subject in ('Synthetic original', 'Synthetic replacement'):
            saved = send(client, headers, review(client, headers, 'knowledge.create', values=fields(subject)))
            assert saved.status_code == 200, saved.text
            entity = client.get(BASE + '/editor', headers=headers, params={'entity_id': saved.json()['entity_id']})
            entities.append(entity.json()['entity'])
        first, second = entities
        now[0] += 30
        command = relation_command(client, headers, 'knowledge.relation.add', {
            'source_id': first['id'], 'target_id': second['id'], 'source_revision': first['revision'],
            'target_revision': second['revision'], 'relation_type': 'knows'})
        forged = {**command, 'payload': {**command['payload'], 'review_id': 'forged'}}
        assert relation_send(client, headers, forged).status_code == 409
        assert admissions.read_command_metadata(headers['X-Client-Session'], command['command_id']) is None
        saved = relation_send(client, headers, command)
        assert saved.status_code == 200 and saved.json()['outcome'] == 'saved', saved.text
        page = client.get(RELATIONS, headers=headers, params={'entity_id': first['id']})
        assert page.status_code == 200 and page.json()['total'] == 1, page.text
        edge = page.json()['items'][0]
        removed = relation_send(client, headers, relation_command(client, headers, 'knowledge.relation.remove', {
            'relation_id': edge['id'], 'relation_revision': edge['revision'],
            'source_revision': first['revision'], 'target_revision': second['revision']}))
        assert removed.status_code == 200 and removed.json()['outcome'] == 'removed', removed.text
        supersede = relation_command(client, headers, 'knowledge.supersede', {
            'old_id': first['id'], 'new_id': second['id'], 'old_revision': first['revision'], 'new_revision': second['revision']})
        replaced = relation_send(client, headers, supersede)
        assert replaced.status_code == 200 and replaced.json()['outcome'] == 'superseded', replaced.text
        old = client.get(BASE + '/editor', headers=headers, params={'entity_id': first['id']})
        assert old.json()['entity']['status'] == 'superseded'
        assert kg.get_entity(second['id']) is not None
        # A later recovery visit refills the unchanged production request budget.
        now[0] += 30
        monkeypatch.setattr(kg, 'add_relation', lambda *_a, **_k: pytest.fail('Never repeat original graph write'))
        assert relation_send(client, headers, command).json() == saved.json()
        receipt = client.get(RELATIONS + '/commands/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200 and receipt.json() == saved.json(), receipt.text
        _, foreign = bootstrap(client)
        assert client.get(RELATIONS + '/commands/' + command['command_id'], headers=foreign).status_code == 404


def test_relation_review_rejects_changed_entity_before_command_claim(service, knowledge_env):
    with _client(service) as client:
        _, headers = bootstrap(client)
        entities = []
        for subject in ('Synthetic first', 'Synthetic second'):
            saved = send(client, headers, review(client, headers, 'knowledge.create', values=fields(subject)))
            entities.append(client.get(BASE + '/editor', headers=headers,
                params={'entity_id': saved.json()['entity_id']}).json()['entity'])
        first, second = entities
        command = relation_command(client, headers, 'knowledge.relation.add', {
            'source_id': first['id'], 'target_id': second['id'], 'source_revision': first['revision'],
            'target_revision': second['revision'], 'relation_type': 'knows'})
        changed = send(client, headers, review(client, headers, 'knowledge.edit', first, fields('Changed source')))
        assert changed.status_code == 200
        result = relation_send(client, headers, command)
        assert result.status_code == 409 and result.json()['code'] == 'knowledge_changed', result.text
        assert admissions.read_command_metadata(headers['X-Client-Session'], command['command_id']) is None
