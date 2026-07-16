"""Checkpoint-safe model-iteration budgets for Row-Bot's full agent loops."""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Mapping, NotRequired, TypedDict

from langgraph.prebuilt.chat_agent_executor import AgentState
from langchain_core.runnables import RunnableConfig

from row_bot.agent_settings import AgentRuntimeSettings, load_agent_runtime_settings


class ExecutionBudgetState(TypedDict):
    schema_version: int
    budget_id: str
    logical_turn_id: str
    max_iterations: int
    used_iterations: int
    finalization_started: bool
    finalization_completed: bool
    terminal_reason: str


class RowBotAgentState(AgentState):
    execution_budget: ExecutionBudgetState
    llm_input_messages: NotRequired[list[Any]]


class ExecutionBudgetError(RuntimeError):
    """Base class for authoritative agent-budget terminal conditions."""

    def __init__(self, message: str, budget: ExecutionBudgetState | None = None) -> None:
        super().__init__(message)
        self.budget = budget


class ExecutionBudgetExhausted(ExecutionBudgetError):
    pass


class AgentNoProgress(ExecutionBudgetError):
    pass


class InvalidExecutionBudget(ExecutionBudgetError):
    pass


@dataclass(frozen=True)
class ExecutionBudget:
    """Small constructor used by runtime plumbing and deterministic tests."""

    max_iterations: int
    logical_turn_id: str
    budget_id: str = ""

    def to_state(self) -> ExecutionBudgetState:
        maximum = _strict_nonnegative_int(self.max_iterations, "max_iterations", positive=True)
        logical_turn_id = str(self.logical_turn_id or "").strip()
        if not logical_turn_id:
            raise InvalidExecutionBudget("logical_turn_id is required")
        return {
            "schema_version": 1,
            "budget_id": str(self.budget_id or secrets.token_urlsafe(18)),
            "logical_turn_id": logical_turn_id,
            "max_iterations": maximum,
            "used_iterations": 0,
            "finalization_started": False,
            "finalization_completed": False,
            "terminal_reason": "",
        }


_ACTIVE_BUDGET_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "row_bot_execution_budget_id",
    default="",
)
_REGISTRY_LOCK = threading.RLock()
_PROCESS_DIGEST_KEY = secrets.token_bytes(32)
_REPEAT_REGISTRY: dict[str, tuple[bytes, int, bool]] = {}
_FINALIZATION_CLAIMS: set[str] = set()


