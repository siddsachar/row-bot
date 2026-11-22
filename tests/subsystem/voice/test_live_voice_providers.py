"""The three Live voice fronts with fake HTTP: session shape, token minting, readiness."""
from __future__ import annotations

import json

import pytest
import requests

from row_bot.voice.agent_bridge import REALTIME_ALLOWED_TOOLS
from row_bot.voice.gemini_live import AUTH_TOKENS_URL, GEMINI_LIVE_MODEL, GEMINI_LIVE_URL, GeminiLiveProvider
from row_bot.voice.grok_voice import CLIENT_SECRETS_URL as GROK_SECRETS_URL, GROK_VOICE_URL, GrokVoiceProvider
from row_bot.voice.live_front import front_instructions
from row_bot.voice.live_voice import live_voice_rows
from row_bot.voice.openai_realtime import CLIENT_SECRETS_URL, DEFAULT_REALTIME_MODEL, OpenAIRealtimeProvider


class _Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.text = json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self.body


@pytest.fixture
def posts(monkeypatch):
    calls, replies = [], []

    def post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return replies.pop(0)

    for module in ("openai_realtime", "gemini_live", "grok_voice"):
        monkeypatch.setattr(f"row_bot.voice.{module}.requests.post", post)
    return calls, replies


def test_openai_secret_carries_the_session_instructions_and_only_bridge_tools(posts):
    calls, replies = posts
    replies.append(_Response({"value": "ek_synthetic", "expires_at": 1060}))
    data = OpenAIRealtimeProvider(api_key="sk_synthetic", voice="cedar").create_client_secret(
        instructions="Synthetic front instructions", expires_after_seconds=60)
    assert data == {"value": "ek_synthetic", "expires_at": 1060}
    sent = calls[0]
    assert sent["url"] == CLIENT_SECRETS_URL and sent["headers"]["Authorization"] == "Bearer sk_synthetic"
    session = sent["json"]["session"]
    assert sent["json"]["expires_after"] == {"anchor": "created_at", "seconds": 60}
    assert session["model"] == DEFAULT_REALTIME_MODEL and session["audio"]["output"]["voice"] == "cedar"
    assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert session["instructions"] == "Synthetic front instructions"
    assert [tool["name"] for tool in session["tools"]] == list(REALTIME_ALLOWED_TOOLS)


def test_openai_defaults_to_the_front_instructions_and_sanitizes_stale_values():
    config = OpenAIRealtimeProvider(api_key="sk", model="local-whisper-base", voice="nope").session_config()
    assert config["instructions"] == front_instructions()
    assert config["model"] == DEFAULT_REALTIME_MODEL and config["audio"]["output"]["voice"] == "marin"


def test_provider_failure_never_carries_the_response_body(posts):
    calls, replies = posts
    replies.append(_Response({"error": {"message": "echo of Synthetic private chat"}}, status=400))
    with pytest.raises(requests.HTTPError) as caught:
        OpenAIRealtimeProvider(api_key="sk").create_client_secret(instructions="Synthetic private chat")
    assert "Synthetic private chat" not in str(caught.value)
    assert caught.value.response.status_code == 400


def test_gemini_token_is_single_use_and_locks_the_front_session(posts):
    calls, replies = posts
    replies.append(_Response({"name": "auth_tokens/synthetic"}))
    data = GeminiLiveProvider(api_key="g_synthetic", clock=lambda: 1_000_000.0).create_client_secret(
        instructions="Synthetic front instructions", expires_after_seconds=60)
    sent = calls[0]
    assert sent["url"] == AUTH_TOKENS_URL and sent["headers"]["x-goog-api-key"] == "g_synthetic"
    body = sent["json"]
    assert body["uses"] == 1 and body["newSessionExpireTime"] == "1970-01-12T13:47:40Z"
    locked = body["liveConnectConstraints"]
    assert locked["model"] == f"models/{GEMINI_LIVE_MODEL}"
    assert locked["config"]["systemInstruction"] == {"parts": [{"text": "Synthetic front instructions"}]}
    declarations = locked["config"]["tools"][0]["functionDeclarations"]
    assert [item["name"] for item in declarations] == list(REALTIME_ALLOWED_TOOLS)
    consult = declarations[0]
    # The front keeps talking while the agent works; schemas are Gemini's subset.
    assert consult["behavior"] == "NON_BLOCKING" and consult["parameters"]["type"] == "OBJECT"
    assert "additionalProperties" not in json.dumps(declarations)
    assert data["value"] == "auth_tokens/synthetic" and data["expires_at"] == 1_000_060.0
    assert data["url"] == GEMINI_LIVE_URL
    setup = data["setup"]["setup"]
    assert setup["model"] == locked["model"] and setup["generationConfig"] == {"responseModalities": ["AUDIO"]}
    assert setup["systemInstruction"] == locked["config"]["systemInstruction"]
    assert setup["inputAudioTranscription"] == {} and setup["outputAudioTranscription"] == {}


def test_grok_secret_has_no_session_and_the_browser_sends_the_openai_schema_update(posts):
    calls, replies = posts
    replies.append(_Response({"value": "xai_ephemeral", "expires_at": 1060}))
    data = GrokVoiceProvider(api_key="xai_synthetic").create_client_secret(
        instructions="Synthetic front instructions", expires_after_seconds=60)
    sent = calls[0]
    assert sent["url"] == GROK_SECRETS_URL and sent["headers"]["Authorization"] == "Bearer xai_synthetic"
    assert sent["json"] == {"expires_after": {"seconds": 60}}
    assert data["value"] == "xai_ephemeral" and data["expires_at"] == 1060 and data["url"] == GROK_VOICE_URL
    update = data["setup"]
    assert update["type"] == "session.update"
    session = update["session"]
    assert session["instructions"] == "Synthetic front instructions"
    assert session["turn_detection"] == {"type": "server_vad"}
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert [tool["name"] for tool in session["tools"]] == list(REALTIME_ALLOWED_TOOLS)


@pytest.mark.parametrize("provider", [OpenAIRealtimeProvider, GeminiLiveProvider, GrokVoiceProvider])
def test_readiness_follows_the_saved_key_without_a_provider_call(provider, posts):
    calls, _ = posts
    missing = provider(api_key="").status()
    assert not missing.ready and missing.unavailable_code == "realtime_key_missing" and not missing.local
    assert provider(api_key="synthetic-key").status().ready
    with pytest.raises(RuntimeError):
        provider(api_key="").create_client_secret()
    assert not calls


def test_rows_say_who_hears_the_audio_what_it_costs_and_why_unavailable():
    rows = {row["id"]: row for row in live_voice_rows({"gemini": True})}
    assert list(rows) == ["openai", "gemini", "grok"]
    assert rows["gemini"]["available"] and rows["gemini"]["reason"] == "available"
    assert not rows["openai"]["available"] and rows["openai"]["reason"] == "realtime_key_missing"
    assert [rows[key]["company"] for key in rows] == ["OpenAI", "Google", "xAI"]
    assert all(row["price"].endswith("a minute") and row["price_checked"] for row in rows.values())
