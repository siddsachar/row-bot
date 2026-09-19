"""Actual media owner construction/request arguments with no network."""

from concurrent.futures import ThreadPoolExecutor
import base64
from dataclasses import replace
import sys
import threading
from types import SimpleNamespace

import pytest

from row_bot.providers.media_auth import (
    CapturedMediaAuth,
    current_media_auth,
    media_auth_scope,
)


def auth(provider="openai", key="synthetic-captured-key"):
    model = {
        "openai": "gpt-image-1.5",
        "google": "veo-3.1-generate-preview",
        "xai_oauth": "grok-imagine-video",
    }[provider]
    base = {
        "openai": "https://api.openai.com/v1",
        "google": "https://generativelanguage.googleapis.com",
        "xai_oauth": "https://api.x.ai/v1",
    }[provider]
    return CapturedMediaAuth(
        provider + "/" + model,
        provider,
        key,
        "private-identity",
        base,
        "captured-org",
        "captured-project",
    )


def test_image_sdk_uses_captured_key_endpoint_account_and_zero_retries(monkeypatch):
    from row_bot.tools import image_gen_tool
    from row_bot import api_keys

    recorded, closed = [], []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-replacement-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foreign.invalid/v1")
    monkeypatch.setenv("OPENAI_ORG_ID", "foreign-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "foreign-project")
    monkeypatch.setattr(
        api_keys, "get_key", lambda *a: pytest.fail("runtime key reread")
    )

    def http(**kwargs):
        assert kwargs == {"trust_env": False, "follow_redirects": False}
        return SimpleNamespace(close=lambda: closed.append("http"))

    def sdk(**kwargs):
        recorded.append(kwargs)
        return SimpleNamespace(close=lambda: closed.append("sdk"))

    monkeypatch.setitem(
        sys.modules, "openai", SimpleNamespace(OpenAI=sdk, DefaultHttpxClient=http)
    )
    captured = auth()
    with image_gen_tool.strict_generation_output(
        selection=captured.selection,
        auth=captured,
        validate=lambda: None,
        sink=lambda data: "saved",
    ):
        _, _, provider = image_gen_tool._get_client()
        assert provider == "openai"
    assert recorded[0]["api_key"] == captured.credential
    assert (
        recorded[0]["base_url"] == captured.base_url and recorded[0]["max_retries"] == 0
    )
    assert (
        recorded[0]["organization"] == "captured-org"
        and recorded[0]["project"] == "captured-project"
    )
    assert closed == ["sdk", "http"] and current_media_auth() is None


def test_google_video_sdk_ignores_environment_routing_and_retries(monkeypatch):
    from row_bot.tools import video_gen_tool
    from row_bot import api_keys
    from google import genai

    recorded = []
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setattr(
        api_keys, "get_key", lambda *a: pytest.fail("runtime key reread")
    )
    monkeypatch.setattr(
        genai,
        "Client",
        lambda **kwargs: recorded.append(kwargs) or SimpleNamespace(close=lambda: None),
    )
    captured = auth("google")
    with video_gen_tool.strict_generation_output(
        selection=captured.selection,
        auth=captured,
        validate=lambda: None,
        sink=lambda data: "saved",
    ):
        video_gen_tool._get_google_client()
    assert recorded == [
        {
            "api_key": captured.credential,
            "vertexai": False,
            "http_options": {
                "base_url": captured.base_url,
                "retry_options": {"attempts": 1},
                "client_args": {"trust_env": False, "follow_redirects": False},
                "async_client_args": {"trust_env": False, "follow_redirects": False},
            },
        }
    ]


def test_xai_401_is_one_request_with_captured_account_and_no_refresh(monkeypatch):
    from row_bot.providers import xai_media, xai_oauth

    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(
            status_code=401, json=lambda: {"error": {"message": "synthetic denied"}}
        )

    monkeypatch.setattr(
        xai_oauth,
        "xai_oauth_runtime_credentials",
        lambda **k: pytest.fail("credential reread"),
    )
    monkeypatch.setattr(
        xai_media,
        "_refresh_oauth_context_once",
        lambda *a: pytest.fail("implicit refresh/retry"),
    )
    captured = auth("xai_oauth")
    with media_auth_scope(
        captured, selection=captured.selection, validate=lambda: None
    ):
        with pytest.raises(xai_media.XAIMediaError):
            xai_media.xai_media_json_request(
                "xai_oauth",
                "POST",
                "/videos/generations",
                json={"prompt": "synthetic"},
                http_client=SimpleNamespace(request=request),
            )
    assert len(calls) == 1
    assert calls[0][1] == "https://api.x.ai/v1/videos/generations"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer " + captured.credential
    assert calls[0][2]["follow_redirects"] is False


def test_xai_captured_auth_never_follows_unreviewed_absolute_endpoint(monkeypatch):
    from row_bot.providers import xai_media

    captured = auth("xai_oauth")
    with media_auth_scope(
        captured, selection=captured.selection, validate=lambda: None
    ):
        with pytest.raises(xai_media.XAIMediaError, match="outside its provider"):
            xai_media.xai_media_get(
                "xai_oauth",
                "https://foreign.invalid/file",
                http_client=SimpleNamespace(
                    request=lambda *a, **k: pytest.fail("foreign request")
                ),
            )


def test_auth_context_is_nested_and_thread_local():
    first, second = auth(key="synthetic-first"), auth(key="synthetic-second")
    barrier = threading.Barrier(2)

    def work(value):
        with media_auth_scope(value, selection=value.selection, validate=lambda: None):
            barrier.wait(5)
            assert current_media_auth().credential == value.credential
            with media_auth_scope(
                None, selection=value.selection, validate=lambda: None
            ):
                assert current_media_auth() is None
            assert current_media_auth().credential == value.credential
        assert current_media_auth() is None

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(work, (first, second)))


