from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from row_bot.approval_policy import DEFAULT_APPROVAL_MODE


def approval_mode_for_config(config: dict) -> str:
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "")
    if not thread_id:
        return DEFAULT_APPROVAL_MODE
    try:
        from row_bot.threads import _get_thread_approval_mode

        return _get_thread_approval_mode(thread_id)
    except Exception:
        return DEFAULT_APPROVAL_MODE


@dataclass(frozen=True)
class ChannelGoalStart:
    """Prepared channel Goal start that should enter the normal agent path."""

    goal: dict[str, Any]
    prompt: str
    objective: str


@dataclass(frozen=True)
class ChannelGoalRunResult:
    """Result from a channel Goal run/continuation loop."""

    turns: int
    status: str
    reason: str
    interrupt_data: Any | None = None


def prepare_channel_goal_start(text: str, thread_id: str | None) -> ChannelGoalStart | None:
    """Start a channel ``/goal <objective>`` and return the initial agent prompt.

    Goal control commands return ``None`` so existing command dispatch keeps
    handling ``/goal``, ``/goal status``, ``/goal pause``, and friends.
    """

    message = str(text or "").strip()
    parts = message.split(maxsplit=1)
    if not parts or parts[0].lower() != "/goal" or not thread_id:
        return None
    arg = parts[1] if len(parts) > 1 else ""
    from row_bot import goals

    if not goals.is_goal_start_argument(arg):
        return None
    goal = goals.start_goal(str(thread_id), arg)
    return ChannelGoalStart(
        goal=goal,
        prompt=goals.build_initial_goal_prompt(goal),
        objective=str(goal.get("objective") or arg),
    )


def format_goal_started_ack(goal_start: ChannelGoalStart) -> str:
    """Return the immediate channel acknowledgement for a started Goal."""

    objective = " ".join(str(goal_start.objective or "Goal").split())
    if len(objective) > 220:
        objective = objective[:217].rstrip() + "..."
    return (
        f"Goal started: {objective}\n\n"
        "I'm working on it now. This may take a bit; I'll send progress or the final result here, "
        "and I'll ask for approval if a step needs it."
    )


def _extract_agent_result(result: Any) -> tuple[str, Any | None]:
    if isinstance(result, tuple):
        answer = str(result[0] or "") if result else ""
        interrupt_data = result[1] if len(result) > 1 else None
        return answer, interrupt_data
    return str(result or ""), None


def _model_override_from_config(config: dict | None) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("model_override") or "")


def thread_id_from_config(config: dict | None) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id") or "").strip()


def resolve_goal_approval_for_config(config: dict | None, approved: bool) -> bool:
    """Update a waiting channel Goal before resuming an approval interrupt."""

    thread_id = thread_id_from_config(config)
    if not thread_id:
        return False
    try:
        from row_bot import goals

        goal = goals.get_current_goal(thread_id, include_terminal=True)
        if not goal or str(goal.get("status") or "") != "waiting_approval":
            return False
        if approved:
            goals.resume_goal(thread_id)
        else:
            goals.block_goal(thread_id, reason="Approval was denied by the user.")
        return True
    except Exception:
        return False


def _turn_id(channel_name: str, thread_id: str) -> str:
    return f"channel:{channel_name}:{thread_id}:{uuid.uuid4().hex[:12]}"


def _after_goal_turn(
    *,
    channel_name: str,
    thread_id: str,
    config: dict | None,
    assistant_text: str,
    interrupt_data: Any | None,
):
    from row_bot import goals

    return goals.after_turn(
        thread_id=thread_id,
        turn_id=_turn_id(channel_name, thread_id),
        assistant_text=assistant_text,
        model_override=_model_override_from_config(config),
        pending_approval=bool(interrupt_data),
    )


