"""Row-Bot Voice HTTP through canonical normal chat, with fakes (Live voice: test_client_live_voice.py)."""
# ruff: noqa: F811 -- shared isolated fixture.
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.voice.coordinator import VoiceSessionCoordinator
from row_bot.voice.provider_base import SynthesizedSpeech
from tests.helpers.client_platform_fakes import ScriptedAgentStream, StreamBarrier
from tests.helpers.voice_fakes import FakeClock, FakeSpeechToText, fake_browser_speech, fake_engine_parts
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.voice.test_dictation_lifecycle import Voice

pytestmark = pytest.mark.subsystem


@pytest.fixture
def api(service):
    voice = Voice()
    coordinator = VoiceSessionCoordinator()
    effects = []
    def transcribe(key, audio, mime, *, validate, mode):
        validate()
        effects.append('transcribe')
        return 'Synthetic request from microphone'
    def synthesize(key, text, *, validate):
        validate()
        effects.append(('synthesize', text))
        return SynthesizedSpeech(b'RIFF-synthetic-WAVE', 'audio/wav')
    browser = fake_browser_speech(voice, transcribe=transcribe, synthesize=synthesize)
    def credentials(**kwargs):
        effects.append('ephemeral-credential')
        return {'value': 'synthetic-ephemeral-only', 'expires_at': 60.0}
    def exchange(offer, *, client_secret, validate):
        validate()
        assert client_secret == 'synthetic-ephemeral-only'
        effects.append('exchange')
        return b'v=0\r\nsynthetic-answer'
    clock = FakeClock()
    stt = FakeSpeechToText(clock, 'Synthetic spoken request')
    engine = SimpleNamespace(readiness=lambda: None, build=lambda: fake_engine_parts(clock, stt))
    service.bind_voice(coordinator, browser_service=lambda: browser, clock=lambda: 0.0,
        realtime_provider_factory=lambda option: SimpleNamespace(
            create_client_secret=credentials, exchange_sdp=exchange, transport='openai_realtime',
            status=lambda: SimpleNamespace(ready=True)),
        voice_engine=engine)
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
            base=f'/api/v1/conversations/{conversation}/voice/', service=service, effects=effects, voice=voice,
            engine=engine, stt=stt)
    service.close_voice()


def start(api, mode='talk'):
    response = api.client.post(api.base + mode, headers=api.headers, json=api.body)
    assert response.status_code == 200, response.text
    return response.json()


def identity(snapshot):
    return {key: snapshot['handle'][key] for key in ('voice_session_id', 'server_epoch')}


def url(api, snapshot, mode='talk', action=''):
    return api.base + mode + '/' + snapshot['handle']['lease_id'] + ('/' + action if action else '')


def test_voice_start_freezes_context_issues_a_ticket_and_another_client_cannot_use_or_stop_it(api):
    started = start(api)
    capture = started['snapshot']
    assert capture['state'] == 'listening' and capture['transport'] == 'row_bot_voice'
    assert len(started['ticket']) >= 20 and 0 < started['ticket_expires_in_ms'] <= 30000
    modified = {**api.body, 'write_targets': [], 'model_selection': {'provider_id': 'fixture', 'model_ref': 'model:fixture:other'}}
    response = api.client.post(api.base + 'talk', headers=api.headers, json=modified)
    assert response.status_code == 409 and response.json()['code'] == 'idempotency_mismatch'
    _, other = bootstrap(api.client)
    for action in ('ticket', 'stop'):
        assert api.client.post(url(api, capture, action=action), headers=other, json=identity(capture)).status_code == 403
    # Another conversation's path never reaches this lease.
    elsewhere = _command(api.client, api.headers, 'conversation.create', {'title': 'Elsewhere'}).json()['conversation_id']
    wrong = f"/api/v1/conversations/{elsewhere}/voice/talk/{capture['handle']['lease_id']}/ticket"
    assert api.client.post(wrong, headers=api.headers, json=identity(capture)).status_code in {403, 410}
    response = api.client.post(url(api, capture, action='ticket'), headers=api.headers, json=identity(capture))
    assert response.status_code == 200 and response.json()['ticket'] != started['ticket']
    response = api.client.post(url(api, capture, action='stop'), headers=api.headers, json=identity(capture))
    assert response.status_code == 200 and response.json()['quiesced'] and response.json()['state'] == 'stopped'
    assert api.service.dictation.coordinator._dictation_lease.chat_context is None
    assert not api.effects


def test_stale_conversation_context_rejects_before_local_or_remote_start(api):
    api.body['conversation_revision'] = '999'
    for mode in ('talk', 'realtime'):
        response = api.client.post(api.base + mode, headers=api.headers, json=api.body)
        assert response.status_code == 409 and response.json()['code'] == 'revision_conflict'
    assert not api.effects


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
        assert not api.effects
        assert api.service.registry.active(api.conversation) == (handle,)
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(5)


SPEECH_TEST = '/api/v1/settings/voice/speech-test'


def test_speech_test_plays_only_the_fixed_phrase_to_the_requesting_browser(api):
    from row_bot.voice.client_duplex import SPEECH_TEST_PHRASE

    assert api.client.post(SPEECH_TEST).status_code in {401, 403}
    assert api.client.post(SPEECH_TEST, headers={'Origin': 'http://localhost'}).status_code in {401, 403}
    assert not api.effects
    response = api.client.post(SPEECH_TEST, headers=api.headers)
    assert response.status_code == 200, response.text
    assert response.content == b'RIFF-synthetic-WAVE'
    assert response.headers['Content-Type'] == 'audio/wav'
    assert response.headers['Cache-Control'] == 'no-store'
    assert api.effects == [('synthesize', SPEECH_TEST_PHRASE)]
    # It needs no voice session and never touches host audio.
    assert api.service.dictation.coordinator._dictation_lease is None


def test_speech_test_reports_missing_voice_instead_of_playing_on_the_host(service):  # noqa: F811
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.post(SPEECH_TEST, headers=headers)
    assert response.status_code == 403 and response.json()['code'] == 'capability_unavailable'
