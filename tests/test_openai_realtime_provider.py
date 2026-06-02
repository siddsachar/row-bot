from __future__ import annotations

import pytest

from voice.openai_realtime import (
    CLIENT_SECRETS_URL,
    DEFAULT_REALTIME_MODEL,
    OpenAIRealtimeProvider,
    THOTH_REALTIME_INSTRUCTIONS,
)
from voice.agent_bridge import REALTIME_ALLOWED_TOOLS, REALTIME_WAIT_TOOL


def test_openai_realtime_status_requires_openai_key(monkeypatch):
    monkeypatch.setattr("voice.openai_realtime.get_key", lambda name: "")

    status = OpenAIRealtimeProvider().status()

    assert status.ready is False
    assert "Providers" in status.reason


def test_openai_realtime_client_secret_uses_ephemeral_endpoint(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"value": "ek_test", "session": {"type": "realtime"}}

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("voice.openai_realtime.requests.post", fake_post)
    provider = OpenAIRealtimeProvider(api_key="sk_test")

    data = provider.create_client_secret(instructions="Route serious work through Thoth.")

    assert data["value"] == "ek_test"
    assert calls[0]["url"] == CLIENT_SECRETS_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer sk_test"
    assert calls[0]["json"]["session"]["type"] == "realtime"
    assert calls[0]["json"]["session"]["model"] == DEFAULT_REALTIME_MODEL
    assert calls[0]["json"]["session"]["output_modalities"] == ["audio"]
    assert calls[0]["json"]["session"]["audio"]["input"]["transcription"]["model"] == "gpt-realtime-whisper"
    assert calls[0]["json"]["session"]["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert calls[0]["json"]["session"]["audio"]["input"]["turn_detection"]["eagerness"] == "high"
    assert calls[0]["json"]["session"]["audio"]["input"]["turn_detection"]["create_response"] is True
    assert calls[0]["json"]["session"]["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert calls[0]["json"]["session"]["audio"]["output"]["voice"] == "marin"
    assert "speed" not in calls[0]["json"]["session"]
    assert [tool["name"] for tool in calls[0]["json"]["session"]["tools"]] == list(REALTIME_ALLOWED_TOOLS)
    assert calls[0]["json"]["session"]["tool_choice"] == "auto"
    assert "turn_detection" not in calls[0]["json"]["session"]
    assert "input_audio_transcription" not in calls[0]["json"]["session"]
    assert calls[0]["json"]["session"]["instructions"].startswith("Route serious work through Thoth.")
    assert REALTIME_WAIT_TOOL in calls[0]["json"]["session"]["instructions"]


def test_openai_realtime_default_session_config_routes_through_thoth():
    provider = OpenAIRealtimeProvider(api_key="sk_test", voice="cedar")
    config = provider.session_config()

    assert config["model"] == DEFAULT_REALTIME_MODEL
    assert config["audio"]["output"]["voice"] == "cedar"
    assert "speed" not in config
    assert config["audio"]["input"]["transcription"]["model"] == "gpt-realtime-whisper"
    assert config["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert config["audio"]["input"]["turn_detection"]["eagerness"] == "high"
    assert config["audio"]["input"]["turn_detection"]["create_response"] is True
    assert config["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert "turn_detection" not in config
    assert "input_audio_transcription" not in config
    assert "You are Thoth in realtime voice mode" in config["instructions"]
    assert "front end" not in config["instructions"]
    assert "worker" not in config["instructions"]
    assert "thoth_agent_consult" in config["instructions"]
    assert REALTIME_WAIT_TOOL in config["instructions"]
    assert [tool["name"] for tool in config["tools"]] == list(REALTIME_ALLOWED_TOOLS)
    assert config["instructions"] == THOTH_REALTIME_INSTRUCTIONS


def test_openai_realtime_provider_sanitizes_stale_runtime_values():
    provider = OpenAIRealtimeProvider(
        api_key="sk_test",
        model="local-whisper-base",
        voice="not-a-voice",
    )
    config = provider.session_config()

    assert config["model"] == DEFAULT_REALTIME_MODEL
    assert config["audio"]["output"]["voice"] == "marin"
    assert "speed" not in config


def test_openai_realtime_transcription_config_is_dictation_only():
    config = OpenAIRealtimeProvider(api_key="sk_test").transcription_session_config()

    assert config["type"] == "transcription"
    assert config["audio"]["input"]["transcription"]["model"] == "gpt-realtime-whisper"
    assert "tools" not in config


def test_openai_realtime_client_secret_error_includes_response_body(monkeypatch):
    class Response:
        text = '{"error":{"message":"bad field"}}'

        def raise_for_status(self) -> None:
            raise requests.HTTPError("400 Client Error", response=self)

    import requests

    monkeypatch.setattr("voice.openai_realtime.requests.post", lambda *args, **kwargs: Response())
    provider = OpenAIRealtimeProvider(api_key="sk_test")

    with pytest.raises(RuntimeError, match="bad field"):
        provider.create_client_secret()


def test_openai_realtime_client_secret_missing_key():
    with pytest.raises(RuntimeError):
        OpenAIRealtimeProvider(api_key="").create_client_secret()


def test_openai_realtime_fake_provider_event_mapping():
    provider = OpenAIRealtimeProvider(api_key="sk_test")

    transcript = provider.map_server_event({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "hello thoth",
    })
    audio_done = provider.map_server_event({"type": "response.output_audio.done"})
    error = provider.map_server_event({"type": "error", "error": {"message": "bad session"}})

    assert transcript is not None
    assert transcript.type == "transcript_final"
    assert transcript.text == "hello thoth"
    assert audio_done is not None
    assert audio_done.type == "response.output_audio.done"
    assert error is not None
    assert error.type == "error"
    assert error.text == "bad session"
