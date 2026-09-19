# ruff: noqa: F811 -- canonical isolated projection fixture.
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from uuid import uuid4

import pytest
from tests.subsystem.knowledge_graph.test_knowledge_projection_recovery import projection_stack  # noqa: F401


@pytest.fixture
def client(projection_stack, tmp_path, monkeypatch):
    from row_bot import tasks, memory_evolution
    from row_bot.application import knowledge_commands as api
    kg = projection_stack[0]
    monkeypatch.setattr(kg, '_skip_reindex', False)
    monkeypatch.setattr(tasks, '_DB_PATH', str(tmp_path / 'tasks.db'))
    monkeypatch.setattr(tasks, '_SCHEMA_READY_PATH', None)
    importlib.reload(memory_evolution)
    def forbidden(*a, **k):
        pytest.fail('A client save must not call a projection/provider')
    monkeypatch.setattr(kg, '_get_embedding_model', forbidden)
    return api, kg, {'owner_id': 'test-owner', 'authority_id': 'test-session', 'validate': lambda: None}


def fields(subject='Synthetic subject'):
    return {'entity_type': 'fact', 'subject': subject, 'description': 'Synthetic description', 'aliases': '', 'tags': ''}


def reviewed(client, kind='knowledge.create', entity=None, changes=None):
    api, _, _ = client
    payload = {'entity_id': entity['id'] if entity else None, 'revision': api._digest(entity) if entity else ''}
    if kind in {'knowledge.create', 'knowledge.edit'}:
        payload['fields'] = changes or fields()
    review = api.read_knowledge_review(kind, payload, validate=lambda: None)
    payload.update(revision=review['revision'], review_id='test-review')
    return {'command_id': str(uuid4()), 'type': kind, 'payload': payload}


def execute(client, command, **callbacks):
    api, _, context = client
    return api.execute_knowledge_command(command, key=command['command_id'], **context,
        **{'validate_action': lambda kind: None, 'validate_review': lambda command, review: None, **callbacks})


def saved(client, subject='Synthetic subject', properties=None):
    _, kg, _ = client
    with kg.projection_batch(drain_on_exit=False):
        return kg.save_entity('fact', subject, 'Original body', properties=properties)


def reviewed_maintenance(client, kind, entities):
    api, _, _ = client
    page = api.knowledge_views.list_saved_entities(limit=1)
    targets = [
        {
            'entity_id': entity['id'],
            'revision': api.knowledge_views.read_saved_entity_detail(entity['id']).revision,
        }
        for entity in entities
    ]
    intent = {'catalog_revision': page.revision, 'targets': targets}
    review = api.read_knowledge_maintenance_review(kind, intent, validate=lambda: None)
    return {
        'command_id': str(uuid4()),
        'type': kind,
        'payload': {**intent, 'action_digest': review['action_digest'], 'review_id': 'test-review'},
    }, review


def execute_maintenance(client, command, review):
    api, _, context = client
    return api.execute_knowledge_maintenance_command(
        command,
        owner_id=context['owner_id'],
        key=command['command_id'],
        validate=context['validate'],
        validate_review=lambda _command, actual: actual == review or pytest.fail('review changed'),
    )


def test_create_saved_pending_and_duplicate_is_read_only(client, monkeypatch):
    api, kg, context = client
    command = reviewed(client)
    result = execute(client, command)
    assert result['saved_state'] == 'saved' and result['projection_state'] == 'pending'
    assert result['entity_id'] == command['command_id'].replace('-', '')[:12]
    entity = kg.get_entity(result['entity_id'])
    assert json.loads(entity['properties'])['source_context']['actor'] == 'manual'
    assert set(api.ENTITY_TYPES) == kg.VALID_ENTITY_TYPES
    def forbidden(*a, **kw):
        pytest.fail('receipt must not mutate or repair')
    monkeypatch.setattr(kg, 'save_entity', forbidden)
    monkeypatch.setattr(kg, 'repair_projections', forbidden)
    assert execute(client, command) == result
    monkeypatch.setattr(api.admissions, 'transaction', forbidden)
    assert api.read_knowledge_command(command_id=command['command_id'], **context) == result
    assert '_knowledge' not in json.dumps(result) and 'Synthetic' not in json.dumps(result)


