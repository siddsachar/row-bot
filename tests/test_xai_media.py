import base64
import json
from pathlib import Path

import pytest

import row_bot.providers.config as provider_config
from row_bot.providers.xai_oauth import XAIOAuthTokenSet, save_xai_oauth_tokens
from row_bot.secret_store import _set_backend_for_tests


class _MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class _Response:
    def __init__(self, status_code=200, payload=None, text="", content=b""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or (json.dumps(self._payload) if payload is not None else "")
        self.content = content

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture
def oauth_store(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    _set_backend_for_tests(_MemoryKeyring())
    try:
        yield
    finally:
        _set_backend_for_tests(None)


def _jwt(claims):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).rstrip(b"=").decode("ascii")

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.sig"


def _valid_token(subject="user-123"):
    return _jwt({
        "exp": 1893456000,
        "sub": subject,
        "account_id": "acct-123",
        "scope": "openid profile offline_access",
    })


def test_xai_media_json_request_uses_api_key_auth(monkeypatch):
    from row_bot.providers.xai_media import xai_media_json_request

    client = _Client([_Response(200, {"ok": True})])
    monkeypatch.setattr("row_bot.api_keys.get_key", lambda key: "xai-api-key" if key == "XAI_API_KEY" else "")

    payload = xai_media_json_request(
        "xai",
        "POST",
        "/images/generations",
        json={"model": "grok-imagine-image"},
        http_client=client,
    )

    assert payload == {"ok": True}
    assert client.calls[0][1] == "https://api.x.ai/v1/images/generations"
    assert client.calls[0][2]["headers"]["Authorization"] == "Bearer xai-api-key"


def test_xai_media_oauth_refreshes_once_after_401(oauth_store, monkeypatch):
    import row_bot.providers.xai_oauth as xai_oauth
    from row_bot.providers.xai_media import xai_media_json_request

    old_token = _valid_token("old-user")
    new_token = _valid_token("new-user")
    save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=old_token, refresh_token="refresh-secret"))
    client = _Client([
        _Response(401, {"error": {"message": "expired"}}),
        _Response(200, {"data": [{"b64_json": "abc"}]}),
    ])
    refreshed = []

    def _refresh(refresh_token):
        refreshed.append(refresh_token)
        return XAIOAuthTokenSet(access_token=new_token, refresh_token=refresh_token)

    monkeypatch.setattr(xai_oauth, "refresh_xai_oauth_token", _refresh)

    payload = xai_media_json_request(
        "xai_oauth",
        "POST",
        "/images/generations",
        json={"model": "grok-imagine-image"},
        http_client=client,
    )

    assert payload["data"][0]["b64_json"] == "abc"
    assert refreshed == ["refresh-secret"]
    assert client.calls[0][2]["headers"]["Authorization"] == f"Bearer {old_token}"
    assert client.calls[1][2]["headers"]["Authorization"] == f"Bearer {new_token}"


def test_xai_media_oauth_403_is_actionable_without_token_leak(oauth_store):
    from row_bot.providers.xai_media import XAIMediaError, xai_media_json_request

    token = _valid_token()
    save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=token, refresh_token="refresh-secret"))
    client = _Client([_Response(403, {"error": {"message": f"Bearer {token} denied"}})])

    with pytest.raises(XAIMediaError) as exc_info:
        xai_media_json_request(
            "xai_oauth",
            "POST",
            "/images/generations",
            json={"model": "grok-imagine-image"},
            http_client=client,
        )

    message = str(exc_info.value)
    assert "not authorized" in message
    assert "xAI API key provider" in message
    assert token not in message
    assert "Bearer [redacted]" in message


def test_xai_oauth_image_generation_saves_b64_result(oauth_store, monkeypatch):
    import row_bot.providers.xai_media as xai_media
    import row_bot.tools.image_gen_tool as image_tool
    from row_bot.tools import registry

    image_bytes = b"oauth-image"
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-secret"))
    client = _Client([_Response(200, {"data": [{"b64_json": image_b64}]})])
    monkeypatch.setattr(xai_media, "_new_http_client", lambda timeout: client)
    monkeypatch.setattr(image_tool, "_save_image_to_disk", lambda b64, prefix="gen": str(Path("saved.png")))
    registry.set_tool_config("image_gen", "model", "xai_oauth/grok-imagine-image")
    image_tool._last_generated_image = None
    image_tool._image_cache.clear()

    result = image_tool._generate_image("paint a small boat", size="1024x1024", quality="high")

    assert "Image generated successfully" in result
    assert "Provider: xAI Grok" in result
    assert image_tool._last_generated_image == image_b64
    assert image_tool._image_cache["__last_generated__"] == image_bytes
    call = client.calls[0]
    assert call[0] == "POST"
    assert call[1] == "https://api.x.ai/v1/images/generations"
    assert call[2]["headers"]["Authorization"].startswith("Bearer ")
    assert call[2]["json"]["model"] == "grok-imagine-image"
    assert call[2]["json"]["response_format"] == "b64_json"
    assert call[2]["json"]["aspect_ratio"] == "1:1"
    assert call[2]["json"]["resolution"] == "2k"


