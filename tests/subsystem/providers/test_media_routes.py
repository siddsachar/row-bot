"""OpenRouter, Vercel AI Gateway, Together AI and Requesty in the image and video tools (fake HTTP only)."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii")


class _Response:
    def __init__(self, payload=None, *, status_code=200, content=b""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.content = content
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def media(monkeypatch):
    """Both tools on a chosen provider/model, with a fake key, no disk writes and no waiting."""
    import httpx

    import row_bot.api_keys as api_keys
    from row_bot.tools import image_gen_tool, video_gen_tool

    state = SimpleNamespace(image="", video="", calls=[], answers={}, image_tool=image_gen_tool,
                            video_tool=video_gen_tool, videos=[])

    def answer(method, url, **kwargs):
        state.calls.append(SimpleNamespace(method=method, url=url, json=kwargs.get("json"),
                                           headers=dict(kwargs.get("headers") or {})))
        for (want_method, suffix), responses in state.answers.items():
            if method == want_method and url.endswith(suffix):
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: answer("POST", url, **kwargs))
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: answer("GET", url, **kwargs))
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: answer(method, url, **kwargs))
    monkeypatch.setattr(api_keys, "get_key", lambda env: f"synthetic-{env.lower()}")
    monkeypatch.setattr(image_gen_tool.registry, "get_tool_config",
                        lambda tool, *args: state.image if tool == "image_gen" else state.video)
    monkeypatch.setattr(image_gen_tool, "_save_image_to_disk", lambda *args, **kwargs: None)
    monkeypatch.setattr(video_gen_tool, "_save_video_to_disk", lambda data, *args: state.videos.append(data) or None)
    monkeypatch.setattr(video_gen_tool.time, "sleep", lambda seconds: None)
    return state


def test_openrouter_makes_an_image_through_its_images_api(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.answers[("POST", "/images")] = [_Response({"data": [{"b64_json": PNG, "media_type": "image/png"}]})]

    result = media.image_tool._generate_image("a lighthouse at dawn", size="1536x1024", quality="high")

    assert result == "Image generated successfully. Model: bytedance-seed/seedream-4.5 | Provider: OpenRouter"
    call = media.calls[0]
    assert call.url == "https://openrouter.ai/api/v1/images"
    assert call.headers["Authorization"] == "Bearer synthetic-openrouter_api_key"
    assert call.json == {"model": "bytedance-seed/seedream-4.5", "prompt": "a lighthouse at dawn", "n": 1,
                         "aspect_ratio": "3:2", "quality": "high"}


def test_openrouter_edits_an_image_sent_as_a_reference(media, monkeypatch):
    media.image = "openrouter/google/gemini-3.1-flash-image-preview"
    monkeypatch.setattr(media.image_tool, "_resolve_image_source", lambda source: b"\x89PNG\r\n\x1a\nsource")
    media.answers[("POST", "/images")] = [_Response({"data": [{"b64_json": PNG}]})]

    result = media.image_tool._edit_image("make it night", image_source="last")

    assert result.startswith("Image edited successfully.")
    reference = media.calls[0].json["input_references"][0]
    assert reference["type"] == "image_url"
    assert reference["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_provider_error_reaches_the_answer(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.answers[("POST", "/images")] = [_Response({"error": {"message": "Insufficient credits"}}, status_code=402)]

    assert media.image_tool._generate_image("a lighthouse") == "Image generation failed: HTTP 402: Insufficient credits"


def test_together_makes_an_image_with_width_and_height(media):
    media.image = "together/black-forest-labs/FLUX.1-schnell"
    media.answers[("POST", "/images/generations")] = [_Response({"data": [{"index": 0, "b64_json": PNG, "type": "b64_json"}]})]

    result = media.image_tool._generate_image("a lighthouse", size="1024x1536")

    assert result.endswith("Provider: Together AI")
    assert media.calls[0].url == "https://api.together.xyz/v1/images/generations"
    assert media.calls[0].json == {"model": "black-forest-labs/FLUX.1-schnell", "prompt": "a lighthouse", "n": 1,
                                   "response_format": "base64", "width": 1024, "height": 1536}


def test_together_says_it_cannot_edit_instead_of_trying(media, monkeypatch):
    media.image = "together/black-forest-labs/FLUX.1-schnell"
    monkeypatch.setattr(media.image_tool, "_resolve_image_source", lambda source: b"\x89PNG\r\n\x1a\nsource")

    result = media.image_tool._edit_image("make it night")

    assert result.startswith("Together AI image models can only make new images here.")
    assert media.calls == []


@pytest.mark.parametrize(("provider", "base_url"), [
    ("vercel", "https://ai-gateway.vercel.sh/v1"),
    ("requesty", "https://router.requesty.ai/v1"),
])
def test_openai_shaped_providers_use_the_openai_images_api_at_their_own_address(media, monkeypatch, provider, base_url):
    import sys

    clients, requests = [], []

    def sdk(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(images=SimpleNamespace(
            generate=lambda **request: requests.append(request) or SimpleNamespace(data=[SimpleNamespace(b64_json=PNG)])))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=sdk))
    media.image = f"{provider}/openai/gpt-image-2"

    result = media.image_tool._generate_image("a lighthouse")

    assert result.startswith("Image generated successfully. Model: openai/gpt-image-2 | Size: auto")
    assert clients == [{"api_key": f"synthetic-{'ai_gateway' if provider == 'vercel' else 'requesty'}_api_key",
                        "base_url": base_url}]
    # No size unless one was asked for: these gateways serve models with their own sizes.
    assert requests == [{"model": "openai/gpt-image-2", "prompt": "a lighthouse", "n": 1}]


def test_openrouter_makes_a_video_from_a_still_and_downloads_it(media):
    media.video = "openrouter/google/veo-3.1"
    media.answers[("POST", "/videos")] = [_Response({"id": "gen-vid-1", "status": "pending"})]
    media.answers[("GET", "/videos/gen-vid-1")] = [_Response({"status": "in_progress"}), _Response({"status": "completed"})]
    media.answers[("GET", "/videos/gen-vid-1/content?index=0")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video_job("waves", 8, "16:9", "720p", b"\x89PNG\r\n\x1a\nstill",
                                                  provider_id="openrouter")

    assert result.startswith("Video generated successfully. Model: google/veo-3.1 | Duration: 8s")
    assert "Mode: image-to-video | Provider: OpenRouter" in result
    body = media.calls[0].json
    assert media.calls[0].url == "https://openrouter.ai/api/v1/videos"
    assert {key: body[key] for key in ("model", "prompt", "duration", "resolution", "aspect_ratio")} == {
        "model": "google/veo-3.1", "prompt": "waves", "duration": 8, "resolution": "720p", "aspect_ratio": "16:9"}
    assert body["frame_images"][0]["frame_type"] == "first_frame"
    assert media.calls[-1].headers["Authorization"] == "Bearer synthetic-openrouter_api_key"
    assert media.videos == [b"mp4-bytes"]


def test_a_failed_openrouter_video_job_says_why(media):
    media.video = "openrouter/google/veo-3.1"
    media.answers[("POST", "/videos")] = [_Response({"id": "gen-vid-2"})]
    media.answers[("GET", "/videos/gen-vid-2")] = [_Response({"status": "failed", "error": "Prompt was rejected"})]

    result = media.video_tool._generate_video("waves")

    assert result == "Video generation failed on OpenRouter's side: Prompt was rejected. Job: gen-vid-2"
    assert media.videos == []


def test_together_makes_a_video_and_fetches_its_link_without_the_key(media):
    media.video = "together/minimax/hailuo-02"
    media.answers[("POST", "/videos")] = [_Response({"id": "vid-1", "status": "queued"})]
    media.answers[("GET", "/videos/vid-1")] = [_Response({"status": "completed",
                                                          "outputs": {"video_url": "https://api.together.ai/shrt/abc"}})]
    media.answers[("GET", "/shrt/abc")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video("waves", duration_seconds=6, aspect_ratio="9:16", resolution="720p")

    assert result.endswith("Mode: text-to-video | Provider: Together AI")
    assert media.calls[0].url == "https://api.together.xyz/v2/videos"
    assert media.calls[0].json == {"model": "minimax/hailuo-02", "prompt": "waves", "seconds": "6",
                                   "width": 720, "height": 1280}
    assert "Authorization" not in media.calls[-1].headers
    assert media.videos == [b"mp4-bytes"]


def test_a_list_that_says_what_a_model_outputs_is_believed():
    from row_bot.providers.capabilities import snapshot_supports_surface
    from row_bot.providers.catalog import model_info_from_metadata

    def surfaces(model_id, outputs):
        info = model_info_from_metadata("openrouter", model_id, {"architecture": {"output_modalities": outputs}})
        return {surface for surface in ("chat", "image", "video") if snapshot_supports_surface(info.capability_snapshot(), surface)}

    assert surfaces("black-forest-labs/flux-2-pro", ["image"]) == {"image"}
    assert surfaces("google/gemini-3.1-flash-image-preview", ["image", "text"]) == {"chat", "image"}
    assert surfaces("bytedance/seedance-2", ["video"]) == {"video"}
    assert surfaces("openai/gpt-5.5", ["text"]) == {"chat"}
    assert surfaces("elevenlabs/eleven-v4", ["audio"]) == set()


def test_preset_lists_keep_image_and_video_models_only_where_the_tools_can_call_them():
    from row_bot.providers.capabilities import snapshot_supports_surface
    from row_bot.providers.presets import PRESETS, model_infos_from_catalog

    rows = {"data": [
        {"id": "black-forest-labs/FLUX.1-schnell", "type": "image"},
        {"id": "minimax/hailuo-02", "type": "video"},
        {"id": "meta-llama/Llama-4-Maverick", "type": "chat", "context_length": 1_048_576},
    ]}

    def jobs(provider):
        return {info.model_id: next(s for s in ("chat", "image", "video")
                                    if snapshot_supports_surface(info.capability_snapshot(), s))
                for info in model_infos_from_catalog(PRESETS[provider], rows)}

    assert jobs("together") == {"black-forest-labs/FLUX.1-schnell": "image", "minimax/hailuo-02": "video",
                                "meta-llama/Llama-4-Maverick": "chat"}
    # Vercel's video models are made only through its TypeScript SDK, so they aren't kept.
    assert jobs("vercel") == {"black-forest-labs/FLUX.1-schnell": "image", "meta-llama/Llama-4-Maverick": "chat"}
    assert jobs("groq") == {"meta-llama/Llama-4-Maverick": "chat"}


def test_new_providers_image_and_video_models_are_offered_once_connected(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models
    from row_bot.tools.video_gen_tool import get_available_video_models

    listed = [("openrouter", "bytedance-seed/seedream-4.5", ["image"]), ("openrouter", "google/veo-3.1", ["video"]),
              ("together", "black-forest-labs/FLUX.1-schnell", ["image"]), ("requesty", "openai/gpt-image-2", ["image"])]
    cache = {}
    for provider, model_id, outputs in listed:
        info = model_info_from_metadata(provider, model_id, {"output_modalities": outputs})
        cache[info.selection_ref] = model_info_to_cache_entry(info)
    monkeypatch.setattr(models, "_cloud_model_cache", cache)
    monkeypatch.setattr(api_keys, "get_key", lambda env: "" if env == "TOGETHER_API_KEY" else "synthetic")

    assert set(get_available_image_models()) == {"openrouter/bytedance-seed/seedream-4.5", "requesty/openai/gpt-image-2"}
    assert set(get_available_video_models()) == {"openrouter/google/veo-3.1"}
