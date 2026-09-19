"""Authenticated original-command recovery for canonical sandbox imports."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
import threading

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer import client_imports
from row_bot.runtime import admissions

_LOCK = threading.RLock()


def _scope(owner: str, conversation: str, binding: str, *, read_only: bool = False) -> str:
    return admissions.keyed_digest({'workspace_import_owner': owner, 'conversation': conversation, 'binding': binding}, read_only=read_only)


def _saved(owner: str, command_id: str, conversation: str, binding: str, validate: Callable[[], None]) -> dict:
    validate()
    metadata = admissions.read_command_metadata(owner, command_id)
    value = admissions.read_command_receipt(owner, command_id)
    private = value.get('_workspace_import') if isinstance(value, dict) else None
    if (not metadata or metadata['target'] != conversation or metadata['type'] != 'workspace.import'
            or not isinstance(private, dict) or private.get('scope') != _scope(owner, conversation, binding, read_only=True)
            or value.get('command_id') != command_id or not isinstance(private.get('review'), dict)):
        raise ClientPlatformError('workspace_import_unavailable')
    validate()
    return value


def read_workspace_import_command(owner: str, command_id: str, conversation: str, binding: str,
                                  *, validate: Callable[[], None]) -> dict:
    validate()
    if admissions.read_command_metadata(owner, command_id) is None:
        validate()
        raise ClientPlatformError('workspace_import_not_admitted')
    saved = _saved(owner, command_id, conversation, binding, validate)
    if isinstance(saved.get('result'), dict):
        return deepcopy(saved['result'])
    review = saved['_workspace_import']['review']
    return {'command_id': command_id, 'resource_id': review['resource_id'], 'conversation_id': conversation,
        'pending_change_id': review['pending_change_id'], 'status': 'partial', 'files_applied': [],
        'change_set_id': None, 'ledger_saved': False, 'imported': False, 'code': 'sandbox_import_unconfirmed'}


def review_workspace_import_recovery(owner: str, command_id: str, conversation: str, binding: str,
                                      *, validate: Callable[[], None]) -> dict:
    """Review only the retained original; current domain checks precede recovery effects."""
    return deepcopy(_saved(owner, command_id, conversation, binding, validate)['_workspace_import']['review'])


def execute_workspace_import(command: dict, conversation: str, binding: str, *, owner: str, key: str,
        validate: Callable[[], None], validate_review: Callable[[dict, str, str], None]) -> dict:
    """Serialize duplicate delivery; canonical workspace lease owns physical writes."""
    if command.get('type') != 'workspace.import' or command.get('command_id') != key:
        raise ClientPlatformError('invalid_command')
    payload = deepcopy(command['payload'])
    nonce = payload.pop('nonce')
    review = payload['review']
    if review['conversation_id'] != conversation or review['binding_id'] != binding:
        raise ClientPlatformError('resource_binding_revoked')
    canonical = {**command, 'payload': payload}
    with _LOCK:
        validate()
        previous = admissions.read_command_metadata(owner, key)
        if previous is None:
            actual = asdict(client_imports.review_workspace_import(review['resource_id'], conversation,
                review['pending_change_id'], validate=validate))
            # Normalize tuples as the closed wire representation does.
            actual['files'], actual['directories'] = list(actual['files']), list(actual['directories'])
            if actual != review:
                raise ClientPlatformError('workspace_import_review_changed')
            validate_review(review, nonce, key)
        initial = {'command_id': key, 'status': 'admitting', '_workspace_import': {
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
                return read_workspace_import_command(owner, key, conversation, binding, validate=validate)
            if saved['_workspace_import']['review'] != review:
                raise ClientPlatformError('workspace_import_review_changed')
        progress = deepcopy(saved) if saved else initial

        def authority() -> None:
            validate()
            validate_review(review, nonce, key)

        def persist(recovery: dict) -> None:
            authority()
            progress['_workspace_import']['recovery'] = deepcopy(recovery)
            admissions.command_progress(owner, key, progress)

        authority()
        domain_review = client_imports.WorkspaceImportReview(**{**review,
            'files': tuple(review['files']), 'directories': tuple(review['directories'])})
        result = asdict(client_imports.import_workspace_change(domain_review, command_id=key, confirmed=True,
            persist_recovery=persist, recovery=progress['_workspace_import']['recovery'], validate=authority))
        validate()
        progress['result'] = result
        progress['status'] = 'partial' if result['status'] == 'partial' else 'completed'
        if progress['status'] == 'partial':
            admissions.command_progress(owner, key, progress)
        else:
            admissions.complete_command(owner, key, progress)
        return result
