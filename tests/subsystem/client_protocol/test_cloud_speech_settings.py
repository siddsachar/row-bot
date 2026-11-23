"""Settings › Voice for cloud speech over HTTP: keys, pickers, describe a voice, no cloning."""
# ruff: noqa: F811 -- the shared Settings API fixture.
from __future__ import annotations

import json
import logging

import httpx
import pytest

from tests.subsystem.client_protocol.test_settings_snapshot_api import BASE, _execute, _review, api  # noqa: F401

pytestmark = pytest.mark.subsystem

KEY = "synthetic-deepgram-key-7731"


def _set(client, headers, field, value):
    before = client.get(BASE, headers=headers).json()
    request = {"settings_revision": before["revision"], "page": "voice", "field": field, "value": value}
    review = _review(client, headers, request)
    response = _execute(client, headers, request, review)
    assert response.status_code == 200, response.text
    return review, response


def _row(snapshot, option_id):
    return next(row for row in snapshot["voice"]["cloud_speech"] if row["id"] == option_id)


@pytest.fixture
def keys(api, monkeypatch):
    client, headers, data, secret_state = api
    from row_bot import api_keys

    # The key store's reads follow what Settings saved (the fixture keeps saved keys in a dict).
    monkeypatch.setattr(api_keys, "get_key", lambda name: secret_state.get(name, ""))
    return api


def test_a_cloud_key_is_saved_from_settings_and_never_returned_or_logged(keys, caplog):
    caplog.set_level(logging.DEBUG)
    client, headers, data, secret_state = keys
    before = client.get(BASE, headers=headers).json()
    row = _row(before, "deepgram")
    assert row["key_saved"] is False and row["key_field"] == "deepgram.credential"
    assert row["company"] == "Deepgram" and row["price"] and row["price_checked"]
    review, response = _set(client, headers, "deepgram.credential", KEY)
    assert review["secret"] is True
    assert secret_state["DEEPGRAM_API_KEY"] == KEY
    after = client.get(BASE, headers=headers)
    assert _row(after.json(), "deepgram")["key_saved"] is True
    for text in (json.dumps(review), response.text, after.text, caplog.text):
        assert KEY not in text
    # Keys for OpenAI and Google stay in Providers: no Voice field holds them.
    assert _row(after.json(), "openai_transcribe")["key_field"] is None
    _set(client, headers, "deepgram.credential", None)
    assert "DEEPGRAM_API_KEY" not in secret_state
    assert _row(client.get(BASE, headers=headers).json(), "deepgram")["key_saved"] is False


def test_listen_and_speak_choices_and_voices_are_checked(keys):
    client, headers, data, _ = keys
    for field, value in (("runtime.talk_provider", "assemblyai"), ("runtime.speech_output_provider", "cartesia"),
                         ("runtime.cartesia_voice", "62ae83ad-4f6a-430b-af41-a9bede9286ca"),
                         ("runtime.gemini_tts_voice_description", "Warm and unhurried, like a radio host.")):
        _set(client, headers, field, value)
    saved = json.loads((data / "voice_runtime_settings.json").read_text(encoding="utf-8"))
    assert saved["talk_provider"] == "assemblyai" and saved["speech_output_provider"] == "cartesia"
    snapshot = client.get(BASE, headers=headers).json()
    assert _row(snapshot, "cartesia")["voice"] == "62ae83ad-4f6a-430b-af41-a9bede9286ca"
    assert _row(snapshot, "gemini_tts")["description"] == "Warm and unhurried, like a radio host."
    before = snapshot["revision"]
    # No such voice service; ElevenLabs' described voice before one was made.
    for field, value in (("runtime.speech_output_provider", "openai_tts"), ("runtime.elevenlabs_voice", "designed")):
        response = client.post(BASE + "/review", headers=headers, json={
            "settings_revision": before, "page": "voice", "field": field, "value": value})
        assert response.json()["code"] == "invalid_settings_command", (field, response.text)