def run_channel_goal_sync(
    *,
    channel_name: str,
    thread_id: str,
    config: dict,
    first_prompt: str,
    run_turn: Callable[[str, dict], Any],
    send_text: Callable[[str], Any],
    max_continuations: int = 20,
) -> ChannelGoalRunResult:
    """Run a channel Goal start plus internal continuations synchronously."""

    prompt = first_prompt
    turns = 0
    status = "active"
    reason = ""
    interrupt_data: Any | None = None
    while prompt and turns < max(1, int(max_continuations or 20)):
        turns += 1
        result = run_turn(prompt, config)
        answer, interrupt_data = _extract_agent_result(result)
        if answer:
            send_text(answer)
        decision = _after_goal_turn(
            channel_name=channel_name,
            thread_id=thread_id,
            config=config,
            assistant_text=answer,
            interrupt_data=interrupt_data,
        )
        status = decision.status or status
        reason = decision.reason or reason
        if interrupt_data or not decision.should_continue or not decision.continuation_prompt:
            return ChannelGoalRunResult(
                turns=turns,
                status=status,
                reason=reason,
                interrupt_data=interrupt_data,
            )
        prompt = decision.continuation_prompt
    return ChannelGoalRunResult(turns=turns, status=status, reason=reason)


def continue_channel_goal_after_turn_sync(
    *,
    channel_name: str,
    thread_id: str,
    config: dict,
    assistant_text: str,
    interrupt_data: Any | None,
    run_turn: Callable[[str, dict], Any],
    send_text: Callable[[str], Any],
    max_continuations: int = 20,
) -> ChannelGoalRunResult:
    """Evaluate a just-finished channel turn and continue the Goal if needed."""

    decision = _after_goal_turn(
        channel_name=channel_name,
        thread_id=thread_id,
        config=config,
        assistant_text=assistant_text,
        interrupt_data=interrupt_data,
    )
    if interrupt_data or not decision.should_continue or not decision.continuation_prompt:
        return ChannelGoalRunResult(
            turns=0,
            status=decision.status,
            reason=decision.reason,
            interrupt_data=interrupt_data,
        )
    return run_channel_goal_sync(
        channel_name=channel_name,
        thread_id=thread_id,
        config=config,
        first_prompt=decision.continuation_prompt,
        run_turn=run_turn,
        send_text=send_text,
        max_continuations=max_continuations,
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def run_channel_goal_async(
    *,
    channel_name: str,
    thread_id: str,
    config: dict,
    first_prompt: str,
    run_turn: Callable[[str, dict], Awaitable[Any] | Any],
    send_text: Callable[[str], Awaitable[Any] | Any],
    max_continuations: int = 20,
) -> ChannelGoalRunResult:
    """Run a channel Goal start plus internal continuations asynchronously."""

    prompt = first_prompt
    turns = 0
    status = "active"
    reason = ""
    interrupt_data: Any | None = None
    while prompt and turns < max(1, int(max_continuations or 20)):
        turns += 1
        result = await _maybe_await(run_turn(prompt, config))
        answer, interrupt_data = _extract_agent_result(result)
        if answer:
            await _maybe_await(send_text(answer))
        decision = _after_goal_turn(
            channel_name=channel_name,
            thread_id=thread_id,
            config=config,
            assistant_text=answer,
            interrupt_data=interrupt_data,
        )
        status = decision.status or status
        reason = decision.reason or reason
        if interrupt_data or not decision.should_continue or not decision.continuation_prompt:
            return ChannelGoalRunResult(
                turns=turns,
                status=status,
                reason=reason,
                interrupt_data=interrupt_data,
            )
        prompt = decision.continuation_prompt
    return ChannelGoalRunResult(turns=turns, status=status, reason=reason)


async def continue_channel_goal_after_turn_async(
    *,
    channel_name: str,
    thread_id: str,
    config: dict,
    assistant_text: str,
    interrupt_data: Any | None,
    run_turn: Callable[[str, dict], Awaitable[Any] | Any],
    send_text: Callable[[str], Awaitable[Any] | Any],
    max_continuations: int = 20,
) -> ChannelGoalRunResult:
    """Evaluate a just-finished async channel turn and continue the Goal."""

    decision = _after_goal_turn(
        channel_name=channel_name,
        thread_id=thread_id,
        config=config,
        assistant_text=assistant_text,
        interrupt_data=interrupt_data,
    )
    if interrupt_data or not decision.should_continue or not decision.continuation_prompt:
        return ChannelGoalRunResult(
            turns=0,
            status=decision.status,
            reason=decision.reason,
            interrupt_data=interrupt_data,
        )
    return await run_channel_goal_async(
        channel_name=channel_name,
        thread_id=thread_id,
        config=config,
        first_prompt=decision.continuation_prompt,
        run_turn=run_turn,
        send_text=send_text,
        max_continuations=max_continuations,
    )
