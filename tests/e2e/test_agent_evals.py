"""Opt-in live smokes of Row-Bot as a benchmark's agent (lane E): one tau2-bench airline task, and one toy
file task through the in-container worker run on this machine.

They use the models you name, so they never run by default:

    ROW_BOT_BENCH=1 ROW_BOT_BENCH_SMOKE_MODEL=codex:gpt-6.1-sol ROW_BOT_BENCH_TAU2_DIR=<tau2-bench v1.0.1 checkout
    with its .venv> ROW_BOT_LIVE_EVAL_CODEX_PROFILE=<eval profile> ROW_BOT_BENCH_BOOKING_FILE=<booking file> \\
    uv run python -m pytest tests/e2e/test_agent_evals.py -m e2e

The eval profile is booked first, like every other bench run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.e2e.bench import orchestrate
from tests.e2e.bench.agent_evals import cli

pytestmark = pytest.mark.e2e

MODEL_ENV = "ROW_BOT_BENCH_SMOKE_MODEL"


def _skip_unless_live(*names: str) -> None:
    missing = [name for name in (MODEL_ENV, *names) if not os.environ.get(name)]
    if not orchestrate.enabled() or missing:
        pytest.skip(f"set ROW_BOT_BENCH=1 and {', '.join(missing) or MODEL_ENV} to run the live smoke")


def test_one_tau2_airline_task_is_played_and_graded_with_row_bot_as_the_agent(tmp_path) -> None:
    _skip_unless_live(cli.TAU2_DIR_ENV)
    model = os.environ[MODEL_ENV]
    run_dir = tmp_path / "run"
    code = cli.main(["tau2", "run", "--domain", "airline", "--split", "train", "--task-ids", "0", "--model", model,
                     "--user-model", model, "--user-effort", "low", "--run-dir", str(run_dir)])
    assert code == 0
    results = json.loads((run_dir / "tau2" / "results.json").read_text(encoding="utf-8"))
    (simulation,) = results["simulations"]
    assert simulation["termination_reason"] not in {"agent_error", "infrastructure_error"}
    assert any(message.get("tool_calls") for message in simulation["messages"])  # Row-Bot used the tau2 tools


def test_the_in_container_worker_does_a_file_task_and_leaves_pawbenchs_transcript(tmp_path) -> None:
    _skip_unless_live()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sales.csv").write_text("region,total\nEU,120\nUS,80\n", encoding="utf-8")
    instruction = tmp_path / "instruction.txt"
    instruction.write_text("Read sales.csv and write report.md with the regions sorted by total.", encoding="utf-8")
    code = subprocess.run([sys.executable, "-m", "tests.e2e.bench.agent_evals.container_task", "--benchmark",
                           "pawbench", "--instruction-file", str(instruction), "--workspace", str(workspace),
                           "--model", os.environ[MODEL_ENV], "--out", str(tmp_path / "out")],
                          cwd=orchestrate.REPO_ROOT, check=False).returncode
    assert code == 0
    assert (workspace / "report.md").is_file()
    (session,) = (workspace / "sessions").glob("rowbot-*.jsonl")
    lines = [json.loads(line) for line in session.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "session" and {line["message"]["role"] for line in lines[1:]} >= {"user", "assistant"}
    assert json.loads((tmp_path / "out" / "atif.json").read_text(encoding="utf-8"))["steps"]
