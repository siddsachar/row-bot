"""Waits tool (5.3.1): wait for something and carry on in this conversation when it happens.

wait_for sets one: a background process of this conversation ends (or prints a text), a check every few minutes (a
command or a web page) shows a text, a time comes, or another conversation or workflow finishes. Nothing runs a
model while it waits; when it happens a turn starts here, "Checked back: …". waits lists them; wait_stop stops one.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool

logger = logging.getLogger(__name__)


class _WaitForInput(BaseModel):
    kind: Literal["process", "check", "time", "conversation"] = Field(
        description=("process: a background process ends or prints `text`. check: `command` or `url` every "
                     "`every_minutes` until it shows `text` (a command without text: exits 0). time: `in_minutes` or "
                     "`at`. conversation: it, or a workflow, finishes."),
    )
    label: str = Field(description="What it waits for, as the person sees it: 'CI on PR 590 is green'.")
    then: str = Field(default="", description="What to do then.")
    process_id: str = Field(default="", description="process: from start_process.")
    command: str = Field(default="", description="check: a read-only command, e.g. 'gh pr checks 590'.")
    url: str = Field(default="", description="check: a page instead of a command.")
    text: str = Field(default="", description="process or check: text to watch for.")
    every_minutes: float = Field(default=5, description="check: minutes between checks (1 or more).")
    in_minutes: float = Field(default=0, description="time: minutes from now.")
    at: str = Field(default="", description="time: local '14:00' or '2026-10-29T09:30'.")
    conversation: str = Field(default="", description="conversation: name or id (a workflow's too).")
    hours: float = Field(default=2, description="At most this long (up to 24).")


class _WaitStopInput(BaseModel):
    wait_id: str = Field(description="The wait's id from waits.")


def _here() -> str:
    from row_bot.linked_conversations import origin

    return origin().conversation_id


def _when(at: str, in_minutes: float) -> datetime | None:
    if in_minutes:
        return datetime.now() + timedelta(minutes=float(in_minutes))
    text = str(at or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = map(int, text.split(":"))
        moment = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        return moment if moment > datetime.now() else moment + timedelta(days=1)
    moment = datetime.fromisoformat(text)
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def _check_approval(command: str, every_minutes: float, label: str) -> str | None:
    """A check's command is classified as run_command classifies it, once, when the wait is set; the refusal or None."""
    from row_bot.tools import approval_gate
    from row_bot.tools.shell_tool import classify_command

    kind = classify_command(command)
    if kind == "blocked":
        return f"BLOCKED: that command can't run: {command}"
    if kind == "private_data":
        return "BLOCKED: a check can't read Row-Bot's private data folder."
    if kind == "safe":
        return None
    refusal, _line = approval_gate.approval_check(
        {"tool": "wait_for", "label": "Check repeatedly",
         "description": f"Run `{command}` every {every_minutes:g} min until “{label}”",
         "args": {"command": command, "every_minutes": every_minutes}},
        risks=("runs a command repeatedly while it waits",),
        blocked_message=f"BLOCKED: checking with a command needs approval, and this chat is on Block: {command}",
        cancelled_message="The person didn't allow that check; nothing waits.",
    )
    return refusal


def _wait_for(kind: str, label: str, then: str = "", process_id: str = "", command: str = "", url: str = "",
              text: str = "", every_minutes: float = 5, in_minutes: float = 0, at: str = "", conversation: str = "",
              hours: float = 2) -> str:
    from row_bot import waits
    from row_bot.linked_conversations import LinkError, resolve

    try:
        here = _here()
        spec: dict = {"then": str(then or "").strip()[:1_000]}
        when = None
        if kind == "process":
            from row_bot import background_processes as processes

            record = processes.get(process_id)
            if record is None or record.conversation_id != here:
                return f"No background process {process_id!r} in this conversation: check_process lists them."
            spec.update(process_id=process_id, text=text)
        elif kind == "check":
            if bool(command) == bool(url):
                return "A check runs a command or reads a url: give one of them."
            if url and not text:
                return "A page check needs the text to look for."
            if command:
                refusal = _check_approval(command, every_minutes, label)
                if refusal:
                    return refusal
                from row_bot.data_paths import default_workspace_root

                spec.update(command=command, text=text, folder=str(default_workspace_root()))
            else:
                spec.update(url=url, text=text)
        elif kind == "time":
            when = _when(at, in_minutes)
        elif kind == "conversation":
            target = resolve(conversation, exclude=here)
            from row_bot.linked_conversations import _name

            spec.update(conversation_id=target, name=_name(target))
        record = waits.create(here, kind, label, spec=spec, every_minutes=every_minutes, hours=hours, at=when)
    except (waits.WaitError, LinkError, ValueError) as error:
        return f"Not set: {error}"
    view = waits.view(record)
    return (f"Waiting for “{view['label']}” ({view['how']}, until {view['until'][11:]}). Nothing runs while it "
            "waits; when it happens a turn starts here: carry on then. End this turn now.")


def _waits() -> str:
    from row_bot import waits

    found = waits.waiting(_here())
    if not found:
        return "This conversation waits for nothing."
    return "\n".join(f"- {view['wait_id']}: {view['label']} ({view['how']}, until {view['until'][11:]})"
                     for view in map(waits.view, found))


def _wait_stop(wait_id: str) -> str:
    from row_bot import waits

    return ("Stopped: nothing starts for it." if waits.stop(wait_id, conversation_id=_here(), by="agent")
            else f"No wait {wait_id!r} is waiting in this conversation: waits lists them.")


class WaitsTool(BaseTool):

    @property
    def name(self) -> str:
        return "waits"

    @property
    def display_name(self) -> str:
        return "⏳ Waits"

    @property
    def description(self) -> str:
        return ("Wait for something and carry on when it happens: a background process ends, a check (CI, a deploy, "
                "a page) shows a text, a time comes, or another conversation or workflow finishes.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_wait_for,
                name="wait_for",
                description=(
                    "Wait for something, then carry on here: a process ends, a check (CI, a deploy, a page) shows "
                    "a text, a time comes ('check back in 10 minutes'), or another conversation finishes. Use it "
                    "instead of polling or a one-time task. Nothing runs while it waits; when it happens a turn "
                    "starts here. Then end this turn."
                ),
                args_schema=_WaitForInput,
            ),
            StructuredTool.from_function(
                func=_waits,
                name="waits",
                description="List what this conversation waits for.",
            ),
            StructuredTool.from_function(
                func=_wait_stop,
                name="wait_stop",
                description="Stop a wait: nothing starts for it.",
                args_schema=_WaitStopInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _waits()


registry.register(WaitsTool())
