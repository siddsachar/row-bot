"""Talk/realtime HTTP through canonical normal chat, with fake speech/providers."""
# ruff: noqa: F811 -- shared isolated fixture.
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from row_bot.voice.coordinator import VoiceSessionCoordinator
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.voice.test_dictation_lifecycle import Voice

pytestmark = pytest.mark.subsystem


@pytest.fixture
def api(service):
    voice = Voice()
    coordinator = VoiceSessionCoordinator(voice)
    effects = []
    def transcribe(key, audio, mime, *, validate):
        validate()
        effects.append('transcribe')
        return 'Synthetic request from microphone'
    def synthesize(key, text, *, validate):
        validate()
        effects.append(('synthesize', text))
        return b'RIFF-synthetic-WAVE'
    browser = SimpleNamespace(voice_service=voice, transcribe=transcribe, synthesize=synthesize)
    def credentials(**kwargs):
        effects.append('ephemeral-credential')
        return {'value': 'synthetic-ephemeral-only', 'expires_at': 60.0}
    def exchange(offer, *, client_secret, validate):
        validate()
        assert client_secret == 'synthetic-ephemeral-only'
        effects.append('exchange')
        return b'v=0\r\nsynthetic-answer'
    service.bind_voice(coordinator, browser_service=lambda: browser, clock=lambda: 0.0,
        realtime_provider_factory=lambda: SimpleNamespace(create_client_secret=credentials, exchange_sdp=exchange))
    service.realtime._wall_clock = lambda: 0.0
    service.readiness_factory = lambda controls: True
    with _client(service) as client:
        _, headers = bootstrap(client)
        result = _command(client, headers, 'conversation.create', {'title': 'Synthetic voice'})
        assert result.status_code == 200
        conversation = result.json()['conversation_id']
        body = {'request_id': str(uuid4()), 'conversation_revision': result.json()['revision'],
                'model_selection': {'provider_id': 'fixture', 'model_ref': 'model:fixture:model'}, 'write_targets': []}
        yield SimpleNamespace(client=client, headers=headers, conversation=conversation, body=body,
            base=f'/api/v1/conversations/{conversation}/voice/', service=service, effects=effects, voice=voice)
    service.close_voice()


def start(api, mode='talk'):
    response = api.client.post(api.base + mode, headers=api.headers, json=api.body)
    assert response.status_code == 200, response.text
    return response.json()


def identity(snapshot):
    return {key: snapshot['handle'][key] for key in ('voice_session_id', 'server_epoch')}


def url(api, snapshot, mode='talk', action=''):
    return api.base + mode + '/' + snapshot['handle']['lease_id'] + ('/' + action if action else '')


def test_actual_talk_transcription_normal_chat_final_speech_and_exclusive_stop(api):
    barrier = StreamBarrier()
    api.service.stream_factory = ScriptedAgentStream((barrier, ('token', 'Saved voice answer'),
        CheckpointCommit((AIMessage(id='spoken-output', content='Saved voice answer'),), 'spoken-output'), ('done', None))).stream
    capture = start(api)
    assert not api.effects and not api.voice.effects
    assert start(api) == capture
    headers = {**api.headers, 'Content-Type': 'audio/webm', 'X-Dictation-Utterance': str(uuid4()),
               'X-Voice-Session-Id': str(capture['handle']['voice_session_id']), 'X-Server-Epoch': capture['handle']['server_epoch']}
    try:
        response = api.client.post(url(api, capture, action='transcribe'), headers=headers, content=b'fake audio')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['outcome'] == 'submitted' and result['run_id']
        assert barrier.entered.wait(5)
        handle = api.service.registry.conversation_generation(api.conversation, result['run_id'])
        assert handle.runtime_surface == 'normal_chat'
    finally:
        barrier.release.set()
    assert handle.producer_done.wait(5)
    lease = api.service.dictation.coordinator._dictation_lease
    for _ in range(2):
        view = api.service.voice_admission.run_view(lease.owner, lease.handle, lambda: None)
        assert view == {'run_id': result['run_id'], 'state': 'completed', 'output_id': 'spoken-output', 'text': 'Saved voice answer.'}
    assert api.effects == ['transcribe']
    speech = {**identity(capture), 'run_id': result['run_id'], 'output_id': 'spoken-output'}
    response = api.client.post(url(api, capture, action='output'), headers=api.headers, json=speech)
    assert response.status_code == 200, response.text
    assert response.content == b'RIFF-synthetic-WAVE' and response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Voice-Output'] == 'spoken-output'
    assert api.effects == ['transcribe', ('synthesize', 'Saved voice answer.')]
    # A repeated synthesis cannot duplicate audio/provider effects.
    assert api.client.post(url(api, capture, action='output'), headers=api.headers, json=speech).status_code == 409
    assert api.effects == ['transcribe', ('synthesize', 'Saved voice answer.')]


