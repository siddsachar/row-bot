"""Authenticated Undo uses canonical retained bytes and original admissions."""
# ruff: noqa: F811 -- canonical disposable fixture dependency chain.
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from uuid import uuid4

import pytest

from row_bot.application import workspace_undo_commands as api
from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions
from tests.subsystem.developer.test_client_workspace_edits import domain  # noqa: F401
from tests.subsystem.developer.test_client_workspace_imports import imports  # noqa: F401
from tests.subsystem.developer.test_client_workspace_undo import undo  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def command(undo):
    d = undo
    identity = d.imported({'file.txt': 'before\r\n'}, {'file.txt': 'after\r\n'})
    review = json.loads(json.dumps(asdict(d.review(identity))))
    d.command = {'command_id': str(uuid4()), 'client_session_id': 'owner', 'type': 'workspace.undo',
                 'expected_revision': '0', 'payload': {'review': review, 'nonce': 'review-1'}}
    d.active, d.nonce = True, 'review-1'
    def validate():
        if not d.active:
            raise ClientPlatformError('capability_revoked')
    def reviewed(actual, nonce, key):
        assert actual == review and key == d.command['command_id']
        if nonce != d.nonce:
            raise ClientPlatformError('approval_expired')
    d.validate, d.reviewed = validate, reviewed
    d.execute = lambda: api.execute_workspace_undo(d.command, 'chat', review['binding_id'],
        owner='owner', key=d.command['command_id'], validate=validate, validate_review=reviewed)
    d.receipt = lambda: api.read_workspace_undo_command('owner', d.command['command_id'], 'chat',
        review['binding_id'], validate=validate)
    d.renew = lambda: api.review_workspace_undo_recovery('owner', d.command['command_id'], 'chat',
        review['binding_id'], validate=validate)
    return d


def test_real_restore_and_completed_replay_are_passive_and_scoped(command, monkeypatch):
    d = command
    result = d.execute()
    assert result['status'] == 'undone' and result['reverted'] and result['ledger_saved']
    assert (d.root / 'file.txt').read_bytes() == b'before\r\n'
    monkeypatch.setattr(api.client_undo, 'undo_workspace_change', lambda *a, **k: pytest.fail('replayed physical Undo'))
    monkeypatch.setattr(api.client_undo, 'review_workspace_undo', lambda *a, **k: pytest.fail('recaptured review'))
    d.nonce = 'expired'
    assert d.receipt() == result and d.execute() == result
    assert d.renew() == d.command['payload']['review']
    assert '_workspace_undo' not in json.dumps(result) and str(d.root) not in json.dumps(result)
    for owner, conversation, binding in [('foreign', 'chat', d.command['payload']['review']['binding_id']),
                                        ('owner', 'other', d.command['payload']['review']['binding_id']),
                                        ('owner', 'chat', 'foreign')]:
        with pytest.raises(ClientPlatformError):
            api.read_workspace_undo_command(owner, d.command['command_id'], conversation, binding, validate=d.validate)


