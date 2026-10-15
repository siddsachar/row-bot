"""Favourite models first and hidden ones under "More models" (F10).

The preference lives in a fixture ``model_settings.json`` under ``tmp_path``,
keyed by provider-qualified model reference; no runtime or provider is read.
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


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "model_settings.json"
    path.write_text(json.dumps({"model": SOL, "context_size": 32768}), encoding="utf-8")
    return path


def _saved(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_nothing_is_hidden_or_favourite_by_default(settings):
    shown = visibility.read_model_visibility()
    assert shown.favourites == () and shown.hidden == ()
    rows = [{"model_ref": QWEN}, {"model_ref": LLAMA}]
    assert visibility.annotate(rows, key="model_ref") == rows
    assert "model_visibility" not in _saved(settings)


def test_favourites_keep_their_order_and_survive_a_restart(settings):
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
    assert saved["model_visibility"] == {"favourites": [LLAMA, SOL, QWEN], "hidden": []}


def test_hide_show_and_show_all(settings):
    visibility.update_model_visibility("hide", GEMMA)
    visibility.update_model_visibility("hide", LLAMA)
    assert visibility.read_model_visibility().hidden == (GEMMA, LLAMA)
    visibility.update_model_visibility("show", GEMMA)
    assert visibility.read_model_visibility().hidden == (LLAMA,)
    visibility.update_model_visibility("favourite", QWEN)
    visibility.update_model_visibility("hide", GEMMA)
    after = visibility.update_model_visibility("show_all")
    assert after.hidden == () and after.favourites == (QWEN,)
    # Showing everything with no favourites leaves no trace in the file.
    visibility.update_model_visibility("unfavourite", QWEN)
    assert "model_visibility" not in _saved(settings)
    assert _saved(settings)["model"] == SOL


def test_a_model_is_a_favourite_or_hidden_never_both(settings):
    visibility.update_model_visibility("favourite", QWEN)
    hidden = visibility.update_model_visibility("hide", QWEN)
    assert hidden.favourites == () and hidden.hidden == (QWEN,)
    chosen = visibility.update_model_visibility("favourite", QWEN)
    assert chosen.favourites == (QWEN,) and chosen.hidden == ()
    # A hand-edited file with both reads as a favourite.
    settings.write_text(json.dumps({"model_visibility": {"favourites": [LLAMA], "hidden": [LLAMA, GEMMA]}}),
                        encoding="utf-8")
    shown = visibility.read_model_visibility()
    assert shown.favourites == (LLAMA,) and shown.hidden == (GEMMA,)


def test_annotate_marks_only_listed_models(settings):
    visibility.update_model_visibility("favourite", SOL)
    visibility.update_model_visibility("favourite", QWEN)
    visibility.update_model_visibility("hide", GEMMA)
    rows = [{"model_ref": GEMMA, "label": "gemma3"}, {"model_ref": QWEN}, {"model_ref": LLAMA}]
    assert visibility.annotate(rows, key="model_ref") == [
        {"model_ref": GEMMA, "label": "gemma3", "hidden": True},
        {"model_ref": QWEN, "favourite_rank": 2},
        {"model_ref": LLAMA},
    ]


def test_a_removed_model_drops_out_and_a_re_pull_keeps_its_place(settings):
    visibility.update_model_visibility("favourite", QWEN)
    visibility.update_model_visibility("favourite", LLAMA)
    visibility.update_model_visibility("hide", GEMMA)
    # qwen3:8b and gemma3:4b are uninstalled: the lists simply don't have them.
    installed = [{"model_ref": LLAMA}]
    assert visibility.annotate(installed, key="model_ref") == [{"model_ref": LLAMA, "favourite_rank": 2}]
    # Pulled again under the same name, each comes back as it was.
    again = [{"model_ref": GEMMA}, {"model_ref": QWEN}, {"model_ref": LLAMA}]
    assert visibility.annotate(again, key="model_ref") == [
        {"model_ref": GEMMA, "hidden": True},
        {"model_ref": QWEN, "favourite_rank": 1},
        {"model_ref": LLAMA, "favourite_rank": 2},
    ]


def test_references_are_provider_qualified_and_canonical(settings):
    with pytest.raises(ValueError, match="invalid_model_selection"):
        visibility.update_model_visibility("hide", "qwen3:8b")
    with pytest.raises(ValueError, match="invalid_model_selection"):
        visibility.update_model_visibility("favourite", "model:bad provider:x")
    with pytest.raises(ValueError, match="invalid_command"):
        visibility.update_model_visibility("delete", QWEN)
    # The legacy "local" provider id is Ollama.
    saved = visibility.update_model_visibility("favourite", "model:local:qwen3:8b")
    assert saved.favourites == (QWEN,)
    assert visibility.annotate([{"selection_ref": QWEN}], key="selection_ref") == [
        {"selection_ref": QWEN, "favourite_rank": 1},
    ]


def test_lists_are_bounded_dropping_the_oldest(settings):
    for index in range(visibility.MAX_FAVOURITES + 2):
        visibility.update_model_visibility("favourite", f"model:ollama:m{index}")
    favourites = visibility.read_model_visibility().favourites
    assert len(favourites) == visibility.MAX_FAVOURITES
    assert favourites[0] == "model:ollama:m2" and favourites[-1] == f"model:ollama:m{visibility.MAX_FAVOURITES + 1}"


def test_an_unreadable_file_lists_every_model(settings):
    settings.write_text("{not json", encoding="utf-8")
    assert visibility.read_model_visibility() == visibility.ModelVisibility()
    rows = [{"model_ref": QWEN}]
    assert visibility.annotate(rows, key="model_ref") == rows
