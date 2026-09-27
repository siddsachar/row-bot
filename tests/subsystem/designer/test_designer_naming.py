"""The agent can name a design that first-turn setup created with a placeholder."""
from __future__ import annotations

import pytest

from row_bot.designer.prompt import build_designer_prompt
from row_bot.designer.setup_flow import (
    DEFAULT_PROJECT_NAME,
    is_placeholder_project_name,
    placeholder_project_name,
)
from row_bot.designer.state import DesignerProject
from tests.subsystem.designer.test_designer_project_concurrency import _isolate_storage

pytestmark = pytest.mark.subsystem


def test_placeholder_names_are_the_ones_nobody_chose() -> None:
    auto = placeholder_project_name("13eee01b-5c4e-559e-a8b4-331844fe3bf9")

    assert auto == "Design 13eee01b"
    assert is_placeholder_project_name(auto)
    assert is_placeholder_project_name(DEFAULT_PROJECT_NAME)
    assert not is_placeholder_project_name("Orbit Notes pitch")
    assert not is_placeholder_project_name("Design review 2026")


def test_prompt_asks_to_name_only_a_placeholder_design() -> None:
    unnamed = build_designer_prompt(DesignerProject(name=placeholder_project_name("0123abcd")))
    named = build_designer_prompt(DesignerProject(name="Orbit Notes pitch"))

    assert "name it with designer_rename_project" in unnamed
    assert "name it with designer_rename_project" not in named
    assert "designer_rename_project" in named  # still listed as a tool


def test_rename_tool_saves_a_clean_name_with_a_history_step(tmp_path, monkeypatch) -> None:
    storage = _isolate_storage(tmp_path, monkeypatch)
    from row_bot.designer import tool

    storage.save_project(DesignerProject(id="deck", name=placeholder_project_name("0123abcd")))
    project = storage.load_project("deck")
    steps: list[str] = []
    monkeypatch.setattr(tool, "_require_project", lambda: project)
    monkeypatch.setattr(tool, "_pre_mutate", lambda _project, label="": steps.append(label))

    assert tool._rename_project("  Orbit \n Notes   pitch ") == 'Renamed the design to "Orbit Notes pitch".'
    assert storage.load_project("deck").name == "Orbit Notes pitch"
    assert steps == ["Rename design"]

    assert tool._rename_project("Orbit Notes pitch") == 'The design is already named "Orbit Notes pitch".'
    for bad in ("   ", "x" * 201, "bad\x00name"):
        assert tool._rename_project(bad).startswith("Error:")
    assert storage.load_project("deck").name == "Orbit Notes pitch"
    assert steps == ["Rename design"]


def test_rename_tool_is_offered_and_not_destructive(monkeypatch) -> None:
    from row_bot.designer import tool

    project = DesignerProject(name="Bound deck")
    monkeypatch.setattr(tool, "get_active_project", lambda: project)
    monkeypatch.setattr(tool, "get_ui_active_project", lambda: project)
    designer = tool.DesignerTool()
    names = [item.name for item in designer.as_langchain_tools()]

    assert "designer_rename_project" in names
    assert "designer_rename_project" not in designer.destructive_tool_names
