"""Waits tool (5.3.1): wait for something and carry on in this conversation when it happens.

wait_for sets one: a background process of this conversation ends (or prints a text), a check every few minutes (a
command or a web page) shows a text, a time comes, or another conversation or workflow finishes. Nothing runs a
model while it waits; when it happens a turn starts here, "Checked back: …". A watch (B4) keeps checking a command,
a page or a question and starts a turn only when the result changes. waits lists them; wait_stop stops one.
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
    # The kind says once which fields it reads.
    kind: Literal["process", "check", "time", "conversation", "watch"] = Field(
        description=("process: process_id ends or prints text. check: command or url until it shows text (a "
                     "command without text: exits 0). time: in_minutes or at. conversation: it or a workflow "
                     "finishes. watch: command, url or question, checked again and again; a turn starts only on "
                     "a change."),
    )
    label: str = Field(description="As the person sees it: 'CI on PR 590 is green'.")
    then: str = Field(default="", description="What to do then.")
    process_id: str = ""
    command: str = Field(default="", description="Read-only, e.g. 'gh pr checks 590'.")
    url: str = ""
    question: str = Field(default="", description="watch: self-contained, answered by a read-only check.")
    text: str = Field(default="", description="watch: ends it once shown.")
    focus: str = Field(default="", description="watch: only lines with this count.")
    every_minutes: float = Field(default=0, description="check: 5 if 0. watch: 0 paces itself; a question 15+.")
    in_minutes: float = 0
    at: str = Field(default="", description="Local '14:00' or '2026-10-29T09:30'.")
    conversation: str = Field(default="", description="Name or id.")
    hours: float = Field(default=0, description="Longest: 2 if 0 (max 24); a watch 24 (max 168).")


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
    from row_bot.data_paths import workspace_folder
    from row_bot.tools import approval_gate
    from row_bot.tools.shell_tool import classify_command

    # A check runs in the person's workspace folder (waits.py): a relative path counts from there (F5).
    kind = classify_command(command, cwd=workspace_folder())
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


def _watch_approval(here: str, command: str, every_minutes: float, hours: float) -> tuple[str | None, dict]:
    """A watch's command, approved once when it's set (B3's exact-command review, B4): (the refusal, the approval).

    A command that can't run here or reads Row-Bot's private data is refused, and on Block only one that just
    reads. One that just reads needs no card; anything else asks by the approval level."""
    from row_bot import goal_checks, waits
    from row_bot.tools import approval_gate

    try:
        approval = waits.command_approval(here, command, "read_only")
    except goal_checks.CheckRefused as refused:
        return f"BLOCKED: {refused.words} {command}", {}
    if approval["classification"].get("read_only"):
        return None, approval
    every = f"every {every_minutes:g} min" if every_minutes else "every 5–60 min"
    refusal, _line = approval_gate.approval_check(
        {"tool": "wait_for", "label": "Watch with a command",
         "description": (f"Run `{command}` {every} in {approval['runs_in']} while it watches "
                         f"(up to {hours or waits.WATCH_HOURS:g} h)"),
         "args": {"command": command, "every_minutes": every_minutes}},
        risks=("runs a command again and again while it watches",),
        blocked_message=f"BLOCKED: watching with a command needs approval, and this chat is on Block: {command}",
        cancelled_message="The person didn't allow that watch; nothing watches.",
    )
    return refusal, {**approval, "approved_by": "approval"}


def _wait_for(kind: str, label: str, then: str = "", process_id: str = "", command: str = "", url: str = "",
              question: str = "", text: str = "", focus: str = "", every_minutes: float = 0, in_minutes: float = 0,
              at: str = "", conversation: str = "", hours: float = 0) -> str:
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
        elif kind == "watch":
            if sum(map(bool, (command, url, question))) != 1:
                return "A watch checks a command, a url or a question: give one of them."
            spec.update(focus=str(focus or "").strip()[:200], ends_when=str(text or "").strip()[:200])
            if question:
                spec.update(mode="judged", question=str(question).strip()[:1_000])
            elif url:
                refusal = waits.page_refusal(url)
                if refusal:
                    return f"Not set: {refusal}"
                spec.update(mode="page", url=url.strip())
            else:
                refusal, approval = _watch_approval(here, command, every_minutes, hours)
                if refusal:
                    return refusal
                spec.update(mode="command", command=command, approval=approval)
        elif kind == "check":
            if bool(command) == bool(url):
                return "A check runs a command or reads a url: give one of them."
            if url and not text:
                return "A page check needs the text to look for."
            if command:
                refusal = _check_approval(command, every_minutes or 5, label)
                if refusal:
                    return refusal
                from row_bot.data_paths import workspace_folder

                spec.update(command=command, text=text, folder=str(workspace_folder()))
            else:
                spec.update(url=url, text=text)
        elif kind == "time":
            when = _when(at, in_minutes)
        elif kind == "conversation":
            target = resolve(conversation, exclude=here)
            from row_bot.linked_conversations import _name

            spec.update(conversation_id=target, name=_name(target))
        minutes = every_minutes or (5 if kind == "check" else 0)
        record = waits.create(here, kind, label, spec=spec, every_minutes=minutes, hours=hours, at=when)
    except (waits.WaitError, LinkError, ValueError) as error:
        return f"Not set: {error}"
    view = waits.view(record)
    if kind == "watch":
        return (f"Watching “{view['label']}” ({view['how']}, until {_until(view)}). It speaks up here only "
                "when the result changes. End this turn now.")
    return (f"Waiting for “{view['label']}” ({view['how']}, until {view['until'][11:]}). Nothing runs while it "
            "waits; when it happens a turn starts here: carry on then. End this turn now.")


def _waits() -> str:
    from row_bot import waits

    found = waits.waiting(_here())
    if not found:
        return "This conversation waits for nothing."
    return "\n".join(f"- {view['wait_id']}: {'watching ' if view['kind'] == 'watch' else ''}{view['label']} "
                     f"({view['how']}, until {_until(view)}{', paused' if view['status'] == 'paused' else ''})"
                     for view in map(waits.view, found))


def _until(view: dict) -> str:
    """"14:00", or "2026-11-16 14:00" for a watch, which may last days."""
    return view["until"].replace("T", " ") if view["kind"] == "watch" else view["until"][11:]


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
                    "Carry on here when something happens: a process ends, a check (CI, a deploy, a page) shows a "
                    "text, a time comes ('check back in 10 minutes'), a conversation finishes, or a watch sees a "
                    "change (/watch, /loop). Use it, not polling or a task. Nothing runs meanwhile. Then end "
                    "this turn."
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
