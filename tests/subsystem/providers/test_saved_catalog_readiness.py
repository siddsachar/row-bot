"""Agent readiness of saved catalog rows, the rules the model pickers show.

Tools only run on models that can call them: a chat model is agent-ready only
with tool metadata and at least a 32k context; otherwise it stays usable as
Chat Only, or is blocked when it cannot chat at all.
"""
from __future__ import annotations

import pytest

from row_bot.providers import client_status, config, model_catalog
from row_bot.providers import model_catalog_cache as cache
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint
from row_bot.providers.ollama import ollama_catalog_rows


pytestmark = pytest.mark.subsystem

CHAT_ONLY = "Chat Only: tools and actions are off."


@pytest.fixture
def providers(tmp_path, monkeypatch):
    """Isolated providers.json and a fixed local provider status."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "providers.json")
    statuses: dict[str, dict] = {}
    monkeypatch.setattr(model_catalog, "_provider_status_by_id", lambda: statuses)
    return statuses


def _chat(tool_calling, *, context=128_000, provider="openrouter"):
    return {
        "provider": provider,
        "label": "Synthetic",
        "ctx": context,
        "capabilities_snapshot": {
            "tasks": ["chat"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "tool_calling": tool_calling,
            "transport": "openai_chat",
        },
    }


def _assess(cloud=None, *, local=(), settings=None):
    rows = model_catalog.build_saved_model_catalog_rows(
        cloud_cache=cloud or {}, ollama_rows=list(local), provider_config=settings or {},
    )
    return {row.model_id: row for row in model_catalog.project_saved_catalog_readiness(rows)}


@pytest.mark.parametrize("tool_calling", [None, False])
def test_chat_model_without_tool_metadata_is_chat_only(providers, tool_calling):
    providers["openrouter"] = {"configured": True}

    row = _assess({"vendor/no-tools": _chat(tool_calling)})["vendor/no-tools"]

    assert row.supports("chat")
    assert row.runtime_ready is True
    assert row.runtime_mode == "chat_only"
    assert row.status_reason == CHAT_ONLY


def test_chat_model_with_tool_metadata_and_large_context_is_agent_ready(providers):
    providers["openrouter"] = {"configured": True}

    row = _assess({"vendor/tools": _chat(True)})["vendor/tools"]

    assert row.runtime_ready is True
    assert row.runtime_mode == "agent"
    assert row.status_reason == ""


@pytest.mark.parametrize(
    ("context", "ready", "mode", "reason"),
    [
        (31_999, True, "chat_only", CHAT_ONLY),
        (16_384, True, "chat_only", CHAT_ONLY),
        (16_383, False, "blocked", "Context window is too small for chat."),
        (32_000, True, "agent", ""),
    ],
)
def test_context_floors_decide_agent_and_chat(providers, context, ready, mode, reason):
    providers["openai"] = {"configured": True}

    row = _assess({"model:openai:tools": _chat(True, context=context, provider="openai")})["tools"]

    assert (row.runtime_ready, row.runtime_mode, row.status_reason) == (ready, mode, reason)


def test_unconnected_provider_is_not_ready(providers):
    row = _assess({"vendor/tools": _chat(True)})["vendor/tools"]

    assert row.configured is False
    assert row.runtime_ready is False
    assert row.runtime_mode == ""
    assert row.status_reason == "Connect this provider before using this model."


def test_image_generation_model_is_never_offered_for_chat(providers):
    providers["openai"] = {"configured": True}
    image = {
        "provider": "openai",
        "label": "Image",
        "capabilities_snapshot": {
            "tasks": ["image_generation"],
            "input_modalities": ["text"],
            "output_modalities": ["image"],
        },
    }

    row = _assess({"model:openai:image-only": image})["image-only"]

    assert row.supports("image")
    assert not row.supports("chat")
    assert row.runtime_mode == ""


def test_ollama_rows_are_ready_only_when_the_daemon_has_them(providers):
    providers["ollama"] = {"configured": True, "source": "local_daemon"}
    local = ollama_catalog_rows(
        [],
        ["gemma4:31b-cloud"],
        ready_cloud_offload={"gemma4:31b-cloud"},
        metadata_by_model={"gemma4:31b-cloud": {"context_window": 262_144, "capabilities": ["completion", "vision"]}},
    )
    missing = {**local[0], "model_id": "gemma4:missing", "installed": False}

    rows = _assess(local=[*local, missing])

    offload = rows["gemma4:31b-cloud"]
    assert offload.installed is True
    assert offload.runtime_ready is True
    assert offload.context_window == 262_144
    assert "vision" in offload.categories
    assert rows["gemma4:missing"].installed is False
    assert rows["gemma4:missing"].runtime_ready is False
    assert rows["gemma4:missing"].status_reason == "This model is not currently available from the provider."


def _custom_row(providers, *, context=65_536, probe=None, models=("model-a",)):
    provider_id = custom_provider_id("synthetic")
    providers[provider_id] = {"configured": True, "runtime_enabled": True}
    snapshot = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"], "transport": "openai_chat"}
    endpoint = {
        "id": "synthetic",
        "base_url": "http://127.0.0.1:1235/v1",
        "auth_required": False,
        "models": [
            {"model_id": model, "context_window": context, "capabilities_snapshot": snapshot} for model in models
        ],
    }
    if probe is not None:
        endpoint["last_probe"] = {"model_id": "model-a", **probe}
    save_custom_endpoint(endpoint)
    return _assess(settings=config.load_provider_config())


AGENT_PROBE = {"chat_ok": True, "tool_calling": True, "tool_round_trip": True}


def test_custom_endpoint_is_agent_ready_only_for_the_probed_model(providers):
    rows = _custom_row(providers, probe=AGENT_PROBE, models=("model-a", "model-b"))

    assert rows["model-a"].runtime_mode == "agent"
    assert rows["model-a"].status_reason == ""
    assert rows["model-b"].runtime_mode == "chat_only"


@pytest.mark.parametrize(
    ("probe", "ready", "mode", "reason"),
    [
        ({"chat_ok": True, "tool_calling": False}, True, "chat_only", "tool round trip"),
        ({"chat_ok": False}, False, "blocked", "did not verify chat"),
    ],
)
def test_custom_endpoint_probe_without_a_tool_round_trip_is_not_agent_ready(providers, probe, ready, mode, reason):
    row = _custom_row(providers, probe=probe)["model-a"]

    assert (row.runtime_ready, row.runtime_mode) == (ready, mode)
    assert reason in row.status_reason


def test_custom_endpoint_probe_cannot_lift_the_agent_context_floor(providers):
    row = _custom_row(providers, context=20_000, probe=AGENT_PROBE)["model-a"]

    assert row.runtime_ready is True
    assert row.runtime_mode == "chat_only"
    assert row.status_reason == CHAT_ONLY


def test_model_status_reports_the_same_readiness(providers, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    providers["openrouter"] = {"configured": True}
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        1, 1000, {"vendor/no-tools": _chat(None), "vendor/tools": _chat(True)}, [], {}, (), "test",
    ))

    page = client_status.list_cached_models(surface="chat", readiness=True, now=1000)
    by_id = {row.model_id: row for row in page.items}

    assert (by_id["vendor/no-tools"].runtime_mode, by_id["vendor/no-tools"].runtime_state) == ("chat_only", "ready")
    assert (by_id["vendor/tools"].runtime_mode, by_id["vendor/tools"].runtime_state) == ("agent", "ready")
