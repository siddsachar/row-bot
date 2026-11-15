"""Computer use as tools: ``computer`` (look at and work in an app) and ``computer_record`` (a video).

``computer`` is one small tool. Each action says in a line what it did, then
names the window and lists its controls with their positions; a model that
sees images also gets the window's picture right after the result
(``browser.model_view``), and checks it itself. Coordinates are in that
picture. Errors say what went wrong and how to fix it.
"""

from __future__ import annotations

import concurrent.futures
import platform
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from row_bot.computer_use.service import ACTIONS, SETUP_FIX, ComputerError, get_computer_use_service
from row_bot.tools import registry
from row_bot.tools.base import BaseTool

# The text of a result: about 1 KB with a picture; a model without one gets more of the controls list.
TEXT_BUDGET_WITH_PICTURE = 1000
TEXT_BUDGET_WITHOUT_PICTURE = 3000


class ComputerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(description=(
        "screenshot, click, double_click, right_click, type, key, scroll, drag, wait, open_app or list_apps"))
    app: str | None = Field(default=None, description=(
        "For screenshot or open_app: the app's name, or words from its window's title. Later actions use that "
        "window."))
    x: int | None = Field(default=None, description="X in the latest screenshot (click, drag start, scroll)")
    y: int | None = Field(default=None, description="Y in the latest screenshot")
    to_x: int | None = Field(default=None, description="Drag end X")
    to_y: int | None = Field(default=None, description="Drag end Y")
    text: str | None = Field(default=None, description="For type: the text, typed where the last click put the caret")
    keys: str | None = Field(default=None, description="For key: one key or chord, e.g. enter, tab, ctrl+s")
    scroll: str | None = Field(default=None, description='For scroll: "down", "up 3", "left" or "right"')
    seconds: float | None = Field(default=None, description="For wait: 1-10 seconds (default 2)")


class ComputerRecordInput(BaseModel):
    action: str = Field(description="start or stop")
    minutes: int = Field(default=0, description="For start: stop by itself after this many minutes (default 5, at most 10)")


def _context() -> tuple[str, str]:
    """This conversation and turn; a ``ComputerError`` from a channel chat.

    A chat, a workflow, a goal or a helper agent may use the computer (5.3.1 B4): each app still asks
    the first time by the approval level, the Stop bar still shows and Esc still ends it, and one
    conversation uses the computer at a time. A channel chat (Telegram, Slack...) can't: the person
    is usually away from this computer's screen.
    """

    try:
        from row_bot.agent import get_active_runtime_context

        context = get_active_runtime_context()
    except Exception:
        context = {}
    if context.get("channel_streaming") or str(context.get("runtime_surface") or "") == "channel":
        raise ComputerError("Computer use works in Row-Bot on this computer, not from a channel chat.",
                            code="surface")
    thread_id = str(context.get("thread_id") or "")
    if not thread_id:
        raise ComputerError("Computer use works only in a chat.", code="surface")
    return thread_id, str(context.get("generation_id") or "")


def _setup() -> None:
    """A ``ComputerError`` with the fix when computer use isn't ready to run."""

    from row_bot.computer_use.readiness import ReadinessCode, readiness

    state = readiness(enabled=True)
    if state.code in {ReadinessCode.READY, ReadinessCode.DEGRADED}:
        return
    message = {
        ReadinessCode.DISCLOSURE_REQUIRED: "Computer use isn't set up yet: its notice about Cua Driver needs a look.",
        ReadinessCode.NOT_INSTALLED: "Computer use isn't set up yet: Cua Driver isn't installed.",
        ReadinessCode.UNSUPPORTED: "Computer use works on Windows and macOS only.",
        ReadinessCode.PERMISSION_MISSING: "Row-Bot doesn't have permission to see or control this computer.",
        ReadinessCode.HASH_MISMATCH: "Cua Driver doesn't match the reviewed version.",
        ReadinessCode.VERSION_MISMATCH: "Cua Driver doesn't match the reviewed version.",
    }.get(state.code, "Computer use isn't ready.")
    fix = {
        ReadinessCode.DISCLOSURE_REQUIRED: f"{SETUP_FIX}, review the notice, then install Cua Driver.",
        ReadinessCode.NOT_INSTALLED: f"{SETUP_FIX} › Install.",
        ReadinessCode.UNSUPPORTED: "Use Row-Bot's browser for websites.",
        ReadinessCode.PERMISSION_MISSING: f"{SETUP_FIX} › Check setup, and allow Row-Bot in Privacy & Security.",
        ReadinessCode.HASH_MISMATCH: f"{SETUP_FIX} › Install, to reinstall it.",
        ReadinessCode.VERSION_MISMATCH: f"{SETUP_FIX} › Install, to reinstall it.",
    }.get(state.code, f"{SETUP_FIX} › Check setup.")
    raise ComputerError(message, fix=fix, code=f"setup_{state.code.value}")


