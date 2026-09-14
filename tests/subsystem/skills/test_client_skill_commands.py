"""Strict client skill ownership uses isolated canonical files and fake policy."""
from dataclasses import asdict
import hashlib
import json
import os
from uuid import uuid4

import pytest

from row_bot.application import skill_commands as commands
from row_bot.runtime import admissions

pytestmark = pytest.mark.subsystem


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv('ROW_BOT_DATA_DIR', str(tmp_path / 'data'))
    from row_bot import skills
    monkeypatch.setattr(commands, 'skills', skills)
    root = tmp_path / 'data'
    root.mkdir(exist_ok=True)
    for name, path in [('DATA_DIR', root), ('USER_SKILLS_DIR', root / 'skills'),
                       ('BUNDLED_SKILLS_DIR', tmp_path / 'bundled'),
                       ('TOOL_GUIDES_DIR', tmp_path / 'guides'), ('CONFIG_PATH', root / 'skills_config.json')]:
        monkeypatch.setattr(skills, name, path)
    monkeypatch.setattr(skills, '_skills_cache', {})
    monkeypatch.setattr(skills, '_enabled', {})
    monkeypatch.setattr(skills, '_pinned', [])
    from row_bot import tasks
    monkeypatch.setattr(tasks, '_DB_PATH', str(root / 'tasks.db'))
    monkeypatch.setattr(tasks, '_SCHEMA_READY_PATH', None)
    path = skills.USER_SKILLS_DIR / 'sample'
    path.mkdir(parents=True)
    (path / 'SKILL.md').write_text('---\nname: sample\ndisplay_name: Sample\ndescription: A saved skill\n---\n\nUse the approved tools.\n', encoding='utf-8')
    return skills


def noop(*_args):
    pass


def prepare(action, payload):
    review = commands.review_skill_command(action, payload, validate=noop)
    command = {'command_id': str(uuid4()), 'type': action,
               'payload': {**payload, 'review_id': 'reviewed-nonce'}}
    return command, review


def execute(command, review, **overrides):
    def accepted(_command, actual):
        assert actual == review
    return commands.execute_skill_command(command, owner_id=overrides.pop('owner_id', 'owner'),
        authority_id=overrides.pop('authority_id', 'authority'), key=command['command_id'],
        validate=overrides.pop('validate', noop), validate_action=overrides.pop('validate_action', noop),
        validate_review=overrides.pop('validate_review', accepted), **overrides)


def fields(**updates):
    return {'display_name': 'Created skill', 'icon': '✨', 'description': 'A bounded skill.',
            'instructions': 'Use only the explicitly approved inputs.', 'tags': ['saved'],
            'activation': {'keywords': ['approved']}, 'version': '1.0', **updates}


def test_passive_snapshot_uses_current_bytes_without_loading_or_writing(library, monkeypatch):
    skills = library
    monkeypatch.setattr(skills, 'load_skills', lambda: pytest.fail('passive skill migration'))
    before = sorted(str(p) for p in skills.DATA_DIR.rglob('*'))
    snapshot = skills.read_client_skills()
    assert snapshot['items']['sample']['skill'].instructions == 'Use the approved tools.'
    assert snapshot['config_revision'] == 'missing'
    assert before == sorted(str(p) for p in skills.DATA_DIR.rglob('*'))
    assert not skills._skills_cache


def test_passive_snapshot_accepts_the_public_description_bound(library):
    path = library.USER_SKILLS_DIR / 'sample' / 'SKILL.md'
    description = 'Detailed local workflow guidance. ' * 20
    path.write_text(
        '---\nname: sample\ndisplay_name: Sample\ndescription: "'
        + description
        + '"\n---\n\nUse the approved tools.\n',
        encoding='utf-8',
    )

    snapshot = library.read_client_skills()

    assert snapshot['items']['sample']['skill'].description == description