def test_xai_oauth_image_generation_missing_token_returns_reconnect_guidance(oauth_store):
    import row_bot.tools.image_gen_tool as image_tool
    from row_bot.tools import registry

    registry.set_tool_config("image_gen", "model", "xai_oauth/grok-imagine-image")

    result = image_tool._generate_image("paint a small boat")

    assert "Image generation failed" in result
    assert "reconnected" in result
    assert "Settings -> Providers -> xAI Grok" in result


def test_xai_oauth_image_edit_sends_data_url_body(oauth_store, monkeypatch):
    import row_bot.providers.xai_media as xai_media
    import row_bot.tools.image_gen_tool as image_tool
    from row_bot.tools import registry

    edited_bytes = b"edited"
    edited_b64 = base64.b64encode(edited_bytes).decode("ascii")
    save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-secret"))
    client = _Client([_Response(200, {"data": [{"b64_json": edited_b64}]})])
    monkeypatch.setattr(xai_media, "_new_http_client", lambda timeout: client)
    monkeypatch.setattr(image_tool, "_save_image_to_disk", lambda b64, prefix="edit": str(Path("edited.png")))
    registry.set_tool_config("image_gen", "model", "xai_oauth/grok-imagine-image")
    image_tool._image_cache.clear()
    image_tool._image_cache["__last_generated__"] = b"\x89PNG original"

    result = image_tool._edit_image("make it blue", image_source="last", quality="medium")

    assert "Image edited successfully" in result
    body = client.calls[0][2]["json"]
    assert body["model"] == "grok-imagine-image"
    assert body["image"]["type"] == "image_url"
    assert body["image"]["url"].startswith("data:image/png;base64,")
    assert body["quality"] == "medium"
    assert body["resolution"] == "1k"
    assert image_tool._image_cache["__last_generated__"] == edited_bytes


def test_xai_oauth_video_generation_polls_downloads_and_saves(oauth_store, monkeypatch):
    import row_bot.providers.xai_media as xai_media
    import row_bot.tools.video_gen_tool as video_tool
    from row_bot.tools import registry

    save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-secret"))
    client = _Client([
        _Response(200, {"request_id": "req-123"}),
        _Response(200, {"status": "done", "video": {"url": "https://cdn.example.test/video.mp4"}}),
        _Response(200, content=b"mp4-bytes"),
    ])
    monkeypatch.setattr(xai_media, "_new_http_client", lambda timeout: client)
    monkeypatch.setattr(video_tool.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(video_tool, "_save_video_to_disk", lambda data, prefix="vid": str(Path("video.mp4")))
    registry.set_tool_config("video_gen", "model", "xai_oauth/grok-imagine-video")
    video_tool._last_generated_video = None

    result = video_tool._generate_video("waves at sunset", duration_seconds=6, aspect_ratio="9:16", resolution="720p")

    assert "Video generated successfully" in result
    assert "Provider: xAI Grok" in result
    assert video_tool._last_generated_video["provider"] == "xAI Grok"
    assert video_tool._last_generated_video["mode"] == "text-to-video"
    start_body = client.calls[0][2]["json"]
    assert start_body == {
        "model": "grok-imagine-video",
        "prompt": "waves at sunset",
        "duration": 6,
        "aspect_ratio": "9:16",
        "resolution": "720p",
    }
    assert client.calls[1][1] == "https://api.x.ai/v1/videos/req-123"
    assert client.calls[2][1] == "https://cdn.example.test/video.mp4"
    assert client.calls[2][2]["headers"] == {}


def test_xai_oauth_image_to_video_includes_image_data_url(oauth_store, monkeypatch):
    import row_bot.providers.xai_media as xai_media
    import row_bot.tools.image_gen_tool as image_tool
    import row_bot.tools.video_gen_tool as video_tool
    from row_bot.tools import registry

    save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-secret"))
    client = _Client([
        _Response(200, {"request_id": "req-img"}),
        _Response(200, {"status": "done", "video": {"url": "https://cdn.example.test/video.mp4"}}),
        _Response(200, content=b"mp4-bytes"),
    ])
    monkeypatch.setattr(xai_media, "_new_http_client", lambda timeout: client)
    monkeypatch.setattr(video_tool.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(video_tool, "_save_video_to_disk", lambda data, prefix="vid": str(Path("video.mp4")))
    registry.set_tool_config("video_gen", "model", "xai_oauth/grok-imagine-video-1.5")
    image_tool._image_cache.clear()
    image_tool._image_cache["__last_generated__"] = b"\x89PNG original"

    result = video_tool._animate_image("make the water move", image_source="last")

    assert "Video generated successfully" in result
    assert video_tool._last_generated_video["mode"] == "image-to-video"
    body = client.calls[0][2]["json"]
    assert body["model"] == "grok-imagine-video-1.5"
    image_url = body["image"]["url"]
    assert image_url.startswith("data:image/png;base64,")
