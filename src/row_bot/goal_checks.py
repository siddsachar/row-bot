"""Goal checks (B3): a command the person set that must exit 0 before their goal counts as done.

Only the person sets a check, in the goal form or with ``/goal … check: <command>``; the goal tool has no
field for it, so the agent can't write or loosen its own gate. The goal's review says where the check runs,
and the person's Confirm of that review is the approval of that exact command, in that folder, for that goal,
at every approval level (``review_check``). A command that can't run here (``blocked``) or reads Row-Bot's
private data is always refused, and a chat on Block allows only checks that just read. Each run classifies the
command again and refuses it when the chat moved to Block, the folder changed or what the command counts as
changed (``preflight``).

When the goal looks done (``goals.request_completion``) the check runs once, on a worker thread: in the
conversation's code folder (its Docker sandbox when it uses one), else in the person's workspace folder, the one
the file tools and the shell use (``data_paths.workspace_folder``), for at most ten minutes.
``goals.finish_check`` turns the result into the goal's next step: exit 0 completes it, another exit code gives
Row-Bot a turn to fix what it reported, and a check that couldn't run pauses it.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from row_bot.cancellation import CancellationScope, use_cancellation_scope

_LOG = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 600
OUTPUT_TAIL_CHARS = 2_000
COMMAND_CHARS = 500
DONE_MEANS_CHARS = 1_000

REFUSED = "goal_check_refused"
READ_ONLY = "goal_check_read_only"
UNAVAILABLE = "goal_check_unavailable"
_REFUSED_WORDS = "That command can't run here."
_READ_ONLY_WORDS = "This chat is on Block, which only allows checks that just read."

_LOCK = threading.Lock()
# Checks running now: goal id -> (conversation id, its cancellation scope).
_RUNNING: dict[str, tuple[str, CancellationScope]] = {}


class CheckRefused(ValueError):
    """A check that can't be set or run, with a stable code and the words the person reads."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(words)
        self.code = code
        self.words = words


@dataclass(frozen=True)
class Place:
    """Where a goal's check runs."""

    folder: str
    workspace_id: str  # The conversation's code folder; '' for the person's workspace folder.
    # "row-bot (Docker sandbox, network off)", "row-bot", "Row-Bot's workspace (~/Work)": what the person approves.
    runs_in: str
    docker: bool = False
    network: str = ""


@dataclass(frozen=True)
class CheckRun:
    """What one run of a check did."""

    exit_code: int | None
    output: str = ""
    timed_out: bool = False
    cancelled: bool = False
    refused: str = ""  # Why it didn't run (the folder's approval rules).
    error: str = ""  # Why it couldn't start.


_NETWORK_WORDS = {"off": "network off", "ask": "network asks first", "on": "network on"}
_FOLDER_CHARS = 200


def place_for(conversation_id: str) -> Place:
    """The conversation's code folder (its Docker sandbox when it uses one), else the person's workspace folder,
    named as ``~/…`` under the home folder."""
    from row_bot.data_paths import display_path, workspace_folder

    try:
        from row_bot.developer import tool_context
        from row_bot.developer.storage import get_workspace

        workspace_id = tool_context.infer_workspace_id_from_thread(conversation_id)
        workspace = get_workspace(workspace_id) if workspace_id else None
    except Exception:
        _LOG.debug("No code folder for the goal check in %s", conversation_id, exc_info=True)
        workspace = None
    if workspace is not None and workspace.path and Path(workspace.path).is_dir():
        docker = workspace.execution_mode == "docker"
        network = str(workspace.sandbox_network or "off") if docker else ""
        name = str(workspace.name or Path(workspace.path).name)
        runs_in = f"{name} (Docker sandbox, {_NETWORK_WORDS.get(network, 'network off')})" if docker else name
        return Place(str(Path(workspace.path)), str(workspace.id), runs_in, docker, network)
    folder = workspace_folder()
    shown = display_path(folder)
    if len(shown) > _FOLDER_CHARS:  # The review names at most 300 characters; a deep folder shows its end.
        shown = "…" + shown[-(_FOLDER_CHARS - 1):]
    return Place(str(folder), "", f"Row-Bot's workspace ({shown})")


def suggestions(place: Place) -> list[str]:
    """The project's own test, lint and type-check commands, in a code folder ("npm test", "python -m pytest")."""
    if not place.workspace_id:
        return []
    from row_bot.developer.runtime import detect_project_commands

    try:
        found = detect_project_commands(place.folder)
    except Exception:
        _LOG.debug("No project commands for the goal check in %s", place.folder, exc_info=True)
        return []
    return [spec.command for spec in found if spec.kind != "server"][:4]


