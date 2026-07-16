from __future__ import annotations

from row_bot.agent_budget import (
    ExecutionBudget,
    RowBotAgentState,
    framework_recursion_limit,
    remaining_iterations,
)


def test_checkpoint_state_is_narrow_json_safe_and_capacity_is_explicit() -> None:
    state = ExecutionBudget(max_iterations=3, logical_turn_id="turn-1", budget_id="budget-1").to_state()

    assert state == {
        "schema_version": 1,
        "budget_id": "budget-1",
        "logical_turn_id": "turn-1",
        "max_iterations": 3,
        "used_iterations": 0,
        "finalization_started": False,
        "finalization_completed": False,
        "terminal_reason": "",
    }
    assert remaining_iterations(state) == 3
    assert framework_recursion_limit(3) == 20
    assert {"messages", "remaining_steps", "execution_budget"} <= set(
        RowBotAgentState.__annotations__
    )


def test_framework_limit_reserves_eight_nodes_after_four_per_remaining_round() -> None:
    assert [framework_recursion_limit(value) for value in range(4)] == [8, 12, 16, 20]
