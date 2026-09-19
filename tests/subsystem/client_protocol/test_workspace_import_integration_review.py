"""Independent API/admission review using canonical isolated workspace fixtures."""
# ruff: noqa: F811 -- reuse isolated canonical owner fixtures
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _isolated_service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.developer.test_client_workspace_imports import domain, imports  # noqa: F401

pytestmark = pytest.mark.subsystem


def test_expiry_after_admission_preserves_original_review_for_explicit_nonce_renewal(imports, monkeypatch):
    d = imports
    pending = d.pending({'file.txt': 'before\n'}, {'file.txt': 'after\n'})
    binding = d.resources.list_bindings('chat').bindings[0].binding_id
    base = f'/api/v1/conversations/chat/workspaces/{binding}/imports'
    service = _isolated_service()
    now = [100.0]
    security = ClientSecurity(instance_id=service.instance_id, clock=lambda: now[0])
    app = create_client_platform_app(service, security=security, choices=lambda: {'models': [], 'capabilities': []})
    original_claim = admissions.claim_command
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12345)) as client:
        _, headers = bootstrap(client)
        reviewed = client.post(base + '/review', headers=headers, json={'pending_change_id': pending.id})
        assert reviewed.status_code == 200, reviewed.text
        review = reviewed.json()
        nonce = review.pop('nonce')
        command = {'command_id': str(uuid4()), 'client_session_id': headers['X-Client-Session'],
            'expected_revision': '0', 'type': 'workspace.import', 'payload': {'review': review, 'nonce': nonce}}
        proof = headers | {'Idempotency-Key': command['command_id']}
        def expires_after_claim(*args, **kwargs):
            result = original_claim(*args, **kwargs)
            now[0] += 301
            return result
        monkeypatch.setattr(admissions, 'claim_command', expires_after_claim)
        refused = client.post(base + '/commands', headers=proof, json=command)
        assert refused.status_code == 409, refused.text
        assert (d.root / 'file.txt').read_bytes() == b'before\n'
        monkeypatch.setattr(admissions, 'claim_command', original_claim)
        renewed = client.post(base + '/commands/' + command['command_id'] + '/review', headers=headers, json={})
        assert renewed.status_code == 200, renewed.text
        fresh = renewed.json()
        command['payload']['nonce'] = fresh.pop('nonce')
        assert fresh == review
        result = client.post(base + '/commands', headers=proof, json=command)
        assert result.status_code == 200 and result.json()['status'] == 'imported', result.text
        assert (d.root / 'file.txt').read_bytes() == b'after\n'


def test_absent_receipt_is_distinct_from_an_existing_incomplete_command(imports):
    from row_bot.application.workspace_import_commands import read_workspace_import_command
    from row_bot.application.client_platform import ClientPlatformError
    identity = str(uuid4())
    with pytest.raises(ClientPlatformError, match='workspace_import_not_admitted'):
        read_workspace_import_command('owner', identity, 'chat', 'binding', validate=lambda: None)
    admissions.claim_command('owner', identity, {'command_id': identity, 'type': 'workspace.import'}, 'chat')
    with pytest.raises(ClientPlatformError, match='workspace_import_unavailable'):
        read_workspace_import_command('owner', identity, 'chat', 'binding', validate=lambda: None)
    assert admissions.read_command_metadata('owner', identity)['status'] == 'admitting'


def test_atomic_initial_result_is_bounded_and_original_replay_does_not_replace_it(imports):
    identity = str(uuid4())
    command = {'command_id': identity, 'type': 'workspace.import'}
    with pytest.raises(admissions.AdmissionError, match='invalid_command'):
        admissions.claim_command('owner', identity, command, 'chat', initial_result={'large': 'x' * (256 * 1024)})
    assert admissions.read_command_metadata('owner', identity) is None
    admissions.claim_command('owner', identity, command, 'chat', initial_result={'proof': 'original'})
    with pytest.raises(admissions.AdmissionError, match='operation_uncertain'):
        admissions.claim_command('owner', identity, command, 'chat', initial_result={'proof': 'replacement'})
    assert admissions.read_command_receipt('owner', identity)['proof'] == 'original'


def test_passive_native_policy_snapshot_never_invokes_readiness_or_dynamic_effect_getters(tmp_path, monkeypatch):
    from row_bot.tools import registry
    from row_bot.api.v1.security import current_policy_snapshot
    from row_bot.mcp_client import runtime
    from row_bot.plugins import registry as plugins
    calls = []
    class Tool:
        @property
        def destructive_tool_names(self):
            calls.append('dynamic effects')
            raise AssertionError('readiness must remain passive')
    monkeypatch.setattr(registry, '_tools', {'synthetic': Tool()})
    monkeypatch.setattr(registry, '_enabled', {'synthetic': True})
    monkeypatch.setattr(registry, '_tool_configs', {})
    monkeypatch.setattr(registry, '_global_config', {})
    monkeypatch.setattr(registry, 'get_row_bot_data_dir', lambda create=False: tmp_path)
    monkeypatch.setattr(registry, '_active_config_path', tmp_path / 'tools_config.json')
    revision = ['one']
    monkeypatch.setattr(registry.tool_configuration, 'read_saved', lambda _: SimpleNamespace(digest=revision[0]))
    monkeypatch.setattr(registry, 'get_all_tools', lambda: pytest.fail('Constructed/readied native tools'))
    monkeypatch.setattr(registry, '_ensure_config_scope', lambda: pytest.fail('Implicit configuration scope mutation'))
    before = registry.read_policy_snapshot()
    revision[0] = 'two'
    after = registry.read_policy_snapshot()
    assert not calls and before['registrations'] == after['registrations']
    assert before['saved_revision'] != after['saved_revision']
    monkeypatch.setattr(runtime, '_get_effective_config', lambda: {'enabled': False, 'servers': {}})
    monkeypatch.setattr(runtime, '_servers', {})
    monkeypatch.setattr(runtime, '_catalog', {})
    monkeypatch.setattr(plugins, 'get_loaded_manifests', lambda: [])
    snapshot = current_policy_snapshot()
    assert snapshot['native'] == after and not calls
