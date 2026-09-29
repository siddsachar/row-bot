"""Testing a plugin and `plugins doctor` work without NiceGUI (B189).

Both used the health helpers in the NiceGUI plugins tab, so React's "Test
plugin" and the CLI imported NiceGUI; without it they failed.
"""
from __future__ import annotations

import sys
from uuid import UUID

import pytest

from row_bot.application import plugin_commands as commands
from tests.subsystem.plugins.conftest import manifest_payload, write_plugin
from tests.subsystem.plugins.test_client_plugin_commands import _fake_admissions, _installed, _valid

pytestmark = pytest.mark.subsystem


@pytest.fixture
def no_nicegui(monkeypatch):
    """NiceGUI is not installed, and no NiceGUI-backed module is loaded yet."""
    monkeypatch.setitem(sys.modules, "nicegui", None)
    for name in [name for name in sys.modules if name.startswith("row_bot.plugins.ui_")]:
        monkeypatch.delitem(sys.modules, name)


def test_test_plugin_records_a_passed_self_test(plugin_modules, monkeypatch, no_nicegui):
    _installed(plugin_modules)
    _fake_admissions(monkeypatch)
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    payload = {"plugin_id": "sample-plugin", "revision": detail["revision"]}
    review = commands.review_plugin_command("plugin.test", payload, validate=_valid)

    receipt = commands.execute_plugin_command(
        owner_id="owner",
        key=str(UUID(int=11)),
        command={
            "command_id": str(UUID(int=11)),
            "type": "plugin.test",
            "payload": {**payload, "action_digest": review["action_digest"]},
        },
        validate=_valid,
        validate_review=lambda _: None,
    )

    assert receipt["status"] == "completed"
    assert commands.read_plugin_detail("sample-plugin", validate=_valid)["health"]["status"] == "passed"


def test_doctor_names_missing_settings_and_secrets(plugin_modules, tmp_path, no_nicegui):
    plugin_dir = write_plugin(
        tmp_path,
        "doctor-plugin",
        manifest=manifest_payload(
            "doctor-plugin",
            settings={"workspace": {"type": "local_path", "label": "Workspace", "required": True}},
            secrets={"TOKEN": {"type": "secret", "label": "Token", "required": True}},
        ),
    )

    result = plugin_modules["devtools"].doctor_plugin(plugin_dir)

    assert result.ok is False
    assert "Missing required settings: Workspace" in result.warnings
    assert "Missing required secrets: Token" in result.warnings
