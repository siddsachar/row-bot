"""Graph saves use actual API, canonical SQLite transaction and durable receipt."""
# ruff: noqa: F811 -- imported pytest fixtures.
from uuid import uuid4

import pytest

from row_bot.application import task_commands
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_task_edit_commands import task_api, fields, body, send  # noqa: F401

pytestmark = pytest.mark.subsystem


def setup_graph(client, headers, fields):
    saved = send(client, headers, body(headers, fields)).json()
    path = f"/api/v1/tasks/{saved['task_id']}/graph"
    state = client.get(path, headers=headers)
    assert state.status_code == 200, state.text
    state = state.json()
    steps = [{key: step[key] for key in ('id', 'type', 'fields')} for step in state['steps']]
    steps[0]['fields']['prompt'] = 'Changed synthetic graph prompt'
    command = {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
               'type': 'task.graph.update', 'expected_revision': '0',
               'payload': {'task_id': saved['task_id'], 'task_revision': state['revision'], 'steps': steps}}
    return path, command


def test_graph_api_saves_stable_ids_and_rejects_stale_and_wrong_route(task_api, fields):
    owner, tasks = task_api
    with _client(owner) as client:
        _, headers = bootstrap(client)
        path, command = setup_graph(client, headers, fields)
        result = send(client, headers, command)
        assert result.status_code == 200, result.text
        assert result.json()['task_saved'] and result.json()['status'] == 'completed'
        assert send(client, headers, command).json() == result.json()
        graph = client.get(path, headers=headers).json()
        assert graph['steps'][0]['id'] == command['payload']['steps'][0]['id']
        assert graph['steps'][0]['fields']['prompt'] == 'Changed synthetic graph prompt'
        stale = {**command, 'command_id': str(uuid4())}
        response = send(client, headers, stale)
        assert response.status_code == 409 and response.json()['code'] == 'task_revision_conflict'
        wrong = client.post('/api/v1/conversations/tasks/commands', json=stale,
                            headers={**headers, 'Idempotency-Key': stale['command_id']})
        assert wrong.status_code == 422
        assert tasks.get_recent_runs() == [] and owner.list_conversations()['items'] == []


def test_lost_graph_receipt_reconciles_without_repeating_graph(task_api, fields, monkeypatch):
    owner, tasks = task_api
    with _client(owner) as client:
        _, headers = bootstrap(client)
        path, command = setup_graph(client, headers, fields)
        original = task_commands.update_saved_task_graph
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Synthetic lost response after SQL commit')
        monkeypatch.setattr(task_commands, 'update_saved_task_graph', lose)
        partial = send(client, headers, command)
        assert partial.status_code == 200, partial.text
        assert partial.json()['status'] == 'partial' and partial.json()['task_saved']
        tasks.update_task(command['payload']['task_id'], name='Concurrent saved name')
        monkeypatch.setattr(task_commands, 'update_saved_task_graph', lambda *a, **k: pytest.fail('Repeated saved graph'))
        replay = send(client, headers, command)
        assert replay.status_code == 200 and replay.json()['status'] == 'completed'
        assert tasks.get_task(command['payload']['task_id'])['name'] == 'Concurrent saved name'
        assert client.get(path, headers=headers).json()['steps'][0]['fields']['prompt'] == 'Changed synthetic graph prompt'
        with admissions.transaction() as conn:
            stored = dict(conn.execute('SELECT * FROM client_commands WHERE command_id=?', (command['command_id'],)).fetchone())
        assert 'Changed synthetic graph prompt' not in str(stored)


def test_graph_proof_failure_rolls_back_canonical_steps(task_api, fields, monkeypatch):
    owner, tasks = task_api
    with _client(owner) as client:
        _, headers = bootstrap(client)
        path, command = setup_graph(client, headers, fields)
        before = client.get(path, headers=headers).json()
        original = task_commands.update_saved_task_graph
        def fail_marker(*args, **kwargs):
            def reject(conn, identity):
                raise RuntimeError('Synthetic failed receipt transaction')
            kwargs['record_commit'] = reject
            return original(*args, **kwargs)
        monkeypatch.setattr(task_commands, 'update_saved_task_graph', fail_marker)
        response = send(client, headers, command)
        assert response.status_code == 503 and response.json()['code'] == 'dependency_unavailable', response.text
        assert client.get(path, headers=headers).json() == before