def _result(done: str, shot: Any, thread_id: str) -> str:
    """What the model reads after an action (the picture goes with it when the model sees images)."""

    from row_bot.browser import model_view

    sees = model_view.model_sees_images()
    window = shot.window
    head = (f'{done}\n{window.app} · "{window.title[:90]}" · screenshot {shot.size[0]}×{shot.size[1]}'
            + ("" if sees else " (no picture: this model reads the controls below)"))
    budget = (TEXT_BUDGET_WITH_PICTURE if sees else TEXT_BUDGET_WITHOUT_PICTURE) - len(head) - 12
    controls = shot.controls(max(0, budget))
    if sees:
        model_view.keep(thread_id, shot.image, caption=(
            f"{window.app} after that action ({shot.size[0]}×{shot.size[1]}; x,y are in this picture)."))
    else:
        model_view.forget(thread_id)
    return head + (f"\nControls: {controls}" if controls else "\nControls: none listed; aim by the picture.")


def _carry_on(service: Any, thread_id: str, generation_id: str) -> str:
    """The person used the computer since the last action: ask "Carry on?" before anything else."""

    from langgraph.types import interrupt

    from row_bot import questions

    app = service.asking()
    card = questions.card(questions.normalize([{
        "header": "Computer",
        "question": f"You used the computer while Row-Bot was working in {app}. Carry on?",
        "options": [
            {"label": "Carry on (Recommended)", "description": "Row-Bot looks at the screen again and goes on."},
            {"label": "Stop", "description": "Row-Bot stops using the computer."},
        ],
    }]))
    answer = interrupt(card)
    given = (answer.get("answers") or [{}])[0] if questions.outcome(answer) == "answered" else {}
    if 1 in (given.get("choices") or []):
        service.stopped_by_person(thread_id, generation_id)
        return ("The person stopped computer use. Don't use the computer again in this turn; say briefly where "
                "the task got to.")
    said = str(given.get("other") or "").strip()
    shot = service.carry_on()
    done = (f"The person used the computer, then said: “{said}”." if said
            else "The person used the computer, then said to carry on.")
    return _result(done + " Nothing was done for this call; this is the window now.", shot, thread_id)


def computer(
    action: str,
    app: str | None = None,
    x: int | None = None,
    y: int | None = None,
    to_x: int | None = None,
    to_y: int | None = None,
    text: str | None = None,
    keys: str | None = None,
    scroll: str | None = None,
    seconds: float | None = None,
) -> str:
    """Look at and work in an app on this computer."""

    action = str(action or "").strip().casefold()
    service = get_computer_use_service()
    thread_id = generation_id = ""
    try:
        if action not in ACTIONS:
            raise ComputerError(f'"{action}" isn\'t an action.', code="input", fix=f"Use one of: {', '.join(ACTIONS)}.")
        thread_id, generation_id = _context()
        _setup()
        service.begin(thread_id, generation_id)
        if service.must_ask():
            return _carry_on(service, thread_id, generation_id)
        if action == "list_apps":
            result = service.list_apps()
        elif action == "open_app":
            result = _result(f"Opened {app}." if app else "Opened.", service.open_app(str(app or "")), thread_id)
        elif action == "screenshot":
            shot = service.screenshot(str(app or ""))
            result = _result("Took a screenshot.", shot, thread_id)
        else:
            if app:
                service.use(str(app))
            done, shot = service.act(action, x=x, y=y, to_x=to_x, to_y=to_y, text=str(text or ""),
                                     keys=str(keys or ""), scroll=str(scroll or ""), seconds=seconds)
            result = _result(done, shot, thread_id)
        service.note_problem(thread_id, None)
        return result
    except ComputerError as error:
        if thread_id and error.code.startswith(("setup", "permission")):
            service.note_problem(thread_id, error, generation_id)
        return error.text()
    except concurrent.futures.CancelledError:
        raise


def _screen_recording_approval(minutes: int) -> dict[str, Any]:
    return {
        "tool": "computer_record",
        "label": "Record the screen",
        "question": "Record your screen?",
        "description": (
            f"Row-Bot will record everything on your main screen, including other apps and notifications, "
            f"for up to {minutes} minutes, and save the video to this chat's files. You can stop it at any time."
        ),
        "args": {"action": "start", "minutes": minutes},
    }