def classification(command: str, place: Place) -> dict[str, Any]:
    """What the command counts as where it runs: the shell's class, a code folder's action, and whether it
    only reads (the strictest of both)."""
    from row_bot.tools.shell_tool import classify_command

    shell = classify_command(command, cwd=place.folder)
    if place.workspace_id:
        from row_bot.developer.runtime import classify_command_action

        action = classify_command_action(command)
        read_only = shell == "safe" and action == "run_safe_command"
    else:
        action = ""
        read_only = shell == "safe"
    return {"shell": shell, "action": action, "read_only": read_only}


def conversation_level(conversation_id: str) -> str:
    """The approval level the conversation runs at now."""
    from row_bot.threads import _get_thread_approval_mode

    return _get_thread_approval_mode(conversation_id)


def _refusal(place: Place, level: str, kind: Mapping[str, Any]) -> CheckRefused | None:
    if kind.get("shell") in {"blocked", "private_data"}:
        return CheckRefused(REFUSED, _REFUSED_WORDS)
    if place.docker and place.network == "off" and kind.get("action") in {"run_network", "run_install"}:
        return CheckRefused(REFUSED, "That command needs the network, and this code folder's Docker sandbox "
                                     "has it off.")
    if level == "block" and not kind.get("read_only"):
        return CheckRefused(READ_ONLY, _READ_ONLY_WORDS)
    return None


def review_check(conversation_id: str, command: str) -> dict[str, Any]:
    """Where the person's check would run and what it counts as; raises ``CheckRefused`` when it can't be set.

    The result is part of the goal's review, so the person's Confirm approves exactly this.
    """
    from row_bot.approval_policy import normalize_approval_mode

    place = place_for(conversation_id)
    level = normalize_approval_mode(conversation_level(conversation_id))
    kind = classification(command, place)
    refused = _refusal(place, level, kind)
    if refused is not None:
        raise refused
    return {"command": command, "workspace_id": place.workspace_id, "runs_in": place.runs_in, "level": level,
            "classification": kind}


def disclosure(check: Mapping[str, Any]) -> str:
    """The sentence the goal's review shows before Start goal."""
    return (f"Each time Row-Bot thinks the goal is done, it runs `{check['command']}` in {check['runs_in']}. "
            "The goal counts as done only when it exits 0.")


def preflight(goal: Mapping[str, Any]) -> str:
    """Why the goal's check may not run now ('' when it may): each run checks the level and classification
    again."""
    from row_bot import goals

    command = str(goal.get("check_command") or "")
    approval = goals.check_approval(goal)
    if approval.get("approved_by") != "person":
        return f"No one approved `{command}`. Set the check again."
    return recheck(str(goal.get("thread_id") or ""), command, str(goal.get("check_workspace_id") or ""),
                   approval.get("classification"), runs_in=str(approval.get("runs_in") or ""))


def recheck(conversation_id: str, command: str, workspace_id: str, approved_kind: Any, *, runs_in: str) -> str:
    """Why an approved command may not run now ('' when it may): the conversation's code folder, or outside one
    the workspace folder (``runs_in`` names it), is still the one it was approved for, the level allows it (Block:
    only commands that just read) and it still counts as what was approved. A goal's check and a watch's command
    (B4) are both checked here before each run."""
    from row_bot.approval_policy import normalize_approval_mode

    place = place_for(conversation_id)
    if place.workspace_id != workspace_id:
        return "The conversation's code folder changed since you set the check. Set the check again."
    if not place.workspace_id and place.runs_in != runs_in:
        return "Row-Bot's workspace folder changed since you set the check. Set the check again."
    level = normalize_approval_mode(conversation_level(conversation_id))
    kind = classification(command, place)
    refused = _refusal(place, level, kind)
    if refused is not None:
        return ("This chat is on Block now, which only allows checks that just read." if refused.code == READ_ONLY
                else refused.words)
    if kind != approved_kind:
        return f"`{command}` counts as a different kind of command than when you set it. Set the check again."
    return ""


def execute_check(command: str, place: Place, *, level: str, thread_id: str, timeout: int) -> CheckRun:
    """Run the check once where it runs, under the current cancellation scope."""
    if place.workspace_id:
        from row_bot.developer.runtime import run_workspace_shell_command

        # The person approved this command for this goal: their Confirm is the approval (``confirmed``).
        result = run_workspace_shell_command(place.folder, command, level,  # type: ignore[arg-type]
                                             workspace_id=place.workspace_id, thread_id=thread_id,
                                             timeout=timeout, confirmed=True)
        if result.returncode is None:
            return CheckRun(None, refused=str(result.stderr or result.decision.reason))
        output = (result.stdout or "") + (result.stderr or "")
        timed_out = result.returncode == 124 and str(result.stderr or "").startswith("Command timed out")
        return CheckRun(result.returncode, output, timed_out=timed_out)
    from row_bot.process_cancellation import run_cancellable_subprocess
    from row_bot.tools.shell_tool import shell_argv

    try:
        done = run_cancellable_subprocess(shell_argv(command, report_cwd=False), cwd=place.folder, timeout=timeout,
                                          encoding="utf-8", errors="replace")
    except OSError as error:
        return CheckRun(None, error=str(error))
    return CheckRun(done.returncode, (done.stdout or "") + (done.stderr or ""), timed_out=done.timed_out,
                    cancelled=done.cancelled)