@pytest.mark.parametrize(
    ('kind', 'count'),
    [('knowledge.delete', 1), ('knowledge.delete.bulk', 2)],
)
def test_reviewed_exact_deletion_cascades_and_replays_without_repeating(client, monkeypatch, kind, count):
    api, kg, context = client
    monkeypatch.setattr(kg, '_skip_reindex', True)
    rows = [saved(client, f'Delete {index}') for index in range(count)]
    if count == 2:
        with kg.projection_batch(drain_on_exit=False):
            kg.add_relation(rows[0]['id'], rows[1]['id'], 'related_to')
    command, review = reviewed_maintenance(client, kind, rows)
    result = execute_maintenance(client, command, review)
    assert result['status'] == 'completed'
    assert result['deleted'] == [row['id'] for row in rows]
    assert all(kg.get_entity(row['id']) is None for row in rows)
    monkeypatch.setattr(kg, 'delete_reviewed_entities', lambda *_a, **_k: pytest.fail('no replay'))
    assert execute_maintenance(client, command, review) == result
    assert api.read_knowledge_maintenance_command(
        owner_id=context['owner_id'], command_id=command['command_id'], validate=lambda: None
    ) == result
    with pytest.raises(ValueError, match='knowledge_operation_unavailable'):
        api.read_knowledge_maintenance_command(
            owner_id='other-owner', command_id=command['command_id'], validate=lambda: None
        )


def test_delete_all_requires_exact_catalog_and_reports_cleanup_truthfully(client, monkeypatch):
    _, kg, _ = client
    monkeypatch.setattr(kg, '_skip_reindex', True)
    rows = [saved(client, 'First'), saved(client, 'Second')]
    cleared = []
    from row_bot import wiki_vault
    monkeypatch.setattr(wiki_vault, 'clear_wiki_folder', lambda: cleared.append(True) or 2)
    command, review = reviewed_maintenance(client, 'knowledge.delete_all', [])
    assert review['entity_count'] == 2
    result = execute_maintenance(client, command, review)
    assert result['status'] == 'completed'
    assert result['deleted'] == [row['id'] for row in sorted(rows, key=lambda row: row['id'])]
    assert result['cleanup'] == {
        'lexical_index': 'completed', 'vector_index': 'skipped', 'wiki': 'completed'
    }
    assert cleared == [True] and kg.count_entities() == 0


def test_maintenance_stale_review_rejects_before_deletion(client):
    _, kg, _ = client
    row = saved(client)
    command, review = reviewed_maintenance(client, 'knowledge.delete', [row])
    with kg.projection_batch(drain_on_exit=False):
        kg.update_entity(row['id'], 'Concurrent change')
    with pytest.raises(ValueError, match='knowledge_changed'):
        execute_maintenance(client, command, review)
    assert kg.get_entity(row['id']) is not None


def test_maintenance_partial_cleanup_and_lost_receipt_are_never_resent(client, monkeypatch):
    api, kg, context = client
    monkeypatch.setattr(kg, '_skip_reindex', True)
    row = saved(client)
    command, review = reviewed_maintenance(client, 'knowledge.delete', [row])
    monkeypatch.setattr(kg, '_delete_fts_entity', lambda *_: (_ for _ in ()).throw(OSError('cleanup')))
    result = execute_maintenance(client, command, review)
    assert result['status'] == 'partial'
    assert result['cleanup']['lexical_index'] == 'failed'
    assert result['deleted'] == [row['id']]

    second = saved(client, 'Lost receipt')
    command, review = reviewed_maintenance(client, 'knowledge.delete', [second])
    monkeypatch.setattr(api.admissions, 'complete_command', lambda *_: (_ for _ in ()).throw(OSError('lost receipt')))
    with pytest.raises(OSError, match='lost receipt'):
        execute_maintenance(client, command, review)
    receipt = api.read_knowledge_maintenance_command(
        owner_id=context['owner_id'], command_id=command['command_id'], validate=lambda: None
    )
    assert receipt['status'] == 'partial' and receipt['code'] == 'knowledge_outcome_uncertain'
    assert kg.get_entity(second['id']) is None