def test_global_pin_publishes_single_link_and_availability_removes_pin(library):
    skills = library
    snapshot = skills.read_client_skills()
    receipts = []
    skills.update_client_skill_preference('sample', 'pin_defaults', True,
        expected_revision=snapshot['revision'], command_id=str(uuid4()), validate=lambda: None, checkpoint=receipts.append)
    assert skills.is_enabled('sample') and skills.is_pinned('sample')
    assert skills.CONFIG_PATH.stat().st_nlink == 1
    snapshot = skills.read_client_skills()
    skills.update_client_skill_preference('sample', 'availability', False,
        expected_revision=snapshot['revision'], command_id=str(uuid4()), validate=lambda: None, checkpoint=receipts.append)
    saved = json.loads(skills.CONFIG_PATH.read_bytes())
    assert saved['skills']['sample'] is False and saved['pinned'] == []
    assert len(receipts) == 2


def test_global_update_rechecks_source_after_review_and_keeps_original_config(library):
    skills = library
    snapshot = skills.read_client_skills()
    path = skills.USER_SKILLS_DIR / 'sample' / 'SKILL.md'
    path.write_text(path.read_text() + 'User edit\n')
    with pytest.raises(ValueError, match='skill_revision_conflict'):
        skills.update_client_skill_preference('sample', 'availability', True,
            expected_revision=snapshot['revision'], command_id=str(uuid4()), validate=lambda: None, checkpoint=lambda _: None)
    assert not skills.CONFIG_PATH.exists()


def test_revocation_after_checkpoint_preserves_original_bytes(library):
    skills = library
    skills.CONFIG_PATH.write_bytes(b'{"skills":{"sample":false},"pinned":[]}')
    before = skills.CONFIG_PATH.read_bytes()
    snapshot = skills.read_client_skills()
    active = [True]
    def validate():
        if not active[0]:
            raise PermissionError('revoked')
    def checkpoint(_):
        active[0] = False
    with pytest.raises(PermissionError, match='revoked'):
        skills.update_client_skill_preference('sample', 'availability', True,
            expected_revision=snapshot['revision'], command_id=str(uuid4()), validate=validate, checkpoint=checkpoint)
    assert skills.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize('target', ['config', 'skill'])
def test_linked_files_are_not_adopted_or_modified(library, tmp_path, target):
    skills = library
    source = tmp_path / 'unowned'
    source.write_bytes(b'{}')
    path = skills.CONFIG_PATH if target == 'config' else skills.USER_SKILLS_DIR / 'sample' / 'SKILL.md'
    if path.exists():
        path.rename(path.with_name('retained'))
    os.link(source, path)
    with pytest.raises(ValueError):
        skills.read_client_skills()
    assert source.read_bytes() == b'{}'


@pytest.mark.parametrize('payload', ['---\nname: sample\na: &a [*a]\n---\nText',
    '---\nname: ../../outside\n---\nText', '---\nname: sample\n---\n' + 'x' * 65536],
    ids=['yaml-alias', 'path-name', 'oversized'])
def test_unsafe_or_oversized_skill_metadata_is_explicitly_unavailable(library, payload):
    path = library.USER_SKILLS_DIR / 'sample' / 'SKILL.md'
    path.write_text(payload)
    with pytest.raises(ValueError):
        library.read_client_skills()
    assert path.read_text() == payload


def test_shared_retirement_preserves_original_and_confirms_exact_proof(tmp_path):
    from row_bot.file_publication import publish_bytes, read_recovery
    path = tmp_path / 'retire.txt'
    before = b'owned original\r\n'
    path.write_bytes(before)
    proof = []
    assert publish_bytes(tmp_path, path.name, None, expected_revision=hashlib.sha256(before).hexdigest(),
        command_id=str(uuid4()), validate=lambda: None, checkpoint=proof.append) == 'missing'
    assert not path.exists() and len(proof) == 1
    assert read_recovery(tmp_path, path.name, proof[0]) == 'applied'
    assert (tmp_path / '.row-bot-edit-recovery' / proof[0].command_id / 'previous').read_bytes() == before
    assert 'owned original' not in json.dumps(asdict(proof[0]))


