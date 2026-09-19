"""Voice callbacks enter the actual normal-chat owner with disposable checkpoints."""
# ruff: noqa: F811 -- shared isolated fixture.
from dataclasses import replace
from types import SimpleNamespace
import threading
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from row_bot.application.client_voice import ClientVoiceAdmission
from row_bot.runtime import admissions
from row_bot.voice.client_transport import DictationOwner, DictationError
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


def setup(service):
    identity = str(uuid4())
    result = service.execute(owner_id=service.instance_id, idempotency_key=identity,
        command={'command_id': identity, 'type': 'conversation.create', 'expected_revision': '0',
                 'payload': {'title': 'Synthetic voice conversation'}}, target='')
    conversation = result['conversation_id']
    owner = DictationOwner(str(uuid4()), 'synthetic-access', conversation, service.server_epoch)
    context = {'conversation_revision': str(service._metadata(conversation)['client_revision']),
               'model_selection': {'provider_id': 'fixture', 'model_ref': 'model:fixture:model'}, 'write_targets': []}
    adapter = ClientVoiceAdmission(service)
    lease = SimpleNamespace(owner=owner, chat_context=adapter.prepare_context(owner, context, lambda: None), revoked=False)
    service.dictation = SimpleNamespace(coordinator=SimpleNamespace(_dictation_lock=threading.RLock(), _dictation_lease=lease))
    service.readiness_factory = lambda controls: True
    return adapter, owner, lease


def test_voice_submission_uses_normal_runtime_and_exact_saved_final_output(service):
    adapter, owner, lease = setup(service)
    fake = ScriptedAgentStream((('token', 'Synthetic spoken answer'),
        CheckpointCommit((AIMessage(id='voice-final', content='Synthetic spoken answer'),), 'voice-final'), ('done', None)))
    service.stream_factory = fake.stream
    generation = adapter.submit(owner, 'Synthetic voice request', str(uuid4()), lambda: None)
    handle = service.registry.conversation_generation(owner.conversation_id, generation)
    assert handle and handle.producer_done.wait(5)
    assert handle.runtime_surface == 'normal_chat' and handle.model_ref == 'model:fixture:model'
    assert 'Synthetic' not in str(handle.view())
    assert adapter.output(owner, generation, 'voice-final', lambda: None).text == 'Synthetic spoken answer'
    for run, output in [(str(uuid4()), 'voice-final'), (generation, 'other-message')]:
        with pytest.raises(DictationError, match='voice_output_unavailable'):
            adapter.output(owner, run, output, lambda: None)
    with pytest.raises(DictationError, match='voice_session_expired'):
        adapter.output(replace(owner, conversation_id='other-conversation'), generation, 'voice-final', lambda: None)


def test_voice_control_exact_run_dedup_queue_and_stop(service):
    adapter, owner, lease = setup(service)
    barrier = StreamBarrier()
    service.stream_factory = ScriptedAgentStream((barrier, ('done', None))).stream
    text = 'Synthetic original request'
    run = adapter.submit(owner, text, str(uuid4()), lambda: None)
    assert barrier.entered.wait(5)
    handle = service.registry.conversation_generation(owner.conversation_id, run)
    try:
        # Transcript-item dedup belongs to the realtime lease; a canonical
        # explicit control input is a real follow-up (covered at HTTP below).
        assert adapter.control(owner, handle, 'status', '', str(uuid4()), lambda: None)['status'] == 'running'
        result = adapter.control(owner, handle, 'input', 'Also preserve the previous file.', str(uuid4()), lambda: None)
        assert result['status'] == 'queued'
        with admissions.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM generation_passes WHERE conversation_id=? AND queue_state='queued'", (owner.conversation_id,)).fetchone()[0] == 1
        with pytest.raises(DictationError, match='voice_run_changed'):
            adapter.control(owner, None, 'cancel', '', str(uuid4()), lambda: None)
        assert not handle.cancel_scope.is_cancelled()
        assert adapter.control(owner, handle, 'cancel', '', str(uuid4()), lambda: None)['status'] == 'cancel_requested'
        assert handle.cancel_scope.is_cancelled()
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(5)


@pytest.mark.parametrize('change', ['revision', 'epoch', 'revoked', 'authority'])
def test_changed_voice_authority_never_admits_a_generation(service, change):
    adapter, owner, lease = setup(service)
    if change == 'revision':
        lease.chat_context['conversation_revision'] = '999'
    if change == 'epoch':
        owner = replace(owner, server_epoch='different')
        lease.owner = owner
    if change == 'revoked':
        lease.revoked = True
    def validate():
        if change == 'authority':
            raise DictationError('action_denied')
    with pytest.raises(DictationError):
        adapter.submit(owner, 'Never execute', str(uuid4()), validate)
    assert service.registry.active(owner.conversation_id) == ()


