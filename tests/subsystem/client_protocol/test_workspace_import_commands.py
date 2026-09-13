"""Sandbox imports reach actual isolated files through authenticated commands."""
# ruff: noqa: F811 -- canonical disposable fixtures.
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _isolated_service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.developer.test_client_workspace_imports import domain, imports  # noqa: F401

pytestmark = pytest.mark.subsystem


def test_import_review_nonce_new_folders_and_passive_exact_original_receipt(imports, monkeypatch):
    d = imports
    violations = []
    # Preserve the fixture's no-runtime assertions while making violations an
    # ordinary worker exception that TestClient cannot mask at portal shutdown.
    for module, name in ((d.sandbox, 'detect_container_runtime'), (d.sandbox, 'ensure_docker_sandbox'), (d.edits.subprocess, 'run')):
        original = getattr(module, name)
        def guarded(*args, _original=original, _name=name, **kwargs):
            try:
                return _original(*args, **kwargs)
            except BaseException as exc:
                import traceback
                violations.append((_name, traceback.format_stack()))
                raise RuntimeError('Fixture prohibited runtime call: ' + _name) from exc
        monkeypatch.setattr(module, name, guarded)
    pending = d.pending({'old.txt': 'before\n'}, {'old.txt': 'after\n', 'new/nested/empty.txt': ''})
    binding = d.resources.list_bindings('chat').bindings[0].binding_id
    base = f'/api/v1/conversations/chat/workspaces/{binding}/imports'
    service = _isolated_service()
    app = create_client_platform_app(service, choices=lambda: {'models': [], 'capabilities': []})
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12345)) as client:
        _, headers = bootstrap(client)
        page = client.get(base, headers=headers)
        assert page.status_code == 200 and page.json()['total'] == 1, page.text
        review = client.post(base + '/review', headers=headers, json={'pending_change_id': pending.id})
        assert review.status_code == 200, review.text
        value = review.json()
        assert value['directories'] == ['new', 'new/nested']
        nonce = value.pop('nonce')
        command = {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
            'expected_revision': '0', 'type': 'workspace.import', 'payload': {'review': value, 'nonce': 'forged'}}
        proof = headers | {'Idempotency-Key': command['command_id']}
        assert client.post(base + '/commands', headers=proof, json=command).status_code == 409
        assert admissions.read_command_metadata(headers['X-Client-Session'], command['command_id']) is None
        assert (d.root / 'old.txt').read_bytes() == b'before\n' and not (d.root / 'new').exists()
        command['payload']['nonce'] = nonce
        saved = client.post(base + '/commands', headers=proof, json=command)
        assert not violations, ''.join(violations[0][1][-14:]) if violations else ''
        assert saved.status_code == 200 and saved.json()['status'] == 'imported', saved.text
        assert (d.root / 'old.txt').read_bytes() == b'after\n'
        assert (d.root / 'new/nested/empty.txt').read_bytes() == b''
        repeated = []
        def repeated_import(*args, **kwargs):
            repeated.append(True)
            raise RuntimeError('Original read/replay executed host effects')
        monkeypatch.setattr(d.imports, 'import_workspace_change', repeated_import)
        original = client.get(base + '/commands/' + command['command_id'], headers=headers)
        assert original.status_code == 200 and original.json() == saved.json(), original.text
        assert client.post(base + '/commands', headers=proof, json=command).json() == saved.json()
        assert not repeated
        assert '_workspace_import' not in original.text and str(d.root) not in original.text
        _, foreign = bootstrap(client)
        assert client.get(base + '/commands/' + command['command_id'], headers=foreign).status_code == 404


def test_partial_import_recovers_original_after_expired_nonce_without_republishing_files(imports, monkeypatch):
    d = imports
    pending = d.pending({'file.txt': 'before\n'}, {'file.txt': 'after\n'})
    binding = d.resources.list_bindings('chat').bindings[0].binding_id
    base = f'/api/v1/conversations/chat/workspaces/{binding}/imports'
    service = _isolated_service()
    now = [100.0]
    security = ClientSecurity(instance_id=service.instance_id, clock=lambda: now[0])
    app = create_client_platform_app(service, security=security, choices=lambda: {'models': [], 'capabilities': []})
    marker = d.sandbox.mark_pending_change_imported
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12345)) as client:
        _, headers = bootstrap(client)
        response = client.post(base + '/review', headers=headers, json={'pending_change_id': pending.id})
        assert response.status_code == 200, response.text
        review = response.json()
        nonce = review.pop('nonce')
        command = {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
            'expected_revision': '0', 'type': 'workspace.import', 'payload': {'review': review, 'nonce': nonce}}
        proof = headers | {'Idempotency-Key': command['command_id']}
        monkeypatch.setattr(d.sandbox, 'mark_pending_change_imported', lambda *_a, **_k: (_ for _ in ()).throw(OSError('Synthetic lost marker')))
        partial = client.post(base + '/commands', headers=proof, json=command)
        assert partial.status_code == 200 and partial.json()['status'] == 'partial', partial.text
        identity = (d.root / 'file.txt').stat().st_ino
        monkeypatch.setattr(d.sandbox, 'mark_pending_change_imported', marker)
        now[0] += 301
        assert client.post(base + '/commands', headers=proof, json=command).status_code == 409
        renewed = client.post(base + '/commands/' + command['command_id'] + '/review', headers=headers, json={})
        assert renewed.status_code == 200, renewed.text
        fresh = renewed.json()
        command['payload']['nonce'] = fresh.pop('nonce')
        assert fresh == review
        final = client.post(base + '/commands', headers=proof, json=command)
        assert final.status_code == 200 and final.json()['status'] == 'imported', final.text
        assert (d.root / 'file.txt').stat().st_ino == identity
        assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1
