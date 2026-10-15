"""Learning's settings: saved through the reviewed Settings commands, read back in
the snapshot, defaults from the setup sheet with Learning off; turning it on
creates the store and turning it off keeps the data; the switch is cached,
follows the file and is off when the file can't be read."""

from __future__ import annotations

import json

import pytest

from row_bot import learning
from row_bot.api.v1.schemas import SettingsSnapshot
from tests.fixtures.learning import learning_files, save_learning_settings
from tests.subsystem.client_protocol.test_settings_snapshot_api import BASE, _execute, _review, api  # noqa: F401

pytestmark = pytest.mark.subsystem

DEFAULTS = {
    "availability": "available",
    "enabled": False,
    "sources": {"my_chats": True, "workflows_and_goals": True, "past_chats": True},
    "apply_mode": "ask_first",
    "monthly_token_limit": 2_000_000,
    "train_in_quiet_hours": True,
    "train_where": "this_computer",
    "has_data": False,
}


def _save(client, headers, field, value):
    snapshot = client.get(BASE, headers=headers).json()
    request = {"settings_revision": snapshot["revision"], "page": "learning", "field": field, "value": value}
    review = _review(client, headers, request)
    response = _execute(client, headers, request, review)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "completed", receipt
    return review, receipt["snapshot"]


def test_learning_is_off_by_default_with_the_setup_sheets_defaults(api):
    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    SettingsSnapshot.model_validate(snapshot)
    assert snapshot["learning"] == DEFAULTS
    assert snapshot["defaults"]["learning"] == {
        "enabled": False,
        "sources.my_chats": True,
        "sources.workflows_and_goals": True,
        "sources.past_chats": True,
        "apply_mode": "ask_first",
        "monthly_token_limit": 2_000_000,
        "train_in_quiet_hours": True,
        "train_where": "this_computer",
    }
    assert learning_files(data) == []


@pytest.mark.parametrize(
    ("field", "value", "path"),
    [
        ("sources.my_chats", False, ("sources", "my_chats")),
        ("sources.workflows_and_goals", False, ("sources", "workflows_and_goals")),
        ("sources.past_chats", False, ("sources", "past_chats")),
        ("apply_mode", "automatic", ("apply_mode",)),
        ("monthly_token_limit", 500_000, ("monthly_token_limit",)),
        ("monthly_token_limit", 0, ("monthly_token_limit",)),
        ("train_in_quiet_hours", False, ("train_in_quiet_hours",)),
        ("train_where", "hugging_face", ("train_where",)),
    ],
)
def test_each_learning_setting_round_trips_through_the_commands_and_snapshot(api, field, value, path):
    client, headers, data, _ = api
    _, snapshot = _save(client, headers, field, value)

    saved = json.loads((data / "learning_config.json").read_text(encoding="utf-8"))
    for part in path:
        saved = saved[part]
    assert saved == value
    shown = snapshot["learning"]
    for part in path:
        shown = shown[part]
    assert shown == value
    assert client.get(BASE, headers=headers).json()["learning"] == snapshot["learning"]
    # Nothing acts on these yet, and saving them while Learning is off creates no data.
    assert snapshot["learning"]["enabled"] is False
    assert learning_files(data) == ["learning_config.json"]


def test_turning_learning_on_creates_its_store_and_turning_it_off_keeps_the_data(api):
    client, headers, data, _ = api
    skills_before = client.get(BASE, headers=headers).json()["preferences"]["identity"]["self_improvement_enabled"]

    review, snapshot = _save(client, headers, "enabled", True)
    assert review["value_summary"].startswith("Turn on Learning")
    assert snapshot["learning"]["enabled"] is True and snapshot["learning"]["has_data"] is True
    assert (data / "learning.db").is_file() and (data / "learning" / "blobs").is_dir()
    assert learning.is_enabled() is True

    review, snapshot = _save(client, headers, "enabled", False)
    assert review["value_summary"].startswith("Turn off Learning")
    assert "kept" in review["value_summary"]
    assert snapshot["learning"]["enabled"] is False and snapshot["learning"]["has_data"] is True
    assert (data / "learning.db").is_file()
    assert learning.is_enabled() is False
    # Preferences' "Learn new skills" is untouched (it moves in L1a, D29).
    after = client.get(BASE, headers=headers).json()["preferences"]["identity"]["self_improvement_enabled"]
    assert after == skills_before


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("enabled", "yes", "invalid_settings_command"),
        ("monthly_token_limit", -1, "invalid_settings_command"),
        ("monthly_token_limit", 1_000_000_001, "invalid_settings_command"),
        ("monthly_token_limit", 2.5, "invalid_settings_command"),
        ("monthly_token_limit", True, "invalid_settings_command"),
        ("apply_mode", "always", "invalid_settings_command"),
        ("train_where", "the_cloud", "invalid_settings_command"),
        ("sources.email", True, "settings_action_unavailable"),
        ("delete_all", True, "settings_action_unavailable"),
    ],
)
def test_invalid_learning_changes_are_refused_without_writing(api, field, value, code):
    client, headers, data, _ = api
    snapshot = client.get(BASE, headers=headers).json()
    response = client.post(
        BASE + "/review",
        headers=headers,
        json={"settings_revision": snapshot["revision"], "page": "learning", "field": field, "value": value},
    )
    assert response.status_code >= 400
    assert response.json()["code"] == code
    assert learning_files(data) == []


def test_the_switch_follows_the_saved_file(data_dir):
    assert learning.is_enabled() is False
    save_learning_settings(data_dir, enabled=True)
    assert learning.is_enabled() is True
    save_learning_settings(data_dir, enabled=False, apply_mode="automatic")
    assert learning.is_enabled() is False
    assert learning.read_settings().apply_mode == "automatic"


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps(["enabled"]), json.dumps({"enabled": "true"}), json.dumps({"enabled": 1})],
)
def test_the_switch_is_off_when_the_setting_cant_be_read(data_dir, content):
    (data_dir / "learning_config.json").write_text(content, encoding="utf-8")
    learning.invalidate()
    assert learning.is_enabled() is False


def test_an_unreadable_settings_file_shows_as_unavailable(data_dir):
    from row_bot.application.settings_snapshot import read_settings_snapshot

    (data_dir / "learning_config.json").write_text("{not json", encoding="utf-8")
    learning.invalidate()
    section = read_settings_snapshot()["learning"]
    assert section["availability"] == "unavailable" and section["enabled"] is False


def test_saved_values_that_cant_be_read_fall_back_to_the_safe_choice(data_dir):
    save_learning_settings(
        data_dir, enabled=True, apply_mode="sometimes", train_where="moon", monthly_token_limit=-5,
        sources={"my_chats": "no"}, train_in_quiet_hours=None,
    )
    settings = learning.read_settings()
    assert settings.enabled is True
    # Ask first, train here, the default limit; a source or schedule that can't be read is off.
    assert (settings.apply_mode, settings.train_where, settings.monthly_token_limit) == (
        "ask_first", "this_computer", 2_000_000,
    )
    assert (settings.my_chats, settings.workflows_and_goals, settings.train_in_quiet_hours) == (False, True, False)