@pytest.mark.parametrize('name', ['../outside', 'folder/name', 'file:stream'])
def test_shared_read_rejects_nonleaf_names_without_open(tmp_path, name):
    from row_bot.file_publication import read_bytes
    with pytest.raises(ValueError, match='invalid_publication_target'):
        read_bytes(None, tmp_path, name)


def test_client_library_and_detail_are_bounded_path_free_and_passive(library, monkeypatch):
    monkeypatch.setattr(library, 'load_skills', lambda: pytest.fail('passive read loaded skills'))
    before = {str(path): path.read_bytes() for path in library.DATA_DIR.rglob('*') if path.is_file()}
    page = commands.read_skill_library(query='saved', validate=noop)
    detail = commands.read_skill_detail('sample', validate=noop)
    assert page['items'][0] == {
        'id': 'sample', 'display_name': 'Sample', 'icon': '✨',
        'description': 'A saved skill', 'source': 'user', 'version': '1.0',
        'tags': [], 'activation': {}, 'available': False, 'pinned': False,
        'editable': True, 'tool_guide': False, 'revision': page['items'][0]['revision'],
        'instructions_preview': 'Use the approved tools.', 'truncated': False,
    }
    assert detail['skill']['instructions'] == 'Use the approved tools.'
    assert str(library.DATA_DIR) not in json.dumps([page, detail])
    assert before == {str(path): path.read_bytes() for path in library.DATA_DIR.rglob('*') if path.is_file()}


def test_client_skills_keep_automatic_tool_guides_out_of_manual_management(library):
    guide = library.TOOL_GUIDES_DIR / 'shell_guide'
    guide.mkdir(parents=True)
    (guide / 'SKILL.md').write_text(
        '---\nname: shell_guide\ntools:\n  - shell\n---\nAutomatic guide.\n', encoding='utf-8')
    page = commands.read_skill_library(validate=noop)
    assert [item['id'] for item in page['items']] == ['sample']
    with pytest.raises(Exception, match='skill_missing'):
        commands.read_skill_detail('shell_guide', validate=noop)


def test_client_library_paging_is_revision_bound(library):
    second = library.USER_SKILLS_DIR / 'second'
    second.mkdir()
    (second / 'SKILL.md').write_text(
        '---\nname: second\ndisplay_name: Second\n---\nSecond instructions.\n', encoding='utf-8')
    first = commands.read_skill_library(limit=1, validate=noop)
    third = library.USER_SKILLS_DIR / 'third'
    third.mkdir()
    (third / 'SKILL.md').write_text(
        '---\nname: third\ndisplay_name: Third\n---\nThird instructions.\n', encoding='utf-8')
    with pytest.raises(Exception, match='cursor_expired'):
        commands.read_skill_library(limit=1, cursor=first['next_cursor'], validate=noop)


def test_reviewed_create_edit_duplicate_delete_and_exact_recovery(library, monkeypatch):
    snapshot = library.read_client_skills()
    create, review = prepare('skill.create', {
        'revision': snapshot['revision'], 'name': 'created', 'fields': fields()})
    created = execute(create, review)
    assert created['status'] == 'completed' and created['skill_id'] == 'created'
    created_path = library.USER_SKILLS_DIR / 'created' / 'SKILL.md'
    assert created_path.stat().st_nlink == 1
    assert 'Use only the explicitly approved inputs.' in created_path.read_text(encoding='utf-8')
    assert execute(create, review) == created

    snapshot = library.read_client_skills()
    edit, edit_review = prepare('skill.edit', {'revision': snapshot['revision'], 'name': 'created',
        'skill_revision': snapshot['items']['created']['revision'],
        'fields': {'instructions': 'Edited only after explicit review.'}})
    assert execute(edit, edit_review)['status'] == 'completed'
    assert 'Edited only after explicit review.' in created_path.read_text(encoding='utf-8')

    snapshot = library.read_client_skills()
    duplicate, duplicate_review = prepare('skill.duplicate', {
        'revision': snapshot['revision'], 'name': 'created', 'new_name': 'created_copy'})
    assert execute(duplicate, duplicate_review)['skill_id'] == 'created_copy'
    assert (library.USER_SKILLS_DIR / 'created_copy' / 'SKILL.md').exists()

    snapshot = library.read_client_skills()
    delete, delete_review = prepare('skill.delete', {'revision': snapshot['revision'], 'name': 'created',
        'skill_revision': snapshot['items']['created']['revision']})
    monkeypatch.setattr(admissions, 'complete_command', lambda *_args: (_ for _ in ()).throw(OSError('lost receipt')))
    recovered = execute(delete, delete_review)
    assert recovered['status'] == 'completed' and not created_path.exists()
    monkeypatch.setattr(library, 'delete_skill', lambda *_args: pytest.fail('no replay'))
    assert execute(delete, delete_review) == recovered