def test_describe_a_voice_makes_an_elevenlabs_voice_from_words_and_picks_it(keys, monkeypatch):
    client, headers, data, secret_state = keys
    secret_state["ELEVENLABS_API_KEY"] = "synthetic-eleven-key"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/text-to-voice/design":
            return httpx.Response(200, json={"previews": [{"generated_voice_id": "gen-7"}]})
        return httpx.Response(200, json={"voice_id": "voice-7"})

    from row_bot.voice import cloud_tts

    monkeypatch.setattr(cloud_tts, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    _set(client, headers, "runtime.elevenlabs_voice_description", "A bright, friendly voice with a soft Irish lilt.")
    review, response = _set(client, headers, "elevenlabs.design_voice", True)
    assert "words only" in review["value_summary"] and "ElevenLabs" in review["value_summary"]
    assert response.json()["status"] == "completed"
    assert [request.url.path for request in requests] == ["/v1/text-to-voice/design", "/v1/text-to-voice"]
    row = _row(client.get(BASE, headers=headers).json(), "elevenlabs")
    assert row["designed"] is True and row["voice"] == "designed"
    assert {"value": "designed", "label": "Your described voice"} in row["voices"]
    assert "synthetic-eleven-key" not in response.text


def test_a_too_short_description_makes_no_request(keys, monkeypatch):
    client, headers, data, secret_state = keys
    secret_state["ELEVENLABS_API_KEY"] = "synthetic-eleven-key"
    from row_bot.voice import cloud_tts

    def never():
        raise AssertionError("no request")

    monkeypatch.setattr(cloud_tts, "http_client", never)
    _set(client, headers, "runtime.elevenlabs_voice_description", "Calm.")
    _review_result, response = _set(client, headers, "elevenlabs.design_voice", True)
    assert response.json()["status"] == "rejected"
    assert "20 characters" in response.json()["action_result"]["message"]


def test_the_api_has_no_voice_cloning_route(keys):
    app = keys[0].app
    while not hasattr(app, "openapi") and hasattr(app, "app"):
        app = app.app
    paths = list(app.openapi()["paths"])
    assert any(path.startswith("/api/v1/settings") for path in paths)
    assert not any(word in path for path in paths for word in ("clone", "cloning", "text-to-voice", "voices/add"))


def test_read_aloud_and_voice_notes_speak_with_the_chosen_cloud_voice(keys, monkeypatch):
    _client, _headers, data, secret_state = keys
    (data / "voice_runtime_settings.json").write_text(json.dumps({"speech_output_provider": "cartesia"}),
                                                      encoding="utf-8")
    from types import SimpleNamespace

    from row_bot.channels import voice_replies
    from row_bot.voice import cloud_tts
    from row_bot.voice.browser_local import BrowserLocalVoiceService, BrowserVoiceError

    heard: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        heard.append(json.loads(request.content))
        return httpx.Response(200, content=b"\x01\x00" * 2400)

    monkeypatch.setattr(cloud_tts, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    kokoro = SimpleNamespace(is_installed=lambda: False)
    browser = BrowserLocalVoiceService(voice_service=SimpleNamespace(), tts_service=kokoro)
    # No key: Kokoro speaks (and it isn't installed here), never another company.
    with pytest.raises(BrowserVoiceError) as missing:
        browser.synthesize("session", "Hello there.")
    assert missing.value.code == "kokoro_model_missing" and not heard
    assert voice_replies.speech_output_ready() is False
    secret_state["CARTESIA_API_KEY"] = "synthetic-cartesia-key"
    speech = browser.synthesize("session", "Hello there.")
    assert speech.content_type == "audio/wav" and speech.audio.startswith(b"RIFF")
    assert heard[-1]["transcript"] == "Hello there."
    assert voice_replies.speech_output_ready() is True


def test_the_languages_said_to_be_spoken_follow_the_chosen_voice(keys):
    client, headers, data, secret_state = keys
    from row_bot.voice import languages
    from row_bot.voice.cloud_speech import option

    _set(client, headers, "runtime.speech_output_provider", "elevenlabs")
    # Without its key, Kokoro speaks, and the list is Kokoro's.
    spoken = client.get(BASE, headers=headers).json()["voice"]["language"]["spoken"]
    assert spoken == sorted(languages.KOKORO_LANGUAGES)
    secret_state["ELEVENLABS_API_KEY"] = "synthetic-eleven-key"
    spoken = client.get(BASE, headers=headers).json()["voice"]["language"]["spoken"]
    assert spoken == sorted(option("elevenlabs", "speak").speaks)
    assert "de" in spoken and "he" not in spoken
