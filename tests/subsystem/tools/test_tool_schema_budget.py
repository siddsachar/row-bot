"""Model-facing tool schemas: sizes on record, closed sets as enums, runtime ids kept out (WP D1).

Every bound tool's schema is sent with every model call. The recorded sizes make growth show
up in review: record a deliberate change with ROW_BOT_UPDATE_SNAPSHOTS=1.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from tests.helpers.snapshots import assert_matches_snapshot


pytestmark = pytest.mark.subsystem

SNAPSHOT = Path(__file__).resolve().parents[2] / "snapshots" / "tools" / "default_tool_schema_sizes.txt"
# Optional tool modules the default tool set includes (tests import optional modules on purpose).
OPTIONAL_DEFAULT_MODULES = ("row_bot.tools.developer_tool", "row_bot.tools.chart_tool", "row_bot.tools.browser_tool")


def _schema_chars(tool) -> int:
    return len(json.dumps(convert_to_openai_tool(tool), ensure_ascii=False, sort_keys=True))


@pytest.fixture
def default_tools(tmp_path, monkeypatch):
    """The tools a new installation binds, from a fresh data folder (accounts not connected)."""
    import row_bot.tools as tool_package
    from row_bot.tool_outputs import build_reader_tool
    from row_bot.tools import registry

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in OPTIONAL_DEFAULT_MODULES:
        importlib.import_module(name)
    registry.set_tool_config("filesystem", "workspace_root", str(tmp_path / "workspace"))
    # Only Row-Bot's own modules: other tests may register stand-ins or import more optional tools.
    modules = set(tool_package._CORE_TOOL_MODULES) | set(OPTIONAL_DEFAULT_MODULES)
    tools = {}
    for owner in registry.get_all_tools():
        if not owner.enabled_by_default or type(owner).__module__ not in modules:
            continue
        for tool in owner.as_langchain_tools():
            tools[tool.name] = tool
    reader = build_reader_tool()
    tools[reader.name] = reader
    # Row-Bot's questions with options (N4) are bound with every tool set, like the reader.
    from row_bot.questions import build_tool as build_question_tool

    asker = build_question_tool()
    tools[asker.name] = asker
    return tools


def test_default_tool_schema_sizes_are_on_record(default_tools):
    sizes = {name: _schema_chars(tool) for name, tool in sorted(default_tools.items())}
    lines = [f"{name}: {chars}" for name, chars in sizes.items()]
    lines.append(f"TOTAL ({len(sizes)} tools): {sum(sizes.values())}")

    assert_matches_snapshot(SNAPSHOT, "\n".join(lines) + "\n")


def test_no_model_facing_description_starts_with_its_display_name(default_tools):
    from row_bot.tools import registry

    for owner in registry.get_all_tools():
        tool = default_tools.get(owner.name)
        if tool is not None:
            assert not tool.description.startswith(f"{owner.display_name}:"), owner.name


@pytest.mark.parametrize(
    ("tool_name", "field", "valid", "invalid"),
    [
        ("row_bot_status", "category", "version", "everything"),
        ("row_bot_update_setting", "setting", "dream_cycle", "model"),
        ("save_memory", "category", "person", "people"),
        ("task_create", "approval_mode", "approve", "safe"),
        ("analyze_image", "source", "screen", "webcam"),
        ("goal_update", "status", "completed", "done"),
        ("delegate_work", "context_mode", "focused", "everything"),
        ("custom_tool_builder", "action", "list", "build"),
        ("create_chart", "chart_type", "pie", "flowchart"),
    ],
)
def test_closed_sets_are_enums_that_reject_other_values(default_tools, tool_name, field, valid, invalid):
    tool = default_tools[tool_name]
    schema = convert_to_openai_tool(tool)["function"]["parameters"]["properties"][field]
    options = schema.get("enum") or next(branch["enum"] for branch in schema["anyOf"] if "enum" in branch)

    assert valid in options and invalid not in options
    required = {
        "row_bot_update_setting": {"value": "on"},
        "save_memory": {"subject": "Ada", "content": "Likes tea"},
        "task_create": {"name": "Check"},
        "analyze_image": {"question": "What is shown?"},
        "delegate_work": {"objective": "Review"},
        "create_chart": {"data_source": "a,b\n1,2"},
    }.get(tool_name, {})
    tool.args_schema.model_validate({**required, field: valid})
    with pytest.raises(ValidationError):
        tool.args_schema.model_validate({**required, field: invalid})


def test_a_goal_update_saying_paused_still_means_blocked_on_the_person(default_tools):
    """B316: the graceful mapping survives the enum."""
    assert default_tools["goal_update"].args_schema.model_validate({"status": "paused"}).status == "blocked"


@pytest.mark.parametrize(
    ("tool_name", "internal"),
    [
        ("delegate_work", {"parent_thread_id", "parent_run_id", "parent_message_id", "orchestration_id"}),
        ("agent_status", {"parent_thread_id"}),
        ("goal_update", {"thread_id"}),
        ("goal_status", {"thread_id"}),
        ("task_create", {"safety_mode"}),
        ("task_update", {"safety_mode"}),
    ],
)
def test_runtime_ids_and_legacy_aliases_are_not_offered_to_the_model(default_tools, tool_name, internal):
    properties = convert_to_openai_tool(default_tools[tool_name])["function"]["parameters"]["properties"]

    assert not internal & set(properties)