def test_talk_context_is_exact_and_another_client_cannot_keep_or_stop_lease(api):
    capture = start(api)
    modified = {**api.body, 'write_targets': [], 'model_selection': {'provider_id': 'fixture', 'model_ref': 'model:fixture:other'}}
    response = api.client.post(api.base + 'talk', headers=api.headers, json=modified)
    assert response.status_code == 409 and response.json()['code'] == 'idempotency_mismatch'
    _, other = bootstrap(api.client)
    for action in ('heartbeat', 'stop'):
        assert api.client.post(url(api, capture, action=action), headers=other, json=identity(capture)).status_code == 403
    response = api.client.post(url(api, capture, action='heartbeat'), headers=api.headers, json=identity(capture))
    assert response.status_code == 200 and response.json()['state'] == 'listening'
    response = api.client.post(url(api, capture, action='stop'), headers=api.headers, json=identity(capture))
    assert response.status_code == 200 and response.json()['quiesced']
    assert api.service.dictation.coordinator._dictation_lease.chat_context is None
    assert not api.effects and not api.voice.effects


def test_realtime_explicit_start_redacted_status_restricted_bridge_and_stop(api):
    started = start(api, 'realtime')
    capture = started['snapshot']
    assert started['client_secret'] is None and started['exchange_available'] is True
    assert 'synthetic-ephemeral-only' not in str(started)
    assert api.effects == ['ephemeral-credential'] and not api.voice.effects
    response = api.client.post(url(api, capture, 'realtime', 'heartbeat'), headers=api.headers, json=identity(capture))
    assert response.status_code == 200 and 'synthetic-ephemeral-only' not in response.text
    event = {'event_id': str(uuid4()), 'type': 'function_call_ready', 'call_id': 'synthetic-call',
             'name': 'shell', 'arguments': '{"command":"forbidden"}'}
    response = api.client.post(url(api, capture, 'realtime', 'event'), headers=api.headers,
        json={**identity(capture), 'event': event})
    assert response.status_code == 200, response.text
    assert 'blocked' in response.json()['function_output'] and not api.service.registry.active(api.conversation)
    assert api.effects == ['ephemeral-credential']
    response = api.client.post(url(api, capture, 'realtime', 'stop'), headers=api.headers, json=identity(capture))
    assert response.status_code == 200 and response.json()['state'] == 'stopped'


def test_stale_conversation_context_rejects_before_local_or_remote_start(api):
    api.body['conversation_revision'] = '999'
    for mode in ('talk', 'realtime'):
        response = api.client.post(api.base + mode, headers=api.headers, json=api.body)
        assert response.status_code == 409 and response.json()['code'] == 'revision_conflict'
    assert not api.effects and not api.voice.effects


def test_realtime_fallback_duplicates_are_scoped_to_transcript_item_and_new_turn_is_real(api):
    import json
    barrier = StreamBarrier()
    api.service.stream_factory = ScriptedAgentStream((barrier, ('done', None))).stream
    capture = start(api, 'realtime')['snapshot']
    text = 'Please explain the synthetic resource clearly'
    def event(kind, run='', **kwargs):
        response = api.client.post(url(api, capture, 'realtime', 'event'), headers=api.headers,
            json={**identity(capture), 'event': {'event_id': str(uuid4()), 'type': kind, 'generation_id': run, **kwargs}})
        assert response.status_code == 200, response.text
        return response.json()
    event('speech_started')
    event('speech_stopped')
    event('transcript_final', item_id='item-one', text=text)
    first = event('consult_fallback_needed', item_id='item-one', text=text)
    run = first['snapshot']['run_id']
    handle = api.service.registry.conversation_generation(api.conversation, run)
    try:
        assert barrier.entered.wait(5)
        for index in range(2):
            duplicate = event('function_call_ready', run, call_id=f'call-{index}', name='row_bot_agent_consult',
                arguments=json.dumps({'request': text}))
            assert duplicate['silent'] and 'already_running' in duplicate['function_output']
        from row_bot.runtime import admissions
        with admissions.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM generation_passes WHERE conversation_id=? AND queue_state='queued'", (api.conversation,)).fetchone()[0] == 0
        event('speech_started', run)
        event('speech_stopped', run)
        event('transcript_final', run, item_id='item-two', text=text)
        followup = event('function_call_ready', run, call_id='new-turn', name='row_bot_agent_consult', arguments=json.dumps({'request': text}))
        assert not followup['silent'] and 'queued' in followup['function_output']
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(5)


