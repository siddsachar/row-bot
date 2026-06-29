from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from row_bot.plugins.api import Channel, ChannelCapabilities, ConfigField, PluginAPI, PluginTool


pytestmark = pytest.mark.contract


class FakeStateBackend:
    def __init__(self) -> None:
        self.config: dict[tuple[str, str], Any] = {}
        self.secrets: dict[tuple[str, str], str] = {}

    def get_plugin_config(self, plugin_id: str, key: str, default: Any = None) -> Any:
        return self.config.get((plugin_id, key), default)

    def set_plugin_config(self, plugin_id: str, key: str, value: Any) -> None:
        self.config[(plugin_id, key)] = value

    def get_plugin_secret(self, plugin_id: str, key: str) -> str | None:
        return self.secrets.get((plugin_id, key))

    def set_plugin_secret(self, plugin_id: str, key: str, value: str) -> None:
        self.secrets[(plugin_id, key)] = value


class EchoTool(PluginTool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def display_name(self) -> str:
        return "Echo Tool"

    @property
    def description(self) -> str:
        return "Echoes input text"

    def execute(self, query: str) -> str:
        return f"echo:{query}"


class DestructiveTool(EchoTool):
    @property
    def name(self) -> str:
        return "destructive_tool"

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"send_message", "delete_record"}

    @property
    def background_allowed_tool_names(self) -> set[str]:
        return {"send_message"}


def test_plugin_api_delegates_config_and_secret_storage(tmp_path: Path) -> None:
    state = FakeStateBackend()
    api = PluginAPI("sample-plugin", tmp_path / "plugin", state)

    assert api.plugin_id == "sample-plugin"
    assert api.plugin_dir == tmp_path / "plugin"
    assert api.get_config("missing", default="fallback") == "fallback"

    api.set_config("limit", 5)
    api.set_secret("API_KEY", "secret-value")

    assert api.get_config("limit") == 5
    assert api.get_secret("API_KEY") == "secret-value"


def test_plugin_api_registers_tools_and_skills(tmp_path: Path) -> None:
    api = PluginAPI("sample-plugin", tmp_path / "plugin", FakeStateBackend())
    tool = EchoTool(api)
    skill = {"name": "sample_skill", "instructions": "Use the sample skill."}
    channel = object()

    api.register_tool(tool)
    api.register_skill(skill)
    api.register_channel(channel)

    assert api._registered_tools == [tool]
    assert api._registered_skills == [skill]
    assert api._registered_channels == [channel]


def test_plugin_api_exports_channel_base_types() -> None:
    assert Channel.__name__ == "Channel"
    assert ChannelCapabilities().__class__.__name__ == "ChannelCapabilities"
    assert ConfigField(key="target", label="Target").key == "target"


def test_plugin_api_background_context_is_read_from_agent_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = types.ModuleType("row_bot.agent")
    fake_agent.is_background_workflow = lambda: True

    class FakeRecipientsVar:
        def get(self) -> list[str]:
            return ["ops@example.com", "alerts@example.com"]

    fake_agent._task_allowed_recipients_var = FakeRecipientsVar()
    monkeypatch.setitem(sys.modules, "row_bot.agent", fake_agent)

    api = PluginAPI("sample-plugin", tmp_path / "plugin", FakeStateBackend())

    assert api.is_background_workflow() is True
    assert api.get_allowed_recipients() == ["ops@example.com", "alerts@example.com"]


def test_plugin_tool_langchain_bridge_and_failure_boundary(tmp_path: Path) -> None:
    api = PluginAPI("sample-plugin", tmp_path / "plugin", FakeStateBackend())
    tool = EchoTool(api)

    langchain_tool = tool.as_langchain_tool()

    assert langchain_tool.name == "echo_tool"
    assert "Echo Tool" in langchain_tool.description
    assert tool.execute("hello") == "echo:hello"


def test_plugin_tool_rich_result_markers_are_stable() -> None:
    assert PluginTool.image_result("base64-data") == "__IMAGE__:base64-data"
    assert PluginTool.image_result("base64-data", "caption") == "__IMAGE__:base64-data\n\ncaption"
    assert PluginTool.html_result("<b>Hi</b>") == "__HTML__:<b>Hi</b>"
    assert PluginTool.html_result("<b>Hi</b>", "summary") == "__HTML__:<b>Hi</b>\n\nsummary"
    assert PluginTool.chart_result('{"data": []}') == '__CHART__:{"data": []}'
    assert PluginTool.chart_result('{"data": []}', "chart") == '__CHART__:{"data": []}\n\nchart'


def test_plugin_tool_permission_defaults_and_overrides(tmp_path: Path) -> None:
    api = PluginAPI("sample-plugin", tmp_path / "plugin", FakeStateBackend())

    assert EchoTool(api).destructive_tool_names == set()
    assert EchoTool(api).background_allowed_tool_names == set()
    assert DestructiveTool(api).destructive_tool_names == {"send_message", "delete_record"}
    assert DestructiveTool(api).background_allowed_tool_names == {"send_message"}
