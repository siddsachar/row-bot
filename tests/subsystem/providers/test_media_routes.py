"""Providers with their own image and video APIs in the image and video tools (fake HTTP only)."""
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
    monkeypatch.setattr(image_gen_tool.time, "sleep", lambda seconds: None)
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


@pytest.mark.parametrize(("selection", "label"), [
    ("together/black-forest-labs/FLUX.1-schnell", "Together AI"),
    ("minimax/image-01", "MiniMax"),
    ("zai/glm-image", "Z.AI"),
    ("atlascloud/bytedance/seedream-v4", "Atlas Cloud"),
])
def test_providers_without_an_edit_api_say_so_instead_of_trying(media, monkeypatch, selection, label):
    media.image = selection
    monkeypatch.setattr(media.image_tool, "_resolve_image_source", lambda source: b"\x89PNG\r\n\x1a\nsource")

    result = media.image_tool._edit_image("make it night")

    assert result.startswith(f"{label} image models can only make new images here.")
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


def test_together_makes_a_video_at_a_resolution_and_ratio_and_fetches_its_link_without_the_key(media):
    """Each model makes its own frame of them: Seedance 1.0 Lite refused 1280×720 for 720p (I25)."""
    media.video = "together/ByteDance/Seedance-1.0-lite"
    media.answers[("POST", "/videos")] = [_Response({"id": "vid-1", "status": "queued"})]
    media.answers[("GET", "/videos/vid-1")] = [_Response({"status": "completed",
                                                          "outputs": {"video_url": "https://api.together.ai/shrt/abc"}})]
    media.answers[("GET", "/shrt/abc")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video("waves", duration_seconds=6, aspect_ratio="9:16", resolution="720p")

    assert result.endswith("Mode: text-to-video | Provider: Together AI")
    assert media.calls[0].url == "https://api.together.xyz/v2/videos"
    assert media.calls[0].json == {"model": "ByteDance/Seedance-1.0-lite", "prompt": "waves", "seconds": "6",
                                   "resolution": "720p", "ratio": "9:16"}
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
    monkeypatch.setattr(api_keys, "get_key", lambda env: "synthetic" if env in {"OPENROUTER_API_KEY", "REQUESTY_API_KEY"} else "")

    assert set(get_available_image_models()) == {"openrouter/bytedance-seed/seedream-4.5", "requesty/openai/gpt-image-2"}
    assert set(get_available_video_models()) == {"openrouter/google/veo-3.1"}


def test_minimax_makes_an_image_and_its_in_body_error_reaches_the_answer(media):
    media.image = "minimax/image-01"
    media.answers[("POST", "/image_generation")] = [
        _Response({"data": {"image_base64": [PNG]}, "base_resp": {"status_code": 0, "status_msg": "success"}}),
        _Response({"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}),
    ]

    assert media.image_tool._generate_image("a lighthouse", size="1536x1024").endswith("Provider: MiniMax")
    assert media.calls[0].url == "https://api.minimax.io/v1/image_generation"
    assert media.calls[0].json == {"model": "image-01", "prompt": "a lighthouse", "n": 1,
                                   "response_format": "base64", "aspect_ratio": "3:2"}
    assert media.image_tool._generate_image("a lighthouse") == (
        "Image generation failed: MiniMax error 1008: insufficient balance")


def test_zai_makes_an_image_at_its_nearest_size_and_downloads_it(media):
    media.image = "zai/glm-image"
    media.answers[("POST", "/images/generations")] = [_Response({"data": [{"url": "https://cdn.z.ai/out/1.png"}]})]
    media.answers[("GET", "/out/1.png")] = [_Response(content=b"\x89PNG\r\n\x1a\nz")]

    result = media.image_tool._generate_image("a lighthouse", size="1024x1536", quality="high")

    assert result == "Image generated successfully. Model: glm-image | Provider: Z.AI"
    assert media.calls[0].url == "https://api.z.ai/api/paas/v4/images/generations"
    assert media.calls[0].json == {"model": "glm-image", "prompt": "a lighthouse", "size": "1056x1568", "quality": "hd"}


def test_qwen_edits_an_image_sent_with_the_prompt(media, monkeypatch):
    media.image = "dashscope/qwen-image-2.0"
    monkeypatch.setattr(media.image_tool, "_resolve_image_source", lambda source: b"\x89PNG\r\n\x1a\nsource")
    media.answers[("POST", "/multimodal-generation/generation")] = [_Response({"output": {"choices": [
        {"message": {"content": [{"image": "https://dashscope-result.oss.example/1.png"}]}}]}})]
    media.answers[("GET", "/1.png")] = [_Response(content=b"\x89PNG\r\n\x1a\nq")]

    result = media.image_tool._edit_image("make it night", size="1024x1024")

    assert result == "Image edited successfully. Model: qwen-image-2.0 | Provider: Qwen (Alibaba Cloud)"
    call = media.calls[0]
    assert call.url == "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    content = call.json["input"]["messages"][0]["content"]
    assert content[0]["image"].startswith("data:image/png;base64,") and content[1] == {"text": "make it night"}
    assert call.json["parameters"] == {"n": 1, "size": "1024*1024"}
    assert "Authorization" not in media.calls[1].headers


def test_atlas_cloud_waits_for_its_image_job(media):
    media.image = "atlascloud/bytedance/seedream-v4"
    media.answers[("POST", "/model/generateImage")] = [_Response({"data": {"id": "pred-1"}})]
    media.answers[("GET", "/model/prediction/pred-1")] = [
        _Response({"data": {"status": "processing"}}),
        _Response({"data": {"status": "completed", "outputs": ["https://atlas.example/out.png"]}}),
    ]
    media.answers[("GET", "/out.png")] = [_Response(content=b"\x89PNG\r\n\x1a\na")]

    result = media.image_tool._generate_image("a lighthouse", size="1536x1024")

    assert result == "Image generated successfully. Model: bytedance/seedream-v4 | Provider: Atlas Cloud"
    assert media.calls[0].url == "https://api.atlascloud.ai/api/v1/model/generateImage"
    assert media.calls[0].json == {"model": "bytedance/seedream-v4", "prompt": "a lighthouse"}
    assert [call.url for call in media.calls].count("https://api.atlascloud.ai/api/v1/model/prediction/pred-1") == 2


def test_minimax_makes_a_video_at_the_settings_its_model_takes(media):
    media.video = "minimax/MiniMax-H3"
    media.answers[("POST", "/video_generation")] = [_Response({"task_id": "424"})]
    media.answers[("GET", "/query/video_generation/424")] = [
        _Response({"task": {"status": "running"}}),
        _Response({"task": {"status": "succeeded", "content": {"url": "https://cdn.minimax.example/v.mp4"}}}),
    ]
    media.answers[("GET", "/v.mp4")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video("waves", duration_seconds=20, aspect_ratio="3:2", resolution="1080p")

    assert result == ("Video generated successfully. Model: MiniMax-H3 | Duration: 15s | Aspect ratio: 16:9 | "
                      "Resolution: 2K | Mode: text-to-video | Provider: MiniMax")
    assert media.calls[0].url == "https://api.minimax.io/v2/video_generation"
    assert media.calls[0].json == {"model": "MiniMax-H3", "content": [{"type": "text", "text": "waves"}],
                                   "resolution": "2K", "duration": 15, "ratio": "16:9"}
    assert "Authorization" not in media.calls[-1].headers
    assert media.videos == [b"mp4-bytes"]


def test_minimax_animates_a_still_in_its_own_shape_and_says_why_a_job_failed(media):
    media.video = "minimax/MiniMax-H3-Highspeed"
    media.answers[("POST", "/video_generation")] = [_Response({"task_id": "425"})]
    media.answers[("GET", "/query/video_generation/425")] = [
        _Response({"task": {"status": "failed", "error": {"code": "1026", "message": "sensitive content"}}})]

    result = media.video_tool._generate_video_job("waves", 8, "16:9", "1080p", b"\x89PNG\r\n\x1a\nstill",
                                                  provider_id="minimax")

    assert result == "Video generation failed on MiniMax's side: sensitive content. Job: 425"
    body = media.calls[0].json
    assert (body["resolution"], body["ratio"]) == ("768P", "adaptive")
    assert body["content"][1]["role"] == "first_frame"
    assert body["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_zai_makes_a_video_at_its_nearest_size_and_length(media):
    media.video = "zai/cogvideox-3"
    media.answers[("POST", "/videos/generations")] = [_Response({"id": "zv-1", "task_status": "PROCESSING"})]
    media.answers[("GET", "/async-result/zv-1")] = [
        _Response({"task_status": "SUCCESS", "video_result": [{"url": "https://cdn.z.ai/v.mp4"}]})]
    media.answers[("GET", "/v.mp4")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video("waves", duration_seconds=8, aspect_ratio="9:16", resolution="4k")

    assert "Duration: 10s | Aspect ratio: 9:16 | Resolution: 1080x1920" in result
    assert media.calls[0].json == {"model": "cogvideox-3", "prompt": "waves", "size": "1080x1920", "duration": 10}
    assert media.videos == [b"mp4-bytes"]


@pytest.mark.parametrize(("model", "image", "task_input", "parameters"), [
    ("wan2.7-t2v", None, {"prompt": "waves"}, {"resolution": "720P", "duration": 8, "ratio": "9:16"}),
    ("wan2.6-t2v", None, {"prompt": "waves"}, {"size": "720*1280", "duration": 8}),
    ("wan2.2-i2v-plus", b"\x89PNG\r\n\x1a\nstill", {"prompt": "waves", "img_url": "data:image/png;base64,"},
     {"resolution": "720P", "duration": 8}),
])
def test_qwen_wan_video_jobs_take_each_generations_shape(media, model, image, task_input, parameters):
    media.video = f"dashscope/{model}"
    media.answers[("POST", "/video-synthesis")] = [_Response({"output": {"task_id": "t-1", "task_status": "PENDING"}})]
    media.answers[("GET", "/tasks/t-1")] = [_Response({"output": {"task_status": "RUNNING"}}),
                                            _Response({"output": {"task_status": "SUCCEEDED",
                                                                  "video_url": "https://oss.example/v.mp4"}})]
    media.answers[("GET", "/v.mp4")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video_job("waves", 8, "9:16", "720p", image, provider_id="dashscope")

    assert result.endswith("Provider: Qwen (Alibaba Cloud)")
    call = media.calls[0]
    assert call.url == "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    assert call.headers["X-DashScope-Async"] == "enable"
    sent = dict(call.json["input"])
    if "img_url" in sent:
        sent["img_url"] = sent["img_url"][:len("data:image/png;base64,")]
    assert (sent, call.json["parameters"]) == (task_input, parameters)
    assert media.videos == [b"mp4-bytes"]


def test_a_failed_qwen_video_job_says_why(media):
    media.video = "dashscope/wan2.7-t2v"
    media.answers[("POST", "/video-synthesis")] = [_Response({"output": {"task_id": "t-2"}})]
    media.answers[("GET", "/tasks/t-2")] = [_Response({"output": {"task_status": "FAILED", "message": "Arrearage"}})]

    assert media.video_tool._generate_video("waves") == (
        "Video generation failed on Qwen (Alibaba Cloud)'s side: Arrearage. Job: t-2")


def test_atlas_cloud_makes_videos_from_text_with_the_models_own_settings(media):
    media.video = "atlascloud/kwaivgi/kling-v2.1-t2v-master"
    media.answers[("POST", "/model/generateVideo")] = [_Response({"data": {"id": "pred-2"}})]
    media.answers[("GET", "/model/prediction/pred-2")] = [
        _Response({"data": {"status": "completed", "outputs": ["https://atlas.example/v.mp4"]}})]
    media.answers[("GET", "/v.mp4")] = [_Response(content=b"mp4-bytes")]

    result = media.video_tool._generate_video("waves")

    assert result == ("Video generated successfully. Model: kwaivgi/kling-v2.1-t2v-master | Duration, aspect ratio "
                      "and resolution: the model's own | Mode: text-to-video | Provider: Atlas Cloud")
    assert media.calls[0].json == {"model": "kwaivgi/kling-v2.1-t2v-master", "prompt": "waves"}
    assert media.video_tool._generate_video_job("waves", 8, "16:9", "720p", b"\x89PNG", provider_id="atlascloud") == (
        "Atlas Cloud video models can only make videos from text here. "
        "Choose another video model in Settings › Models to animate an image.")


def test_minimax_and_zai_media_models_join_their_lists_for_the_pickers_and_the_tools(monkeypatch):
    """I24: neither provider's model list names its image or video models, so the Settings pickers, which list
    only the catalog, never offered them. Their refresh now adds the ones their docs name. Fake HTTP only."""
    import httpx

    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.model_catalog import build_saved_model_catalog_rows
    from row_bot.providers.presets import PRESETS, models_url
    from row_bot.tools.image_gen_tool import get_available_image_models
    from row_bot.tools.video_gen_tool import get_available_video_models

    lists = {f"{models.MINIMAX_ANTHROPIC_BASE_URL}/v1/models": {"data": [{"id": "MiniMax-M3"}], "has_more": False},
             models_url(PRESETS["zai"]): {"data": [{"id": "glm-5.2"}]}}
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: _Response(lists[url]))
    monkeypatch.setattr(models, "_cloud_model_cache", {})
    keys = {"MINIMAX_API_KEY", "ZAI_API_KEY"}
    monkeypatch.setattr(api_keys, "get_key", lambda env: "synthetic" if env in keys else "")
    # Before a refresh nothing is offered: the catalog is the one list.
    assert get_available_image_models() == {} and get_available_video_models() == {}

    models._fetch_minimax_models("synthetic")
    models._fetch_preset_models("zai", "synthetic")

    rows = build_saved_model_catalog_rows(cloud_cache=dict(models._cloud_model_cache), ollama_rows=[],
                                          provider_config={})
    by_surface = {surface: {row.selection_ref for row in rows if surface in row.categories}
                  for surface in ("chat", "image", "video")}
    assert by_surface["image"] == {"model:minimax:image-01", "model:zai:glm-image"}
    assert by_surface["video"] == {"model:minimax:MiniMax-H3", "model:minimax:MiniMax-H3-Highspeed",
                                   "model:minimax:MiniMax-H3-Lite", "model:minimax:MiniMax-H3-Max",
                                   "model:zai:cogvideox-3"}
    assert by_surface["chat"] >= {"model:minimax:MiniMax-M3", "model:zai:glm-5.2"}
    assert not by_surface["chat"] & (by_surface["image"] | by_surface["video"])
    assert set(get_available_image_models()) == {"minimax/image-01", "zai/glm-image"}
    assert "zai/cogvideox-3" in get_available_video_models()

    keys.clear()
    assert get_available_image_models() == {} and get_available_video_models() == {}


def test_qwen_and_atlas_cloud_media_models_come_from_their_own_lists(monkeypatch):
    import httpx

    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.tools.image_gen_tool import get_available_image_models
    from row_bot.tools.video_gen_tool import get_available_video_models

    lists = {
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models": {"data": [{"id": "qwen3.8-max"}]},
        "https://dashscope-intl.aliyuncs.com/api/v1/models": {"output": {"total": 3, "models": [
            {"model": "qwen-image-2.0", "name": "Qwen-Image-2.0",
             "inference_metadata": {"request_modality": ["Text"], "response_modality": ["Image"]}},
            {"model": "wan2.7-t2v", "inference_metadata": {"response_modality": ["Video"]}},
            {"model": "qwen3-omni", "inference_metadata": {"response_modality": ["Text", "Audio"]}},
        ]}},
        "https://api.atlascloud.ai/v1/models": {"data": [{"id": "deepseek-ai/deepseek-v4", "context_length": 128_000}]},
        "https://api.atlascloud.ai/api/v1/models": {"code": "200", "data": [
            {"model": "bytedance/seedream-v4", "type": "Image", "displayName": "Seedream v4"},
            {"model": "kwaivgi/kling-v2.1-t2v-master", "type": "Video"},
            {"model": "hidden/video", "type": "Video", "display_console": False},
            {"model": "minimax/speech-2.6", "type": "Audio"},
        ]},
    }
    seen = []

    def get(url, **kwargs):
        seen.append((url, kwargs.get("params")))
        return _Response(lists[url])

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(api_keys, "get_key", lambda env: "synthetic" if env in {"DASHSCOPE_API_KEY", "ATLASCLOUD_API_KEY"} else "")
    monkeypatch.setattr(models, "_cloud_model_cache", {})

    models._fetch_preset_models("dashscope", "synthetic")
    models._fetch_atlascloud_models("synthetic")

    assert ("https://dashscope-intl.aliyuncs.com/api/v1/models",
            {"capabilities": ["IG", "VG"], "page_no": 1, "page_size": 100}) in seen
    assert get_available_image_models() == {
        "atlascloud/bytedance/seedream-v4": "AC  Seedream v4  (Atlas Cloud)",
        "dashscope/qwen-image-2.0": "QW  Qwen-Image-2.0  (Qwen (Alibaba Cloud))",
    }
    assert set(get_available_video_models()) == {"atlascloud/kwaivgi/kling-v2.1-t2v-master", "dashscope/wan2.7-t2v"}


OPENROUTER_LISTS = {
    "/models?output_modalities=all": {"data": [
        {"id": "google/veo-3.1", "name": "Veo 3.1", "architecture": {"output_modalities": ["video"]}},
        {"id": "kwaivgi/kling-v3", "name": "Kling v3", "architecture": {"output_modalities": ["video"]}},
        {"id": "bytedance-seed/seedream-4.5", "name": "Seedream 4.5", "architecture": {"output_modalities": ["image"]}},
        {"id": "google/gemini-3.1-flash-image-preview", "name": "Gemini 3.1 Flash Image",
         "architecture": {"output_modalities": ["image", "text"]}},
    ]},
    "/videos/models": {"data": [
        {"id": "google/veo-3.1", "supported_durations": [4, 6, 8], "supported_resolutions": ["720p", "1080p"],
         "supported_aspect_ratios": ["16:9", "9:16"], "supported_sizes": None,
         "supported_frame_images": ["first_frame", "last_frame"]},
        {"id": "kwaivgi/kling-v3", "supported_durations": [5, 10], "supported_resolutions": None,
         "supported_aspect_ratios": None, "supported_sizes": ["1280x720", "720x1280", "1920x1080"]},
    ]},
    "/images/models": {"data": [
        {"id": "bytedance-seed/seedream-4.5",
         "supported_parameters": {"resolution": {"type": "enum", "values": ["1K", "2K", "4K"]},
                                  "seed": {"type": "boolean"}}},
        {"id": "google/gemini-3.1-flash-image-preview",
         "supported_parameters": {"aspect_ratio": {"type": "enum", "values": ["1:1", "16:9", "9:16", "4:3", "3:4"]},
                                  "quality": {"type": "enum", "values": ["low", "high"]}}},
    ]},
}


@pytest.fixture
def openrouter_listed(media, monkeypatch):
    """OpenRouter's catalogue refreshed from fake lists, its media models chosen in the tools."""
    import row_bot.models as models
    import row_bot.providers.auth_store as auth_store

    monkeypatch.setattr(models, "_cloud_model_cache", {})
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider: "synthetic")

    def refresh(lists):
        for suffix, payload in lists.items():
            media.answers[("GET", suffix)] = [payload if isinstance(payload, _Response) else _Response(payload)]
        models._fetch_cloud_models("openrouter")
        media.calls.clear()

    return refresh


def test_openrouter_videos_are_made_at_the_nearest_settings_their_model_lists(media, openrouter_listed):
    from row_bot.tools.video_gen_tool import get_available_video_models

    openrouter_listed(OPENROUTER_LISTS)
    media.answers[("POST", "/videos")] = [_Response({"id": "gen-vid-3"})]
    media.answers[("GET", "/videos/gen-vid-3")] = [_Response({"status": "completed"})]
    media.answers[("GET", "/videos/gen-vid-3/content?index=0")] = [_Response(content=b"mp4-bytes")]

    assert {"openrouter/google/veo-3.1", "openrouter/kwaivgi/kling-v3"} <= set(get_available_video_models())

    media.video = "openrouter/google/veo-3.1"
    result = media.video_tool._generate_video("waves", duration_seconds=20, aspect_ratio="4:3", resolution="4k")

    assert "Duration: 8s | Aspect ratio: 16:9 | Resolution: 1080p" in result
    assert {key: media.calls[0].json.get(key) for key in ("duration", "aspect_ratio", "resolution", "size")} == {
        "duration": 8, "aspect_ratio": "16:9", "resolution": "1080p", "size": None}

    media.calls.clear()
    media.video = "openrouter/kwaivgi/kling-v3"
    result = media.video_tool._generate_video("waves", duration_seconds=6, aspect_ratio="9:16", resolution="720p")

    # A model listed by size takes the size of the shape and resolution asked for, and its own ratio.
    assert "Duration: 5s | Aspect ratio: the model's own | Resolution: 720x1280" in result
    assert {key: media.calls[0].json.get(key) for key in ("duration", "aspect_ratio", "resolution", "size")} == {
        "duration": 5, "aspect_ratio": None, "resolution": None, "size": "720x1280"}


def test_openrouter_images_send_only_the_settings_their_model_lists(media, openrouter_listed):
    from row_bot.providers.capabilities import snapshot_supports_surface
    from row_bot.providers.capability_resolution import resolve_capability_snapshot

    openrouter_listed(OPENROUTER_LISTS)
    media.answers[("POST", "/images")] = [_Response({"data": [{"b64_json": PNG}]})]

    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.image_tool._generate_image("a lighthouse", size="1536x1024", quality="high")
    # Listed by resolution tier only: high quality is its 2K, and it takes no ratio.
    assert media.calls[0].json == {"model": "bytedance-seed/seedream-4.5", "prompt": "a lighthouse", "n": 1,
                                   "resolution": "2K"}

    media.calls.clear()
    media.image = "openrouter/google/gemini-3.1-flash-image-preview"
    media.image_tool._generate_image("a lighthouse", size="1536x1024", quality="medium")
    assert media.calls[0].json == {"model": "google/gemini-3.1-flash-image-preview", "prompt": "a lighthouse", "n": 1,
                                   "aspect_ratio": "4:3"}

    # The settings a model lists don't change what it does: this one still chats as well.
    snapshot = resolve_capability_snapshot("openrouter", "google/gemini-3.1-flash-image-preview",
                                           include_static_fallback=False)
    assert {surface for surface in ("chat", "image", "video") if snapshot_supports_surface(snapshot, surface)} == {
        "chat", "image"}


def test_openrouter_image_and_video_models_are_the_ones_its_media_lists_name(media, openrouter_listed):
    """I26: the chat list says Auto Router draws, but /images takes only the models /images/models names."""
    import row_bot.models as models
    from row_bot.providers.capabilities import snapshot_supports_surface
    from row_bot.tools.image_gen_tool import get_available_image_models
    from row_bot.tools.video_gen_tool import get_available_video_models

    listed = OPENROUTER_LISTS["/models?output_modalities=all"]["data"]
    openrouter_listed({**OPENROUTER_LISTS, "/models?output_modalities=all": {"data": [
        *listed,
        {"id": "openrouter/auto", "name": "Auto Router", "architecture": {"output_modalities": ["image", "text"]}},
        {"id": "acme/unlisted-video", "name": "Unlisted", "architecture": {"output_modalities": ["video"]}},
    ]}})

    assert set(get_available_image_models()) == {"openrouter/bytedance-seed/seedream-4.5",
                                                 "openrouter/google/gemini-3.1-flash-image-preview"}
    assert set(get_available_video_models()) == {"openrouter/google/veo-3.1", "openrouter/kwaivgi/kling-v3"}
    auto = models._cloud_model_cache["openrouter/auto"]["capabilities_snapshot"]
    assert {surface for surface in ("chat", "image") if snapshot_supports_surface(auto, surface)} == {"chat"}
    # A video model neither list names can't be made here at all.
    assert "acme/unlisted-video" not in models._cloud_model_cache


def test_openrouter_media_models_keep_the_settings_asked_for_when_its_settings_lists_fail(media, openrouter_listed):
    from row_bot.tools.video_gen_tool import get_available_video_models

    openrouter_listed({**OPENROUTER_LISTS, "/videos/models": _Response({}, status_code=500),
                       "/images/models": _Response({}, status_code=500)})
    media.answers[("POST", "/videos")] = [_Response({"id": "gen-vid-4"})]
    media.answers[("GET", "/videos/gen-vid-4")] = [_Response({"status": "completed"})]
    media.answers[("GET", "/videos/gen-vid-4/content?index=0")] = [_Response(content=b"mp4-bytes")]

    assert "openrouter/google/veo-3.1" in get_available_video_models()

    media.video = "openrouter/google/veo-3.1"
    result = media.video_tool._generate_video("waves", duration_seconds=20, aspect_ratio="4:3", resolution="4k")

    assert "Duration: 20s | Aspect ratio: 4:3 | Resolution: 4k" in result
    assert {key: media.calls[0].json[key] for key in ("duration", "aspect_ratio", "resolution")} == {
        "duration": 20, "aspect_ratio": "4:3", "resolution": "4k"}


def test_a_key_saved_in_the_provider_panel_reaches_the_image_and_video_tools(media, monkeypatch):
    # The 5.3.3 provider panel keeps a key per provider, not under the old key name; on 9 Nov Qwen,
    # Together and Vercel images failed "No API key" while chat on them worked.
    import row_bot.api_keys as api_keys
    import row_bot.providers.auth_store as auth_store

    monkeypatch.setattr(api_keys, "get_key", lambda env: "")
    monkeypatch.setattr(auth_store, "_get_provider_secret_value",
                        lambda provider, name="api_key": f"panel-{provider}" if name == "api_key" else "")
    provider = "together"
    media.image = "together/black-forest-labs/FLUX.1-schnell"
    media.answers[("POST", "/images/generations")] = [_Response({"data": [{"b64_json": PNG}]})]

    result = media.image_tool._generate_image("a red bicycle by a canal")

    assert "No API key" not in result
    assert media.calls[0].headers["Authorization"] == f"Bearer panel-{provider}"
    media.calls.clear()
    media.video = "together/minimax/hailuo-02"
    media.answers[("POST", "/videos")] = [_Response({"id": "vid-2", "status": "queued"})]
    media.answers[("GET", "/videos/vid-2")] = [_Response({"status": "completed",
                                                          "outputs": {"video_url": "https://api.together.ai/v"}})]
    media.answers[("GET", "/v")] = [_Response(content=b"mp4")]

    media.video_tool._generate_video("waves", duration_seconds=6, aspect_ratio="16:9", resolution="720p")

    assert media.calls[0].headers["Authorization"] == f"Bearer panel-{provider}"
