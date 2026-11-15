"""Your models: the starred models every picker lists, and the 5.3.3 fold into stars.

The stars live in a fixture ``model_settings.json`` under ``tmp_path``, keyed by
provider-qualified model reference; no runtime or provider is read.
"""
from __future__ import annotations

import json

import pytest

from row_bot.providers import model_visibility as visibility

pytestmark = pytest.mark.subsystem

QWEN = "model:ollama:qwen3:8b"
LLAMA = "model:ollama:llama3.1:8b"
GEMMA = "model:ollama:gemma3:4b"
SOL = "model:codex:gpt-5.6-sol"
OPUS = "model:anthropic:claude-opus-4-7"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "model_settings.json"
    path.write_text(json.dumps({"model": SOL, "context_size": 32768}), encoding="utf-8")
    return path


def _saved(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_nothing_is_starred_by_default(settings):
    assert visibility.read_model_visibility().favourites == ()
    rows = [{"model_ref": QWEN}, {"model_ref": LLAMA}]
    assert visibility.annotate(rows, key="model_ref") == rows
    assert "model_visibility" not in _saved(settings)


def test_stars_keep_their_order_and_survive_a_restart(settings):
    visibility.update_model_visibility("favourite", LLAMA)
    visibility.update_model_visibility("favourite", QWEN)
    visibility.update_model_visibility("favourite", SOL)
    visibility.update_model_visibility("unfavourite", QWEN)
    visibility.update_model_visibility("favourite", QWEN)

    shown = visibility.read_model_visibility()
    assert shown.favourites == (LLAMA, SOL, QWEN)
    assert [shown.rank(ref) for ref in (LLAMA, SOL, QWEN, GEMMA)] == [1, 2, 3, None]
    # Written beside the default model and context settings, which are kept.
    saved = _saved(settings)
    assert saved["model"] == SOL and saved["context_size"] == 32768
    assert saved["model_visibility"] == {"favourites": [LLAMA, SOL, QWEN]}
    # Unstarring the last one leaves no trace in the file.
    for ref in (LLAMA, SOL, QWEN):
        visibility.update_model_visibility("unfavourite", ref)
    assert "model_visibility" not in _saved(settings) and _saved(settings)["model"] == SOL


def test_starring_a_first_model_keeps_an_existing_star_in_its_place(settings):
    visibility.update_model_visibility("favourite", LLAMA)
    visibility.update_model_visibility("favourite", QWEN)
    visibility.star_model(LLAMA)
    visibility.star_model(SOL)
    assert visibility.read_model_visibility().favourites == (LLAMA, QWEN, SOL)


def test_annotate_marks_only_starred_models_and_a_re_pull_keeps_its_place(settings):
    visibility.update_model_visibility("favourite", SOL)
    visibility.update_model_visibility("favourite", QWEN)
    rows = [{"model_ref": GEMMA, "label": "gemma3"}, {"model_ref": QWEN}, {"model_ref": LLAMA}]
    assert visibility.annotate(rows, key="model_ref") == [
        {"model_ref": GEMMA, "label": "gemma3"},
        {"model_ref": QWEN, "favourite_rank": 2},
        {"model_ref": LLAMA},
    ]
    # qwen3:8b is uninstalled, then pulled again under the same name: it comes back as it was.
    assert visibility.annotate([{"model_ref": LLAMA}], key="model_ref") == [{"model_ref": LLAMA}]
    assert visibility.annotate([{"model_ref": QWEN}], key="model_ref") == [{"model_ref": QWEN, "favourite_rank": 2}]


def test_references_are_provider_qualified_and_canonical(settings):
    with pytest.raises(ValueError, match="invalid_model_selection"):
        visibility.update_model_visibility("favourite", "qwen3:8b")
    with pytest.raises(ValueError, match="invalid_model_selection"):
        visibility.update_model_visibility("favourite", "model:bad provider:x")
    with pytest.raises(ValueError, match="invalid_command"):
        visibility.update_model_visibility("hide", QWEN)
    # The legacy "local" provider id is Ollama.
    saved = visibility.update_model_visibility("favourite", "model:local:qwen3:8b")
    assert saved.favourites == (QWEN,)


def test_stars_are_bounded_dropping_the_oldest(settings):
    for index in range(visibility.MAX_FAVOURITES + 2):
        visibility.update_model_visibility("favourite", f"model:ollama:m{index}")
    favourites = visibility.read_model_visibility().favourites
    assert len(favourites) == visibility.MAX_FAVOURITES
    assert favourites[0] == "model:ollama:m2" and favourites[-1] == f"model:ollama:m{visibility.MAX_FAVOURITES + 1}"


def test_an_unreadable_file_reads_as_no_stars(settings):
    settings.write_text("{not json", encoding="utf-8")
    assert visibility.read_model_visibility() == visibility.ModelVisibility()


# ── The 5.3.3 fold ──────────────────────────────────────────────────────────


def _legacy_profile(tmp_path, *, favourites=(), hidden=(), pins=(), starred_cloud=(), task_routes=None):
    (tmp_path / "model_settings.json").write_text(json.dumps({
        "model": SOL, "context_size": 32768,
        "model_visibility": {"favourites": list(favourites), "hidden": list(hidden)},
    }), encoding="utf-8")
    routes = [{"id": "balanced", "display_name": "Balanced", "task_routes": task_routes or {}},
              {"id": "private", "display_name": "Private", "task_routes": {}}]
    (tmp_path / "providers.json").write_text(json.dumps({
        "version": 1,
        "providers": {"openai": {"credential_ref": {"legacy": True}}},
        "quick_choices": [{"id": ref, "kind": "model", "provider_id": ref.split(":")[1],
                           "model_id": ref.split(":", 2)[2], "visibility": ["chat"]} for ref in pins],
        "routes": routes,
        "custom_endpoints": [],
    }), encoding="utf-8")
    if starred_cloud:
        (tmp_path / "cloud_config.json").write_text(json.dumps({"starred_models": list(starred_cloud)}),
                                                    encoding="utf-8")


def test_pins_legacy_stars_and_favourites_fold_into_one_list_once(settings, tmp_path):
    _legacy_profile(tmp_path, favourites=[QWEN], hidden=[GEMMA], pins=[SOL, QWEN, OPUS],
                    starred_cloud=["gpt-5.5", "claude-opus-4-7"])
    original_providers = (tmp_path / "providers.json").read_bytes()
    original_settings = (tmp_path / "model_settings.json").read_bytes()

    assert visibility.migrate_to_stars(tmp_path) is True

    # Favourites first, then pins, then the oldest stars; once each, hidden models gone.
    assert visibility.read_model_visibility().favourites == (QWEN, SOL, OPUS, "model:openai:gpt-5.5")
    settings_now = _saved(tmp_path / "model_settings.json")
    assert settings_now["model"] == SOL and settings_now["context_size"] == 32768
    assert settings_now["model_visibility"] == {"favourites": [QWEN, SOL, OPUS, "model:openai:gpt-5.5"]}
    providers_now = _saved(tmp_path / "providers.json")
    assert "quick_choices" not in providers_now and "routes" not in providers_now
    assert providers_now["providers"] == {"openai": {"credential_ref": {"legacy": True}}}
    # Each file is kept as it was, beside it.
    assert (tmp_path / "providers.pre-5.3.3.json").read_bytes() == original_providers
    assert (tmp_path / "model_settings.pre-5.3.3.json").read_bytes() == original_settings

    # A second start changes nothing.
    assert visibility.migrate_to_stars(tmp_path) is False
    assert visibility.read_model_visibility().favourites == (QWEN, SOL, OPUS, "model:openai:gpt-5.5")


def test_a_background_model_named_on_the_old_balanced_route_is_kept(settings, tmp_path, monkeypatch):
    from row_bot.providers import config
    from row_bot.providers.routing import task_route_model

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "providers.json")
    _legacy_profile(tmp_path, task_routes={"dream": "model:ollama:qwen3:8b"})
    visibility.migrate_to_stars(tmp_path)
    assert _saved(tmp_path / "providers.json")["task_routes"] == {"dream": "model:ollama:qwen3:8b"}
    assert task_route_model("dream") == "model:ollama:qwen3:8b"
    assert task_route_model("extraction") == ""


def test_a_new_profile_has_nothing_to_fold(settings, tmp_path):
    assert visibility.migrate_to_stars(tmp_path) is False
    assert not (tmp_path / "providers.json").exists()
    assert not (tmp_path / "model_settings.pre-5.3.3.json").exists()


def test_a_failed_fold_leaves_the_files_as_they_were(settings, tmp_path, monkeypatch):
    from row_bot.providers import config

    _legacy_profile(tmp_path, favourites=[QWEN], pins=[SOL])
    before = (tmp_path / "providers.json").read_bytes()

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(config, "write_provider_metadata", refuse)
    with pytest.raises(OSError):
        visibility.migrate_to_stars(tmp_path)
    assert (tmp_path / "providers.json").read_bytes() == before
    assert _saved(tmp_path / "model_settings.json")["model_visibility"]["favourites"] == [QWEN]
