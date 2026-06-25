from __future__ import annotations


def test_requesty_is_registered_in_provider_catalog():
    from row_bot.providers.catalog import PROVIDER_DEFINITIONS, get_provider_definition

    assert "requesty" in PROVIDER_DEFINITIONS
    definition = get_provider_definition("requesty")
    assert definition is not None
    assert definition.id == "requesty"
    assert definition.display_name == "Requesty"
    assert definition.base_url == "https://router.requesty.ai/v1"


def test_requesty_api_key_env_mapping():
    from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV

    assert PROVIDER_API_KEY_ENV.get("requesty") == "REQUESTY_API_KEY"


def test_requesty_listed_among_configured_provider_ids(monkeypatch):
    import row_bot.providers.runtime as runtime

    # list_configured_provider_ids() only returns providers whose key is set,
    # so configure a Requesty key and assert it surfaces.
    monkeypatch.setattr(
        runtime,
        "is_provider_available",
        lambda provider_id: provider_id == "requesty",
    )

    assert "requesty" in runtime.list_configured_provider_ids()


def test_requesty_runtime_builds_openai_compatible_client(monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id: "requesty-key")

    model = runtime.create_chat_model("openai/gpt-4o-mini", provider_id="requesty")

    assert type(model).__name__ == "ChatOpenAICompatible"
    assert model.model_name == "openai/gpt-4o-mini"
    assert model.base_url == "https://router.requesty.ai/v1"
    assert model.endpoint["provider_id"] == "requesty"
    assert model.endpoint["profile"] == "requesty"


def test_requesty_runtime_requires_a_key(monkeypatch):
    import pytest

    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id: "")

    with pytest.raises(ValueError):
        runtime.create_chat_model("openai/gpt-4o-mini", provider_id="requesty")


def test_requesty_capabilities_use_boolean_fields():
    from row_bot.providers.catalog import classify_model_capabilities

    tool_and_vision = classify_model_capabilities(
        "requesty",
        "openai/gpt-4o",
        {"supports_tool_calling": True, "supports_vision": True},
    )
    no_tools = classify_model_capabilities(
        "requesty",
        "deepseek/deepseek-chat",
        {"supports_tool_calling": False, "supports_vision": False},
    )

    assert tool_and_vision["tool_calling"] is True
    assert "image" in tool_and_vision["input_modalities"]
    assert no_tools["tool_calling"] is False