def _strict_nonnegative_int(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidExecutionBudget(f"{field} must be an integer")
    if value < 0 or (positive and value == 0):
        raise InvalidExecutionBudget(f"{field} is outside its valid range")
    return value


def new_execution_budget(
    logical_turn_id: str,
    settings: AgentRuntimeSettings | None = None,
) -> ExecutionBudgetState:
    effective = settings or load_agent_runtime_settings()
    return ExecutionBudget(
        max_iterations=effective.max_iterations,
        logical_turn_id=str(logical_turn_id or secrets.token_urlsafe(12)),
    ).to_state()


def validate_execution_budget(value: Mapping[str, Any] | None) -> ExecutionBudgetState:
    if not isinstance(value, Mapping):
        raise InvalidExecutionBudget("Missing checkpointed execution budget")
    if value.get("schema_version") != 1:
        raise InvalidExecutionBudget("Unsupported execution budget schema")
    budget_id = str(value.get("budget_id") or "").strip()
    logical_turn_id = str(value.get("logical_turn_id") or "").strip()
    if not budget_id or not logical_turn_id:
        raise InvalidExecutionBudget("Execution budget identity is missing")
    maximum = _strict_nonnegative_int(value.get("max_iterations"), "max_iterations", positive=True)
    used = _strict_nonnegative_int(value.get("used_iterations"), "used_iterations")
    if used > maximum:
        raise InvalidExecutionBudget("Execution budget usage exceeds its maximum")
    started = value.get("finalization_started")
    completed = value.get("finalization_completed")
    if not isinstance(started, bool) or not isinstance(completed, bool):
        raise InvalidExecutionBudget("Execution budget finalization flags are invalid")
    if completed and not started:
        raise InvalidExecutionBudget("Completed finalization must have been started")
    terminal_reason = value.get("terminal_reason", "")
    if not isinstance(terminal_reason, str):
        raise InvalidExecutionBudget("Execution budget terminal reason is invalid")
    return {
        "schema_version": 1,
        "budget_id": budget_id,
        "logical_turn_id": logical_turn_id,
        "max_iterations": maximum,
        "used_iterations": used,
        "finalization_started": started,
        "finalization_completed": completed,
        "terminal_reason": terminal_reason,
    }


def remaining_iterations(value: Mapping[str, Any]) -> int:
    budget = validate_execution_budget(value)
    return max(0, budget["max_iterations"] - budget["used_iterations"])


def framework_recursion_limit(remaining: int) -> int:
    remaining = _strict_nonnegative_int(remaining, "remaining")
    return 4 * remaining + 8


def current_execution_budget_id() -> str:
    return _ACTIVE_BUDGET_ID.get("")


def activate_execution_budget(value: Mapping[str, Any]) -> ExecutionBudgetState:
    budget = validate_execution_budget(value)
    _ACTIVE_BUDGET_ID.set(budget["budget_id"])
    return budget


def pre_model_budget_hook(
    state: Mapping[str, Any],
    config: RunnableConfig = None,
) -> ExecutionBudgetState:
    """Validate capacity immediately before a provider model call."""

    del config
    budget = activate_execution_budget(state.get("execution_budget"))
    with _REGISTRY_LOCK:
        repeat = _REPEAT_REGISTRY.get(budget["budget_id"])
        no_progress_pending = bool(repeat and repeat[2])
    if no_progress_pending or budget["terminal_reason"] == "no_progress":
        budget["terminal_reason"] = "no_progress"
        raise AgentNoProgress("The same tool action repeated without progress", budget)
    if budget["used_iterations"] >= budget["max_iterations"]:
        budget["terminal_reason"] = "budget_exhausted"
        raise ExecutionBudgetExhausted("The model-iteration budget is exhausted", budget)
    return budget


def post_model_budget_hook(
    state: Mapping[str, Any],
    config: RunnableConfig = None,
) -> dict[str, ExecutionBudgetState]:
    """Charge exactly one successful provider response to the logical turn."""

    budget = activate_execution_budget(state.get("execution_budget"))
    if budget["used_iterations"] >= budget["max_iterations"]:
        budget["terminal_reason"] = "budget_exhausted"
        raise ExecutionBudgetExhausted("The model-iteration budget is exhausted", budget)
    budget["used_iterations"] += 1
    configurable = dict((config or {}).get("configurable") or {})
    run_id = str(configurable.get("agent_run_id") or "")
    if run_id:
        try:
            from row_bot.agent_runs import update_agent_budget_progress

            update_agent_budget_progress(
                run_id,
                used_iterations=budget["used_iterations"],
                max_iterations=budget["max_iterations"],
            )
        except (ImportError, AttributeError):
            pass
    return {"execution_budget": budget}


def _call_digest(tool_name: str, arguments: Mapping[str, Any] | None) -> bytes:
    payload = json.dumps(
        [str(tool_name or ""), dict(arguments or {})],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hmac.new(_PROCESS_DIGEST_KEY, payload, hashlib.sha256).digest()


def register_exact_tool_request(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    budget_id: str = "",
) -> str:
    """Return ``allow``, ``block``, or ``terminal`` without persisting inputs."""

    active_id = str(budget_id or current_execution_budget_id() or "")
    if not active_id:
        return "allow"
    digest = _call_digest(tool_name, arguments)
    with _REGISTRY_LOCK:
        previous = _REPEAT_REGISTRY.get(active_id)
        count = previous[1] + 1 if previous and hmac.compare_digest(previous[0], digest) else 1
        terminal = bool(previous and previous[2])
        if count >= 5 or terminal:
            _REPEAT_REGISTRY[active_id] = (digest, count, True)
            return "terminal"
        if count == 4:
            _REPEAT_REGISTRY[active_id] = (digest, count, False)
            return "block"
        _REPEAT_REGISTRY[active_id] = (digest, count, False)
        return "allow"


def exact_repeat_block_payload(*, terminal: bool = False) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": True,
            "error_code": "no_progress" if terminal else "repeated_action_blocked",
            "retryable": False,
            "display_summary": (
                "The repeated action was stopped because it made no progress."
                if terminal
                else "The fourth identical action was blocked; choose a different method or finish."
            ),
        },
        separators=(",", ":"),
    )


def claim_budget_finalization(value: Mapping[str, Any]) -> tuple[bool, ExecutionBudgetState]:
    budget = validate_execution_budget(value)
    with _REGISTRY_LOCK:
        if budget["finalization_started"] or budget["budget_id"] in _FINALIZATION_CLAIMS:
            budget["finalization_started"] = True
            return False, budget
        _FINALIZATION_CLAIMS.add(budget["budget_id"])
        budget["finalization_started"] = True
        return True, budget


def complete_budget_finalization(
    value: Mapping[str, Any],
    terminal_reason: str,
) -> ExecutionBudgetState:
    budget = validate_execution_budget(value)
    budget["finalization_started"] = True
    budget["finalization_completed"] = True
    budget["terminal_reason"] = str(terminal_reason or "")
    clear_execution_budget_runtime(budget["budget_id"])
    return budget


def clear_execution_budget_runtime(budget_id: str) -> None:
    with _REGISTRY_LOCK:
        _REPEAT_REGISTRY.pop(str(budget_id or ""), None)
        _FINALIZATION_CLAIMS.discard(str(budget_id or ""))


def reset_agent_budget_test_state() -> None:
    """Clear process-local registries for deterministic tests."""

    with _REGISTRY_LOCK:
        _REPEAT_REGISTRY.clear()
        _FINALIZATION_CLAIMS.clear()
    _ACTIVE_BUDGET_ID.set("")
