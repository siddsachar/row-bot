# ruff: noqa: F811 -- reuse canonical isolated client/projection fixtures.
import json
import os
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from tests.subsystem.knowledge_graph.test_knowledge_commands import client, projection_stack, saved  # noqa: F401


def command(client, kind, first, second, edge=None, relation_type='works for'):
    from row_bot.application import knowledge_relations as api
    base = client[0]
    if kind == 'knowledge.relation.add':
        payload = {'source_id':first['id'],'target_id':second['id'],'source_revision':base._digest(first),
                   'target_revision':base._digest(second),'relation_type':relation_type}
    elif kind == 'knowledge.relation.remove':
        payload = {'relation_id':edge['id'],'relation_revision':base._digest(edge),
                   'source_revision':base._digest(first),'target_revision':base._digest(second)}
    else:
        payload = {'old_id':first['id'],'new_id':second['id'],'old_revision':base._digest(first),'new_revision':base._digest(second)}
    api.read_relation_review(kind,payload,validate=lambda:None)
    return {'command_id':str(uuid4()),'type':kind,'payload':{**payload,'review_id':'reviewed-test'}}


def execute(client,value,**callbacks):
    from row_bot.application import knowledge_relations as api
    return api.execute_relation_command(value,key=value['command_id'],**client[2],
        **{'validate_action':lambda kind:None,'validate_review':lambda command,review:None,**callbacks})


def pair(client):
    return saved(client,'First'), saved(client,'Second')


def test_add_normalizes_and_reuses_existing_canonical_edge(client):
    _, kg, _ = client
    first, second = pair(client)
    result = execute(client,command(client,'knowledge.relation.add',first,second))
    edge = kg.get_relations(first['id'])[0]
    assert edge['relation_type'] == 'employed_by'
    another = execute(client,command(client,'knowledge.relation.add',first,second,relation_type='works_for'))
    assert result['relation_id'] == another['relation_id'] and kg.count_relations() == 1


def test_add_replay_and_passive_receipt_do_not_repeat_graph_or_admission_writes(client,monkeypatch):
    from row_bot.application import knowledge_relations as api
    first,second = pair(client)
    value = command(client,'knowledge.relation.add',first,second)
    result = execute(client,value)
    def forbidden(*a,**kw):
        pytest.fail('unexpected write')
    monkeypatch.setattr(client[1],'add_relation',forbidden)
    assert execute(client,value) == result
    monkeypatch.setattr(api.admissions,'transaction',forbidden)
    assert api.read_relation_command(command_id=value['command_id'],**client[2]) == result


@pytest.mark.parametrize('kind',['knowledge.relation.add','knowledge.supersede'])
def test_endpoint_same_id_changed_after_review_preserves_current_rows(client,kind,monkeypatch):
    _,kg,_ = client
    first,second = pair(client)
    value = command(client,kind,first,second)
    function = 'add_relation' if kind.endswith('add') else 'update_entity_properties_pair'
    original = getattr(kg,function)
    def race(*args,**kwargs):
        with kg.projection_batch(drain_on_exit=False):
            kg.update_entity(second['id'],'Changed by another owner')
        return original(*args,**kwargs)
    monkeypatch.setattr(kg,function,race)
    assert execute(client,value)['status'] == 'rejected'
    assert kg.get_entity(first['id']) == first
    assert kg.get_entity(second['id'])['description'] == 'Changed by another owner'
    assert kg.count_relations() == 0


def test_supersede_updates_both_atomically_and_retains_provenance(client):
    from row_bot import memory_evolution as evo
    _,kg,_ = client
    first = saved(client,'First',{'evidence':['first'],'private':'old'})
    second = saved(client,'Second',{'evidence':['second'],'private':'new'})
    result = execute(client,command(client,'knowledge.supersede',first,second))
    assert result['outcome'] == 'superseded' and result['relation_id'] is None
    old,new = kg.get_entity(first['id']),kg.get_entity(second['id'])
    old_props,new_props = map(lambda row:json.loads(row['properties']),(old,new))
    assert old_props['status'] == 'superseded' and old_props['superseded_by'] == second['id']
    assert first['id'] in new_props['supersedes']
    assert old_props['private'] == 'old' and new_props['private'] == 'new'
    assert old['updated_at'] == new['updated_at']
    assert evo.get_journal()[-1]['entity_ids'] == [first['id'],second['id']]


def test_failure_after_second_sql_update_rolls_back_both_entities_and_projection_work(client,monkeypatch):
    from row_bot import memory_evolution as evo
    _,kg,_ = client
    first,second = pair(client)
    before = kg._projection_state()
    original = kg._get_conn
    class FaultConnection:
        def __init__(self,conn):
            self.conn,self.count = conn,0
        def __getattr__(self,name):
            return getattr(self.conn,name)
        def execute(self,sql,*args):
            result = self.conn.execute(sql,*args)
            if sql.startswith('UPDATE entities SET properties='):
                self.count += 1
                if self.count == 2:
                    raise OSError('after second update')
            return result
    monkeypatch.setattr(kg,'_get_conn',lambda:FaultConnection(original()))
    with kg.projection_batch(drain_on_exit=False),pytest.raises(OSError,match='second update'):
        evo.mark_superseded(first['id'],second['id'],expected_entities=(first,second),validate=lambda:None)
    assert kg.get_entity(first['id']) == first and kg.get_entity(second['id']) == second
    assert kg._projection_state() == before
    assert not evo.get_journal()