def test_no_admission_is_distinct_from_unknown_saved_proof(command, monkeypatch):
    d = command
    with pytest.raises(ClientPlatformError, match='workspace_undo_not_admitted'):
        d.receipt()
    monkeypatch.setattr(api.client_undo, 'undo_workspace_change', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupt')))
    with pytest.raises(OSError):
        d.execute()
    assert d.receipt()['status'] == 'partial'
    with admissions.transaction() as conn:
        conn.execute("UPDATE client_commands SET result_json='{}' WHERE owner_id='owner'")
    with pytest.raises(ClientPlatformError, match='workspace_undo_unavailable'):
        d.receipt()
    with pytest.raises(ClientPlatformError, match='workspace_undo_unavailable'):
        d.execute()
    assert (d.root / 'file.txt').read_bytes() == b'after\r\n'


def test_cold_receipt_does_not_initialize_admissions(undo, monkeypatch, tmp_path):
    path = tmp_path / 'absent' / 'tasks.db'
    monkeypatch.setattr(undo.tasks, '_DB_PATH', str(path))
    monkeypatch.setattr(admissions, 'transaction', lambda: pytest.fail('passive initialization'))
    with pytest.raises(ClientPlatformError, match='workspace_undo_not_admitted'):
        api.read_workspace_undo_command('owner', str(uuid4()), 'chat', 'binding', validate=lambda: None)
    assert not path.parent.exists()


@pytest.mark.parametrize('failure', ['nonce', 'authority', 'changed_review'])
def test_preclaim_rejection_has_no_admission_or_restore(command, failure):
    d = command
    if failure == 'nonce':
        d.nonce = 'expired'
    elif failure == 'authority':
        d.active = False
    else:
        d.command['payload']['review']['resource_revision'] = 'changed'
    with pytest.raises(ClientPlatformError):
        d.execute()
    assert admissions.read_command_metadata('owner', d.command['command_id']) is None
    assert (d.root / 'file.txt').read_bytes() == b'after\r\n'


def test_postclaim_expiry_retains_original_review_atomically(command, monkeypatch):
    d = command
    claim = admissions.claim_command
    def expire(*args, **kwargs):
        result = claim(*args, **kwargs)
        d.nonce = 'renewed'
        return result
    monkeypatch.setattr(admissions, 'claim_command', expire)
    with pytest.raises(ClientPlatformError, match='approval_expired'):
        d.execute()
    assert d.renew() == d.command['payload']['review'] and d.receipt()['status'] == 'partial'
    assert (d.root / 'file.txt').read_bytes() == b'after\r\n'
    monkeypatch.setattr(admissions, 'claim_command', claim)
    d.command['payload']['nonce'] = d.nonce
    assert d.execute()['status'] == 'undone'


@pytest.mark.parametrize('after_commit', [False, True])
def test_final_receipt_failure_is_readonly_and_same_command_recovers(command, monkeypatch, after_commit):
    d = command
    complete = admissions.complete_command
    def fail(*args):
        if after_commit:
            complete(*args)
        raise OSError('lost final publication acknowledgement')
    monkeypatch.setattr(admissions, 'complete_command', fail)
    with pytest.raises(OSError):
        d.execute()
    inode = (d.root / 'file.txt').stat().st_ino
    assert (d.root / 'file.txt').read_bytes() == b'before\r\n'
    assert d.receipt()['status'] == ('undone' if after_commit else 'partial')
    monkeypatch.setattr(admissions, 'complete_command', complete)
    monkeypatch.setattr(d.edits, 'publish_text_revision', lambda *a, **k: pytest.fail('restored bytes republished'))
    assert d.execute()['status'] == 'undone'
    assert (d.root / 'file.txt').stat().st_ino == inode


def test_ledger_failure_keeps_original_review_and_recovers_with_new_nonce(command, monkeypatch):
    d = command
    marker = d.ledger.mark_reverted
    monkeypatch.setattr(d.ledger, 'mark_reverted', lambda *a, **k: (_ for _ in ()).throw(OSError('lost ledger')))
    first = d.execute()
    assert first['status'] == 'partial' and not first['ledger_saved']
    assert first['files_restored'] == ['file.txt']
    inode = (d.root / 'file.txt').stat().st_ino
    assert d.receipt() == first
    monkeypatch.setattr(d.ledger, 'mark_reverted', marker)
    d.nonce = 'renewed'
    with pytest.raises(ClientPlatformError, match='approval_expired'):
        d.execute()
    assert d.renew() == d.command['payload']['review']
    d.command['payload']['nonce'] = d.nonce
    assert d.execute()['status'] == 'undone'
    assert (d.root / 'file.txt').stat().st_ino == inode


def test_revocation_at_actual_file_publication_prevents_restore_and_retains_proof(command, monkeypatch):
    d = command
    publish = d.edits.publish_text_revision
    def revoke(*args, **kwargs):
        d.active = False
        return publish(*args, **kwargs)
    monkeypatch.setattr(d.edits, 'publish_text_revision', revoke)
    with pytest.raises(ClientPlatformError, match='capability_revoked'):
        d.execute()
    assert (d.root / 'file.txt').read_bytes() == b'after\r\n'
    assert admissions.read_command_metadata('owner', d.command['command_id']) is not None
    d.active = True
    assert d.renew() == d.command['payload']['review']
    monkeypatch.setattr(d.edits, 'publish_text_revision', publish)
    assert d.execute()['status'] == 'undone'


def test_same_command_changed_intent_cannot_retarget_completed_undo(command):
    d = command
    assert d.execute()['status'] == 'undone'
    d.command['payload']['review']['change_set_id'] = 'replacement'
    with pytest.raises(ClientPlatformError, match='idempotency_mismatch'):
        d.execute()


def test_passive_receipt_never_returns_private_result_fields(command):
    d = command
    expected = d.execute()
    saved = admissions.read_command_receipt('owner', d.command['command_id'])
    saved['result']['private_path'] = str(d.root)
    admissions.complete_command('owner', d.command['command_id'], saved)
    before = Path(d.tasks._DB_PATH).read_bytes()
    assert d.receipt() == expected
    assert Path(d.tasks._DB_PATH).read_bytes() == before
    returned = d.renew()
    returned['files'].clear()
    assert d.renew()['files'] == ['file.txt']


def test_completed_receipt_revocation_is_not_bypassed(command):
    d = command
    d.execute()
    d.active = False
    for operation in (d.receipt, d.renew, d.execute):
        with pytest.raises(ClientPlatformError, match='capability_revoked'):
            operation()


def test_simultaneous_duplicate_waits_and_reuses_completed_receipt(command, monkeypatch):
    d = command
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = api.client_undo.undo_workspace_change
    def blocked(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(api.client_undo, 'undo_workspace_change', blocked)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(d.execute)
        assert entered.wait(10)
        second = workers.submit(d.execute)
        release.set()
        one, two = first.result(15), second.result(15)
    assert one == two and one['status'] == 'undone' and calls == [True]
    assert (d.root / 'file.txt').read_bytes() == b'before\r\n'


@pytest.mark.parametrize('corruption', ['review', 'recovery', 'completion'])
def test_corrupt_saved_shapes_do_not_become_valid_receipts(command, monkeypatch, corruption):
    d = command
    monkeypatch.setattr(api.client_undo, 'undo_workspace_change', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupt')))
    with pytest.raises(OSError):
        d.execute()
    saved = admissions.read_command_receipt('owner', d.command['command_id'])
    if corruption == 'review':
        saved['_workspace_undo']['review']['resource_id'] = str(d.root)
    elif corruption == 'recovery':
        saved['_workspace_undo']['recovery'] = ['invalid']
    else:
        saved['result'] = {**d.receipt(), 'status': 'undone', 'ledger_saved': True, 'reverted': True}
    admissions.command_progress('owner', d.command['command_id'], saved)
    with pytest.raises(ClientPlatformError, match='workspace_undo_unavailable'):
        d.receipt()
    assert (d.root / 'file.txt').read_bytes() == b'after\r\n'
