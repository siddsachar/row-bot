"""D1 Always allow, from the real tools: the card ``run_command`` and a moved file raise offers the rule
the person would save, scoped to where the action runs; and a command only the person approves offers
none. The person says no on each card, so nothing runs. Approval tests: they stay in the pull request lane.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool

from row_bot import approval_rules

pytestmark = pytest.mark.platform


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    folder = tmp_path / "Documents"
    folder.mkdir()
    from row_bot.tools import registry

    real = registry.get_tool_config
    monkeypatch.setattr(registry, "get_tool_config", lambda tool, key, default=None: str(folder) if (
        tool, key) == ("filesystem", "workspace_root") else real(tool, key, default))
    return folder


def _cards(monkeypatch, target: str) -> list[dict]:
    asked: list[dict] = []
    monkeypatch.setattr(target, lambda payload: asked.append(payload) or False)
    return asked


def test_a_command_card_offers_that_command_in_the_folder_it_runs_in(workspace, monkeypatch):
    from row_bot.tools import approval_gate, shell_tool

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    asked = _cards(monkeypatch, "langgraph.types.interrupt")
    config = {"configurable": {"thread_id": "rule-shell-d1"}}

    output = shell_tool.ShellTool().execute("mkdir build-output", runtime_config=config)
    shell_tool.ShellTool().execute("git config user.name Sam", runtime_config=config)

    assert "cancelled" in output.casefold() and not (workspace / "build-output").exists()
    offered = approval_rules.from_interrupt(asked[0])
    assert offered is not None and (offered.kind, offered.command) == ("command", "mkdir build-output")
    assert Path(offered.folder) == workspace.resolve()
    assert approval_rules.from_interrupt(asked[1]) is None, "Git's identity is the person's own"


def test_a_moved_file_card_offers_moves_in_the_workspace(workspace, monkeypatch):
    import row_bot.agent as agent

    asked = _cards(monkeypatch, "row_bot.agent.interrupt")
    monkeypatch.setattr(agent, "get_approval_mode", lambda: "approve")
    moved: list[tuple[str, str]] = []
    tool = StructuredTool.from_function(func=lambda source_path, destination_path: moved.append(
        (source_path, destination_path)) or "Moved.", name="workspace_move_file", description="Move a file.")
    agent._wrap_with_interrupt_gate(tool)

    output = tool.invoke({"source_path": "invoice.pdf", "destination_path": "Taxes/invoice.pdf"})

    assert "cancelled" in output.casefold() and moved == []
    offered = approval_rules.from_interrupt(asked[0])
    assert offered is not None and offered.kind == "move" and Path(offered.folder) == workspace.resolve()