def test_revocation_blocks_actual_request_and_preserves_exception():
    from row_bot.providers import xai_media

    captured = auth("xai_oauth")
    denied = []
    sentinel = PermissionError("synthetic revoked")

    def validate():
        if denied:
            raise sentinel

    with media_auth_scope(captured, selection=captured.selection, validate=validate):
        denied.append(True)
        with pytest.raises(PermissionError) as caught:
            xai_media.xai_media_json_request(
                "xai_oauth",
                "POST",
                "/videos",
                http_client=SimpleNamespace(
                    request=lambda *a, **k: pytest.fail("revoked request")
                ),
            )
        assert caught.value is sentinel


@pytest.mark.parametrize(
    "base",
    [
        "https://foreign.invalid/v1",
        "http://api.openai.com/v1",
        "https://user@api.openai.com/v1",
    ],
)
def test_invalid_captured_endpoint_fails_before_entering_effect_scope(base):
    captured = replace(auth(), base_url=base)
    with pytest.raises(ValueError, match="media_endpoint_unavailable"):
        with media_auth_scope(
            captured, selection=captured.selection, validate=lambda: None
        ):
            pytest.fail("invalid endpoint admitted")


def test_captured_secret_is_not_in_repr():
    captured = auth()
    assert captured.credential not in repr(captured)


def test_actual_image_request_keeps_captured_auth_through_account_aba(monkeypatch):
    from row_bot.tools import image_gen_tool
    from row_bot.application.attachment_context import prepared_attachments

    captured = auth()
    active = ["synthetic-account-A"]
    requests, outputs = [], []

    def sdk(**kwargs):
        # Account was replaced before SDK construction, then restored before
        # request validation. The SDK must still receive the captured key.
        assert active[0] == "synthetic-account-B"
        active[0] = "synthetic-account-A"

        def generate(**request):
            requests.append((kwargs["api_key"], request["model"], active[0]))
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=base64.b64encode(b"synthetic-image").decode()
                    )
                ]
            )

        return SimpleNamespace(
            images=SimpleNamespace(generate=generate), close=lambda: None
        )

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            OpenAI=sdk,
            DefaultHttpxClient=lambda **kwargs: SimpleNamespace(close=lambda: None),
        ),
    )
    with (
        prepared_attachments("synthetic-auth", []),
        image_gen_tool.strict_generation_output(
            selection=captured.selection,
            auth=captured,
            validate=lambda: None,
            sink=lambda value: outputs.append(value) or "owned",
        ),
    ):
        active[0] = "synthetic-account-B"
        image_gen_tool._generate_image("synthetic prompt")
    assert requests == [(captured.credential, "gpt-image-1.5", "synthetic-account-A")]
    assert outputs == [b"synthetic-image"]


def test_legacy_sdk_default_behavior_remains_without_captured_auth(monkeypatch):
    from row_bot.tools import image_gen_tool
    from row_bot import api_keys

    recorded = []
    monkeypatch.setattr(
        image_gen_tool.registry, "get_tool_config", lambda *a: "openai/gpt-image-1"
    )
    monkeypatch.setattr(api_keys, "get_key", lambda key: "synthetic-legacy-key")
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda **kwargs: recorded.append(kwargs)),
    )
    image_gen_tool._get_client()
    assert recorded == [{"api_key": "synthetic-legacy-key"}]
