"""Reviewed relation and paired supersession controls over existing graph owners."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlite3 import Row

import base64
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from uuid import UUID

from row_bot import knowledge_views
from row_bot.application import knowledge_commands as entities
from row_bot.data_paths import get_memory_db_path
from row_bot.runtime import admissions

_KINDS = {'knowledge.relation.add', 'knowledge.relation.remove', 'knowledge.supersede'}
_COLUMNS = {'id':128, 'source_id':128, 'target_id':128, 'relation_type':64, 'properties':65536,
            'source':4096, 'created_at':128, 'updated_at':128}


@dataclass(frozen=True)
class RelationSummary:
    id: str
    source_id: str
    target_id: str
    relation_type: str
    confidence: float
    revision: str
    peer_id: str
    peer_subject: str
    truncated: bool


@dataclass(frozen=True)
class RelationPage:
    schema_version: int
    revision: str
    items: tuple[RelationSummary, ...]
    total: int | None
    next_cursor: str | None
    availability: str


def _scope(owner, authority, *, read_only=False):
    if not isinstance(authority, str) or not 1 <= len(authority) <= 256:
        raise entities._error('action_denied')
    return admissions.keyed_digest({'knowledge_relations_authority': authority, 'owner': owner}, read_only=read_only)


def _relation_row(row):
    result = {key: row[key] for key in (*_COLUMNS, 'confidence')}
    if any(not isinstance(result[key], str) or len(result[key]) > limit for key, limit in _COLUMNS.items()):
        raise ValueError('Relation exceeds review budget')
    if type(result['confidence']) not in {int, float} or not math.isfinite(result['confidence']) or not 0 <= result['confidence'] <= 1:
        raise ValueError('Invalid relation confidence')
    for key in ('id', 'source_id', 'target_id'):
        entities._id(result[key])
    return result


def _select_columns(prefix='r.'):
    return ','.join(f'substr({prefix}"{key}",1,{limit+1}) "{key}"' for key, limit in _COLUMNS.items()) + f',{prefix}confidence'


def _edge(identifier):
    def build(row: Row) -> entities._Row:
        return entities._Row(json.dumps(_relation_row(row)))
    page = knowledge_views._read(get_memory_db_path(create_parent=False), {'relations': ' '.join((*_COLUMNS, 'confidence'))},
        f'SELECT {_select_columns()},1 matched FROM relations r WHERE r.id=?', (entities._id(identifier),),
        build, entities._Page, 'relation-review', 0, None, 1)
    if page.availability == 'unavailable':
        raise entities._error()
    return json.loads(page.items[0].value) if page.items else None


def read_entity_relations(entity_id: str, *, cursor: str | None = None, limit: int = 50,
                          validate: Callable[[], None]) -> RelationPage:
    validate()
    entity_id = entities._id(entity_id)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise entities._error('invalid_relation_page')
    key, offset, expected = 'entity-relations:' + entity_id, 0, None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError
            value = json.loads(base64.urlsafe_b64decode(cursor))
            if (value.keys() != {'v','key','revision','offset'} or value['v'] != 1 or value['key'] != key
                    or type(value['offset']) is not int or not 0 < value['offset'] < 2**53
                    or not isinstance(value['revision'], str) or not re.fullmatch('[a-f0-9]{64}', value['revision'])):
                raise ValueError
            offset, expected = value['offset'], value['revision']
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise entities._error('invalid_relation_page') from None
    def build(row: Row) -> RelationSummary:
        edge = _relation_row(row)
        subject = row['peer_subject']
        if not isinstance(subject, str):
            raise ValueError
        peer_id = edge['target_id'] if edge['source_id'] == entity_id else edge['source_id']
        return RelationSummary(edge['id'], edge['source_id'], edge['target_id'], edge['relation_type'], edge['confidence'],
            entities._digest(edge), peer_id, subject[:256], len(subject) > 256)
    page = knowledge_views._read(get_memory_db_path(create_parent=False),
        {'relations': ' '.join((*_COLUMNS, 'confidence')), 'entities': 'id subject'},
        f'''SELECT {_select_columns()},substr(e.subject,1,257) peer_subject,1 matched
            FROM relations r LEFT JOIN entities e ON e.id=CASE WHEN r.source_id=? THEN r.target_id ELSE r.source_id END
            WHERE r.source_id=? OR r.target_id=? ORDER BY r.updated_at DESC,r.id''',
        (entity_id, entity_id, entity_id), build, RelationPage, key, offset, expected, limit)
    validate()
    return page


def _required_entity(identifier, revision):
    entity = entities._entity(entities._id(identifier))
    if entity is None:
        raise entities._error('knowledge_missing')
    if entities._digest(entity) != revision:
        raise entities._error('knowledge_changed')
    return entity


def _review(kind, payload):
    if not isinstance(payload, dict) or kind not in _KINDS:
        raise entities._error('invalid_relation_command')
    edge = None
    if kind == 'knowledge.relation.add':
        first = _required_entity(payload.get('source_id'), payload.get('source_revision'))
        second = _required_entity(payload.get('target_id'), payload.get('target_revision'))
        relation_type = payload.get('relation_type')
        if (not isinstance(relation_type, str) or not relation_type.strip() or len(relation_type) > 64
                or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in relation_type)):
            raise entities._error('invalid_relation_type')
    elif kind == 'knowledge.relation.remove':
        edge = _edge(payload.get('relation_id'))
        if edge is None or entities._digest(edge) != payload.get('relation_revision'):
            raise entities._error('relation_changed')
        first = _required_entity(edge['source_id'], payload.get('source_revision'))
        second = _required_entity(edge['target_id'], payload.get('target_revision'))
    else:
        first = _required_entity(payload.get('old_id'), payload.get('old_revision'))
        second = _required_entity(payload.get('new_id'), payload.get('new_revision'))
    if first['id'] == second['id']:
        raise entities._error('invalid_relation_target')
    review = {'schema_version':1, 'action':kind, 'entity_ids':[first['id'],second['id']],
        'entity_revisions':[entities._digest(first),entities._digest(second)],
        'relation_id':edge['id'] if edge else None, 'relation_revision':entities._digest(edge),
        'intent_digest':entities._digest({key:value for key,value in payload.items() if key != 'review_id'})}
    return review, first, second, edge


def read_relation_review(kind: str, payload: dict, *, validate: Callable[[], None]) -> dict:
    validate()
    value, _, _, _ = _review(kind, deepcopy(payload))
    validate()
    return value


def public_receipt(saved: dict) -> dict:
    command_id = entities._uuid(saved.get('command_id'))
    if saved.get('status') == 'rejected' and saved.get('code') == 'relation_changed_or_invalid':
        return {'command_id':command_id, 'status':'rejected', 'code':'relation_changed_or_invalid'}
    if (saved.get('status') != 'completed' or saved.get('outcome') not in {'saved','removed','superseded'}
            or saved.get('projection_state') not in {'pending','unknown'}
            or not isinstance(saved.get('entity_ids'), list) or len(saved['entity_ids']) != 2):
        raise entities._error('knowledge_operation_unavailable')
    identifiers = [entities._id(value) for value in saved['entity_ids']]
    if identifiers[0] == identifiers[1]:
        raise entities._error('knowledge_operation_unavailable')
    relation_id = saved.get('relation_id')
    if (saved['outcome'] == 'superseded') != (relation_id is None):
        raise entities._error('knowledge_operation_unavailable')
    return {'command_id':command_id, 'status':'completed', 'outcome':saved['outcome'], 'entity_ids':identifiers,
            'relation_id': entities._id(relation_id) if relation_id is not None else None,
            'projection_state':saved['projection_state']}


def read_relation_command(*, owner_id: str, authority_id: str, command_id: str, validate: Callable[[], None]) -> dict:
    validate()
    command_id = entities._uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    saved = admissions.read_command_receipt(owner_id, command_id)
    if (not metadata or metadata['target'] != 'knowledge-relations' or metadata['type'] not in _KINDS
            or not saved or saved.get('command_id') != command_id or not isinstance(saved.get('_knowledge_relations'), dict)
            or saved['_knowledge_relations'].get('scope') != _scope(owner_id, authority_id, read_only=True)):
        raise entities._error('knowledge_operation_unavailable')
    if saved.get('status') == 'completed' and saved.get('outcome') != {
            'knowledge.relation.add':'saved', 'knowledge.relation.remove':'removed', 'knowledge.supersede':'superseded'}[metadata['type']]:
        raise entities._error('knowledge_operation_unavailable')
    result = public_receipt(saved) if saved.get('status') in {'completed','rejected'} else {
        'command_id':command_id, 'status':'partial', 'code':'knowledge_outcome_uncertain'}
    validate()
    return result


def execute_relation_command(command: dict, *, owner_id: str, authority_id: str, key: str,
        validate: Callable[[], None], validate_action: Callable[[str], None], validate_review: Callable[[dict,dict],None]) -> dict:
    validate()
    command = deepcopy(command)
    command_id = entities._uuid(command.get('command_id'))
    kind, payload = command.get('type'), command.get('payload')
    keys = {'knowledge.relation.add': {'source_id','target_id','source_revision','target_revision','relation_type','review_id'},
            'knowledge.relation.remove': {'relation_id','relation_revision','source_revision','target_revision','review_id'},
            'knowledge.supersede': {'old_id','new_id','old_revision','new_revision','review_id'}}
    if kind not in keys or not isinstance(payload, dict) or payload.keys() != keys[kind]:
        raise entities._error('invalid_relation_command')
    if admissions.read_command_metadata(owner_id, command_id) is not None:
        try:
            admissions.claim_command(owner_id,key,command,'knowledge-relations')
        except admissions.AdmissionError as error:
            if str(error) != 'operation_uncertain':
                raise entities._error(str(error)) from error
        return read_relation_command(owner_id=owner_id,authority_id=authority_id,command_id=command_id,validate=validate)
    review, first, second, edge = _review(kind,payload)
    def authority() -> None:
        validate()
        validate_action(kind)
        validate_review(command,review)
    authority()
    try:
        prior = admissions.claim_command(owner_id,key,command,'knowledge-relations')
    except admissions.AdmissionError as error:
        if str(error) != 'operation_uncertain':
            raise entities._error(str(error)) from error
        return read_relation_command(owner_id=owner_id,authority_id=authority_id,command_id=command_id,validate=validate)
    if prior is not None:
        return read_relation_command(owner_id=owner_id,authority_id=authority_id,command_id=command_id,validate=validate)
    proof = {'scope':_scope(owner_id,authority_id)}
    admissions.command_progress(owner_id,key,{'command_id':command_id,'status':'effect_started','_knowledge_relations':proof})
    from row_bot import knowledge_graph as kg, memory_evolution as evolution
    with kg.projection_batch(drain_on_exit=False):
        if kind == 'knowledge.relation.add':
            result = kg.add_relation(first['id'],second['id'],payload['relation_type'],relation_id=UUID(command_id).hex[:12],
                                     expected_entities=(first,second),validate=authority)
            relation_id, outcome = result['id'] if result else None, 'saved'
        elif kind == 'knowledge.relation.remove':
            result = kg.delete_relation(edge['id'],expected_relation=edge,expected_entities=(first,second),validate=authority)
            relation_id, outcome = edge['id'], 'removed'
        else:
            props = evolution.superseded_properties(first,second)
            if any(len(json.dumps(value)) > 65536 for value in props):
                raise entities._error('knowledge_unavailable')
            pair = evolution.mark_superseded(first['id'],second['id'],reason='Superseded from entity editor',actor='manual',
                expected_entities=(first,second),validate=authority)
            result, relation_id, outcome = all(pair), None, 'superseded'
    receipt = {'command_id':command_id,'status':'completed' if result else 'rejected','_knowledge_relations':proof}
    if result:
        projection = 'unknown' if kind == 'knowledge.relation.add' and relation_id != UUID(command_id).hex[:12] else 'pending'
        receipt.update(outcome=outcome,entity_ids=[first['id'],second['id']],relation_id=relation_id,projection_state=projection)
    else:
        receipt['code'] = 'relation_changed_or_invalid'
    admissions.complete_command(owner_id,key,receipt)
    validate()
    return public_receipt(receipt)