def test_realtime_very_fast_owned_run_keeps_lease_and_saved_generation_identity(api):
    api.service.stream_factory = ScriptedAgentStream((('token', 'Fast answer'),
        CheckpointCommit((AIMessage(id='fast-output', content='Fast answer'),), 'fast-output'), ('done', None))).stream
    original = api.service.realtime._submit
    def finished_before_return(*args):
        run = original(*args)
        assert api.service.registry.conversation_generation(api.conversation, run).producer_done.wait(5)
        return run
    api.service.realtime._submit = finished_before_return
    capture = start(api, 'realtime')['snapshot']
    response = api.client.post(url(api, capture, 'realtime', 'event'), headers=api.headers,
        json={**identity(capture), 'event': {'event_id': str(uuid4()), 'type': 'function_call_ready',
            'call_id': 'fast-call', 'name': 'row_bot_agent_consult', 'arguments': '{"request":"Explain the synthetic resource"}'}})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['accepted'] and value['snapshot']['run_id']
    assert api.service.dictation.coordinator._dictation_lease.revoked is False


def test_local_talk_start_during_typed_run_has_no_capture_or_second_generation(api):
    barrier = StreamBarrier()
    api.service.stream_factory = ScriptedAgentStream((barrier, ('done', None))).stream
    submitted = _command(api.client, api.headers, 'conversation.submit', {
        'submission_id': str(uuid4()), 'text': 'Synthetic typed request', 'attachment_refs': [],
        'model_selection': api.body['model_selection'], 'write_targets': []}, target=api.conversation,
        revision=api.body['conversation_revision'])
    assert submitted.status_code == 202, submitted.text
    handle = api.service.registry.conversation_generation(api.conversation, submitted.json()['generation_id'])
    try:
        assert barrier.entered.wait(5)
        response = api.client.post(api.base + 'talk', headers=api.headers, json=api.body)
        assert response.status_code == 409 and response.json()['code'] == 'generation_active'
        assert not api.effects and not api.voice.effects
        assert api.service.registry.active(api.conversation) == (handle,)
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(5)


def test_same_origin_sdp_exchange_has_no_secret_wire_and_exact_once_owner(api):
    started = start(api, 'realtime')
    capture = started['snapshot']
    headers = {**api.headers, 'Content-Type': 'application/sdp',
        'X-Voice-Session-Id': str(capture['handle']['voice_session_id']), 'X-Server-Epoch': capture['handle']['server_epoch']}
    response = api.client.post(url(api, capture, 'realtime', 'exchange'), headers=headers, content=b'v=0\r\nsynthetic-offer')
    assert response.status_code == 200, response.text
    assert response.content == b'v=0\r\nsynthetic-answer' and response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Content-Type'].startswith('application/sdp')
    assert api.effects == ['ephemeral-credential', 'exchange']
    again = api.client.post(url(api, capture, 'realtime', 'exchange'), headers=headers, content=b'v=0\r\nsynthetic-offer')
    assert again.status_code == 409 and api.effects == ['ephemeral-credential', 'exchange']
    view = api.client.get(url(api, capture, 'realtime', 'run'), headers=headers)
    assert view.status_code == 200 and view.json()['state'] == 'idle'
    assert 'synthetic-ephemeral-only' not in str(started) + response.text + view.text


def test_sdp_exchange_rejects_another_client_before_provider_and_retains_owner_lease(api):
    capture = start(api, 'realtime')['snapshot']
    _, other = bootstrap(api.client)
    headers = {**other, 'Content-Type': 'application/sdp',
        'X-Voice-Session-Id': str(capture['handle']['voice_session_id']), 'X-Server-Epoch': capture['handle']['server_epoch']}
    response = api.client.post(url(api, capture, 'realtime', 'exchange'), headers=headers, content=b'v=0\r\n')
    assert response.status_code == 403 and api.effects == ['ephemeral-credential']
    assert not api.service.dictation.coordinator._dictation_lease.revoked
