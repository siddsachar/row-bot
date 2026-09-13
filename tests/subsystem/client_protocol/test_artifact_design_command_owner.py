"""Actual Designer effects over isolated canonical command and resource owners."""
# ruff: noqa: F811 -- reused pytest fixture.
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import importlib
import json
import sqlite3
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from row_bot.application import artifact_design_commands as commands
from row_bot.application.client_platform import ClientPlatformError
from row_bot.designer import client_design_controls as controls, storage, history, brand, fonts
from row_bot.designer.state import DesignerProject, DesignerPage, BrandConfig
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(artifact_service, monkeypatch, tmp_path):
    from row_bot import threads, conversation_resources
    monkeypatch.setattr(history, 'HISTORY_DIR', tmp_path / 'history')
    monkeypatch.setattr(brand, '_BRAND_DIR', tmp_path / 'presets')
    monkeypatch.setattr(fonts, '_CACHE_DIR', tmp_path / 'fonts')
    monkeypatch.setattr(fonts, 'ensure_font', lambda *_a, **_kw: pytest.fail('No font download'))
    project = DesignerProject(name='Synthetic design', brand=BrandConfig(), pages=[
        DesignerPage(title='First', route_id='first', html='<html><body><h1>Hello</h1></body></html>'),
        DesignerPage(title='Second', route_id='second', html='<html><body><h2>World</h2></body></html>')])
    storage.save_project(project)
    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("INSERT INTO thread_meta(thread_id,name,approval_mode) VALUES('chat','Synthetic chat','approve')")
    conversation_resources.bind('chat', 'artifact', project.id, expected_revision=0)
    instance = admissions.instance_identity()
    image = io.BytesIO()
    Image.new('RGB', (4, 4), '#123456').save(image, format='PNG')
    data = image.getvalue()
    def body(kind='artifact.design.control', **payload):
        current = storage.load_project(project.id)
        binding = conversation_resources.list_bindings('chat').bindings[0]
        defaults = {'operation': 'brand', 'parameters': {'primary_color': '#112233'}} if kind == 'artifact.design.control' else {}
        if kind == 'artifact.asset.upload':
            defaults = {'upload_id': 'authorized-upload', 'filename': 'synthetic.png', 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
        if kind == 'artifact.preset.mutate':
            defaults = {'action': 'save', 'name': 'Synthetic shared preset', 'nonce': 'private-confirmation'}
        return {'type': kind, 'command_id': str(uuid4()), 'client_session_id': 'session',
            'expected_revision': str(artifact_service._metadata('chat')['client_revision']),
            'payload': {'target': {'kind': 'artifact', 'resource_id': project.id, 'resource_revision': current.updated_at,
                'binding_id': binding.binding_id, 'binding_revision': binding.revision}, **defaults, **payload}}
    def execute(command, **options):
        return commands.execute_artifact_design_command(artifact_service, command, 'chat', owner_id=instance,
            key=command['command_id'], **({'validate': lambda: None, 'resolve_upload': lambda identity: data,
                                       'validate_confirmation': lambda command: None} | options))
    return SimpleNamespace(service=artifact_service, project=project, instance=instance, body=body, execute=execute,
                           data=data, threads=threads, bindings=conversation_resources)


@pytest.mark.parametrize('mode', ['deck', 'document', 'landing', 'app_mockup', 'storyboard'])
def test_brand_control_is_once_with_saved_history_and_exact_receipt(owner, mode):
    project = storage.load_project(owner.project.id)
    project.mode = mode
    storage.save_project(project)
    body = owner.body()
    result = owner.execute(body)
    assert result['artifact_design']['status'] == 'saved'
    assert owner.execute(body) == result
    assert storage.load_project(project.id).brand.primary_color == '#112233'
    assert len(list((history.HISTORY_DIR / project.id).glob('*.json'))) == 1
    assert '_artifact_design' not in result and 'parameters' not in json.dumps(result)


def test_style_hotspot_and_asset_insert_remove_forget_use_real_domain(owner):
    project = storage.load_project(owner.project.id)
    project.mode = 'landing'
    storage.save_project(project)
    element = controls.read_controls(project.id).items[0].id
    style = owner.body(operation='style', parameters={'color': '#334455'}, page_id='first', element_id=element)
    assert owner.execute(style)['artifact_design']['status'] == 'saved'
    element = controls.read_controls(project.id).items[0].id
    hotspot = owner.body(operation='hotspot', parameters={'action': 'navigate', 'target': 'second'}, page_id='first', element_id=element)
    assert owner.execute(hotspot)['artifact_design']['status'] == 'saved'
    uploaded = owner.execute(owner.body('artifact.asset.upload'))['artifact_design']['asset_id']
    for operation in ('asset_insert', 'asset_remove', 'asset_forget'):
        assert owner.execute(owner.body(operation=operation, parameters={'asset_id': uploaded}, page_id='first'))['artifact_design']['status'] == 'saved'
    assert storage.load_project(project.id).assets == []
    assert list((storage.ASSETS_DIR / project.id).glob('*')), 'Forget retains canonical bytes for history'


def test_enumerated_review_fix_and_preset_application_are_explicit_saved_controls(owner):
    project = storage.load_project(owner.project.id)
    project.pages[0].html = '<h2>Heading</h2><img src="row-bot-asset:missing">'
    storage.save_project(project)
    report = controls.read_review(project.id)
    finding = next(item for item in report.findings if item.source == 'brand_lint' and item.category == 'missing_alt')
    body = owner.body(operation='review_fix', parameters={'finding_id': finding.id}, page_id=report.page_id)
    assert owner.execute(body)['artifact_design']['status'] == 'saved'
    assert 'alt=""' in storage.load_project(project.id).pages[0].html
    preset = controls.read_controls(project.id, section='presets').items[0]
    result = owner.execute(owner.body(operation='preset', parameters={'preset_id': preset.id}))
    assert result['artifact_design']['status'] in {'saved', 'unchanged'}


@pytest.mark.parametrize('field', ['binding_revision', 'resource_revision'])
def test_stale_targets_fail_before_effect(owner, field):
    command = owner.body()
    command['payload']['target'][field] = 'wrong'
    before = storage.load_project(owner.project.id).to_dict()
    with pytest.raises(ClientPlatformError):
        owner.execute(command)
    assert storage.load_project(owner.project.id).to_dict() == before
    assert not history.HISTORY_DIR.exists()


def test_policy_nonce_and_auth_are_rechecked_at_effect_boundary(owner):
    command = owner.body('artifact.preset.mutate')
    def confirmation(_command):
        with sqlite3.connect(owner.threads.DB_PATH) as conn:
            conn.execute("UPDATE thread_meta SET approval_mode='block' WHERE thread_id='chat'")
    with pytest.raises(ClientPlatformError, match='edit_policy_denied'):
        owner.execute(command, validate_confirmation=confirmation)
    assert not brand._BRAND_DIR.exists()
    with pytest.raises(ClientPlatformError):
        owner.execute(owner.body(), validate=lambda: (_ for _ in ()).throw(ClientPlatformError('session_revoked')))


def test_upload_requires_exact_resolved_digest_and_never_calls_resolver_on_retry(owner):
    body = owner.body('artifact.asset.upload')
    with pytest.raises(ClientPlatformError, match='upload_identity_conflict'):
        owner.execute(body, resolve_upload=lambda _: b'foreign')
    assert storage.load_project(owner.project.id).assets == []
    body = owner.body('artifact.asset.upload')
    calls = []
    first = owner.execute(body, resolve_upload=lambda identity: (calls.append(identity), owner.data)[1])
    assert owner.execute(body, resolve_upload=lambda _: pytest.fail('Retry reloaded staged bytes')) == first
    assert calls == ['authorized-upload']


def test_hmac_changed_body_never_reuses_existing_effect(owner):
    body = owner.body()
    owner.execute(body)
    body['payload']['parameters']['primary_color'] = '#445566'
    with pytest.raises(ClientPlatformError, match='idempotency_mismatch'):
        owner.execute(body)
    assert storage.load_project(owner.project.id).brand.primary_color == '#112233'


def test_commit_then_lost_all_outcome_checkpoints_remains_partial_without_resave(owner, monkeypatch):
    body, original = owner.body(), storage.save_project
    def lost(project):
        original(project)
        raise OSError('synthetic response lost after actual project commit')
    monkeypatch.setattr(storage, 'save_project', lost)
    result = owner.execute(body)
    assert result['artifact_design']['status'] == 'partial'
    monkeypatch.setattr(storage, 'save_project', lambda *_: pytest.fail('Uncertain effect was resaved'))
    assert owner.execute(body)['artifact_design']['status'] == 'partial'
    assert storage.load_project(owner.project.id).brand.primary_color == '#112233'
    assert len(list((history.HISTORY_DIR / owner.project.id).glob('*.json'))) == 1


def test_confirmed_outcome_recovers_lost_command_completion_without_domain_call(owner, monkeypatch):
    body, complete = owner.body(), admissions.complete_command
    monkeypatch.setattr(admissions, 'complete_command', lambda *_: (_ for _ in ()).throw(OSError('synthetic command receipt unavailable')))
    assert owner.execute(body)['status'] == 'partial'
    monkeypatch.setattr(admissions, 'complete_command', complete)
    monkeypatch.setattr(controls, 'apply_control', lambda *_a, **_kw: pytest.fail('Completed project effect repeated'))
    assert owner.execute(body)['artifact_design']['status'] == 'saved'


@pytest.mark.parametrize('stage', ['asset_prepared', 'asset_written', 'asset_attached'])
def test_asset_checkpoint_fault_keeps_original_stage_and_never_reattaches(owner, monkeypatch, stage):
    body, persist = owner.body('artifact.asset.upload'), admissions.command_progress
    failed = []
    def fault(*args):
        if args[2].get('_artifact_design', {}).get('asset', {}).get('stage') == stage and not failed:
            failed.append(1)
            raise OSError('synthetic checkpoint failure')
        return persist(*args)
    monkeypatch.setattr(admissions, 'command_progress', fault)
    result = owner.execute(body)
    assert result['status'] == 'partial'
    monkeypatch.setattr(controls, 'upload_asset', lambda *_a, **_kw: pytest.fail('Uncertain upload repeated'))
    replay = owner.execute(body)
    assert replay['artifact_design']['status'] == ('saved' if stage == 'asset_attached' else 'partial')
    assert len(storage.load_project(owner.project.id).assets) == (1 if stage == 'asset_attached' else 0)


@pytest.mark.parametrize('action', ['save', 'replace', 'delete'])
def test_preset_exact_private_file_proof_recovers_post_effect_exception(owner, monkeypatch, action):
    preset_id = None
    if action != 'save':
        preset_id = owner.execute(owner.body('artifact.preset.mutate'))['artifact_design']['preset_id']
        if action == 'replace':
            owner.execute(owner.body())
    body = owner.body('artifact.preset.mutate', **({'preset_id': preset_id} if preset_id else {}), action='delete' if action == 'delete' else 'save')
    mutate = controls.mutate_preset
    def lost(*args, **kwargs):
        mutate(*args, **kwargs)
        raise OSError('synthetic preset return lost')
    monkeypatch.setattr(controls, 'mutate_preset', lost)
    result = owner.execute(body)
    assert result['status'] == 'partial'
    stored = admissions.receipt(owner.instance, body['command_id'])
    assert stored['_artifact_design']['preset']['command_id'] == body['command_id']
    assert '_artifact_design' not in result and 'candidate_identity' not in json.dumps(result)
    monkeypatch.setattr(controls, 'mutate_preset', lambda *_a, **_kw: pytest.fail('Uncertain global effect repeated'))
    replay = owner.execute(body, validate_confirmation=lambda _: pytest.fail('Read-only receipt consumed an approval'))
    assert replay['artifact_design']['status'] == 'saved'
    assert 'private-confirmation' not in json.dumps(stored)


def test_foreign_identical_preset_bytes_do_not_prove_publication(owner, monkeypatch):
    body, mutate = owner.body('artifact.preset.mutate'), controls.mutate_preset
    def lost(*args, **kwargs):
        mutate(*args, **kwargs)
        raise OSError('synthetic lost result')
    monkeypatch.setattr(controls, 'mutate_preset', lost)
    owner.execute(body)
    path = brand._BRAND_DIR / brand._preset_filename(body['payload']['name'])
    retained = path.with_suffix('.retained')
    path.rename(retained)
    path.write_bytes(retained.read_bytes())
    monkeypatch.setattr(controls, 'mutate_preset', lambda *_a, **_kw: pytest.fail('Foreign target overwritten'))
    assert owner.execute(body)['artifact_design']['status'] == 'partial'
    assert retained.read_bytes() == path.read_bytes()


def test_concurrent_duplicate_never_enters_domain_twice(owner, monkeypatch):
    body, original = owner.body(), controls.apply_control
    entered, release = threading.Event(), threading.Event()
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(controls, 'apply_control', paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(owner.execute, body)
        assert entered.wait(5)
        try:
            with pytest.raises(ClientPlatformError, match='artifact_busy'):
                owner.execute(body)
        finally:
            release.set()
        result = first.result(timeout=5)
    assert owner.execute(body) == result


def test_invalid_control_and_missing_global_confirmation_are_effect_free(owner):
    with pytest.raises(ClientPlatformError, match='invalid_design_control'):
        owner.execute(owner.body(operation='unsafe_unknown'))
    with pytest.raises(ClientPlatformError, match='action_denied'):
        owner.execute(owner.body('artifact.preset.mutate'), validate_confirmation=None)
    assert not brand._BRAND_DIR.exists() and not history.HISTORY_DIR.exists()


def test_failed_initial_checkpoint_never_enters_domain(owner, monkeypatch):
    body = owner.body()
    monkeypatch.setattr(admissions, 'command_progress', lambda *_: (_ for _ in ()).throw(OSError('synthetic receipt fault')))
    monkeypatch.setattr(controls, 'apply_control', lambda *_a, **_kw: pytest.fail('Effect without durable admission'))
    with pytest.raises(ClientPlatformError, match='artifact_design_unconfirmed'):
        owner.execute(body)
    assert not history.HISTORY_DIR.exists()


def test_event_failure_does_not_undo_durable_success_or_emit_on_replay(owner, monkeypatch):
    body, events = owner.body(), []
    def publish(*args):
        events.append(args)
        raise OSError('synthetic event stream fault')
    monkeypatch.setattr(owner.service.projection, 'publish', publish)
    result = owner.execute(body)
    assert result['status'] == 'completed'
    assert owner.execute(body) == result
    assert len(events) == 1 and events[0][1] == 'resource.changed'


def test_auth_revocation_at_last_project_validation_retains_history_without_publication(owner):
    body = owner.body()
    before = storage.load_project(owner.project.id).to_dict()
    def validate():
        if history.HISTORY_DIR.exists():
            raise ClientPlatformError('session_revoked')
    assert owner.execute(body, validate=validate)['status'] == 'partial'
    assert storage.load_project(owner.project.id).to_dict() == before
    assert len(list((history.HISTORY_DIR / owner.project.id).glob('*.json'))) == 1
    assert owner.execute(body)['status'] == 'partial', 'Fresh authorization does not replay uncertain work'


def test_binding_detach_readd_cannot_recover_original_effect(owner, monkeypatch):
    body, original = owner.body(), storage.save_project
    def lost(project):
        original(project)
        raise OSError('synthetic outcome loss')
    monkeypatch.setattr(storage, 'save_project', lost)
    assert owner.execute(body)['status'] == 'partial'
    snapshot = owner.bindings.list_bindings('chat')
    owner.bindings.unbind('chat', snapshot.bindings[0].binding_id, expected_revision=snapshot.bindings_revision)
    snapshot = owner.bindings.list_bindings('chat')
    owner.bindings.bind('chat', 'artifact', owner.project.id, expected_revision=snapshot.bindings_revision)
    with pytest.raises(ClientPlatformError, match='resource_binding_revoked'):
        owner.execute(body)


def test_preset_confirmation_expiry_at_publication_keeps_candidate_and_original(owner):
    body = owner.body('artifact.preset.mutate')
    def confirm(_body):
        if list(brand._BRAND_DIR.glob('.row-bot-edit-recovery/*/candidate')):
            raise ClientPlatformError('approval_expired')
    result = owner.execute(body, validate_confirmation=confirm)
    assert result['status'] == 'partial'
    assert not (brand._BRAND_DIR / brand._preset_filename(body['payload']['name'])).exists()
    assert list(brand._BRAND_DIR.glob('.row-bot-edit-recovery/*/candidate'))
    assert owner.execute(body)['status'] == 'partial'


@pytest.mark.parametrize('corrupt', [None, [], {'asset': 'foreign'}, {'preset': {'relative_path': '../outside.json'}}])
def test_corrupt_private_checkpoint_never_exposes_or_executes_paths(owner, monkeypatch, corrupt):
    body, original = owner.body(), storage.save_project
    def lost(project):
        original(project)
        raise OSError('synthetic outcome loss')
    monkeypatch.setattr(storage, 'save_project', lost)
    owner.execute(body)
    receipt = admissions.receipt(owner.instance, body['command_id'])
    receipt['_artifact_design'] = corrupt
    admissions.command_progress(owner.instance, body['command_id'], receipt)
    monkeypatch.setattr(controls, 'apply_control', lambda *_a, **_kw: pytest.fail('Corrupt proof triggered effect'))
    result = owner.execute(body)
    assert result['status'] == 'partial'
    assert '_artifact_design' not in result and 'outside' not in json.dumps(result)


def test_confirmed_checkpoint_survives_module_reload_and_policy_withdrawal(owner, monkeypatch):
    body, complete = owner.body(), admissions.complete_command
    monkeypatch.setattr(admissions, 'complete_command', lambda *_: (_ for _ in ()).throw(OSError('synthetic completion fault')))
    owner.execute(body)
    monkeypatch.setattr(admissions, 'complete_command', complete)
    with sqlite3.connect(owner.threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET approval_mode='block' WHERE thread_id='chat'")
    importlib.reload(commands)
    monkeypatch.setattr(controls, 'apply_control', lambda *_a, **_kw: pytest.fail('Recovery repeated blocked effect'))
    assert owner.execute(body)['artifact_design']['status'] == 'saved'