def test_revocation_before_second_entity_update_rolls_back_first(client):
    from row_bot import memory_evolution as evo
    _,kg,_ = client
    first,second = pair(client)
    calls = []
    sentinel = PermissionError('revoked before second effect')
    def validate():
        calls.append(True)
        if len(calls) == 3:
            raise sentinel
    with kg.projection_batch(drain_on_exit=False),pytest.raises(PermissionError) as caught:
        evo.mark_superseded(first['id'],second['id'],expected_entities=(first,second),validate=validate)
    assert caught.value is sentinel
    assert kg.get_entity(first['id']) == first and kg.get_entity(second['id']) == second


def test_remove_edge_checks_full_edge_and_endpoints(client,monkeypatch):
    from row_bot.application import knowledge_relations as api
    _,kg,_ = client
    first,second = pair(client)
    result = execute(client,command(client,'knowledge.relation.add',first,second))
    edge = api._edge(result['relation_id'])
    value = command(client,'knowledge.relation.remove',first,second,edge)
    original = kg.delete_relation
    def changed(*args,**kwargs):
        with sqlite3.connect(kg.DB_PATH) as conn:
            conn.execute('UPDATE relations SET properties=? WHERE id=?',(json.dumps({'private':'preserve'}),edge['id']))
        return original(*args,**kwargs)
    monkeypatch.setattr(kg,'delete_relation',changed)
    assert execute(client,value)['status'] == 'rejected'
    assert json.loads(api._edge(edge['id'])['properties'])['private'] == 'preserve'


def test_remove_confirmed_edge_keeps_other_relations(client):
    from row_bot.application import knowledge_relations as api
    first,second = pair(client)
    one = execute(client,command(client,'knowledge.relation.add',first,second))
    execute(client,command(client,'knowledge.relation.add',first,second,relation_type='knows'))
    value = command(client,'knowledge.relation.remove',first,second,api._edge(one['relation_id']))
    result = execute(client,value)
    assert result['outcome'] == 'removed' and client[1].count_relations() == 1
    assert execute(client,value) == result


def test_postcommit_unknown_never_repeats_supersede_or_exposes_private_values(client,monkeypatch):
    from row_bot.application import knowledge_relations as api
    first,second = pair(client)
    value = command(client,'knowledge.supersede',first,second)
    def fault(*a,**kw):
        raise OSError('receipt fault')
    monkeypatch.setattr(api.admissions,'complete_command',fault)
    with pytest.raises(OSError):
        execute(client,value)
    result = execute(client,value)
    assert result['status'] == 'partial' and result['code'] == 'knowledge_outcome_uncertain'
    assert 'First' not in json.dumps(result) and '_knowledge' not in json.dumps(result)


@pytest.mark.parametrize('kind',['knowledge.relation.add','knowledge.supersede'])
def test_self_target_rejected_before_claim(client,kind):
    first = saved(client)
    with pytest.raises(ValueError,match='invalid_relation_target'):
        command(client,kind,first,first)


@pytest.mark.parametrize('label',['related_to','associated_with','has_relation'])
def test_vague_normalized_relations_do_not_mutate(client,label):
    first,second = pair(client)
    result = execute(client,command(client,'knowledge.relation.add',first,second,relation_type=label))
    assert result['status'] == 'rejected' and client[1].count_relations() == 0


def test_pagination_complete_and_revision_bound(client):
    from row_bot.application import knowledge_relations as api
    _,kg,_ = client
    first = saved(client,'First')
    with kg.projection_batch(drain_on_exit=False):
        for number in range(63):
            other = kg.save_entity('fact',f'Peer {number}','Body')
            kg.add_relation(first['id'],other['id'],'knows',properties={'private':'not on wire'})
    page = api.read_entity_relations(first['id'],limit=50,validate=lambda:None)
    assert page.total == 63 and len(page.items) == 50 and page.next_cursor
    next_page = api.read_entity_relations(first['id'],cursor=page.next_cursor,validate=lambda:None)
    assert len(next_page.items) == 13 and next_page.next_cursor is None
    assert len({item.id for item in (*page.items,*next_page.items)}) == 63
    assert 'private' not in repr(page)
    with kg.projection_batch(drain_on_exit=False):
        kg.delete_relation(page.items[0].id)
    with pytest.raises(ValueError,match='cursor_expired'):
        api.read_entity_relations(first['id'],cursor=page.next_cursor,validate=lambda:None)


def test_read_relation_cursor_cannot_cross_entity(client):
    from row_bot.application import knowledge_relations as api
    first,second = pair(client)
    execute(client,command(client,'knowledge.relation.add',first,second))
    execute(client,command(client,'knowledge.relation.add',first,second,relation_type='knows'))
    page = api.read_entity_relations(first['id'],limit=1,validate=lambda:None)
    with pytest.raises(ValueError,match='invalid_relation_page'):
        api.read_entity_relations(second['id'],cursor=page.next_cursor,validate=lambda:None)


