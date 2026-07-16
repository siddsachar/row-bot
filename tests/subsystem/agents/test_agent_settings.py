from __future__ import annotations

import json

import pytest

from row_bot.agent_settings import (
    AgentRuntimeSettings,
    agent_runtime_settings_path,
    load_agent_runtime_settings,
    reset_agent_runtime_settings,
    save_agent_runtime_settings,
)


def test_missing_settings_use_reviewed_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    settings = load_agent_runtime_settings()

    assert settings == AgentRuntimeSettings()
    assert settings.max_iterations == 90
    assert not agent_runtime_settings_path().exists()


def test_settings_round_trip_atomically_at_call_time(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(first))
    saved = save_agent_runtime_settings(
        AgentRuntimeSettings(
            max_iterations=120,
            max_spawn_depth=4,
            max_concurrent_children=7,
            max_active_children_global=16,
            child_timeout_seconds=900,
        )
    )
    assert load_agent_runtime_settings() == saved
    assert not list(first.glob("agent_settings.json.*.tmp"))

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(second))
    assert load_agent_runtime_settings() == AgentRuntimeSettings()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_iterations", 0),
        ("max_iterations", True),
        ("max_spawn_depth", -1),
        ("max_concurrent_children", 1.5),
        ("max_active_children_global", 0),
        ("child_timeout_seconds", -1),
    ],
)
def test_invalid_complete_form_does_not_replace_prior_settings(
    tmp_path,
    monkeypatch,
    field: str,
    value: object,
) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    prior = save_agent_runtime_settings(AgentRuntimeSettings(max_iterations=91))
    raw = prior.__dict__ | {field: value}

    with pytest.raises(ValueError):
        save_agent_runtime_settings(raw)

    assert load_agent_runtime_settings() == prior


def test_corrupt_file_falls_back_without_rewriting(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = agent_runtime_settings_path()
    path.write_text("{not json", encoding="utf-8")

    assert load_agent_runtime_settings() == AgentRuntimeSettings()
    assert path.read_text(encoding="utf-8") == "{not json"


def test_reset_persists_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    save_agent_runtime_settings(AgentRuntimeSettings(max_iterations=140))

    reset = reset_agent_runtime_settings()

    assert reset == AgentRuntimeSettings()
    assert json.loads(agent_runtime_settings_path().read_text(encoding="utf-8"))[
        "max_iterations"
    ] == 90