def test_edit_one_commit_preserves_private_metadata_and_marks_manual(client):
    api, kg, _ = client
    original = saved(client, properties={'private': 'keep', 'status': 'needs_review', 'review_reason': 'old'})
    before = kg._projection_state()['revision']
    result = execute(client, reviewed(client, 'knowledge.edit', original, fields('Changed')))
    entity = kg.get_entity(result['entity_id'])
    assert entity['subject'] == 'Changed'
    props = json.loads(entity['properties'])
    assert props['private'] == 'keep' and props['status'] == 'active' and 'review_reason' not in props
    assert kg._projection_state()['revision'] == before + 1
    public = api.read_entity_editor(entity['id'], validate=lambda: None)
    assert 'private' not in json.dumps(public)


@pytest.mark.parametrize('kind,status,expected', [('knowledge.archive','active','archived'), ('knowledge.restore','archived','active'), ('knowledge.resolve','needs_review','active')])
def test_lifecycle_canonical_properties_and_journal(client, kind, status, expected):
    from row_bot import memory_evolution as evo
    original = saved(client, properties={'status': status, 'private': 'keep', 'review_reason': 'old'})
    result = execute(client, reviewed(client, kind, original))
    entity = client[1].get_entity(result['entity_id'])
    props = json.loads(entity['properties'])
    assert props['status'] == expected and props['private'] == 'keep'
    assert evo.get_journal()[-1]['entity_id'] == original['id']


def test_full_row_conflict_including_timestamp_less_recall_preserves_source(client):
    _, kg, _ = client
    original = saved(client)
    command = reviewed(client, 'knowledge.edit', original)
    kg.touch_recalled([original['id']])
    with pytest.raises(ValueError, match='knowledge_changed'):
        execute(client, command)
    assert kg.get_entity(original['id'])['description'] == 'Original body'


def test_stale_row_after_review_before_write_admission_rejected(client, monkeypatch):
    api, kg, _ = client
    original = saved(client)
    command = reviewed(client, 'knowledge.edit', original)
    old = kg.update_entity
    def race(*a, **kw):
        with kg.projection_batch(drain_on_exit=False):
            old(original['id'], 'Concurrent source')
        return old(*a, **kw)
    monkeypatch.setattr(kg, 'update_entity', race)
    result = execute(client, command)
    assert result['status'] == 'rejected'
    assert kg.get_entity(original['id'])['description'] == 'Concurrent source'


def test_current_authority_after_actual_writer_wait_rolls_back(client, monkeypatch):
    _, kg, _ = client
    original = saved(client)
    entered, revoked = threading.Event(), threading.Event()
    original_conn = kg._get_conn
    def connect():
        entered.set()
        return original_conn()
    monkeypatch.setattr(kg, '_get_conn', connect)
    sentinel = PermissionError('synthetic revocation')
    def validate():
        if revoked.is_set():
            raise sentinel
    with sqlite3.connect(kg.DB_PATH) as held, ThreadPoolExecutor(max_workers=1) as pool:
        held.execute('BEGIN IMMEDIATE')
        future = pool.submit(kg.update_entity, original['id'], 'Forbidden', expected_entity=original, validate=validate)
        assert entered.wait(5)
        revoked.set()
        held.rollback()
        with pytest.raises(PermissionError) as caught:
            future.result(5)
    assert caught.value is sentinel and kg.get_entity(original['id']) == original


def test_final_validation_rejection_rolls_back_source_and_projection_intent(client):
    _, kg, _ = client
    original = saved(client)
    calls = []
    def validate():
        calls.append(True)
        if len(calls) == 2:
            raise PermissionError('precommit')
    with pytest.raises(PermissionError, match='precommit'):
        kg.update_entity(original['id'], 'Forbidden', expected_entity=original, validate=validate)
    assert kg.get_entity(original['id']) == original


def test_create_user_reuses_reviewed_identity_without_overwrite(client):
    _, kg, _ = client
    original = saved(client, 'User')
    command = reviewed(client, changes=fields(' USER '))
    result = execute(client, command)
    assert result['reused'] is True and result['entity_id'] == original['id']
    assert kg.get_entity(original['id']) == original


