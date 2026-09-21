import json
from pathlib import Path

import pytest

from scripts.marketing.capture_run import (
    CaptureSafetyError,
    GenerationBudget,
    GenerationBudgetError,
    RunReceipt,
    find_profile_mutators,
    require_contained,
    require_exact_normal_profile,
)


class _Process:
    def __init__(self, pid: int, command: list[str], environment: dict[str, str]):
        self.info = {"pid": pid, "name": "python.exe", "cmdline": command}
        self._environment = environment

    def environ(self) -> dict[str, str]:
        return self._environment


def _receipt() -> RunReceipt:
    return RunReceipt(
        run_id="20260921T120000Z-a1b2c3",
        story_id="canonical-launch-story",
        prompt_version=1,
        git_commit="46d88892",
        client="nicegui",
        models={"local": "model:ollama:qwen3.8:27b"},
        max_generation_attempts=6,
    )


def test_real_profile_authorization_is_exact(tmp_path: Path) -> None:
    normal = tmp_path / "normal"
    normal.mkdir()
    assert require_exact_normal_profile(normal, authorize=True, normal_profile=normal) == normal
    with pytest.raises(CaptureSafetyError, match="requires --authorize-real-profile"):
        require_exact_normal_profile(normal, authorize=False, normal_profile=normal)
    with pytest.raises(CaptureSafetyError, match="exact normal"):
        require_exact_normal_profile(tmp_path / "copy", authorize=True, normal_profile=normal)


def test_profile_quiescence_ignores_other_profiles_and_owns_no_processes(tmp_path: Path) -> None:
    normal = tmp_path / "normal"
    other = tmp_path / "other"
    processes = [
        _Process(11, ["python", "app.py"], {}),
        _Process(12, ["python", "app.py"], {"ROW_BOT_DATA_DIR": str(other)}),
        _Process(13, ["python", "capture_landing_story.py"], {}),
    ]
    assert find_profile_mutators(normal, processes=processes, current_pid=99) == [
        {"pid": 11, "name": "python.exe"}
    ]


def test_generation_budget_is_bounded_and_uncertain_is_terminal() -> None:
    budget = GenerationBudget(2)
    first = budget.begin(model="model:ollama:qwen", purpose="research")
    budget.finish(first, status="succeeded", conversation_id="thread-1")
    second = budget.begin(model="model:codex:sol", purpose="campaign")
    budget.finish(second, status="uncertain", operation_id="op-2")
    with pytest.raises(GenerationBudgetError, match="terminal status uncertain"):
        budget.begin(model="model:ollama:qwen", purpose="retry")
    assert [item["status"] for item in budget.attempts] == ["succeeded", "uncertain"]


def test_output_containment_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    assert require_contained(root / "raw" / "scene.png", root).is_relative_to(root)
    with pytest.raises(CaptureSafetyError, match="escapes"):
        require_contained(root / ".." / "private.txt", root)


def test_receipt_is_public_safe_and_round_trips(tmp_path: Path) -> None:
    receipt = _receipt()
    receipt.records = {"campaign": "thread-123"}
    receipt.sources = ["https://row-bot.ai/"]
    receipt.write(tmp_path)
    raw = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert raw["profile"] == "normal"
    assert "profile_path" not in raw
    assert RunReceipt.read(tmp_path).records == receipt.records

    receipt.sources = [r"C:\Users\private\notes.txt"]
    with pytest.raises(CaptureSafetyError, match="private-data"):
        receipt.write(tmp_path)
