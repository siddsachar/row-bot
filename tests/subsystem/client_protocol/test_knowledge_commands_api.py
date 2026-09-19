"""Entity edits use exact authenticated review and canonical saved projections."""
# ruff: noqa: F811 -- shared isolated fixtures.
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.knowledge_graph.test_knowledge_commands import client as knowledge_env, fields  # noqa: F401
from tests.subsystem.knowledge_graph.test_knowledge_projection_recovery import projection_stack  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = '/api/v1/knowledge/entities'


def review(client, headers, action, entity=None, values=None):
    payload = {'entity_id':entity['id'] if entity else None, 'revision':entity['revision'] if entity else ''}
    if action in {'knowledge.create', 'knowledge.edit'}:
        payload['fields'] = values or fields()
    result = client.post(BASE + '/review', headers=headers, json={'action':action,'payload':payload})
    assert result.status_code == 200, result.text
    payload.update(revision=result.json()['revision'], review_id=result.json()['review_id'])
    return {'command_id':str(uuid4()), 'client_session_id':headers['X-Client-Session'], 'type':action,
            'expected_revision':'0', 'payload':payload}


def send(client, headers, command):
    return client.post(BASE + '/commands', headers=headers | {'Idempotency-Key':command['command_id']}, json=command)


def test_entity_create_edit_archive_restore_and_readonly_original_recovery(service, knowledge_env, monkeypatch):
    api, kg, _ = knowledge_env
    with _client(service) as client:
        _, headers = bootstrap(client)
        empty = client.get(BASE + '/editor', headers=headers)
        assert empty.status_code == 200 and empty.json()['entity'] is None, empty.text
        original = review(client, headers, 'knowledge.create')
        forged = {**original, 'payload':{**original['payload'], 'review_id':'forged'}}
        assert send(client, headers, forged).status_code == 409
        assert admissions.read_command_metadata(headers['X-Client-Session'], original['command_id']) is None
        saved = send(client, headers, original)
        assert saved.status_code == 200 and saved.json()['projection_state'] == 'pending', saved.text
        identifier = saved.json()['entity_id']
        for action in ('knowledge.edit', 'knowledge.archive', 'knowledge.restore'):
            current = client.get(BASE + '/editor', params={'entity_id':identifier}, headers=headers)
            assert current.status_code == 200, current.text
            result = send(client, headers, review(client, headers, action, current.json()['entity'], fields('Changed subject')))
            assert result.status_code == 200 and result.json()['saved_state'] == 'saved', result.text
        monkeypatch.setattr(kg, 'save_entity', lambda *_a, **_k: pytest.fail('Original receipt must not repeat source save'))
        receipt = client.get(BASE + '/commands/' + original['command_id'], headers=headers)
        assert receipt.status_code == 200 and receipt.json() == saved.json(), receipt.text
        assert send(client, headers, original).json() == saved.json()
        _, foreign = bootstrap(client)
        assert client.get(BASE + '/commands/' + original['command_id'], headers=foreign).status_code == 404
        assert '_knowledge' not in receipt.text
        assert api.read_entity_editor(identifier, validate=lambda: None)['entity']['status'] == 'active'


def test_lost_save_reply_never_repeats_entity_mutation(service, knowledge_env, monkeypatch):
    api, kg, _ = knowledge_env
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers, 'knowledge.create')
        original = admissions.complete_command
        def lose(owner, key, result):
            if result.get('saved_state') == 'saved':
                raise OSError('Synthetic lost commit acknowledgement')
            return original(owner, key, result)
        monkeypatch.setattr(admissions, 'complete_command', lose)
        response = send(client, headers, command)
        assert response.status_code != 200
        entity_id = command['command_id'].replace('-', '')[:12]
        assert kg.get_entity(entity_id) is not None
        monkeypatch.setattr(kg, 'save_entity', lambda *_a, **_k: pytest.fail('No repeated write after lost commit proof'))
        receipt = client.get(BASE + '/commands/' + command['command_id'], headers=headers)
        assert receipt.status_code == 200 and receipt.json()['status'] == 'partial', receipt.text
        assert send(client, headers, command).json() == receipt.json()