def test_user_appearing_after_review_is_not_overwritten(client, monkeypatch):
    _, kg, _ = client
    command = reviewed(client, changes=fields('User'))
    old = kg.save_entity
    made = []
    def race(*a, **kw):
        with kg.projection_batch(drain_on_exit=False):
            made.append(old('person', 'User', 'Concurrent User'))
        return old(*a, **kw)
    monkeypatch.setattr(kg, 'save_entity', race)
    with pytest.raises(ValueError, match='Canonical User changed'):
        execute(client, command)
    assert kg.get_entity(made[0]['id'])['description'] == 'Concurrent User'
    assert kg.count_entities() == 1


def test_postcommit_missing_receipt_proof_stays_uncertain_without_replay(client, monkeypatch):
    api, kg, context = client
    command = reviewed(client)
    def fault(*a, **kw):
        raise OSError('synthetic receipt failure')
    monkeypatch.setattr(api.admissions, 'complete_command', fault)
    with pytest.raises(OSError, match='receipt failure'):
        execute(client, command)
    assert kg.count_entities() == 1
    receipt = api.read_knowledge_command(command_id=command['command_id'], **context)
    assert receipt['status'] == 'partial' and receipt['code'] == 'knowledge_outcome_uncertain'
    assert execute(client, command) == receipt
    assert kg.count_entities() == 1


def test_original_command_changed_payload_or_session_rejected(client):
    api, _, context = client
    command = reviewed(client)
    execute(client, command)
    command['payload']['fields']['subject'] = 'Other'
    with pytest.raises(ValueError, match='idempotency_mismatch'):
        execute(client, command)
    with pytest.raises(ValueError, match='knowledge_operation_unavailable'):
        api.read_knowledge_command(command_id=command['command_id'], **{**context, 'authority_id': 'other'})


@pytest.mark.parametrize('field,value', [('subject',''), ('subject','x'*257), ('description','x'*32769), ('entity_type','unknown'), ('aliases','x'*4097), ('tags','x\0y')], ids=['empty-subject','long-subject','long-description','invalid-type','long-aliases','null-tag'])
def test_invalid_fields_rejected_before_initialization(client, field, value):
    payload = fields()
    payload[field] = value
    with pytest.raises(ValueError, match='invalid_knowledge_fields'):
        reviewed(client, changes=payload)


def test_oversized_saved_fields_cannot_be_silently_overwritten(client):
    api, kg, _ = client
    original = saved(client)
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.execute('UPDATE entities SET description=? WHERE id=?', ('x'*32769, original['id']))
    with pytest.raises(ValueError, match='knowledge_unavailable'):
        api.read_entity_editor(original['id'], validate=lambda: None)


