"""The image and video tools follow each provider's own model list; nothing is a fixed list of IDs."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem


def _listed(*models: tuple[str, str]) -> dict[str, dict]:
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry

    return {
        f"model:{provider}:{model}": model_info_to_cache_entry(model_info_from_metadata(provider, model))
        for provider, model in models
    }


@pytest.fixture
def image_tool(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.tools import image_gen_tool

    selection = {"value": ""}
    monkeypatch.setattr(api_keys, "get_key", lambda key: "synthetic-key")
    monkeypatch.setattr(image_gen_tool.registry, "get_tool_config", lambda *args: selection["value"])
    monkeypatch.setattr(image_gen_tool, "_save_image_to_disk", lambda *args, **kwargs: None)
    monkeypatch.setattr(models, "_cloud_model_cache", {})
    return SimpleNamespace(tool=image_gen_tool, models=models, selection=selection)


def test_a_new_imagen_model_is_sent_to_the_imagen_api(image_tool, monkeypatch):
    calls = []

    def generate_images(**request):
        calls.append(("generate_images", request["model"]))
        image = SimpleNamespace(image=SimpleNamespace(image_bytes=b"synthetic-image"))
        return SimpleNamespace(generated_images=[image])

    def generate_content(**request):
        calls.append(("generate_content", request["model"]))
        raise AssertionError("Imagen models don't take generate_content")

    client = SimpleNamespace(models=SimpleNamespace(generate_images=generate_images, generate_content=generate_content))
    monkeypatch.setattr(image_tool.tool, "_get_client", lambda: (client, "Google", "google"))
    image_tool.selection["value"] = "google/imagen-5.0-generate-001"

    result = image_tool.tool._generate_image("a lighthouse at dawn")

    assert calls == [("generate_images", "imagen-5.0-generate-001")]
    assert result.startswith("Image generated successfully. Model: imagen-5.0-generate-001")


def _failing_openai(monkeypatch, image_tool):
    def generate(**request):
        raise RuntimeError(f"The model {request['model']} does not exist")

    client = SimpleNamespace(images=SimpleNamespace(generate=generate))
    monkeypatch.setattr(image_tool.tool, "_get_client", lambda: (client, "OpenAI", "openai"))


def test_a_failed_request_says_when_the_provider_no_longer_lists_the_model(image_tool, monkeypatch):
    _failing_openai(monkeypatch, image_tool)
    image_tool.models._cloud_model_cache.update(_listed(("openai", "gpt-image-2"), ("openai", "gpt-5.5")))
    image_tool.selection["value"] = "openai/gpt-image-1"

    result = image_tool.tool._generate_image("a lighthouse at dawn")

    assert result == (
        "Image generation failed: The model gpt-image-1 does not exist "
        "OpenAI no longer lists gpt-image-1, so it may have been retired. "
        "Choose another image model in Settings › Models."
    )


def test_a_failed_request_for_a_listed_model_shows_only_the_error(image_tool, monkeypatch):
    _failing_openai(monkeypatch, image_tool)
    image_tool.models._cloud_model_cache.update(_listed(("openai", "gpt-image-2")))
    image_tool.selection["value"] = "openai/gpt-image-2"

    assert image_tool.tool._generate_image("a lighthouse") == "Image generation failed: The model gpt-image-2 does not exist"


def test_before_a_provider_lists_anything_nothing_is_called_retired(image_tool, monkeypatch):
    _failing_openai(monkeypatch, image_tool)
    image_tool.models._cloud_model_cache.update(_listed(("google", "imagen-4.0-generate-001")))
    image_tool.selection["value"] = "openai/gpt-image-1"

    assert image_tool.tool._generate_image("a lighthouse") == "Image generation failed: The model gpt-image-1 does not exist"


def test_a_failed_video_request_says_when_the_provider_no_longer_lists_the_model(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.tools import video_gen_tool

    def generate_videos(**request):
        raise RuntimeError(f"models/{request['model']} is not found")

    client = SimpleNamespace(models=SimpleNamespace(generate_videos=generate_videos))
    monkeypatch.setattr(api_keys, "get_key", lambda key: "synthetic-key")
    monkeypatch.setattr(video_gen_tool.registry, "get_tool_config", lambda *args: "google/veo-2.0-generate-001")
    monkeypatch.setattr(video_gen_tool, "_get_google_client", lambda: client)
    monkeypatch.setattr(models, "_cloud_model_cache", _listed(("google", "veo-3.1-generate-preview")))

    result = video_gen_tool._generate_video("waves on a beach")

    assert result.endswith(
        "Google no longer lists veo-2.0-generate-001, so it may have been retired. "
        "Choose another video model in Settings › Models."
    )