def test_repeated_turn_refreshes_same_resource_only_and_keeps_start_context(service, monkeypatch):
    from copy import deepcopy
    from dataclasses import asdict
    from row_bot import conversation_resources as resources
    adapter, owner, lease = setup(service)
    binding = resources.ResourceBinding('binding-one', 'artifact', 'artifact-one', 'primary', '1')
    current = [binding]
    revision = ['resource-one']
    monkeypatch.setattr(resources, 'list_bindings', lambda conversation: resources.ResourceSnapshot(conversation, '0', '1', tuple(current)))
    monkeypatch.setattr(resources, 'describe', lambda value: resources.ResourceDescriptor(value, 'Synthetic', revision[0], True))
    lease.chat_context['write_targets'] = [{**{key: value for key, value in asdict(binding).items() if key != 'role'},
        'binding_revision': binding.revision, 'resource_revision': revision[0]}]
    lease.chat_context['write_targets'][0].pop('revision')
    original = deepcopy(lease.chat_context)
    seen = []
    def stream(*args, **kwargs):
        seen.append(resources.current_execution_context().bindings)
        yield ('done', None)
    # The actual normal runtime captures only the original resource identity.
    monkeypatch.setattr(resources, 'current_execution_context', lambda: resources._execution_context.get(), raising=False)
    service.stream_factory = stream
    for index in range(2):
        revision[0] = f'resource-{index + 2}'
        run = adapter.submit(owner, f'Next synthetic turn {index}', str(uuid4()), lambda: None)
        assert service.registry.conversation_generation(owner.conversation_id, run).producer_done.wait(5)
    assert seen == [(binding,), (binding,)]
    assert lease.chat_context == original
    current[0] = replace(binding, resource_id='replacement')
    with pytest.raises(DictationError, match='resource_binding_revoked'):
        adapter.submit(owner, 'Never target replacement', str(uuid4()), lambda: None)
    assert len(seen) == 2


def test_private_policy_digest_rejects_global_profile_edit_without_metadata_revision(service, monkeypatch):
    from row_bot.application import profile_controls
    profile = ['original']
    original = profile_controls.freeze_profile
    def freeze(config, **kwargs):
        original(config, **kwargs)
        config['agent_profile_snapshot'] = {'id': 'synthetic', 'instructions': profile[0]}
    monkeypatch.setattr(profile_controls, 'freeze_profile', freeze)
    adapter, owner, lease = setup(service)
    before = service._metadata(owner.conversation_id)['client_revision']
    profile[0] = 'changed'
    with pytest.raises(DictationError, match='voice_policy_changed'):
        adapter.submit(owner, 'Must not use new policy', str(uuid4()), lambda: None)
    assert service._metadata(owner.conversation_id)['client_revision'] == before
    assert service.registry.active(owner.conversation_id) == ()


def test_submission_passes_frozen_server_policy_without_wire_or_handle_content(service, monkeypatch):
    adapter, owner, lease = setup(service)
    accepted = []
    original_start = service._start
    def start(*args, **kwargs):
        assert kwargs['frozen_context']['configurable'] == lease.chat_context['_policy']
        accepted.append(kwargs['frozen_context'])
        return original_start(*args, **kwargs)
    monkeypatch.setattr(service, '_start', start)
    fake = ScriptedAgentStream((('done', None),))
    service.stream_factory = fake.stream
    run = adapter.submit(owner, 'Synthetic content', str(uuid4()), lambda: None)
    handle = service.registry.conversation_generation(owner.conversation_id, run)
    assert handle.producer_done.wait(5) and len(accepted) == 1
    assert '_policy' not in handle.view() and 'Synthetic content' not in str(handle.view())


def test_revoked_lease_cannot_control_or_read_completed_output(service):
    adapter, owner, lease = setup(service)
    service.stream_factory = ScriptedAgentStream((('token', 'Saved answer'),
        CheckpointCommit((AIMessage(id='final', content='Saved answer'),), 'final'), ('done', None))).stream
    run = adapter.submit(owner, 'Request', str(uuid4()), lambda: None)
    assert service.registry.conversation_generation(owner.conversation_id, run).producer_done.wait(5)
    lease.revoked = True
    with pytest.raises(DictationError, match='voice_session_expired'):
        adapter.output(owner, run, 'final', lambda: None)
    with pytest.raises(DictationError, match='voice_session_expired'):
        adapter.control(owner, None, 'status', '', str(uuid4()), lambda: None)


@pytest.mark.parametrize('state', ['running', 'waiting_approval', 'stopped', 'uncommitted'])
def test_only_completed_committed_assistant_output_can_be_spoken(service, state):
    adapter, owner, lease = setup(service)
    service.stream_factory = ScriptedAgentStream((('token', 'Saved answer'),
        CheckpointCommit((AIMessage(id='final', content='Saved answer'),), 'final'), ('done', None))).stream
    run = adapter.submit(owner, 'Request', str(uuid4()), lambda: None)
    handle = service.registry.conversation_generation(owner.conversation_id, run)
    assert handle.producer_done.wait(5)
    if state == 'uncommitted':
        handle.segment_committed = False
    else:
        handle.status = state
    with pytest.raises(DictationError, match='voice_output_unavailable'):
        adapter.output(owner, run, 'final', lambda: None)
