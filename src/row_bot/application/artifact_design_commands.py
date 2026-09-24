"""Exact Designer effects and conservative recovery in canonical admissions."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import UUID

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

_TYPES = {'artifact.design.control', 'artifact.asset.upload', 'artifact.preset.mutate', 'artifact.document.import', 'artifact.notes.generate'}
_OPERATIONS = {'brand', 'preset', 'style', 'hotspot', 'review_fix', 'asset_insert', 'asset_remove', 'asset_forget', 'block_insert'}
_ASSET_STAGES = {'asset_prepared': 1, 'asset_written': 2, 'asset_attached': 3}
_PURE_FAILURES = {'invalid_design_control', 'font_unavailable', 'design_preset_unavailable', 'design_component_unavailable',
    'invalid_document_import', 'document_import_unavailable', 'document_import_too_large', 'artifact_type_unavailable', 'page_unavailable',
    'design_preset_exists', 'design_preset_builtin', 'design_finding_unavailable',
    'asset_still_referenced', 'asset_already_on_page', 'asset_type_unavailable', 'asset_content_unsafe', 'asset_too_large'}


def public_receipt(value: dict) -> dict:
    """Use for command responses AND generic receipt/replay endpoints."""
    return {name: item for name, item in value.items() if name != '_artifact_design'}


def _preset_proof(raw: dict, command_id: str, action: str) -> dict | None:
    """Read exact retained publication identities; never restore or rewrite."""
    from row_bot.designer import brand
    from row_bot.designer.state import BrandConfig
    from row_bot.developer import edits
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    proof = edits.FileEditRecovery(**raw)
    if (proof.command_id != command_id or Path(proof.relative_path).name != proof.relative_path
            or not proof.relative_path.endswith('.json') or len(proof.relative_path) > 55
            or any(char in proof.relative_path for char in '/\\:\0')):
        return None
    root = brand._BRAND_DIR
    with ExitStack() as stack:
        root_fd = stack.enter_context(_empty_parent_guard(root, _directory_identity(root, parent=True)))
        info = os.fstat(root_fd) if root_fd is not None else root.stat()
        if f'{info.st_dev}:{info.st_ino}' != proof.root_identity:
            return None
        recovery = root / '.row-bot-edit-recovery'
        attempt = recovery / command_id
        if root_fd is None:
            stack.enter_context(_empty_parent_guard(recovery, _directory_identity(recovery, parent=True)))
            stack.enter_context(_empty_parent_guard(attempt, _directory_identity(attempt, parent=True)))
            attempt_fd = None
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            recovery_fd = os.open(recovery.name, flags, dir_fd=root_fd)
            stack.callback(os.close, recovery_fd)
            attempt_fd = os.open(command_id, flags, dir_fd=recovery_fd)
            stack.callback(os.close, attempt_fd)

        def read(directory: Path, fd: int | None, name: str) -> tuple[bytes | None, str, str, str]:
            data, digest, identity, _mode = (edits._read_edit_at(fd, name) if fd is not None
                                            else edits.read_edit_bytes(directory, name))
            if data is None:
                return data, digest, identity, ''
            info = os.stat(name, dir_fd=fd, follow_symlinks=False) if fd is not None else (directory / name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or len(data) > 65536:
                raise ValueError('preset_proof_unavailable')
            metadata = edits._edit_metadata_at(fd, name) if fd is not None else edits.file_edit_metadata_digest(directory / name)
            return data, digest, identity, metadata

        target = read(root, root_fd, proof.relative_path)
        previous = read(attempt, attempt_fd, 'previous')
        candidate = read(attempt, attempt_fd, 'candidate')
        if (target[1:3] != (proof.after_digest, proof.candidate_identity)
                or previous[1:3] != (proof.before_digest, proof.original_identity)
                or candidate[0] is not None):
            return None
        actual_metadata = target[3] if target[0] is not None else previous[3]
        if actual_metadata != proof.metadata_digest:
            return None
        if action == 'delete':
            return {} if target[0] is None and previous[0] is not None else None
        if target[0] is None:
            return None
        value = json.loads(target[0])
        name = value.pop('name')
        if brand._preset_filename(name) != proof.relative_path:
            return None
        from row_bot.designer.client_design_controls import _hash
        return {'preset_id': _hash([name, asdict(BrandConfig.from_dict(value))])}


def execute_artifact_design_command(service: Any, command: dict, conversation_id: str, *,
        owner_id: str, key: str, validate: Callable[[], None],
        resolve_upload: Callable[[str], bytes] | None = None,
        validate_confirmation: Callable[[dict], None] | None = None) -> dict:
    """Never replay a possibly committed control, upload or global preset effect."""
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer import client_design_controls as controls, storage
    from row_bot.designer.client_service import ArtifactError, read_artifact
    from row_bot.developer.edits import FileEditRecovery, ordinary_edit_decision
    from row_bot.thread_cleanup import is_thread_deleting
    from row_bot.threads import _get_thread_approval_mode

    kind, payload = command.get('type'), command['payload']
    target = payload['target']
    try:
        if kind not in _TYPES or target['kind'] != 'artifact' or str(UUID(command['command_id'])) != command['command_id']:
            raise ValueError
        if kind == 'artifact.design.control' and payload.get('operation') not in _OPERATIONS:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ClientPlatformError('invalid_design_control') from None
    operation = (payload['operation'] if kind == 'artifact.design.control' else
                 'asset_upload' if kind == 'artifact.asset.upload' else
                 'document_import' if kind == 'artifact.document.import' else
                 'notes_generate' if kind == 'artifact.notes.generate' else
                 'preset_' + str(payload.get('action')))

    def authority(*, effect: bool = False) -> None:
        if effect and kind == 'artifact.preset.mutate':
            if validate_confirmation is None:
                raise ClientPlatformError('action_denied')
            validate_confirmation(command)
        validate()
        if is_thread_deleting(conversation_id, initialized_read=True):
            raise ClientPlatformError('conversation_deleting')
        metadata = service._metadata(conversation_id)
        binding = next((item for item in list_bindings(conversation_id).bindings if item.binding_id == target['binding_id']), None)
        if (binding is None or binding.kind != 'artifact' or binding.resource_id != target['resource_id']
                or binding.revision != target['binding_revision']):
            raise ClientPlatformError('resource_binding_revoked')
        if effect:
            if str(metadata['client_revision']) != command['expected_revision']:
                raise ClientPlatformError('revision_conflict', str(metadata['client_revision']))
            if ordinary_edit_decision(_get_thread_approval_mode(conversation_id)).decision != 'allow':
                raise ClientPlatformError('edit_policy_denied')
            if read_artifact(target['resource_id']).updated_at != target['resource_revision']:
                raise ClientPlatformError('resource_revision_conflict')

    authority()
    # The sole canonical project lock also excludes read-only reconciliation
    # while the original admitted call is still executing its checkpoints.
    lock = storage._project_save_lock(target['resource_id'])
    if not lock.acquire(blocking=False):
        raise ClientPlatformError('artifact_busy')
    try:
        # Claim initializes existing canonical lifecycle tables for this write.
        authority()
        retry = False
        try:
            prior = admissions.claim_command(owner_id, key, command, conversation_id)
        except admissions.AdmissionError as exc:
            if str(exc) != 'operation_uncertain':
                raise ClientPlatformError(str(exc), exc.current_revision) from exc
            retry, prior = True, admissions.receipt(owner_id, command['command_id'])
        authority()
        if prior and prior.get('status') == 'completed':
            return public_receipt(prior)
        progress = prior or {'command_id': command['command_id'], 'conversation_id': conversation_id,
            'binding_id': target['binding_id'], 'binding_revision': target['binding_revision'],
            'resource_id': target['resource_id'], 'resource_revision': target['resource_revision'], 'status': 'admitting'}
        private = progress.setdefault('_artifact_design', {})
        if type(private) is not dict:
            private = {}  # Corrupt saved proof can only remain unconfirmed.

        def public_asset() -> dict[str, str]:
            asset = private.get('asset')
            expected = 'asset-' + UUID(command['command_id']).hex
            return {'asset_id': expected} if type(asset) is dict and asset.get('asset_id') == expected else {}

        def partial() -> dict:
            progress.update(status='partial', artifact_design={'resource_id': target['resource_id'],
                'resource_revision': progress['resource_revision'], 'operation': operation,
                'status': 'partial', 'code': 'artifact_design_unconfirmed',
                **public_asset()})
            try:
                admissions.command_progress(owner_id, key, progress)
            except Exception:
                pass  # Existing saved effect marker remains authoritative.
            return public_receipt(progress)

        def completed(outcome: dict) -> dict:
            if (type(outcome) is not dict or set(outcome) - {'resource_id', 'resource_revision', 'operation', 'status', 'code', 'asset_id', 'preset_id'}
                    or outcome.get('resource_id') != target['resource_id'] or outcome.get('operation') != operation
                    or outcome.get('status') not in {'saved', 'unchanged'} or outcome.get('code') != ''
                    or not isinstance(outcome.get('resource_revision'), str) or len(outcome['resource_revision']) > 128
                    or ('asset_id' in outcome and outcome['asset_id'] != 'asset-' + UUID(command['command_id']).hex)
                    or ('preset_id' in outcome and (not isinstance(outcome['preset_id'], str) or not re.fullmatch('[0-9a-f]{64}', outcome['preset_id'])))):
                raise ClientPlatformError('invalid_design_checkpoint')
            progress.update(status='completed', resource_revision=outcome['resource_revision'], artifact_design=outcome)
            result = admissions.complete_command(owner_id, key, progress)
            try:
                service.projection.publish(conversation_id, 'resource.changed', {'revision': command['expected_revision']})
            except Exception:
                pass  # Durable completion must survive an unavailable event stream.
            return public_receipt(result)

        if retry:
            try:
                if private.get('outcome'):
                    return completed(private['outcome'])
                if kind == 'artifact.preset.mutate' and private.get('preset'):
                    proof = _preset_proof(private['preset'], command['command_id'], payload['action'])
                    if proof is not None:
                        return completed({'resource_id': target['resource_id'], 'resource_revision': target['resource_revision'],
                            'operation': operation, 'status': 'saved', 'code': '',
                            **({'preset_id': payload['preset_id']} if payload.get('preset_id') else {}), **proof})
                asset = private.get('asset')
                if kind == 'artifact.asset.upload' and asset and asset['stage'] == 'asset_attached':
                    project = read_artifact(target['resource_id'])
                    saved = next((item for item in project.assets if item.id == asset['asset_id']), None)
                    if saved and saved.sha256 == asset['sha256'] and saved.size_bytes == asset['size_bytes']:
                        controls._asset_bytes(project, saved)
                        return completed({'resource_id': project.id, 'resource_revision': project.updated_at,
                            'operation': operation, 'status': 'saved', 'asset_id': saved.id, 'code': ''})
                imported = private.get('import')
                if kind == 'artifact.document.import' and type(imported) is dict:
                    from row_bot.designer.client_import import imported_pages_present
                    count = imported.get('count')
                    if type(count) is int:
                        revision = imported_pages_present(target['resource_id'], command['command_id'], count)
                        if revision:
                            return completed({'resource_id': target['resource_id'], 'resource_revision': revision,
                                'operation': operation, 'status': 'saved', 'code': ''})
                notes = private.get('notes')
                if kind == 'artifact.notes.generate' and type(notes) is dict:
                    from row_bot.designer.client_notes import saved_notes_revision
                    if (notes.get('page_id') == payload.get('page_id') and
                            isinstance(notes.get('digest'), str) and re.fullmatch('[0-9a-f]{64}', notes['digest'])):
                        revision = saved_notes_revision(target['resource_id'], notes['page_id'], notes['digest'])
                        if revision:
                            return completed({'resource_id': target['resource_id'], 'resource_revision': revision,
                                'operation': operation, 'status': 'saved', 'code': ''})
            except Exception:
                pass
            return partial()

        started = False
        try:
            authority(effect=True)
            data = None
            if kind in {'artifact.asset.upload', 'artifact.document.import'}:
                if resolve_upload is None:
                    raise ClientPlatformError('upload_unavailable')
                data = resolve_upload(payload['upload_id'])
                if (type(data) is not bytes or type(payload.get('size_bytes')) is not int
                        or not 0 < len(data) <= 25 * 1024 * 1024 or len(data) != payload['size_bytes']
                        or not isinstance(payload.get('sha256'), str) or hashlib.sha256(data).hexdigest() != payload['sha256']):
                    raise ClientPlatformError('upload_identity_conflict')
            authority(effect=True)
            private['stage'] = 'effect_started'
            admissions.command_progress(owner_id, key, progress)
            started = True

            def checkpoint(value: FileEditRecovery | dict) -> None:
                authority()
                if isinstance(value, FileEditRecovery):
                    if value.command_id != command['command_id'] or kind != 'artifact.preset.mutate':
                        raise ClientPlatformError('invalid_design_checkpoint')
                    private['preset'] = asdict(value)
                elif type(value) is dict:
                    if (set(value) != {'stage', 'asset_id', 'sha256', 'size_bytes'}
                            or value['stage'] not in _ASSET_STAGES
                            or value['asset_id'] != 'asset-' + UUID(command['command_id']).hex
                            or type(value['size_bytes']) is not int or not 0 < value['size_bytes'] <= 25 * 1024 * 1024
                            or not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256'])):
                        raise ClientPlatformError('invalid_design_checkpoint')
                    previous = private.get('asset')
                    if previous and (value['sha256'] != previous['sha256'] or value['size_bytes'] != previous['size_bytes']
                            or _ASSET_STAGES[value['stage']] < _ASSET_STAGES[previous['stage']]):
                        raise ClientPlatformError('invalid_design_checkpoint')
                    private['asset'] = dict(value)
                else:
                    raise ClientPlatformError('invalid_design_checkpoint')
                admissions.command_progress(owner_id, key, progress)

            options = {'expected_revision': target['resource_revision'], 'command_id': command['command_id'],
                       'validate': lambda: authority(effect=True), 'checkpoint': checkpoint}
            if kind == 'artifact.design.control':
                result = controls.apply_control(target['resource_id'], operation=payload['operation'], payload=payload['parameters'],
                    page_id=payload.get('page_id'), element_id=payload.get('element_id'), **options)
            elif kind == 'artifact.asset.upload':
                result = controls.upload_asset(target['resource_id'], filename=payload['filename'], data=data, **options)
            elif kind == 'artifact.document.import':
                from row_bot.designer.client_import import _parse, import_document
                pages = _parse(payload['filename'], data)
                private['import'] = {'count': len(pages), 'sha256': payload['sha256']}
                admissions.command_progress(owner_id, key, progress)
                result = import_document(target['resource_id'], expected_revision=target['resource_revision'],
                    filename=payload['filename'], pages=pages, command_id=command['command_id'],
                    replace=payload['replace'], validate=lambda: authority(effect=True))
            elif kind == 'artifact.notes.generate':
                from row_bot.designer.client_notes import generate_page_notes
                from row_bot.designer.client_editing import apply_edit
                notes, unchanged = generate_page_notes(target['resource_id'],
                    expected_revision=target['resource_revision'], page_id=payload['page_id'],
                    validate=lambda: authority(effect=True))
                if unchanged:
                    result = read_artifact(target['resource_id'])
                else:
                    private['notes'] = {'page_id': payload['page_id'],
                        'digest': hashlib.sha256(notes.encode('utf-8')).hexdigest()}
                    admissions.command_progress(owner_id, key, progress)
                    result = apply_edit(target['resource_id'], expected_revision=target['resource_revision'],
                        operation='page_properties', page_id=payload['page_id'], notes=notes,
                        validate=lambda: authority(effect=True))
            else:
                result = controls.mutate_preset(target['resource_id'], action=payload['action'],
                    name=payload.get('name'), preset_id=payload.get('preset_id'), **options)
            revision = result['resource_revision'] if isinstance(result, dict) else result.updated_at
            outcome = {'resource_id': target['resource_id'], 'resource_revision': revision, 'operation': operation,
                'status': 'saved' if kind == 'artifact.preset.mutate' or revision != target['resource_revision'] else 'unchanged', 'code': ''}
            if private.get('asset'):
                outcome['asset_id'] = private['asset']['asset_id']
            if isinstance(result, dict):
                outcome['preset_id'] = result['preset_id']
            private['outcome'] = outcome
            admissions.command_progress(owner_id, key, progress)
            return completed(outcome)
        except Exception as exc:
            code = exc.code if isinstance(exc, (ClientPlatformError, ArtifactError)) else 'artifact_design_unconfirmed'
            if started and not (code in _PURE_FAILURES and not private.get('asset') and not private.get('preset')):
                return partial()
            admissions.reject_command(owner_id, key, code)
            raise ClientPlatformError(code, getattr(exc, 'current_revision', None)) from exc
    finally:
        lock.release()
