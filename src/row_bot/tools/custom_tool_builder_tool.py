"""Global Custom Tool builder utility."""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from row_bot.developer.sandbox import decide_action
from row_bot.developer.tool_capsules import _active_approval_mode, custom_tool_builder, get_custom_tool_draft
from row_bot.tools import registry
from row_bot.tools.base import BaseTool


class _CustomToolBuilderInput(BaseModel):
    action: str = Field(
        description="One of: start, show, list, refine, update, test, setup, create, enable, promote, delete."
    )
    source_path: str = Field(default="", description="Local repo/folder path for action=start.")
    source_url: str = Field(default="", description="Repo URL or label for action=start.")
    draft_id: str = Field(default="", description="Custom Tool draft id for draft actions.")
    instruction: str = Field(default="", description="Natural-language refinement instruction for action=refine.")
    command_name: str = Field(default="", description="Optional draft command name for action=test; defaults to first command.")
    fields: dict = Field(
        default_factory=dict,
        description=(
            "Extra fields. For action=start with a repo URL, pass clone_parent; if the user approves creating a "
            "missing parent, pass create_clone_parent=true. For delete, pass tool_id."
        ),
    )
    enable: bool = Field(default=True, description="Whether action=enable should enable or disable the created tool.")
    overwrite: bool = Field(default=False, description="Overwrite existing Custom Tool config during action=create.")
    delete_files: bool = Field(default=False, description="Also delete managed Custom Tool files when deleting. Usually false.")


def _run_builder(
    action: str,
    source_path: str = "",
    source_url: str = "",
    draft_id: str = "",
    instruction: str = "",
    command_name: str = "",
    fields: dict | None = None,
    enable: bool = True,
    overwrite: bool = False,
    delete_files: bool = False,
) -> str:
    refusal = _approve_running_code(action, draft_id, command_name, enable)
    if refusal:
        return refusal
    result = custom_tool_builder(
        action,
        source_path=source_path,
        source_url=source_url,
        draft_id=draft_id,
        instruction=instruction,
        command_name=command_name,
        fields=fields or {},
        enable=enable,
        overwrite=overwrite,
        delete_files=delete_files,
    )
    return json.dumps(result, indent=2, default=str)


_RUNS_CODE = {
    "test": "Test",
    "setup": "Install dependencies for",
    "create": "Create",
    "enable": "Turn on",
    "promote": "Offer in chats",
}


def _approve_running_code(action: str, draft_id: str, command_name: str, enable: bool) -> str:
    """Testing, setting up, creating, turning on or offering a Custom Tool lets its commands run on this computer,
    so the agent asks first (by the approval mode) and the card shows the exact commands."""
    verb = _RUNS_CODE.get(action.strip().lower())
    if not verb or not draft_id or (action.strip().lower() == "enable" and not enable):
        return ""
    try:
        draft = get_custom_tool_draft(draft_id)
    except KeyError:
        return ""
    decision = decide_action(_active_approval_mode(), "run_command")
    if decision.allowed:
        return ""
    if not decision.requires_approval:
        return decision.reason
    commands = [item for item in draft.commands if not command_name or item.get("name") == command_name]
    listed = "; ".join(str(item.get("command", "")) for item in commands) or "no commands"
    approval = interrupt({
        "tool": "custom_tool_builder",
        "label": f"{verb} Custom Tool",
        "description": f"{verb} {draft.name}. It runs on this computer: {listed}",
        "args": {"tool": draft.name, "action": action, "commands": [item.get("command", "") for item in commands]},
    })
    return "" if approval else "Cancelled by user."


class CustomToolBuilderTool(BaseTool):
    @property
    def name(self) -> str:
        return "custom_tool_builder"

    @property
    def display_name(self) -> str:
        return "Custom Tool Builder"

    @property
    def description(self) -> str:
        return (
            "Create reusable Custom Tools from GitHub repos or local folders. "
            "Use this instead of writing shell scripts when the user asks to turn a repo/folder into a tool."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def inference_keywords(self) -> list[str]:
        return [
            "custom tool",
            "turn repo into a tool",
            "add github repo as tool",
            "create tool from repo",
            "repo as a tool",
        ]

    def execute(self, query: str) -> str:
        return _run_builder("list")

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_run_builder,
                name="custom_tool_builder",
                description=(
                    "Use when the user asks to create, add, generate, or turn a GitHub repo/local folder into a "
                    "Row-Bot Custom Tool. Start with action='start'. If the user gives a repo URL, pass it as "
                    "source_url and pass fields.clone_parent when provided; if clone_parent is missing or must be "
                    "created, ask for it. Show proposed commands before action='create'. For Python repos, use "
                    "action='setup' to install dependencies into the tool's isolated .venv; do not run pip installs "
                    "manually in Row-Bot's environment. Use action='promote' only after explicit user request to make "
                    "the tool available in normal chat. Use this tool for Custom Tool lifecycle state; shell is only "
                    "for extra inspection or approved testing, not manual registration."
                ),
                args_schema=_CustomToolBuilderInput,
            )
        ]


registry.register(CustomToolBuilderTool())
