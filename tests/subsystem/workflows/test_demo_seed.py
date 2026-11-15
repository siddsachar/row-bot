"""The docs and demo capture seed its workflows through the app's own workflow API."""

from __future__ import annotations

from pathlib import Path

from scripts.docs import seed_real_app_demo_data as seeder


def test_the_demo_seed_creates_its_workflows(tmp_path: Path, reload_for_data_dir) -> None:
    from row_bot.docs_capture import default_docs_capture_demo_state

    (tasks,) = reload_for_data_dir(tmp_path / "data", "row_bot.tasks")
    state = default_docs_capture_demo_state()

    seeder._seed_workflows(state)

    by_name = {task["name"]: task for task in tasks.list_tasks()}
    launch = by_name["Launch Summary"]
    assert launch["safety_mode"] == "approve"
    message = "\n".join(launch["prompts"])
    assert "Draft the launch summary." in message
    assert "Write the approved summary to the demo workspace." in message
    assert {"Morning Brief", "Research Digest", "Weekly Tidy-up", "Garden Reminder"} <= set(by_name)
    assert state["workflows"][1]["id"] == launch["id"]