def test_import_canonicalizes_manual_skill_and_rejects_tool_guides(library):
    snapshot = library.read_client_skills()
    content = ('---\nname: imported\ndisplay_name: Imported\ndescription: Browser import\n'
               'tags:\n  - local\n---\n\nImported instructions.\n')
    command, review = prepare('skill.import', {'revision': snapshot['revision'], 'content': content})
    result = execute(command, review)
    assert result['skill_id'] == 'imported'
    saved = (library.USER_SKILLS_DIR / 'imported' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'author: User' in saved and 'tools:' not in saved
    snapshot = library.read_client_skills()
    with pytest.raises(Exception, match='invalid_skill_import'):
        prepare('skill.import', {'revision': snapshot['revision'], 'content':
            '---\nname: guide\ntools:\n  - shell\n---\nNever execute.\n'})


@pytest.mark.parametrize('preference,value,available,pinned', [
    ('pin_defaults', True, True, True),
    ('availability', False, False, False),
])
def test_reviewed_preferences_preserve_pin_invariants(library, preference, value, available, pinned):
    if preference == 'availability':
        snapshot = library.read_client_skills()
        first, first_review = prepare('skill.preference', {'revision': snapshot['revision'], 'name': 'sample',
            'preference': 'pin_defaults', 'value': True})
        execute(first, first_review)
    snapshot = library.read_client_skills()
    command, review = prepare('skill.preference', {'revision': snapshot['revision'], 'name': 'sample',
        'preference': preference, 'value': value})
    result = execute(command, review)
    current = library.read_client_skills()
    assert result['status'] == 'completed'
    assert current['enabled']['sample'] is available
    assert ('sample' in current['pinned']) is pinned


def test_changed_source_and_revoked_review_never_start_a_skill_effect(library):
    snapshot = library.read_client_skills()
    command, review = prepare('skill.edit', {'revision': snapshot['revision'], 'name': 'sample',
        'skill_revision': snapshot['items']['sample']['revision'],
        'fields': {'description': 'Reviewed change'}})
    path = library.USER_SKILLS_DIR / 'sample' / 'SKILL.md'
    path.write_text(path.read_text(encoding='utf-8') + '\nExternal edit.\n', encoding='utf-8')
    with pytest.raises(Exception, match='skill_revision_conflict'):
        execute(command, review)
    assert admissions.read_command_metadata('owner', command['command_id']) is None

    fresh = library.read_client_skills()
    denied, denied_review = prepare('skill.edit', {'revision': fresh['revision'], 'name': 'sample',
        'skill_revision': fresh['items']['sample']['revision'], 'fields': {'description': 'Denied'}})
    with pytest.raises(ValueError, match='revoked'):
        execute(denied, denied_review, validate_action=lambda *_: (_ for _ in ()).throw(ValueError('revoked')))
    assert 'description: Denied' not in path.read_text(encoding='utf-8')
    assert admissions.read_command_metadata('owner', denied['command_id']) is None


def test_foreign_receipt_and_idempotency_mismatch_are_denied(library):
    snapshot = library.read_client_skills()
    command, review = prepare('skill.create', {
        'revision': snapshot['revision'], 'name': 'private_skill', 'fields': fields()})
    execute(command, review)
    with pytest.raises(Exception, match='skill_operation_unavailable'):
        commands.read_skill_command(owner_id='owner', authority_id='foreign',
            command_id=command['command_id'], validate=noop)
    command['payload']['name'] = 'changed_skill'
    with pytest.raises(Exception, match='idempotency_mismatch'):
        execute(command, review)


def test_proposal_reads_are_passive_redacted_and_actions_use_canonical_owner(library):
    store = library.DATA_DIR / 'controlled_evolution.json'
    proposal = {
        'id': 'proposal_1', 'proposal_type': 'create_skill', 'title': '<script>Proposal</script>',
        'rationale': 'Review this proposal.', 'risk': 'low', 'status': 'ready',
        'preview': {'instructions_preview': r'Plain text C:\Users\private sk-abcdefghijklmnop.'},
    }
    store.write_text(json.dumps({'proposals': [proposal]}), encoding='utf-8')
    before = store.read_bytes()
    result = commands.read_skill_proposals(validate=noop)
    assert result['items'][0]['title'] == '<script>Proposal</script>'
    assert '[local-user-path]' in result['items'][0]['preview']['instructions_preview']
    assert '[redacted-secret]' in result['items'][0]['preview']['instructions_preview']
    assert before == store.read_bytes() and str(library.DATA_DIR) not in json.dumps(result)

    snapshot = library.read_client_skills()
    command, review = prepare('skill.proposal.reject', {'revision': snapshot['revision'],
        'proposal_id': 'proposal_1', 'reason': 'Does not fit this workflow.'})
    assert execute(command, review)['status'] == 'completed'
    saved = json.loads(store.read_text(encoding='utf-8'))
    assert saved['proposals'][0]['status'] == 'rejected'
    assert saved['rejected_proposals'][0]['reason'] == 'Does not fit this workflow.'


def test_reviewed_proposal_apply_uses_canonical_evolution_and_is_not_replayed(library, monkeypatch):
    from row_bot import evolution
    proposal = evolution.create_proposal(insight_ids=[], proposal_type='create_skill',
        title='Create proposed skill', rationale='A repeatable workflow.', payload={
            'name': 'proposed_skill', 'display_name': 'Proposed skill', 'icon': '✨',
            'description': 'A reviewed proposal.', 'instructions': 'Follow the reviewed workflow.',
            'tags': ['proposal'], 'enabled': True, 'version': '1.0'}, preview={}, status='ready')
    snapshot = library.read_client_skills()
    command, review = prepare('skill.proposal.apply', {'revision': snapshot['revision'],
        'proposal_id': proposal['id'], 'reason': ''})
    result = execute(command, review)
    assert result['status'] == 'completed'
    assert (library.USER_SKILLS_DIR / 'proposed_skill' / 'SKILL.md').exists()
    monkeypatch.setattr(evolution, 'apply_proposal', lambda *_args, **_kwargs: pytest.fail('no proposal replay'))
    assert execute(command, review) == result


@pytest.mark.parametrize('bad_fields', [
    {'display_name': '', 'icon': 'x', 'description': '', 'instructions': 'ok', 'tags': [], 'activation': {}, 'version': '1'},
    {'display_name': 'X', 'icon': 'x', 'description': '', 'instructions': 'x' * (48 * 1024 + 1), 'tags': [], 'activation': {}, 'version': '1'},
    {'display_name': 'X', 'icon': 'x', 'description': '', 'instructions': 'ok', 'tags': [], 'activation': {'unknown': []}, 'version': '1'},
])
def test_invalid_fields_fail_before_admission(library, bad_fields):
    snapshot = library.read_client_skills()
    with pytest.raises(Exception, match='invalid_skill_fields'):
        prepare('skill.create', {'revision': snapshot['revision'], 'name': 'bad_skill', 'fields': bad_fields})