def computer_record(action: str, minutes: int = 0) -> str:
    """Start or stop recording the whole screen through Cua (F2); plain words, never JSON."""

    from row_bot import recordings
    from row_bot.computer_use.recording import ScreenRecording, setup_problem

    action = str(action or "").strip().casefold()
    if action not in {"start", "stop"}:
        return 'Use action "start" or "stop".'
    try:
        conversation, _generation = _context()
    except ComputerError:
        return "Recording the screen works in Row-Bot on this computer, not from a channel chat."
    if action == "stop":
        record = recordings.active_for(conversation, "screen")
        if record is None:
            latest = recordings.latest_for(conversation, "screen")
            return recordings.summary(latest) if latest else "The screen isn't being recorded in this chat."
        recordings.stop(record.recording_id, by="agent")
        recordings.wait(record, 30)
        return recordings.summary(record)
    running = recordings.active_for(conversation, "screen")
    if running is not None:
        return recordings.summary(running)
    problem = setup_problem()
    if problem:
        return problem
    chosen = recordings.minutes(minutes, "screen")
    from row_bot import ffmpeg_runtime
    from row_bot.tools.approval_gate import approval_check

    # On Windows Cua's video needs ffmpeg (F3): the one on PATH, the one Row-Bot downloaded, or (asked
    # for on this same card, so there is one question) a download first.
    ffmpeg = ffmpeg_runtime.find() if platform.system().casefold() == "windows" else None
    download = platform.system().casefold() == "windows" and ffmpeg is None
    approval = _screen_recording_approval(chosen)
    risks: tuple[str, ...] = ("records the whole screen, including other apps, to a video",)
    if download:
        wheel = ffmpeg_runtime.current_wheel()
        if wheel is None:
            return ffmpeg_runtime.unavailable_reason()
        consent = ffmpeg_runtime.consent_request("screen", "computer_record", wheel=wheel)
        approval = {
            **approval,
            "question": (f"Recording your screen needs ffmpeg (about {ffmpeg_runtime.size_words(wheel.size)}) "
                         "from PyPI. Download it and record your screen?"),
            "description": f"{consent['description']} Then {approval['description']}",
            "args": {**approval["args"], **consent["args"]},
        }
        risks += ffmpeg_runtime.consent_risks(wheel)
    refusal, _line = approval_check(
        approval,
        risks=risks,
        blocked_message="BLOCKED: Recording the screen needs approval, and this chat is on Block.",
        cancelled_message="The person didn't allow recording the screen; nothing was recorded.",
    )
    if refusal:
        return refusal
    if download:
        try:
            ffmpeg = ffmpeg_runtime.install(check=ffmpeg_runtime.turn_check())
        except ffmpeg_runtime.FfmpegError as exc:
            return f"{exc} The screen wasn't recorded."
    ffmpeg_folder = ffmpeg.folder if ffmpeg is not None and ffmpeg.source == "row_bot" else ""

    def begin(record, folder):
        capture = ScreenRecording(
            folder,
            max_seconds=record.max_seconds,
            size_limit=recordings.SIZE_LIMIT,
            on_end=lambda reason, video: recordings.finished(record, reason, video),
            ffmpeg_folder=ffmpeg_folder,
        )
        capture.begin()
        return capture

    try:
        record = recordings.start(conversation, "screen", max_minutes=chosen, begin=begin)
    except recordings.RecordingError as exc:
        return str(exc)
    except Exception:
        return "The screen recording couldn't start. Try again in a moment."
    return (recordings.summary(record) + " Do the steps to record, then call computer_record with action stop; "
            "the video is saved to this chat's files.")


DESCRIPTION = (
    "Use an app on this computer with the real mouse and keyboard while the person watches (Esc stops you); only "
    "for a task on the desktop. Start with list_apps, then screenshot or open_app with app=. Each action returns what it did, the "
    "window's controls with their x,y and, if you see images, its picture: check it before the next step. "
    "x,y are in the latest screenshot. Click a field before you type. For websites use the browser tools "
    "unless the person asks for their own browser."
)


class ComputerUseTool(BaseTool):
    @property
    def name(self) -> str:
        return "computer_use"

    @property
    def display_name(self) -> str:
        return "Computer use"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def enabled_by_default(self) -> bool:
        return False

    @property
    def destructive_tool_names(self) -> set[str]:
        # Risk depends on the app and the control: the service asks first (D19).
        return set()

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {"computer"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(func=computer, name="computer", description=DESCRIPTION,
                                         args_schema=ComputerInput),
            StructuredTool.from_function(
                func=computer_record, name="computer_record", args_schema=ComputerRecordInput,
                description=("Record the whole screen to a video in the chat: start (asks first), do the steps, "
                             "then stop."),
            ),
        ]


registry.register(ComputerUseTool())