def test_cold_relation_read_creates_no_domain_store(tmp_path):
    data = tmp_path/'absent'
    code = '''
import sys
from pathlib import Path
from row_bot.application.knowledge_relations import read_entity_relations
assert read_entity_relations('missing',validate=lambda:None).availability == 'missing'
assert 'row_bot.knowledge_graph' not in sys.modules
assert not Path(sys.argv[1]).exists()
'''
    result = subprocess.run([sys.executable,'-c',code,str(data)],env={**os.environ,'ROW_BOT_DATA_DIR':str(data)},capture_output=True,text=True,timeout=30)
    assert result.returncode == 0,result.stdout+result.stderr


def test_relation_writer_revocation_rolls_back_and_preserves_authority_exception(client):
    _,kg,_ = client
    first,second = pair(client)
    calls = []
    sentinel = PermissionError('revoked at commit')
    def validate():
        calls.append(True)
        if len(calls) == 2:
            raise sentinel
    with kg.projection_batch(drain_on_exit=False),pytest.raises(PermissionError) as caught:
        kg.add_relation(first['id'],second['id'],'knows',expected_entities=(first,second),validate=validate)
    assert caught.value is sentinel and kg.count_relations() == 0


def test_relation_id_collision_does_not_adopt_or_overwrite_other_edge(client):
    _,kg,_ = client
    first,second = pair(client)
    value = command(client,'knowledge.relation.add',first,second)
    identifier = value['command_id'].replace('-','')[:12]
    with kg.projection_batch(drain_on_exit=False):
        existing = kg.add_relation(second['id'],first['id'],'knows',relation_id=identifier)
    with pytest.raises(sqlite3.IntegrityError):
        execute(client,value)
    assert kg.count_relations() == 1 and kg.get_relations(first['id'])[0]['id'] == existing['id']


def test_relation_receipt_cannot_change_semantic_outcome_or_auth_lifetime(client,monkeypatch):
    from row_bot.application import knowledge_relations as api
    first,second = pair(client)
    value = command(client,'knowledge.relation.add',first,second)
    execute(client,value)
    with pytest.raises(ValueError,match='knowledge_operation_unavailable'):
        api.read_relation_command(command_id=value['command_id'],**{**client[2],'authority_id':'other-session'})
    original = api.admissions.read_command_receipt
    def altered(*a,**kw):
        result = original(*a,**kw)
        result['outcome'] = 'removed'
        return result
    monkeypatch.setattr(api.admissions,'read_command_receipt',altered)
    with pytest.raises(ValueError,match='knowledge_operation_unavailable'):
        api.read_relation_command(command_id=value['command_id'],**client[2])


def test_oversized_saved_edge_fails_without_truncation_or_mutation(client):
    from row_bot.application import knowledge_relations as api
    _,kg,_ = client
    first,second = pair(client)
    value = execute(client,command(client,'knowledge.relation.add',first,second))
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.execute('UPDATE relations SET properties=? WHERE id=?',('x'*65537,value['relation_id']))
    page = api.read_entity_relations(first['id'],validate=lambda:None)
    assert page.availability == 'unavailable'
    with pytest.raises(ValueError,match='knowledge_unavailable'):
        api._edge(value['relation_id'])
    assert kg.count_relations() == 1


def test_legacy_supersede_default_is_atomic_on_concurrent_snapshot_conflict(client,monkeypatch):
    from row_bot import memory_evolution as evo
    _,kg,_ = client
    first,second = pair(client)
    original = kg.get_entity
    def concurrent(identifier):
        if identifier == second['id']:
            with sqlite3.connect(kg.DB_PATH) as conn:
                conn.execute('UPDATE entities SET description=? WHERE id=?',('Concurrent edit',first['id']))
        return original(identifier)
    monkeypatch.setattr(kg,'get_entity',concurrent)
    with kg.projection_batch(drain_on_exit=False):
        assert evo.mark_superseded(first['id'],second['id'],reason='manual correction',actor='manual') == (None,None)
    assert original(first['id'])['description'] == 'Concurrent edit'
    assert original(second['id']) == second
    assert not evo.get_journal()


def test_legacy_supersede_retains_properties_and_publishes_two_row_coverage_once(client,projection_stack,monkeypatch):
    from row_bot import memory_evolution as evo
    _,kg,_ = client
    first,second = pair(client)
    embedding = projection_stack[1]
    monkeypatch.setattr(kg,'_get_embedding_model',lambda **kw:embedding)
    kg.rebuild_index()
    before = len(embedding.batches)
    old,new = evo.mark_superseded(first['id'],second['id'],reason='manual correction',actor='manual')
    assert json.loads(old['properties'])['superseded_by'] == new['id']
    assert old['id'] in json.loads(new['properties'])['supersedes']
    assert evo.get_journal()[-1]['reason'] == 'manual correction'
    assert sum(map(len,embedding.batches[before:])) == 2
    assert kg.memory_vector_status()['ready'] is True
