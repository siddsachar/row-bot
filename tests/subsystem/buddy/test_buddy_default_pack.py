"""New installs start with Buddy Tile; a profile set up before 5.3.4 keeps the Glyph it shows."""

from __future__ import annotations

import json

import pytest

from row_bot.application import client_first_run
from row_bot.buddy import config


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_BUDDY_CONFIG_PATH", tmp_path / "buddy_config.json")
    monkeypatch.setattr(client_first_run, "saved_default_model", lambda: None)
    return tmp_path


def test_a_new_install_starts_with_tile(profile) -> None:
    assert config.keep_earlier_default_pack() == "tile"
    assert config.get_buddy_config()["pack_id"] == "tile"
    # Written once, so finishing setup later never swaps it.
    (profile / "app_config.json").write_text(json.dumps({"setup_complete": True}), encoding="utf-8")
    assert config.keep_earlier_default_pack() is None
    assert config.get_buddy_config()["pack_id"] == "tile"


def test_a_profile_set_up_before_keeps_glyph(profile) -> None:
    (profile / "app_config.json").write_text(json.dumps({"setup_complete": True}), encoding="utf-8")
    assert config.keep_earlier_default_pack() == "glyph"
    assert config.get_buddy_config()["pack_id"] == "glyph"


def test_a_profile_with_a_chosen_chat_model_counts_as_set_up(profile, monkeypatch) -> None:
    monkeypatch.setattr(client_first_run, "saved_default_model", lambda: "model:codex:gpt-6.1-sol")
    assert config.keep_earlier_default_pack() == "glyph"


def test_a_buddy_the_person_chose_is_never_touched(profile) -> None:
    config.save_buddy_config({"pack_id": "orbit", "display_name": "Pal"})
    assert config.keep_earlier_default_pack() is None
    saved = config.get_buddy_config()
    assert (saved["pack_id"], saved["display_name"]) == ("orbit", "Pal")
