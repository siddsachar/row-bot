"""Goal-turn harness: the goals module on a test data folder, and a fake verifier per turn.

    goals = goal_modules(tmp_path, reload_for_data_dir)
    goal = goals.start_goal("thread", "Draft the launch post")
    judge(goals, "thread", 1, progress="blocked", reason="Needs the launch date.")

``judge`` runs ``after_turn`` with a verifier that returns the given verdict fields, so no model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def goal_modules(tmp_path: Path, reload_for_data_dir) -> Any:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    _tasks, _runs, goals = reload_for_data_dir(data_dir, "row_bot.tasks", "row_bot.agent_runs", "row_bot.goals")
    return goals


def judge(goals: Any, thread: str, turn: int, *, assistant_text: str = "", **verdict: Any) -> Any:
    return goals.after_turn(
        thread_id=thread,
        turn_id=f"turn-{turn}",
        assistant_text=assistant_text,
        verifier=lambda _goal, _context: verdict,
    )
