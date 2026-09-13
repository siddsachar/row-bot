"""Authenticated original-command recovery for byte-exact workspace Undo."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, fields
import re
import threading

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer import client_undo
from row_bot.runtime import admissions

_LOCK = threading.RLock()


def _scope(owner: str, conversation: str, binding: str, *, read_only: bool = False) -> str:
    return admissions.keyed_digest({'workspace_undo_owner': owner, 'conversation': conversation,
                                    'binding': binding}, read_only=read_only)


def _review(value: dict) -> client_undo.WorkspaceUndoReview:
    if (not isinstance(value, dict) or set(value) != {item.name for item in fields(client_undo.WorkspaceUndoReview)}
            or any(not isinstance(value[name], list) or len(value[name]) > 100
                   or any(not isinstance(path, str) or not 1 <= len(path) <= 4096 for path in value[name])
                   for name in ('files', 'directories_retained'))):
        raise ClientPlatformError('workspace_undo_unavailable')
    if (any(not isinstance(value[name], str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', value[name])
            for name in ('resource_id', 'conversation_id', 'binding_id', 'change_set_id'))
            or any(not isinstance(value[name], str) or not re.fullmatch(r'[0-9a-f]{64}', value[name])
                   for name in ('change_set_revision', 'host_revision', 'policy_revision', 'action_digest'))
            or not isinstance(value['resource_revision'], str) or not 1 <= len(value['resource_revision']) <= 128
            or not isinstance(value['binding_revision'], str) or not re.fullmatch(r'0|[1-9][0-9]{0,19}', value['binding_revision'])
            or type(value['approval_required']) is not bool or value['policy_decision'] not in ('allow', 'ask', 'block')):
        raise ClientPlatformError('workspace_undo_unavailable')
    return client_undo.WorkspaceUndoReview(**{**value, 'files': tuple(value['files']),
        'directories_retained': tuple(value['directories_retained'])})


def _saved(owner: str, command_id: str, conversation: str, binding: str,
           validate: Callable[[], None]) -> dict:
    validate()
    metadata = admissions.read_command_metadata(owner, command_id)
    value = admissions.read_command_receipt(owner, command_id)
    private = value.get('_workspace_undo') if isinstance(value, dict) else None
    if (not metadata or metadata['target'] != conversation or metadata['type'] != 'workspace.undo'
            or not isinstance(private, dict) or set(private) != {'scope', 'review', 'recovery'}
            or private['scope'] != _scope(owner, conversation, binding, read_only=True)
            or private['recovery'] is not None and not isinstance(private['recovery'], dict)
            or value.get('command_id') != command_id):
        raise ClientPlatformError('workspace_undo_unavailable')
    review = _review(private['review'])
    if review.conversation_id != conversation or review.binding_id != binding:
        raise ClientPlatformError('workspace_undo_unavailable')
    # The canonical row owns completion; an unfinished JSON snapshot cannot
    # promote itself to completed after an interrupted receipt publication.
    value['status'] = metadata['status']
    validate()
    return value


def _public_result(value: dict, command_id: str, review: dict) -> dict:
    names = {item.name for item in fields(client_undo.WorkspaceUndoResult)}
    if (not isinstance(value, dict) or not names.issubset(value)
            or any(value.get(name) != expected for name, expected in (
                ('command_id', command_id), ('resource_id', review['resource_id']),
                ('conversation_id', review['conversation_id']), ('change_set_id', review['change_set_id'])))
            or value.get('status') not in {'undone', 'partial', 'conflict', 'denied'}
            or type(value.get('ledger_saved')) is not bool or type(value.get('reverted')) is not bool
            or not isinstance(value.get('code'), str) or len(value['code']) > 128
            or not isinstance(value.get('files_restored'), (list, tuple))
            or len(value['files_restored']) > 100
            or any(path not in review['files'] for path in value['files_restored'])):
        raise ClientPlatformError('workspace_undo_unavailable')
    result = {name: deepcopy(value[name]) for name in names}
    result['files_restored'] = list(result['files_restored'])
    return result


def read_workspace_undo_command(owner: str, command_id: str, conversation: str, binding: str,
                                *, validate: Callable[[], None]) -> dict:
    """Read saved proof only; never acquire the writer or repair files/ledger."""
    validate()
    if admissions.read_command_metadata(owner, command_id) is None:
        validate()
        raise ClientPlatformError('workspace_undo_not_admitted')
    saved = _saved(owner, command_id, conversation, binding, validate)
    review = saved['_workspace_undo']['review']
    if isinstance(saved.get('result'), dict):
        result = _public_result(saved['result'], command_id, review)
        if result['status'] == 'undone' and saved['status'] != 'completed':
            raise ClientPlatformError('workspace_undo_unavailable')
        return result
    return {'command_id': command_id, 'resource_id': review['resource_id'], 'conversation_id': conversation,
        'change_set_id': review['change_set_id'], 'status': 'partial', 'files_restored': [],
        'ledger_saved': False, 'reverted': False, 'code': 'workspace_undo_unconfirmed'}


def review_workspace_undo_recovery(owner: str, command_id: str, conversation: str, binding: str,
                                   *, validate: Callable[[], None]) -> dict:
    """Renew review authority for the retained original, never a new host snapshot."""
    return deepcopy(_saved(owner, command_id, conversation, binding, validate)['_workspace_undo']['review'])


def execute_workspace_undo(command: dict, conversation: str, binding: str, *, owner: str, key: str,
        validate: Callable[[], None], validate_review: Callable[[dict, str, str], None]) -> dict:
    """Serialize duplicate requests; canonical Undo retains physical writer ownership."""
    if command.get('type') != 'workspace.undo' or command.get('command_id') != key:
        raise ClientPlatformError('invalid_command')
    payload = deepcopy(command.get('payload'))
    if not isinstance(payload, dict) or set(payload) != {'nonce', 'review'}:
        raise ClientPlatformError('invalid_command')
    nonce = payload.pop('nonce')
    review = payload['review']
    domain_review = _review(review)
    if domain_review.conversation_id != conversation or domain_review.binding_id != binding:
        raise ClientPlatformError('resource_binding_revoked')
    canonical = {**command, 'payload': payload}
    with _LOCK:
        validate()
        previous = admissions.read_command_metadata(owner, key)
        if previous is None:
            actual = asdict(client_undo.review_workspace_undo(review['resource_id'], conversation,
                review['change_set_id'], validate=validate))
            actual['files'] = list(actual['files'])
            actual['directories_retained'] = list(actual['directories_retained'])
            if actual != review:
                raise ClientPlatformError('workspace_undo_review_changed')
            validate_review(review, nonce, key)
        initial = {'command_id': key, 'status': 'admitting', '_workspace_undo': {
            'scope': _scope(owner, conversation, binding), 'review': review, 'recovery': None}}
        try:
            saved = admissions.claim_command(owner, key, canonical, conversation,
                                             initial_result=initial if previous is None else None)
        except admissions.AdmissionError as exc:
            if str(exc) != 'operation_uncertain':
                raise ClientPlatformError(str(exc), exc.current_revision) from exc
            saved = _saved(owner, key, conversation, binding, validate)
        if saved:
            saved = _saved(owner, key, conversation, binding, validate)
            if saved['status'] == 'completed':
                return read_workspace_undo_command(owner, key, conversation, binding, validate=validate)
            if saved['_workspace_undo']['review'] != review:
                raise ClientPlatformError('workspace_undo_review_changed')
        progress = deepcopy(saved) if saved else initial

        def authority() -> None:
            validate()
            validate_review(review, nonce, key)

        def persist(recovery: dict) -> None:
            authority()
            progress['_workspace_undo']['recovery'] = deepcopy(recovery)
            admissions.command_progress(owner, key, progress)

        authority()
        result = _public_result(asdict(client_undo.undo_workspace_change(domain_review, command_id=key,
            confirmed=True, persist_recovery=persist, recovery=progress['_workspace_undo']['recovery'],
            validate=authority)), key, review)
        authority()
        progress['result'] = result
        progress['status'] = 'partial' if result['status'] == 'partial' else 'completed'
        if progress['status'] == 'partial':
            admissions.command_progress(owner, key, progress)
        else:
            admissions.complete_command(owner, key, progress)
        return result
