from types import SimpleNamespace


def test_openai_fetch_keeps_new_image_generation_model(monkeypatch):
    import row_bot.models as models
    import row_bot.api_keys as api_keys

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "gpt-image-2"},
                    {"id": "text-embedding-3-large"},
                ]
            }

    fake_httpx = SimpleNamespace(get=lambda *args, **kwargs: _Response())
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)
    monkeypatch.setattr(api_keys, "get_key", lambda key: "sk-test" if key == "OPENAI_API_KEY" else "")
    models._cloud_model_cache.clear()

    count = models.fetch_cloud_models("openai")

    assert count == 1
    assert "gpt-image-2" in models._cloud_model_cache
    snapshot = models._cloud_model_cache["gpt-image-2"]["capabilities_snapshot"]
    assert snapshot["tasks"] == ["image_generation"]
    assert snapshot["output_modalities"] == ["image"]
    assert "text-embedding-3-large" not in models._cloud_model_cache


def test_openai_fetch_accepts_new_gpt5_family_models_without_an_allowlist(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "gpt-5.6-sol"},
                    {"id": "gpt-5.6-terra"},
                    {"id": "gpt-5.6-luna"},
                ]
            }

    fake_httpx = SimpleNamespace(get=lambda *args, **kwargs: _Response())
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)
    monkeypatch.setattr(api_keys, "get_key", lambda key: "sk-test" if key == "OPENAI_API_KEY" else "")
    models._cloud_model_cache.clear()

    count = models.fetch_cloud_models("openai")

    assert count == 3
    assert {model_id for model_id in models._cloud_model_cache if model_id.startswith("gpt-5.6-")} == {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    }
    assert all(
        models._cloud_model_cache[model_id]["transport"] == "openai_responses"
        for model_id in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
    )


def test_dynamic_provider_image_model_appears_in_image_options(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "sk-test" if key == "OPENAI_API_KEY" else "")
    info = model_info_from_metadata("openai", "gpt-image-2", display_name="GPT Image 2")
    models._cloud_model_cache.clear()
    models._cloud_model_cache["gpt-image-2"] = model_info_to_cache_entry(info)

    options = get_available_image_models()

    assert options == {"openai/gpt-image-2": "⬡  GPT Image 2  (OpenAI)"}


def test_dynamic_provider_video_model_appears_in_video_options(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.video_gen_tool import get_available_video_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "test" if key == "GOOGLE_API_KEY" else "")
    info = model_info_from_metadata("google", "veo-4.0-generate-preview", display_name="Veo 4")
    models._cloud_model_cache.clear()
    models._cloud_model_cache["veo-4.0-generate-preview"] = model_info_to_cache_entry(info)

    options = get_available_video_models()

    assert "google/veo-4.0-generate-preview" in options
    assert "Veo 4" in options["google/veo-4.0-generate-preview"]
    assert options["google/veo-4.0-generate-preview"] == "💎  Veo 4  (Google)"


def test_google_nano_banana_catalog_entry_appears_in_image_options(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "test" if key == "GOOGLE_API_KEY" else "")
    info = model_info_from_metadata("google", "gemini-3.1-flash-image-preview", display_name="Nano Banana 2")
    models._cloud_model_cache.clear()
    models._cloud_model_cache["gemini-3.1-flash-image-preview"] = model_info_to_cache_entry(info)

    options = get_available_image_models()

    assert "google/gemini-3.1-flash-image-preview" in options
    assert options["google/gemini-3.1-flash-image-preview"] == "💎  Nano Banana 2  (Google)"


def test_xai_imagine_catalog_entry_appears_in_image_options(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "test" if key == "XAI_API_KEY" else "")
    info = model_info_from_metadata("xai", "grok-imagine-image", display_name="Grok Imagine")
    models._cloud_model_cache.clear()
    models._cloud_model_cache["grok-imagine-image"] = model_info_to_cache_entry(info)

    options = get_available_image_models()

    # Only what xAI's list has: the quality model isn't offered until xAI lists it.
    assert options == {"xai/grok-imagine-image": "𝕏  Grok Imagine  (xAI)"}


def test_xai_oauth_media_options_require_oauth_runtime(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    import row_bot.providers.xai_oauth as xai_oauth
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models
    from row_bot.tools.video_gen_tool import get_available_video_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "")
    monkeypatch.setattr(xai_oauth, "xai_oauth_runtime_available", lambda *, refresh_if_needed=False: False)
    models._cloud_model_cache.clear()
    for model_id, label in (("grok-imagine-image", "Grok Imagine"), ("grok-imagine-image-quality", "Grok Imagine Quality"),
                            ("grok-imagine-video", "Grok Imagine Video")):
        info = model_info_from_metadata("xai_oauth", model_id, display_name=label)
        models._cloud_model_cache[info.selection_ref] = model_info_to_cache_entry(info)

    assert "xai_oauth/grok-imagine-image" not in get_available_image_models()
    assert "xai_oauth/grok-imagine-video" not in get_available_video_models()

    monkeypatch.setattr(xai_oauth, "xai_oauth_runtime_available", lambda *, refresh_if_needed=False: True)

    image_options = get_available_image_models()
    video_options = get_available_video_models()

    assert "xai_oauth/grok-imagine-image" in image_options
    assert "xai_oauth/grok-imagine-image-quality" in image_options
    assert image_options["xai_oauth/grok-imagine-image"] == "X  Grok Imagine  (xAI Grok)"
    assert "xai_oauth/grok-imagine-video" in video_options
    assert video_options["xai_oauth/grok-imagine-video"] == "X  Grok Imagine Video  (xAI Grok)"
    assert "xai/grok-imagine-image" not in image_options
    assert "xai/grok-imagine-video" not in video_options


def test_xai_api_key_media_options_stay_separate_from_oauth(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    import row_bot.providers.xai_oauth as xai_oauth
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.image_gen_tool import get_available_image_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "xai-api-key" if key == "XAI_API_KEY" else "")
    monkeypatch.setattr(xai_oauth, "xai_oauth_runtime_available", lambda *, refresh_if_needed=False: False)
    models._cloud_model_cache.clear()
    models._cloud_model_cache["grok-imagine-image"] = model_info_to_cache_entry(
        model_info_from_metadata("xai", "grok-imagine-image", display_name="Grok Imagine"))

    options = get_available_image_models()

    assert "xai/grok-imagine-image" in options
    assert "xai_oauth/grok-imagine-image" not in options
