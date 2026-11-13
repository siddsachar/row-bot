"""Images with a ChatGPT plan: the Codex backend's image endpoints (fake HTTP only)."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii")


class _Response:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(SimpleNamespace(url=url, json=kwargs.get("json"), headers=dict(kwargs.get("headers") or {})))
        return self.responses.pop(0)

    def close(self):
        pass


def _signed_in(monkeypatch, *, plan="plus", refreshed=None):
    from row_bot.providers import codex
    tokens = {"access": "synthetic-access"}

    def credentials(**_kwargs):
        return codex.CodexTokenSet(access_token=tokens["access"], refresh_token="synthetic-refresh",
                                   account_id="synthetic-account", plan_type=plan)

    monkeypatch.setattr(codex, "codex_runtime_credentials", credentials)
    monkeypatch.setattr(codex, "refresh_codex_token", lambda token: refreshed)
    monkeypatch.setattr(codex, "save_codex_oauth_tokens", lambda token_set, **_k: tokens.update(access="synthetic-renewed"))
    monkeypatch.setattr(codex, "check_codex_token_health",
                        lambda **_k: codex.CodexTokenHealth("valid", "", credentials()))


@pytest.fixture
def chatgpt_images(monkeypatch):
    """The image tool on ChatGPT's image model, signed in on a paid plan, with no disk writes."""
    from row_bot.providers.transports import codex_responses
    from row_bot.tools import image_gen_tool

    _signed_in(monkeypatch)
    client = _Client([])
    monkeypatch.setattr(codex_responses, "_new_http_client", lambda timeout: client)
    monkeypatch.setattr(image_gen_tool.registry, "get_tool_config",
                        lambda tool, *args: "codex/gpt-image-2" if tool == "image_gen" else "")
    monkeypatch.setattr(image_gen_tool, "_save_image_to_disk", lambda *args, **kwargs: None)
    return SimpleNamespace(client=client, tool=image_gen_tool)


def test_a_chatgpt_plan_makes_an_image_on_the_codex_backend(chatgpt_images):
    chatgpt_images.client.responses.append(_Response({"data": [{"b64_json": PNG}]}))

    result = chatgpt_images.tool._generate_image("a lighthouse at dawn", size="1536x1024", quality="high")

    assert result == "Image generated successfully. Model: gpt-image-2 | Provider: ChatGPT"
    call = chatgpt_images.client.calls[0]
    assert call.url == "https://chatgpt.com/backend-api/codex/images/generations"
    assert call.headers["Authorization"] == "Bearer synthetic-access"
    assert call.headers["ChatGPT-Account-ID"] == "synthetic-account"
    assert call.json == {"model": "gpt-image-2", "prompt": "a lighthouse at dawn", "n": 1,
                         "background": "opaque", "quality": "high", "size": "1536x1024"}


def test_a_chatgpt_plan_edits_the_last_image(chatgpt_images, monkeypatch):
    monkeypatch.setattr(chatgpt_images.tool, "_resolve_image_source", lambda source: b"\x89PNG\r\n\x1a\nsource")
    chatgpt_images.client.responses.append(_Response({"data": [{"b64_json": PNG}]}))

    result = chatgpt_images.tool._edit_image("make it night", image_source="last")

    assert result.startswith("Image edited successfully.")
    call = chatgpt_images.client.calls[0]
    assert call.url.endswith("/codex/images/edits")
    assert call.json["images"][0]["image_url"].startswith("data:image/png;base64,")


def test_a_reached_plan_limit_says_so_and_when_it_resets(chatgpt_images):
    chatgpt_images.client.responses.append(_Response(
        {"error": {"type": "usage_limit_reached", "message": "limit", "resets_at": 1_800_000_000}}, status_code=429))

    result = chatgpt_images.tool._generate_image("a lighthouse")

    assert result.startswith("Image generation failed: Your ChatGPT plan's image limit is reached. It resets at ")
    assert "Choose another image model in Settings › Models" in result


def test_an_expired_sign_in_is_renewed_once_and_the_image_still_comes(chatgpt_images, monkeypatch):
    from row_bot.providers import codex
    _signed_in(monkeypatch, refreshed=codex.CodexTokenSet(access_token="synthetic-renewed"))
    chatgpt_images.client.responses.extend([_Response({}, status_code=401), _Response({"data": [{"b64_json": PNG}]})])

    assert chatgpt_images.tool._generate_image("a lighthouse").startswith("Image generated successfully.")
    assert [call.headers["Authorization"] for call in chatgpt_images.client.calls] == [
        "Bearer synthetic-access", "Bearer synthetic-renewed"]


def test_a_free_plan_is_told_images_need_a_paid_plan_without_a_request(chatgpt_images, monkeypatch):
    _signed_in(monkeypatch, plan="free")

    assert chatgpt_images.tool._generate_image("a lighthouse") == (
        "Image generation failed: Images need a paid ChatGPT plan.")
    assert chatgpt_images.client.calls == []


@pytest.mark.parametrize(("plan", "offered"), [("plus", True), ("free", False)])
def test_chatgpt_image_model_is_offered_to_a_signed_in_paid_plan(monkeypatch, plan, offered):
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models

    _signed_in(monkeypatch, plan=plan)
    # ChatGPT's own list names chat models only; its refresh adds the image model.
    infos = models._documented_media_model_infos("codex", set())
    monkeypatch.setattr(models, "_cloud_model_cache",
                        {info.selection_ref: model_info_to_cache_entry(info) for info in infos})

    assert ("codex/gpt-image-2" in get_available_image_models()) is offered


@pytest.mark.parametrize(("saved", "plan", "after"), [
    (None, "plus", "codex/gpt-image-2"),
    ("openai/gpt-image-1", "plus", "openai/gpt-image-1"),
    (None, "free", None),
])
def test_signing_in_with_chatgpt_makes_it_the_image_model_only_when_none_is_chosen(monkeypatch, saved, plan, after):
    from row_bot.application import provider_settings_commands, subscription_controls
    from row_bot.providers import model_catalog_cache
    from row_bot.tools import registry

    _signed_in(monkeypatch, plan=plan)
    config = {"model": saved} if saved else {}
    monkeypatch.setattr(provider_settings_commands, "invalidate_provider_runtime", lambda: None)
    monkeypatch.setattr(model_catalog_cache, "provider_connection_changed", lambda *a, **k: None)
    monkeypatch.setattr(registry, "get_tool_config", lambda tool, key, default=None: config.get(key, default))
    monkeypatch.setattr(registry, "set_tool_config", lambda tool, key, value: config.update({key: value}))
    enabled = []
    monkeypatch.setattr(registry, "set_enabled", lambda tool, on: enabled.append((tool, on)))

    subscription_controls._connection_changed("codex")

    assert config.get("model") == after
    # Only when it set the model does it switch the image tool on too (T6).
    assert enabled == ([("image_gen", True)] if after and not saved else [])
