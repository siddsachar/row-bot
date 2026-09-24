"""Onboarding is an explicit, resumable client choice over existing config."""

from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

import pytest

from row_bot.application import client_onboarding
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def test_onboarding_read_is_passive_and_command_preserves_unrelated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "app_config.json"
    assert client_onboarding.read_onboarding()["setup_complete"] is False
    assert not path.exists()
    path.write_text(json.dumps({"unrelated": {"token": "synthetic"}}), encoding="utf-8")
    before = client_onboarding.read_onboarding()
    command_id = str(uuid4())
    changed = client_onboarding.execute_onboarding(
        command_id=command_id, expected_revision=before["revision"],
        action="save_profile", profile=["chat", "developer"], step="",
    )
    assert changed["snapshot"]["profile"] == ["chat", "developer"]
    assert client_onboarding.execute_onboarding(
        command_id=command_id, expected_revision=before["revision"],
        action="save_profile", profile=["chat", "developer"], step="",
    ) == changed
    assert json.loads(path.read_text(encoding="utf-8"))["unrelated"] == {"token": "synthetic"}
    with pytest.raises(Exception, match="onboarding_changed"):
        client_onboarding.execute_onboarding(
            command_id=str(uuid4()), expected_revision=before["revision"],
            action="dismiss_home", profile=[], step="",
        )


def test_starter_count_is_a_passive_read_of_existing_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "app_config.json").write_text(
        json.dumps({"setup_complete": True}), encoding="utf-8"
    )
    database = tmp_path / "tasks.db"
    assert client_onboarding.read_onboarding()["starter_workflows_missing"] == 5
    assert not database.exists()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE tasks (name TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO tasks (name) VALUES (?)",
            [(name,) for name in client_onboarding._STARTER_WORKFLOW_NAMES],
        )
    assert client_onboarding.read_onboarding()["starter_workflows_missing"] == 0


def test_starter_names_match_task_templates(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.tasks import _DEFAULT_TASKS

    assert {template["name"] for template in _DEFAULT_TASKS} == set(
        client_onboarding._STARTER_WORKFLOW_NAMES
    )


def test_onboarding_model_finish_and_checklist_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.application import client_models_settings

    monkeypatch.setattr(client_models_settings, "read_models_settings", lambda: {"brain": {"current_ref": "", "warning": ""}})
    first = client_onboarding.read_onboarding()
    with pytest.raises(Exception, match="onboarding_model_required"):
        client_onboarding.execute_onboarding(
            command_id=str(uuid4()), expected_revision=first["revision"],
            action="finish_models", profile=[], step="",
        )
    monkeypatch.setattr(client_models_settings, "read_models_settings", lambda: {"brain": {"current_ref": "model:fixture:offline", "warning": "Provider unavailable"}})
    with pytest.raises(Exception, match="onboarding_model_required"):
        client_onboarding.execute_onboarding(
            command_id=str(uuid4()), expected_revision=first["revision"],
            action="finish_models", profile=[], step="",
        )
    monkeypatch.setattr(client_models_settings, "read_models_settings", lambda: {"brain": {"current_ref": "model:fixture:ready", "warning": ""}})
    finished = client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=first["revision"],
        action="finish_models", profile=[], step="",
    )["snapshot"]
    assert finished["setup_complete"] is True
    assert "models" in finished["completed_steps"]
    skipped = client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=finished["revision"],
        action="skip_step", profile=[], step="voice",
    )["snapshot"]
    assert "voice" in client_onboarding.read_onboarding()["skipped_steps"]
    completed = client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=skipped["revision"],
        action="mark_done", profile=[], step="voice",
    )["snapshot"]
    assert "voice" in completed["completed_steps"]
    assert "voice" not in completed["skipped_steps"]


def test_starter_workflows_are_explicit_and_replay_one_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.application import client_models_settings
    from row_bot import tasks

    monkeypatch.setattr(client_models_settings, "read_models_settings", lambda: {
        "brain": {"current_ref": "model:fixture:ready", "warning": ""}})
    first = client_onboarding.read_onboarding()
    ready = client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=first["revision"],
        action="finish_models", profile=[], step="",
    )["snapshot"]
    calls = []
    monkeypatch.setattr(tasks, "add_default_workflow_templates", lambda: calls.append("add") or 2)
    command_id = str(uuid4())
    result = client_onboarding.execute_onboarding(
        command_id=command_id, expected_revision=ready["revision"],
        action="add_starters", profile=[], step="",
    )
    assert calls == ["add"]
    assert "workflows" in result["snapshot"]["completed_steps"]
    assert client_onboarding.execute_onboarding(
        command_id=command_id, expected_revision=ready["revision"],
        action="add_starters", profile=[], step="",
    ) == result
    assert calls == ["add"]
    with pytest.raises(Exception, match="onboarding_changed"):
        client_onboarding.execute_onboarding(
            command_id=str(uuid4()), expected_revision=ready["revision"],
            action="add_starters", profile=[], step="",
        )


def test_onboarding_refuses_to_overwrite_unreadable_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "app_config.json"
    path.write_text("{incomplete", encoding="utf-8")
    with pytest.raises(Exception, match="onboarding_config_unavailable"):
        client_onboarding.read_onboarding()
    assert path.read_text(encoding="utf-8") == "{incomplete"


def test_onboarding_api_requires_session_and_returns_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.application import client_models_settings

    monkeypatch.setattr(client_models_settings, "read_models_settings", lambda: {"brain": {"current_ref": "", "warning": ""}})
    client, _, _ = client_app()
    with client:
        assert client.get("/api/v1/setup/onboarding").status_code in {401, 403}
        _, headers = bootstrap(client)
        first = client.get("/api/v1/setup/onboarding", headers=headers)
        assert first.status_code == 200, first.text
        command_id = str(uuid4())
        body = {
            "command_id": command_id,
            "expected_revision": first.json()["revision"],
            "action": "save_profile", "profile": ["chat"], "step": "",
        }
        assert client.post("/api/v1/setup/onboarding/commands", headers=headers, json=body).status_code == 409
        result = client.post("/api/v1/setup/onboarding/commands", headers={**headers, "Idempotency-Key": command_id}, json={
            **body,
        })
        finish_id = str(uuid4())
        missing = client.post("/api/v1/setup/onboarding/commands", headers={**headers, "Idempotency-Key": finish_id}, json={
            "command_id": finish_id,
            "expected_revision": result.json()["snapshot"]["revision"],
            "action": "finish_models", "profile": [], "step": "",
        })
    assert result.status_code == 200, result.text
    assert result.json()["snapshot"]["profile"] == ["chat"]
    assert missing.status_code == 409
    assert missing.json()["code"] == "onboarding_model_required"