def _seconds_words(seconds: float) -> str:
    """"2 min 14 s", "3 s"."""
    total = max(0, round(seconds))
    minutes, rest = divmod(total, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def run_check(goal_id: str, *, execute: Callable[..., CheckRun] | None = None) -> Any:
    """Run the goal's pending check now, on this thread, and settle the goal by its result.

    Returns ``goals.finish_check``'s decision, or ``None`` when no check was pending, one already runs, or the
    person paused or stopped the goal while it ran.
    """
    from row_bot import goals
    from row_bot.approval_policy import normalize_approval_mode

    goal = goals.get_goal(goal_id)
    if not goal:
        return None
    with _LOCK:
        if goal_id in _RUNNING:
            return None
        scope = CancellationScope()
        _RUNNING[goal_id] = (str(goal.get("thread_id") or ""), scope)
    try:
        goal = goals.mark_check_running(goal_id)
        if not goal:
            return None
        _publish()
        command = str(goal.get("check_command") or "")
        conversation_id = str(goal.get("thread_id") or "")
        started_at, started = goals._now(), time.monotonic()
        why = preflight(goal)
        if why:
            return goals.finish_check(goal_id, state="error", reason=why,
                                      result=_result(None, "", started_at, 0.0))
        place = place_for(conversation_id)
        with use_cancellation_scope(scope):
            try:
                run = (execute or execute_check)(
                    command, place, level=normalize_approval_mode(conversation_level(conversation_id)),
                    thread_id=conversation_id, timeout=CHECK_TIMEOUT_SECONDS)
            except Exception as error:
                _LOG.warning("The goal check of %s could not run", goal_id, exc_info=True)
                run = CheckRun(None, error=str(error) or type(error).__name__)
        if scope.is_cancelled() or run.cancelled:
            return None  # The person paused or stopped the goal: ``stop`` already set it back.
        result = _result(run.exit_code, run.output, started_at, time.monotonic() - started)
        if run.refused:
            return goals.finish_check(goal_id, state="error", result=result,
                                      reason=f"Row-Bot couldn't run `{command}`: {run.refused}")
        if run.error:
            return goals.finish_check(goal_id, state="error", result=result,
                                      reason=f"`{command}` couldn't start: {run.error}")
        if run.timed_out:
            return goals.finish_check(goal_id, state="error", result=result,
                                      reason=f"`{command}` didn't finish in {CHECK_TIMEOUT_SECONDS // 60} minutes.")
        state = "passed" if run.exit_code == 0 else "failed"
        return goals.finish_check(goal_id, state=state, result=result)
    finally:
        with _LOCK:
            _RUNNING.pop(goal_id, None)
        _publish()


def _result(exit_code: int | None, output: str, started_at: str, seconds: float) -> dict[str, Any]:
    text = str(output or "").strip()
    from row_bot import goals

    return {"exit_code": exit_code, "started_at": started_at, "finished_at": goals._now(),
            "duration_seconds": max(0, round(seconds)), "duration": _seconds_words(seconds),
            "output_tail": text[-OUTPUT_TAIL_CHARS:], "output_cut": len(text) > OUTPUT_TAIL_CHARS}


def start(goal_id: str, on_result: Callable[[Any], None] | None = None) -> threading.Thread:
    """Run the goal's pending check on a worker thread; ``on_result`` gets ``run_check``'s decision."""

    def work() -> None:
        try:
            decision = run_check(goal_id)
            if decision is not None and on_result is not None:
                on_result(decision)
        except Exception:
            _LOG.exception("The goal check of %s failed", goal_id)

    thread = threading.Thread(target=work, name="row-bot-goal-check", daemon=True)
    thread.start()
    return thread


def stop_conversation(conversation_id: str) -> None:
    """The person paused, stopped, completed or replaced the conversation's goal: a running check stops and a
    pending one is dropped."""
    from row_bot import goals

    with _LOCK:
        running = [scope for owner, scope in _RUNNING.values() if owner == conversation_id]
    for scope in running:
        scope.cancel("goal changed")
    if goals.reset_checks(conversation_id) or running:
        _publish()


def _publish() -> None:
    """Open clients read the goal again (its card shows the check's state)."""
    try:
        from row_bot.application.live_updates import live_updates

        live_updates.publish("conversations")
    except Exception:
        _LOG.debug("Live update for a goal check skipped", exc_info=True)
