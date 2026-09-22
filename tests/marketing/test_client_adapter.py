from pathlib import Path

import pytest

from scripts.marketing.capture_contract import load_manifest
from scripts.marketing.clients.base import ClientAdapter, ClientAdapterError
from scripts.marketing.clients.nicegui import NiceGuiAdapter


ROOT = Path(__file__).resolve().parents[2]


def test_adapter_contract_is_semantic_and_manifest_is_client_neutral() -> None:
    methods = {
        "open_conversation",
        "open_home_surface",
        "open_model_picker",
        "open_activity",
        "open_workflow",
        "open_designer_project",
        "open_approval",
        "await_settled_state",
        "capture_scene",
    }
    assert methods <= ClientAdapter.__abstractmethods__
    manifest_text = (ROOT / "scripts" / "marketing" / "landing_story.yml").read_text(
        encoding="utf-8"
    )
    assert "selector:" not in manifest_text
    assert "route:" not in manifest_text
    assert "docs_surface" not in manifest_text
    assert (
        len(load_manifest(ROOT / "scripts" / "marketing" / "landing_story.yml").scenes)
        == 9
    )


def test_capture_adapter_masks_hard_sensitive_ui_without_flattening_real_context() -> (
    None
):
    adapter_source = (
        ROOT / "scripts" / "marketing" / "clients" / "nicegui.py"
    ).read_text(encoding="utf-8")
    sidebar_source = (ROOT / "src" / "row_bot" / "ui" / "sidebar.py").read_text(
        encoding="utf-8"
    )
    graph_source = (ROOT / "src" / "row_bot" / "ui" / "graph_panel.py").read_text(
        encoding="utf-8"
    )

    assert "row-bot-capture-privacy" in adapter_source
    assert ".q-notification,[data-sensitive],[data-row-bot-secret]" in adapter_source
    assert "document.querySelectorAll('[data-thread-id]')" not in adapter_source
    assert "knowledge-entry-" in adapter_source
    assert "data-docs-id=sidebar-thread-filters" in sidebar_source
    assert "data-docs-id=sidebar-show-all" in sidebar_source
    assert "data-docs-id=sidebar-agent-profiles" in sidebar_source
    assert "marketing_capture_knowledge_ids" in graph_source
    assert '"edges": capture_edges' in graph_source
    assert "capture-owned connected knowledge subgraph" in adapter_source


def test_record_lookup_rejects_missing_or_empty_values() -> None:
    adapter = object.__new__(NiceGuiAdapter)
    adapter.records = {"empty": ""}
    with pytest.raises(ClientAdapterError, match="missing persisted"):
        adapter.record_id("missing")
    with pytest.raises(ClientAdapterError, match="empty persisted"):
        adapter.record_id("empty")


def test_model_option_label_requires_one_exact_provider_qualified_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from row_bot.providers import selection

    options = [
        {
            "value": "model:codex:gpt-5.6-sol",
            "label": "GPT-5.6-Sol - ChatGPT / Codex",
            "active": True,
        },
        {
            "value": "model:openai:gpt-5.6-sol",
            "label": "gpt-5.6-sol - OpenAI API",
            "active": True,
        },
    ]
    monkeypatch.setattr(
        selection, "list_model_choice_options", lambda *_args, **_kwargs: options
    )

    assert NiceGuiAdapter.model_option_label("model:codex:gpt-5.6-sol") == (
        "GPT-5.6-Sol - ChatGPT / Codex"
    )
    with pytest.raises(ClientAdapterError, match="found 0"):
        NiceGuiAdapter.model_option_label("model:ollama:gpt-5.6-sol")