def test_passive_cold_reads_never_create_domain_admissions_or_keys(tmp_path):
    data = tmp_path / 'absent'
    code = '''
import sys
from pathlib import Path
from row_bot.application.knowledge_commands import read_entity_editor
from row_bot.runtime import admissions
assert read_entity_editor(None, validate=lambda:None)['entity'] is None
assert admissions.read_command_receipt('owner','missing') is None
try: admissions.keyed_digest({}, read_only=True)
except admissions.AdmissionError: pass
else: raise AssertionError('missing key accepted')
assert not Path(sys.argv[1]).exists()
assert 'row_bot.knowledge_graph' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code, str(data)], env={**os.environ, 'ROW_BOT_DATA_DIR': str(data)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr



def test_read_only_hmac_matches_existing_owner_and_corrupt_receipt_rejected(client, monkeypatch):
    api, _, context = client
    command = reviewed(client)
    execute(client, command)
    value = {'private': 'synthetic value'}
    assert api.admissions.keyed_digest(value) == api.admissions.keyed_digest(value, read_only=True)
    original = api.admissions.read_command_receipt
    def corrupt(*a, **kw):
        result = original(*a, **kw)
        result['entity_id'] = 'D:/private/source'
        result['code'] = 'secret-content'
        return result
    monkeypatch.setattr(api.admissions, 'read_command_receipt', corrupt)
    with pytest.raises(ValueError, match='invalid_knowledge_target'):
        api.read_knowledge_command(command_id=command['command_id'], **context)


def test_captured_create_identity_collision_preserves_existing_row(client):
    _, kg, _ = client
    command = reviewed(client)
    collision = command['command_id'].replace('-', '')[:12]
    with kg.projection_batch(drain_on_exit=False):
        original = kg.save_entity('fact', 'Existing source', 'Do not overwrite', entity_id=collision)
    with pytest.raises(sqlite3.IntegrityError):
        execute(client, command)
    assert kg.get_entity(collision) == original


def test_policy_denied_before_claim_or_domain_effect(client):
    api, kg, _ = client
    command = reviewed(client)
    def deny(kind):
        raise PermissionError('blocked by current policy')
    with pytest.raises(PermissionError, match='blocked by current policy'):
        execute(client, command, validate_action=deny)
    assert kg.count_entities() == 0
    assert api.admissions.read_command_metadata('test-owner', command['command_id']) is None


def test_lifecycle_stale_snapshot_cannot_append_successful_journal(client):
    from row_bot import memory_evolution as evo
    _, kg, _ = client
    original = saved(client)
    with kg.projection_batch(drain_on_exit=False):
        kg.update_entity(original['id'], 'Concurrent revision')
        before = evo.get_journal()
        assert evo.set_status(original['id'], 'archived', expected_entity=original, validate=lambda: None) is None
    assert evo.get_journal() == before
    assert kg.get_entity(original['id'])['description'] == 'Concurrent revision'


def test_legacy_user_create_cannot_insert_from_pre_admission_read(client, monkeypatch):
    _, kg, _ = client
    reached, strict_done = threading.Event(), threading.Event()
    connect = kg._get_conn
    class PausedConnection:
        def __init__(self, connection):
            self.connection = connection
            self.once = False
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def execute(self, sql, *args):
            if not self.once and (sql.startswith('SELECT') or sql == 'BEGIN IMMEDIATE'):
                self.once = True
                # The old legacy path executed its first SELECT here and then
                # inserted its empty snapshot after the strict create committed.
                result = self.connection.execute(sql, *args) if sql.startswith('SELECT') else None
                reached.set()
                assert strict_done.wait(5)
                return result if result is not None else self.connection.execute(sql, *args)
            return self.connection.execute(sql, *args)
    def wrapped():
        connection = connect()
        return PausedConnection(connection) if threading.current_thread().name.startswith('legacy-user') else connection
    monkeypatch.setattr(kg, '_get_conn', wrapped)
    def legacy():
        with kg.projection_batch(drain_on_exit=False):
            return kg.save_entity('fact', 'User', 'Legacy detail', properties={'ignored_on_merge': True})
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='legacy-user') as pool:
        future = pool.submit(legacy)
        assert reached.wait(5)
        try:
            with kg.projection_batch(drain_on_exit=False):
                strict = kg.save_entity('person', 'User', 'Strict detail', entity_id='strict-user', validate=lambda: None)
        finally:
            strict_done.set()
        merged = future.result(5)
    assert kg.count_entities() == 1 and merged['id'] == strict['id']
    assert merged['description'] == 'Strict detail. Legacy detail'
    assert merged['entity_type'] == 'person'
    assert 'ignored_on_merge' not in json.loads(merged['properties'])


def test_legacy_user_alias_selection_preserves_priority_and_merge_behavior(client):
    _, kg, _ = client
    with kg.projection_batch(drain_on_exit=False):
        person = kg.save_entity('person', 'Synthetic person', 'Personal detail', aliases=' User , Me', tags='keep', source='test')
        kg.save_entity('fact', 'Other record', 'Other detail', aliases='user')
        merged = kg.save_entity('fact', ' USER ', 'New detail', aliases='ignored', source='ignored')
    assert merged['id'] == person['id'] and merged['description'] == 'Personal detail. New detail'
    assert merged['aliases'] == person['aliases'] and merged['tags'] == 'keep' and merged['source'] == 'test'
