from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from row_bot.plugins.api import PluginAPI, PluginTool
from row_bot.plugins.manifest import PluginAuthor, PluginManifest

from .conftest import manifest_payload, write_plugin


pytestmark = pytest.mark.subsystem


class PluginFixtureTool(PluginTool):
    @property
    def name(self) -> str:
        return "test_tool"

    @property
    def display_name(self) -> str:
        return "Test Tool"

    @property
    def description(self) -> str:
        return "A plugin registry test tool"

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"send_message", "delete_record"}

    @property
    def background_allowed_tool_names(self) -> set[str]:
        return {"send_message"}

    def execute(self, query: str) -> str:
        return f"result:{query}"


def _manifest(plugin_id: str = "test-plugin") -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        name="Test Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="A deterministic test plugin.",
        tags=["testing"],
    )


def test_registry_tracks_enabled_tools_skills_and_permission_sets(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    state = plugin_modules["state"]
    registry = plugin_modules["registry"]
    api = PluginAPI("test-plugin", tmp_path / "plugin", state)
    tool = PluginFixtureTool(api)

    state.set_plugin_enabled("test-plugin", True)
    warnings = registry.register_plugin(
        manifest=_manifest(),
        tools=[tool],
        skills=[{
            "name": "test_skill",
            "display_name": "Test Skill",
            "icon": "*",
            "description": "A test skill",
            "instructions": "Use the test skill.",
        }],
    )

    assert warnings == []
    assert registry.get_plugin_tool_names() == ["test_tool"]
    assert [t.name for t in registry.get_langchain_tools()] == ["test_tool"]
    assert [t.name for t in registry.get_langchain_tools(allow_names=["test-plugin"])] == ["test_tool"]
    assert registry.get_enabled_plugin_tool_names() == ["test_tool"]
    assert registry.get_destructive_names() == {"send_message", "delete_record"}
    assert registry.get_destructive_names(allow_names=["send_message"]) == {"send_message"}
    assert registry.get_background_allowed_names() == {"send_message"}

    prompt = registry.get_skills_prompt()
    assert "Plugin Skills" in prompt
    assert "Test Skill" in prompt
    assert "Use the test skill." in prompt

    records = registry.get_enabled_plugin_tool_records()
    assert records[0]["runtime_name"] == "test_tool"
    assert records[0]["plugin_id"] == "test-plugin"
    assert records[0]["destructive"] is False

    state.set_plugin_enabled("test-plugin", False)
    assert registry.get_langchain_tools() == []
    assert registry.get_destructive_names() == set()
    assert registry.get_background_allowed_names() == set()


def test_registry_detects_tool_and_skill_name_collisions(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    registry = plugin_modules["registry"]
    state = plugin_modules["state"]
    api = PluginAPI("first-plugin", tmp_path / "plugin", state)
    tool = PluginFixtureTool(api)

    registry.register_plugin(
        manifest=_manifest("first-plugin"),
        tools=[tool],
        skills=[{"name": "shared_skill", "instructions": "first"}],
    )
    warnings = registry.register_plugin(
        manifest=_manifest("second-plugin"),
        tools=[tool],
        skills=[{"name": "shared_skill", "instructions": "second"}],
    )

    assert len(warnings) == 2
    assert all("collides" in warning for warning in warnings)
    assert registry.get_plugin_tool_names() == ["test_tool"]
    assert registry.get_plugin_skills("second-plugin") == []


def test_loader_loads_valid_plugin_discovers_skills_and_registers_tool(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    loader = plugin_modules["loader"]
    registry = plugin_modules["registry"]
    skill = (
        "---\n"
        "name: sample_skill\n"
        "display_name: Sample Skill\n"
        "description: Skill from plugin\n"
        "---\n"
        "Follow the plugin skill instructions.\n"
    )
    plugin_dir = write_plugin(tmp_path, "sample-plugin", skill=skill)

    result = loader._load_single_plugin(plugin_dir)

    assert result.success is True
    assert result.error == ""
    assert result.manifest is not None
    assert result.manifest.id == "sample-plugin"
    assert registry.get_plugin_tool_names() == ["sample_tool"]
    assert registry.get_plugin_skills("sample-plugin")[0]["name"] == "sample_skill"


def test_loader_keeps_disabled_plugin_visible_without_registering_runtime_tools(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    state = plugin_modules["state"]
    loader = plugin_modules["loader"]
    registry = plugin_modules["registry"]
    plugin_dir = write_plugin(tmp_path, "disabled-plugin")

    state.set_plugin_enabled("disabled-plugin", False)
    result = loader._load_single_plugin(plugin_dir)

    assert result.success is True
    assert result.manifest is not None
    assert "disabled" in result.warnings[0].lower()
    assert registry.get_manifest("disabled-plugin") is not None
    assert registry.get_plugin_tool_names() == []


def test_loader_reports_broken_plugin_without_crashing(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    loader = plugin_modules["loader"]
    registry = plugin_modules["registry"]
    plugin_dir = write_plugin(
        tmp_path,
        "broken-plugin",
        manifest=manifest_payload("broken-plugin", provides={"tools": []}),
        main="def register(api):\n    raise RuntimeError('boom')\n",
    )

    result = loader._load_single_plugin(plugin_dir)

    assert result.success is False
    assert "crashed" in result.error
    assert registry.get_plugin_tool_names() == []


@pytest.mark.parametrize(
    ("filename", "source", "expected"),
    [
        ("plugin_main.py", "import os\nos.system('echo bad')\n", "os.system"),
        ("plugin_main.py", "import subprocess\n", "subprocess"),
        ("plugin_main.py", "value = eval('1 + 1')\n", "eval"),
        ("plugin_main.py", "from agent import get_agent_graph\n", "agent"),
    ],
)
def test_loader_security_scan_rejects_dangerous_code(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
    filename: str,
    source: str,
    expected: str,
) -> None:
    loader = plugin_modules["loader"]
    plugin_dir = tmp_path / "unsafe-plugin"
    plugin_dir.mkdir()
    (plugin_dir / filename).write_text(source, encoding="utf-8")

    error = loader._security_scan(plugin_dir)

    assert error is not None
    assert expected in error


def test_loader_security_scan_allows_plugin_api_import(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    loader = plugin_modules["loader"]
    plugin_dir = tmp_path / "safe-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin_main.py").write_text(
        "from plugins.api import PluginAPI, PluginTool\n"
        "def register(api):\n"
        "    return None\n",
        encoding="utf-8",
    )

    assert loader._security_scan(plugin_dir) is None


def test_unregister_removes_plugin_manifest_tools_and_skills(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    loader = plugin_modules["loader"]
    registry = plugin_modules["registry"]
    plugin_dir = write_plugin(
        tmp_path,
        "sample-plugin",
        skill="---\nname: sample_skill\n---\nSkill instructions.\n",
    )
    result = loader._load_single_plugin(plugin_dir)
    assert result.success is True

    registry.unregister_plugin("sample-plugin")

    assert registry.get_manifest("sample-plugin") is None
    assert registry.get_plugin_tool_names() == []
    assert registry.get_plugin_skills("sample-plugin") == []
