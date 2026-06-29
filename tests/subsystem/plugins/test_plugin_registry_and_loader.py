from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from row_bot.plugins.api import PluginAPI, PluginTool
from row_bot.plugins.manifest import PluginAuthor, PluginManifest, PluginProvides

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
    assert "plugin: Test Plugin" in prompt
    assert "Use the test skill." in prompt
    assert registry.get_plugin_skills("test-plugin")[0]["name"] == "test_skill"

    records = registry.get_enabled_plugin_tool_records()
    assert records[0]["runtime_name"] == "test_tool"
    assert records[0]["plugin_id"] == "test-plugin"
    assert records[0]["destructive"] is False

    state.set_plugin_enabled("test-plugin", False)
    assert registry.get_langchain_tools() == []
    assert registry.get_destructive_names() == set()
    assert registry.get_background_allowed_names() == set()
    assert registry.get_skills_prompt() == ""
    assert registry.get_plugin_skills("test-plugin") == []
    assert registry.get_plugin_skills("test-plugin", enabled_only=False)[0]["name"] == "test_skill"


def test_registry_includes_plugin_owned_mcp_tools_when_enabled(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = plugin_modules["state"]
    registry = plugin_modules["registry"]
    manifest = _manifest("mcp-plugin")
    manifest.provides = PluginProvides(mcp_servers=[{"id": "fake-mcp"}])
    fake_tool = StructuredTool.from_function(
        func=lambda query="": f"mcp:{query}",
        name="mcp_plugin_fake_read",
        description="Fake plugin MCP tool",
    )

    calls: list[tuple[str, Any]] = []

    from row_bot.mcp_client import runtime as mcp_runtime

    def fake_get_plugin_langchain_tools(plugin_id: str, allow_names=None):
        calls.append((plugin_id, allow_names))
        return [fake_tool]

    def fake_get_plugin_tool_records(plugin_id: str):
        return [{
            "runtime_name": "mcp_plugin_fake_read",
            "plugin_id": plugin_id,
            "plugin_name": "MCP Plugin",
            "source": "mcp",
            "destructive": False,
        }]

    monkeypatch.setattr(mcp_runtime, "get_plugin_langchain_tools", fake_get_plugin_langchain_tools)
    monkeypatch.setattr(
        mcp_runtime,
        "get_plugin_destructive_tool_names",
        lambda plugin_id, allow_names=None: {"mcp_plugin_fake_delete"},
    )
    monkeypatch.setattr(mcp_runtime, "get_plugin_tool_records", fake_get_plugin_tool_records)

    state.set_plugin_enabled("mcp-plugin", True)
    registry.register_plugin(manifest=manifest, tools=[], skills=[])

    assert [tool.name for tool in registry.get_langchain_tools()] == ["mcp_plugin_fake_read"]
    assert registry.get_langchain_tools(allow_names=["mcp-plugin"])[0].name == "mcp_plugin_fake_read"
    assert registry.get_destructive_names() == {"mcp_plugin_fake_delete"}
    assert registry.get_enabled_plugin_tool_names() == ["mcp_plugin_fake_read"]
    assert registry.get_enabled_plugin_tool_records()[0]["source"] == "mcp"
    assert calls[1] == ("mcp-plugin", None)

    state.set_plugin_enabled("mcp-plugin", False)
    assert registry.get_langchain_tools() == []
    assert registry.get_destructive_names() == set()


def test_plugin_mcp_overlay_uses_enabled_manifests_and_resolves_config(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    state = plugin_modules["state"]
    registry = plugin_modules["registry"]
    manifest = _manifest("office-plugin")
    manifest.path = tmp_path / "office-plugin"
    manifest.provides = PluginProvides(
        mcp_servers=[{
            "id": "office",
            "command": "python",
            "args": ["-m", "office_mcp"],
            "env": {"TENANT": "setting:tenant", "TOKEN": "secret:TOKEN"},
            "tools": {"include": ["search_mail"]},
        }]
    )
    registry.register_plugin(manifest=manifest, tools=[], skills=[])
    state.set_plugin_config("office-plugin", "tenant", "contoso")
    state.set_plugin_secret("office-plugin", "TOKEN", "secret-value")
    state.set_plugin_enabled("office-plugin", True)

    from row_bot.plugins.mcp import plugin_mcp_server_name, with_plugin_mcp_servers

    cfg = with_plugin_mcp_servers({
        "enabled": False,
        "servers": {"user-server": {"enabled": True, "transport": "stdio"}},
    })
    server_name = plugin_mcp_server_name("office-plugin", "office")
    server = cfg["servers"][server_name]

    assert cfg["enabled"] is True
    assert cfg["servers"]["user-server"]["enabled"] is False
    assert server["enabled"] is True
    assert server["cwd"] == str(manifest.path)
    assert server["env"] == {"TENANT": "contoso", "TOKEN": "secret-value"}
    assert server["source"]["kind"] == "plugin"
    assert server["source"]["plugin_id"] == "office-plugin"

    state.set_plugin_enabled("office-plugin", False)
    disabled_cfg = with_plugin_mcp_servers({"enabled": False, "servers": {}})

    assert disabled_cfg["enabled"] is False
    assert server_name not in disabled_cfg["servers"]


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
    state = plugin_modules["state"]
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

    state.set_plugin_enabled("sample-plugin", True)
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


def test_loader_registers_plugin_channel_and_disable_unregisters(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    state = plugin_modules["state"]
    loader = plugin_modules["loader"]
    from row_bot.channels import registry as channel_registry

    plugin_dir = write_plugin(
        tmp_path,
        "channel-plugin",
        manifest=manifest_payload(
            "channel-plugin",
            provides={
                "native_tools": [],
                "mcp_servers": [],
                "channels": [{"id": "plugin-fake"}],
                "skills": [],
            },
        ),
        main=(
            "from plugins.api import Channel\n\n"
            "class PluginFakeChannel(Channel):\n"
            "    @property\n"
            "    def name(self): return 'plugin_fake'\n"
            "    @property\n"
            "    def display_name(self): return 'Plugin Fake'\n"
            "    async def start(self): self.running = True; return True\n"
            "    async def stop(self): self.running = False\n"
            "    def is_configured(self): return True\n"
            "    def is_running(self): return getattr(self, 'running', False)\n"
            "    def send_message(self, target, text): self.last = (target, text)\n\n"
            "def register(api):\n"
            "    api.register_channel(PluginFakeChannel())\n"
        ),
    )

    state.set_plugin_enabled("channel-plugin", True)
    result = loader._load_single_plugin(plugin_dir)

    assert result.success is True
    assert channel_registry.get("plugin_fake") is not None
    assert channel_registry.get_source("plugin_fake").kind == "plugin"
    assert channel_registry.get_source("plugin_fake").plugin_id == "channel-plugin"

    state.set_plugin_enabled("channel-plugin", False)

    assert channel_registry.get("plugin_fake") is None


def test_loader_reports_broken_plugin_without_crashing(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    state = plugin_modules["state"]
    loader = plugin_modules["loader"]
    registry = plugin_modules["registry"]
    plugin_dir = write_plugin(
        tmp_path,
        "broken-plugin",
        manifest=manifest_payload(
            "broken-plugin",
            provides={
                "native_tools": [],
                "mcp_servers": [],
                "channels": [],
                "skills": [],
            },
        ),
        main="def register(api):\n    raise RuntimeError('boom')\n",
    )

    state.set_plugin_enabled("broken-plugin", True)
    result = loader._load_single_plugin(plugin_dir)

    assert result.success is False
    assert "crashed" in result.error
    assert registry.get_plugin_tool_names() == []
    logs = loader.read_plugin_logs("broken-plugin")
    assert logs[-1]["success"] is False
    assert "crashed" in logs[-1]["error"]


@pytest.mark.parametrize(
    ("filename", "source", "expected"),
    [
        ("plugin_main.py", "import os\nos.system('echo bad')\n", "os.system"),
        ("plugin_main.py", "from os import system\nsystem('echo bad')\n", "os.system"),
        ("plugin_main.py", "import subprocess\n", "subprocess"),
        ("plugin_main.py", "value = eval('1 + 1')\n", "eval"),
        ("plugin_main.py", "from agent import get_agent_graph\n", "agent"),
        ("plugin_main.py", "from row_bot.agent import get_agent_graph\n", "row_bot.agent"),
        ("plugin_main.py", "from row_bot.channels.base import Channel\n", "row_bot.channels.base"),
        ("plugin_main.py", "from nicegui import ui\n", "nicegui"),
        ("plugin_main.py", "import streamlit\n", "streamlit"),
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
        "from plugins.api import Channel, PluginAPI, PluginTool\n"
        "def register(api):\n"
        "    return None\n",
        encoding="utf-8",
    )

    assert loader._security_scan(plugin_dir) is None


def test_unregister_removes_plugin_manifest_tools_and_skills(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    state = plugin_modules["state"]
    loader = plugin_modules["loader"]
    registry = plugin_modules["registry"]
    plugin_dir = write_plugin(
        tmp_path,
        "sample-plugin",
        skill="---\nname: sample_skill\n---\nSkill instructions.\n",
    )
    state.set_plugin_enabled("sample-plugin", True)
    result = loader._load_single_plugin(plugin_dir)
    assert result.success is True

    registry.unregister_plugin("sample-plugin")

    assert registry.get_manifest("sample-plugin") is None
    assert registry.get_plugin_tool_names() == []
    assert registry.get_plugin_skills("sample-plugin") == []
