"""Turns the server starts to continue a conversation after a reply ends.

A goal keeps working turn after turn up to its limit, and work the assistant
set up in a reply (a new design or code folder) continues in a follow-up turn
once that reply has finished, when the new resource is bound for the turn.

A follow-up starts only after a completed turn: never after Stop, an error or
an approval pause, and a message the person queued goes first. Its prompt is
stored as an internal input whose public text is a short note ("Goal · turn 3
of 10"), so the transcript explains why the assistant continues.

Pending follow-ups live in memory: a restart drops them, and active goals are
paused at start (``goals.settle_interrupted_goals``) so nothing reads "Working"
while idle.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Literal

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()

FollowupKind = Literal["goal", "resource"]


@dataclass(frozen=True)
class Followup:
    kind: FollowupKind
    prompt: str
    note: str
    goal_id: str = ""


_PENDING: dict[str, Followup] = {}


def schedule(conversation_id: str, followup: Followup) -> None:
    """Remember the next follow-up; resource work goes before a goal step."""
    with _LOCK:
        current = _PENDING.get(conversation_id)
        if current is not None and current.kind == "resource" and followup.kind == "goal":
            return
        _PENDING[conversation_id] = followup


def discard(conversation_id: str, kind: FollowupKind | None = None) -> None:
    with _LOCK:
        current = _PENDING.get(conversation_id)
        if current is not None and (kind is None or current.kind == kind):
            _PENDING.pop(conversation_id, None)


def pending(conversation_id: str) -> Followup | None:
    with _LOCK:
        return _PENDING.get(conversation_id)


def take(conversation_id: str) -> Followup | None:
    with _LOCK:
        return _PENDING.pop(conversation_id, None)


def start(service: Any, conversation_id: str, followup: Followup, *, model_ref: str = "",
          runtime_surface: str = "normal_chat") -> dict | None:
    """Start a follow-up turn now; returns the submit receipt or ``None``.

    The model is the one the last turn used, else the conversation's saved
    choice. Bindings are read fresh, so a resource created in the previous
    turn is available to this one.
    """
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.providers.selection import model_choice_value, parse_model_ref

    model = model_choice_value(model_ref or _conversation_model(service, conversation_id))
    parsed = parse_model_ref(model) if model else None
    if not parsed:
        _LOG.info("Follow-up for %s skipped: no model", conversation_id)
        return None
    payload = {
        "submission_id": str(uuid.uuid4()),
        "text": followup.prompt,
        "model_selection": {"provider_id": parsed[0], "model_ref": model},
    }
    try:
        return service._start(conversation_id, payload, resume=False, followup=followup,
                              runtime_surface=runtime_surface)
    except ClientPlatformError as error:
        _LOG.info("Follow-up for %s not started: %s", conversation_id, error)
        return None


def _conversation_model(service: Any, conversation_id: str) -> str:
    try:
        row = service._metadata(conversation_id)
    except Exception:
        return ""
    from row_bot.models import get_current_model
    selected = str(row.get("model_override") or "")
    if selected:
        return selected
    try:
        return str(get_current_model() or "")
    except Exception:
        return ""


def after_finish(service: Any, handle: Any, status: str) -> None:
    """Run the pending follow-up once a platform turn has fully finished."""
    conversation_id = handle.conversation_id
    if status != "completed":
        discard(conversation_id)
        goal = live_goal(conversation_id)
        # Stop can land while the goal step was still deciding.
        if goal is not None and goal.get("status") == "active" and status != "waiting_approval":
            from row_bot import goals
            goals.set_goal_status(str(goal["id"]), "paused",
                                  reason="You stopped the reply." if status == "stopped"
                                  else "The reply didn't finish.",
                                  verdict="paused", expected_revision=int(goal.get("revision") or 0))
        return
    if service.registry.active(conversation_id):
        return  # A message the person queued went first; the follow-up waits.
    followup = take(conversation_id)
    if followup is None:
        return
    goal = live_goal(conversation_id)
    if followup.kind == "goal" and (goal is None or goal.get("status") != "active"):
        return  # Paused, stopped or done since it was scheduled.
    start(service, conversation_id, followup, model_ref=handle.model_ref,
          runtime_surface=handle.runtime_surface)


# ------------------------------------------------------------------ goals


def goal_note(goal: dict[str, Any]) -> str:
    used = int(goal.get("turns_used") or 0)
    limit = int(goal.get("max_turns") or 0)
    return f"Goal · turn {min(used + 1, limit) if limit else used + 1} of {limit}" if limit else "Goal"


def live_goal(conversation_id: str) -> dict[str, Any] | None:
    from row_bot import goals
    goal = goals.get_current_goal(conversation_id)
    if goal and str(goal.get("status") or "") in goals.GOAL_ACTIVE_STATUSES:
        return goal
    return None


def after_platform_turn(conversation_id: str, *, generation_id: str, status: str,
                        assistant_text: str, model_ref: str, goal_id: str = "") -> None:
    """Advance the conversation's goal after one of its turns ended.

    Completed turns count toward the goal and may schedule the next step;
    an approval pause marks the goal as waiting; Stop and failures pause it
    with the reason, so it never reads as working while nothing runs. A turn
    in which the model finished the goal (``goal_id`` was live when it began)
    still counts, so the card reads "Done · Turn 3 of 3".
    """
    from row_bot import goals
    goal = live_goal(conversation_id)
    if goal is None:
        finished = (goals.get_current_goal(conversation_id, include_terminal=True)
                    if goal_id and status == "completed" else None)
        if finished and str(finished.get("id") or "") == goal_id:
            try:
                goals.after_turn(thread_id=conversation_id, turn_id=generation_id,
                                 assistant_text=assistant_text, model_override=model_ref)
            except Exception:
                _LOG.exception("Counting the goal's last turn failed for %s", conversation_id)
        return
    try:
        if status == "completed":
            decision = goals.after_turn(thread_id=conversation_id, turn_id=generation_id,
                                        assistant_text=assistant_text, model_override=model_ref)
            if decision.should_continue and decision.goal:
                schedule(conversation_id, Followup(
                    kind="goal", prompt=decision.continuation_prompt,
                    note=goal_note(decision.goal), goal_id=str(decision.goal.get("id") or "")))
            else:
                discard(conversation_id, "goal")
        elif status == "waiting_approval":
            goals.after_turn(thread_id=conversation_id, turn_id=generation_id,
                             assistant_text=assistant_text, model_override=model_ref,
                             pending_approval=True)
            discard(conversation_id, "goal")
        else:
            discard(conversation_id, "goal")
            reason = ("You stopped the reply." if status == "stopped"
                      else "The reply didn't finish.")
            goals.set_goal_status(str(goal["id"]), "paused", reason=reason, verdict="paused",
                                  expected_revision=int(goal.get("revision") or 0))
    except Exception:
        _LOG.exception("Goal step after a turn failed for %s", conversation_id)


def start_goal_turn(service: Any, conversation_id: str, goal: dict[str, Any], *,
                    initial: bool) -> None:
    """Begin working on a goal now, or right after the running turn."""
    from row_bot import goals
    prompt = (goals.build_initial_goal_prompt(goal) if initial
              else goals.build_continuation_prompt(goal))
    followup = Followup(kind="goal", prompt=prompt, note=goal_note(goal),
                        goal_id=str(goal.get("id") or ""))
    if service.registry.active(conversation_id):
        schedule(conversation_id, followup)
        return
    if start(service, conversation_id, followup) is None:
        goals.set_goal_status(str(goal["id"]), "paused",
                              reason="The goal couldn't start. Choose a model, then resume it.",
                              verdict="paused", expected_revision=int(goal.get("revision") or 0))


def after_goal_change(service: Any, conversation_id: str, operation: str,
                      goal: dict[str, Any] | None) -> None:
    """Start, resume, pause or end the goal's turns after a goal command."""
    if operation in {"start", "resume"} and goal and goal.get("status") == "active":
        start_goal_turn(service, conversation_id, goal, initial=operation == "start")
    elif operation in {"pause", "complete", "clear"}:
        discard(conversation_id, "goal")


def after_approval(conversation_id: str, *, approved: bool) -> None:
    """A decided approval lets a waiting goal go on, or ends it when denied."""
    from row_bot import goals
    goal = goals.get_current_goal(conversation_id)
    if not goal or goal.get("status") != "waiting_approval":
        return
    if approved:
        goals.set_goal_status(str(goal["id"]), "active", reason="Approved. Continuing.",
                              verdict="continue", expected_revision=int(goal.get("revision") or 0))
    else:
        goals.set_goal_status(str(goal["id"]), "blocked", reason="You denied the approval it needed.",
                              verdict="blocked", finish_run_status="blocked",
                              expected_revision=int(goal.get("revision") or 0))
