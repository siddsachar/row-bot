"""Explicit reviewed entity mutations over canonical SQLite and admissions owners.

Passive reads never initialize knowledge stores. Missing commit proof remains
uncertain; receipt reads never repeat a write or run a projection/provider.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlite3 import Row

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID

from row_bot import knowledge_views
from row_bot.application.client_platform import ClientPlatformError
from row_bot.data_paths import get_memory_db_path
from row_bot.runtime import admissions

# Closed client capability enum, checked against its canonical owner in tests.
ENTITY_TYPES = ('concept', 'event', 'fact', 'media', 'organisation', 'person', 'place',
                'preference', 'project', 'self_knowledge', 'skill')
_FIELDS = {'entity_type': 64, 'subject': 256, 'description': 32768, 'aliases': 4096, 'tags': 4096}
_COLUMNS = {**_FIELDS, 'id': 128, 'properties': 65536, 'source': 4096, 'created_at': 128, 'updated_at': 128}
_KINDS = {'knowledge.create', 'knowledge.edit', 'knowledge.archive', 'knowledge.restore', 'knowledge.resolve'}
_MAINTENANCE_KINDS = {'knowledge.delete', 'knowledge.delete.bulk', 'knowledge.delete_all'}


def _error(code='knowledge_unavailable'):
    return ClientPlatformError(code)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise _error('invalid_knowledge_target')
    return value


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (TypeError, ValueError, AttributeError):
        raise _error('invalid_knowledge_command') from None


def _scope(owner, authority, *, read_only=False):
    if not isinstance(authority, str) or not 1 <= len(authority) <= 256:
        raise _error('action_denied')
    return admissions.keyed_digest({'knowledge_authority': authority, 'owner': owner}, read_only=read_only)


@dataclass(frozen=True)
class _Row:
    value: str


@dataclass(frozen=True)
class _Page:
    schema_version: int
    revision: str
    items: tuple[_Row, ...]
    total: int | None
    next_cursor: str | None
    availability: str


def _rows(identifier=None, *, user=False):
    used = 0
    def build(row: Row) -> _Row:
        nonlocal used
        entity = dict(row)
        entity.pop('matched')
        if any(not isinstance(entity.get(k), str) or len(entity[k]) > bound for k, bound in _COLUMNS.items()):
            raise ValueError('Entity exceeds editing budget')
        raw = json.dumps(entity, ensure_ascii=True)
        used += len(raw)
        if used > 256 * 1024:
            raise ValueError('Entity review exceeds budget')
        return _Row(raw)
    columns = ','.join(f'substr("{key}",1,{bound + 1}) "{key}"' for key, bound in _COLUMNS.items())
    # A broad SQL prefilter admits every normalized User/alias candidate; Python
    # applies the canonical whitespace rule without an unbounded full row load.
    where = "instr(lower(subject),'user') OR instr(lower(aliases),'user')" if user else 'id=?'
    page = knowledge_views._read(get_memory_db_path(create_parent=False), {'entities': ' '.join(_COLUMNS)},
        f'SELECT {columns},1 matched FROM entities WHERE {where} ORDER BY (entity_type=\'person\') DESC,updated_at DESC,id',
        () if user else (_id(identifier),), build, _Page, 'entity-editor', 0, None, 1000)
    if page.availability == 'unavailable' or page.next_cursor:
        raise _error()
    rows = [json.loads(row.value) for row in page.items]
    if user:
        rows = [row for row in rows if any(' '.join(value.lower().split()) == 'user'
                for value in [row['subject'], *row['aliases'].split(',')])]
    return rows


def _entity(identifier):
    values = _rows(identifier)
    return values[0] if values else None


def _props(entity):
    try:
        props = json.loads(entity['properties'])
        if not isinstance(props, dict):
            raise ValueError
        return props
    except (ValueError, RecursionError):
        raise _error() from None


def _public(entity):
    props = _props(entity)
    status = props.get('status', 'active')
    if status not in {'active', 'archived', 'needs_review', 'superseded'}:
        status = 'active'
    return {'id': _id(entity['id']), 'revision': _digest(entity),
            'fields': {k: entity[k] for k in _FIELDS}, 'status': status,
            'created_at': entity['created_at'], 'updated_at': entity['updated_at'],
            'saved_state': 'saved', 'projection_state': 'unknown'}


def read_entity_editor(entity_id: str | None, *, validate: Callable[[], None]) -> dict:
    validate()
    entity = _entity(entity_id) if entity_id is not None else None
    if entity_id is not None and entity is None:
        raise _error('knowledge_missing')
    result = {'schema_version': 1, 'entity': _public(entity) if entity else None,
              'entity_types': list(ENTITY_TYPES)}
    validate()
    return result


def _fields(value):
    if not isinstance(value, dict) or value.keys() != _FIELDS.keys():
        raise _error('invalid_knowledge_fields')
    for name, limit in _FIELDS.items():
        if (not isinstance(value[name], str) or len(value[name]) > limit or '\0' in value[name]
                or any(0xD800 <= ord(char) <= 0xDFFF for char in value[name])):
            raise _error('invalid_knowledge_fields')
    result = {key: item.strip() for key, item in value.items()}
    if not result['subject'] or result['entity_type'] not in ENTITY_TYPES:
        raise _error('invalid_knowledge_fields')
    return result


def _review(kind, payload):
    if kind not in _KINDS or not isinstance(payload, dict):
        raise _error('invalid_knowledge_command')
    fields = _fields(payload.get('fields')) if kind in {'knowledge.create', 'knowledge.edit'} else None
    identifier = payload.get('entity_id')
    entity = None
    if kind == 'knowledge.create':
        if identifier is not None:
            raise _error('invalid_knowledge_target')
        if ' '.join(fields['subject'].lower().split()) == 'user':
            matches = _rows(user=True)
            entity = matches[0] if matches else None
    else:
        entity = _entity(_id(identifier))
        if entity is None:
            raise _error('knowledge_missing')
        if payload.get('revision') != _digest(entity):
            raise _error('knowledge_changed')
        status = _public(entity)['status']
        if (kind == 'knowledge.restore' and status != 'archived') or (kind == 'knowledge.resolve' and status != 'needs_review'):
            raise _error('knowledge_changed')
    review = {'schema_version': 1, 'action': kind, 'entity_id': identifier,
              'revision': _digest(entity), 'fields_digest': _digest(fields),
              'reuse_entity_id': entity['id'] if kind == 'knowledge.create' and entity else None}
    return review, entity, fields


def read_knowledge_review(kind: str, payload: dict, *, validate: Callable[[], None]) -> dict:
    validate()
    review, _, _ = _review(kind, deepcopy(payload))
    validate()
    return review


def public_receipt(saved: dict) -> dict:
    command_id = _uuid(saved.get('command_id'))
    if saved.get('status') == 'rejected' and saved.get('code') == 'knowledge_changed':
        return {'command_id': command_id, 'status': 'rejected', 'code': 'knowledge_changed'}
    if (saved.get('status') != 'completed' or saved.get('saved_state') != 'saved'
            or saved.get('projection_state') not in {'pending', 'unknown'} or type(saved.get('reused')) is not bool
            or not isinstance(saved.get('revision'), str) or not re.fullmatch(r'[a-f0-9]{64}', saved['revision'])):
        raise _error('knowledge_operation_unavailable')
    return {'command_id': command_id, 'status': 'completed', 'entity_id': _id(saved.get('entity_id')),
            'revision': saved['revision'], 'saved_state': 'saved', 'projection_state': saved['projection_state'],
            'reused': saved['reused']}


def read_knowledge_command(*, owner_id: str, authority_id: str, command_id: str, validate: Callable[[], None]) -> dict:
    validate()
    metadata = admissions.read_command_metadata(owner_id, _uuid(command_id))
    saved = admissions.read_command_receipt(owner_id, command_id)
    if (not metadata or metadata['target'] != 'knowledge' or metadata['type'] not in _KINDS
            or not saved or saved.get('command_id') != command_id or not isinstance(saved.get('_knowledge'), dict)
            or saved['_knowledge'].get('scope') != _scope(owner_id, authority_id, read_only=True)):
        raise _error('knowledge_operation_unavailable')
    if saved.get('status') not in {'completed', 'rejected'}:
        result = {'command_id': command_id, 'status': 'partial', 'code': 'knowledge_outcome_uncertain'}
    else:
        result = public_receipt(saved)
    validate()
    return result


def execute_knowledge_command(command: dict, *, owner_id: str, authority_id: str, key: str,
        validate: Callable[[], None], validate_action: Callable[[str], None],
        validate_review: Callable[[dict, dict], None]) -> dict:
    validate()
    command = deepcopy(command)
    command_id = _uuid(command.get('command_id'))
    kind, payload = command.get('type'), command.get('payload')
    if kind not in _KINDS or not isinstance(payload, dict):
        raise _error('invalid_knowledge_command')
    expected = {'entity_id', 'revision', 'review_id'} | ({'fields'} if kind in {'knowledge.create', 'knowledge.edit'} else set())
    if payload.keys() != expected:
        raise _error('invalid_knowledge_command')
    if admissions.read_command_metadata(owner_id, command_id) is not None:
        try:
            admissions.claim_command(owner_id, key, command, 'knowledge')
        except admissions.AdmissionError as error:
            if str(error) != 'operation_uncertain':
                raise _error(str(error)) from error
        return read_knowledge_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    review, original, fields = _review(kind, payload)
    if payload['revision'] != review['revision']:
        raise _error('knowledge_changed')
    def authority() -> None:
        validate()
        validate_action(kind)
        validate_review(command, review)
    authority()
    try:
        prior = admissions.claim_command(owner_id, key, command, 'knowledge')
    except admissions.AdmissionError as error:
        if str(error) != 'operation_uncertain':
            raise _error(str(error)) from error
        return read_knowledge_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    if prior is not None:
        return read_knowledge_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    proof = {'scope': _scope(owner_id, authority_id)}
    admissions.command_progress(owner_id, key, {'command_id': command_id, 'status': 'effect_started', '_knowledge': proof})
    # Only an authorized explicit write initializes the canonical store.
    from row_bot import knowledge_graph as kg, memory_evolution as evolution
    with kg.projection_batch(drain_on_exit=False):
        if kind == 'knowledge.create':
            proposed = {**fields, 'source': 'live', 'properties': '{}'}
            props = evolution.user_modified_properties(proposed, source_context={'surface': 'knowledge_client', 'action': 'create'})
            entity = kg.save_entity(**fields, properties=props, entity_id=UUID(command_id).hex[:12],
                                    expected_user=original, validate=authority)
        elif kind == 'knowledge.edit':
            props = evolution.user_modified_properties(original, source_context={'surface': 'knowledge_client', 'action': 'save'})
            entity = kg.update_entity(original['id'], **fields, properties=props, expected_entity=original, validate=authority)
        elif kind == 'knowledge.archive':
            entity = evolution.set_status(original['id'], 'archived', reason='Archived from Knowledge UI', actor='manual',
                                          expected_entity=original, validate=authority)
        else:
            entity = evolution.mark_user_modified(original['id'], status='active', actor='manual',
                source_context={'surface': 'knowledge_client', 'action': 'resolve_review' if kind == 'knowledge.resolve' else 'restore'},
                expected_entity=original, validate=authority)
        if entity is None:
            result = {'command_id': command_id, 'status': 'rejected', 'code': 'knowledge_changed', '_knowledge': proof}
            admissions.complete_command(owner_id, key, result)
            return public_receipt(result)
        if kind in {'knowledge.create', 'knowledge.edit'} and not (kind == 'knowledge.create' and original):
            authority()
            evolution.append_journal('user_modified', entity_id=entity['id'], actor='manual',
                reason='high_authority_update', source=entity.get('source', ''), new_status='active')
    result = {'command_id': command_id, 'status': 'completed', 'entity_id': entity['id'], 'revision': _digest(entity),
              'saved_state': 'saved', 'projection_state': 'unknown' if kind == 'knowledge.create' and original else 'pending',
              'reused': bool(kind == 'knowledge.create' and original), '_knowledge': proof}
    # Save proof before final authority check; revocation cannot erase a committed outcome.
    admissions.complete_command(owner_id, key, result)
    validate()
    return public_receipt(result)


def _maintenance_intent(kind: str, payload: dict) -> dict:
    if kind not in _MAINTENANCE_KINDS or not isinstance(payload, dict):
        raise _error('invalid_knowledge_command')
    if set(payload) != {'catalog_revision', 'targets'}:
        raise _error('invalid_knowledge_command')
    catalog_revision = payload.get('catalog_revision')
    if not isinstance(catalog_revision, str) or not re.fullmatch(r'[a-f0-9]{64}', catalog_revision):
        raise _error('invalid_knowledge_command')
    raw_targets = payload.get('targets')
    if not isinstance(raw_targets, list) or len(raw_targets) > 100:
        raise _error('invalid_knowledge_command')
    targets = []
    seen = set()
    for item in raw_targets:
        if not isinstance(item, dict) or set(item) != {'entity_id', 'revision'}:
            raise _error('invalid_knowledge_command')
        identifier = _id(item.get('entity_id'))
        revision = item.get('revision')
        if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision) or identifier in seen:
            raise _error('invalid_knowledge_command')
        seen.add(identifier)
        targets.append({'entity_id': identifier, 'revision': revision})
    if kind == 'knowledge.delete' and len(targets) != 1:
        raise _error('invalid_knowledge_command')
    if kind == 'knowledge.delete.bulk' and not 1 <= len(targets) <= 100:
        raise _error('invalid_knowledge_command')
    if kind == 'knowledge.delete_all' and targets:
        raise _error('invalid_knowledge_command')
    return {'catalog_revision': catalog_revision, 'targets': targets}


def read_knowledge_maintenance_review(kind: str, payload: dict, *, validate: Callable[[], None]) -> dict:
    validate()
    intent = _maintenance_intent(kind, deepcopy(payload))
    page = knowledge_views.list_saved_entities(limit=1)
    if page.availability not in {'available', 'missing'} or page.revision != intent['catalog_revision']:
        raise _error('knowledge_changed')
    if kind != 'knowledge.delete_all':
        for target in intent['targets']:
            detail = knowledge_views.read_saved_entity_detail(target['entity_id'])
            if detail.availability != 'available' or detail.revision != target['revision']:
                raise _error('knowledge_changed')
    review = {
        'schema_version': 1,
        'action': kind,
        **intent,
        'entity_count': page.total or 0 if kind == 'knowledge.delete_all' else len(intent['targets']),
        'side_effects': (
            ['entities', 'relations', 'lexical_index', 'vector_index', 'managed_wiki_files']
            if kind == 'knowledge.delete_all'
            else ['entities', 'relations', 'lexical_index', 'vector_index', 'managed_wiki_files']
        ),
    }
    review['action_digest'] = _digest(review)
    validate()
    return review


def _maintenance_public(saved: dict) -> dict:
    command_id = _uuid(saved.get('command_id'))
    status = saved.get('status')
    if status not in {'completed', 'partial', 'rejected'}:
        raise _error('knowledge_operation_unavailable')
    result = {
        'command_id': command_id,
        'status': status,
        'action': saved.get('action'),
        'deleted': saved.get('deleted', []),
        'stale': saved.get('stale', []),
        'missing': saved.get('missing', []),
        'cleanup': saved.get('cleanup', {}),
        'code': saved.get('code'),
    }
    if result['action'] not in _MAINTENANCE_KINDS:
        raise _error('knowledge_operation_unavailable')
    if any(not isinstance(value, list) or len(value) > 100 for value in (result['deleted'], result['stale'], result['missing'])):
        raise _error('knowledge_operation_unavailable')
    if not isinstance(result['cleanup'], dict):
        raise _error('knowledge_operation_unavailable')
    return result


def read_knowledge_maintenance_command(*, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict:
    validate()
    command_id = _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    saved = admissions.read_command_receipt(owner_id, command_id)
    if not metadata or metadata['target'] != 'knowledge:maintenance' or metadata['type'] not in _MAINTENANCE_KINDS or not saved:
        raise _error('knowledge_operation_unavailable')
    if saved.get('status') not in {'completed', 'partial', 'rejected'}:
        saved = {
            'command_id': command_id, 'status': 'partial', 'action': metadata['type'],
            'deleted': [], 'stale': [], 'missing': [], 'cleanup': {},
            'code': 'knowledge_outcome_uncertain',
        }
    validate()
    return _maintenance_public(saved)


def execute_knowledge_maintenance_command(
    command: dict,
    *,
    owner_id: str,
    key: str,
    validate: Callable[[], None],
    validate_review: Callable[[dict, dict], None],
) -> dict:
    validate()
    command = deepcopy(command)
    command_id = _uuid(command.get('command_id'))
    kind = command.get('type')
    payload = command.get('payload')
    if kind not in _MAINTENANCE_KINDS or not isinstance(payload, dict):
        raise _error('invalid_knowledge_command')
    if set(payload) != {'catalog_revision', 'targets', 'action_digest', 'review_id'}:
        raise _error('invalid_knowledge_command')
    intent = _maintenance_intent(kind, {key: payload[key] for key in ('catalog_revision', 'targets')})
    if admissions.read_command_metadata(owner_id, command_id) is not None:
        try:
            admissions.claim_command(owner_id, key, command, 'knowledge:maintenance')
        except admissions.AdmissionError as error:
            if str(error) != 'operation_uncertain':
                raise _error(str(error)) from error
        return read_knowledge_maintenance_command(owner_id=owner_id, command_id=command_id, validate=validate)
    review = read_knowledge_maintenance_review(kind, intent, validate=validate)
    if payload.get('action_digest') != review['action_digest']:
        raise _error('knowledge_changed')
    validate_review(command, review)
    initial = {
        'command_id': command_id, 'status': 'partial', 'action': kind, 'deleted': [],
        'stale': [], 'missing': [], 'cleanup': {}, 'code': 'knowledge_outcome_uncertain',
    }
    try:
        replay = admissions.claim_command(owner_id, key, command, 'knowledge:maintenance', exclusive_target=True, initial_result=initial)
    except admissions.AdmissionError as error:
        raise _error(str(error)) from error
    if replay is not None:
        return _maintenance_public(replay)
    try:
        from row_bot import knowledge_graph as kg

        outcome = kg.delete_reviewed_entities(
            {item['entity_id']: item['revision'] for item in intent['targets']},
            catalog_revision=intent['catalog_revision'],
            delete_all=kind == 'knowledge.delete_all',
            validate=validate,
        )
        partial = bool(outcome['stale'] or outcome['missing'] or 'failed' in outcome['cleanup'].values())
        result = {
            'command_id': command_id,
            'status': 'partial' if partial else 'completed',
            'action': kind,
            **outcome,
            'code': 'knowledge_cleanup_partial' if partial else None,
        }
    except ValueError as exc:
        if str(exc) == 'knowledge_changed':
            result = {**initial, 'status': 'rejected', 'code': 'knowledge_changed'}
        else:
            result = initial
    except Exception:
        result = initial
    saved = admissions.complete_command(owner_id, key, result)
    return _maintenance_public(saved)
