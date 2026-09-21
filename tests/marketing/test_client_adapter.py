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
    assert len(load_manifest(ROOT / "scripts" / "marketing" / "landing_story.yml").scenes) == 9


def test_record_lookup_rejects_missing_or_empty_values() -> None:
    adapter = object.__new__(NiceGuiAdapter)
    adapter.records = {"empty": ""}
    with pytest.raises(ClientAdapterError, match="missing persisted"):
        adapter.record_id("missing")
    with pytest.raises(ClientAdapterError, match="empty persisted"):
        adapter.record_id("empty")
