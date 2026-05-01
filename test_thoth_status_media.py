def test_thoth_status_normalizes_dynamic_image_model_label(monkeypatch):
    import tools.image_gen_tool as image_gen_tool
    from tools.thoth_status_tool import _normalize_provider_model_value

    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {
        "openai/gpt-image-2": "⬡  GPT Image 2  (OpenAI)",
        "google/gemini-3-pro-image-preview": "💎  Nano Banana Pro  (Google)",
    })

    assert _normalize_provider_model_value("image_gen_model", "GPT Image 2") == "openai/gpt-image-2"
    assert _normalize_provider_model_value("image_gen_model", "gpt-image-2") == "openai/gpt-image-2"
    assert _normalize_provider_model_value("image_gen_model", "Nano Banana Pro") == "google/gemini-3-pro-image-preview"


def test_thoth_status_normalizes_dynamic_video_model_label(monkeypatch):
    import tools.video_gen_tool as video_gen_tool
    from tools.thoth_status_tool import _normalize_provider_model_value

    monkeypatch.setattr(video_gen_tool, "get_available_video_models", lambda: {
        "google/veo-3.1-generate-preview": "💎  Veo 3.1  (Google)",
        "xai/grok-imagine-video": "𝕏  Grok Imagine Video  (xAI)",
    })

    assert _normalize_provider_model_value("video_gen_model", "Veo 3.1") == "google/veo-3.1-generate-preview"
    assert _normalize_provider_model_value("video_gen_model", "grok-imagine-video") == "xai/grok-imagine-video"


def test_thoth_status_leaves_ambiguous_media_bare_id_unchanged(monkeypatch):
    import tools.image_gen_tool as image_gen_tool
    from tools.thoth_status_tool import _normalize_provider_model_value

    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {
        "openai/shared-image": "⬡  Shared Image  (OpenAI)",
        "custom_openai_lab/shared-image": "↔  Shared Image  (Lab)",
    })

    assert _normalize_provider_model_value("image_gen_model", "shared-image") == "shared-image"


def test_thoth_status_media_update_seeds_quick_choices(monkeypatch):
    import langgraph.types
    import providers.selection as provider_selection
    import tools.image_gen_tool as image_gen_tool
    import tools.registry as tool_registry
    from tools.thoth_status_tool import _update_setting

    calls = []
    monkeypatch.setattr(langgraph.types, "interrupt", lambda payload: True)
    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {
        "openai/gpt-image-2": "⬡  GPT Image 2  (OpenAI)",
    })
    monkeypatch.setattr(tool_registry, "set_tool_config", lambda tool, key, value: calls.append((tool, key, value)))
    monkeypatch.setattr(provider_selection, "seed_configured_media_quick_choices", lambda: calls.append(("seed", "media", "quick_choices")))

    result = _update_setting("image_gen_model", "GPT Image 2")

    assert result == "Image generation model set to: openai/gpt-image-2"
    assert ("image_gen", "model", "openai/gpt-image-2") in calls
    assert ("seed", "media", "quick_choices") in calls
