from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.marketing.capture_contract import (
    CaptureContractError,
    load_manifest,
    parse_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts" / "marketing" / "landing_story.yml"


@pytest.fixture
def raw_manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_canonical_manifest_is_client_neutral_and_complete() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest.story.id == "sovereignty-workbench-v1"
    assert manifest.story.buddy_pack == "orbit"
    assert manifest.story.max_generation_attempts == 6
    assert manifest.models.local == "model:ollama:qwen3.8:27b"
    assert manifest.models.frontier == "model:codex:gpt-5.6-sol"
    assert manifest.viewports["desktop"].width == 1920
    assert manifest.viewports["mobile"].height == 844
    assert set(manifest.scenes_by_id) == {
        "approval-boundary",
        "designer-output",
        "hero-app",
        "knowledge-control",
        "mobile-shell",
        "model-choice",
        "research-local",
        "synthesis-sol",
        "workflow-repeat",
    }
    assert all(scene.public_safe for scene in manifest.scenes)
    assert all("webp" in scene.outputs for scene in manifest.scenes)


@pytest.mark.parametrize("key", ["route", "selector", "selectors", "actions"])
def test_scene_rejects_client_specific_implementation_keys(
    raw_manifest: dict,
    key: str,
) -> None:
    changed = deepcopy(raw_manifest)
    changed["scenes"][0][key] = "/client-specific"

    with pytest.raises(CaptureContractError, match="client-specific keys"):
        parse_manifest(changed)


def test_video_requires_a_still_poster(raw_manifest: dict) -> None:
    changed = deepcopy(raw_manifest)
    changed["scenes"][0]["outputs"] = ["webm"]

    with pytest.raises(CaptureContractError, match="WebP poster fallback"):
        parse_manifest(changed)


def test_every_scene_must_be_explicitly_public_safe(raw_manifest: dict) -> None:
    changed = deepcopy(raw_manifest)
    changed["scenes"][0]["public_safe"] = False

    with pytest.raises(CaptureContractError, match="public_safe must be true"):
        parse_manifest(changed)


def test_generation_budget_is_bounded(raw_manifest: dict) -> None:
    changed = deepcopy(raw_manifest)
    changed["story"]["max_generation_attempts"] = 13

    with pytest.raises(CaptureContractError, match="must not exceed 12"):
        parse_manifest(changed)


def test_model_references_must_be_provider_qualified(raw_manifest: dict) -> None:
    changed = deepcopy(raw_manifest)
    changed["models"]["local"] = "qwen3.8:27b"

    with pytest.raises(CaptureContractError, match="provider-qualified"):
        parse_manifest(changed)


def test_scene_ids_and_assets_must_be_unique(raw_manifest: dict) -> None:
    duplicate_id = deepcopy(raw_manifest)
    duplicate_id["scenes"][1]["id"] = duplicate_id["scenes"][0]["id"]
    with pytest.raises(CaptureContractError, match="scene IDs must be unique"):
        parse_manifest(duplicate_id)

    duplicate_asset = deepcopy(raw_manifest)
    duplicate_asset["scenes"][1]["asset"] = duplicate_asset["scenes"][0]["asset"]
    with pytest.raises(CaptureContractError, match="asset names must be unique"):
        parse_manifest(duplicate_asset)
