"""Opt-in live memory benchmark smoke: a few LoCoMo questions through Row-Bot's real memory path.

It uses the reader model you name, so it never runs by default:

    ROW_BOT_BENCH=1 ROW_BOT_BENCH_SMOKE_MODEL=codex:auto \\
    ROW_BOT_LIVE_EVAL_CODEX_PROFILE=<eval profile> ROW_BOT_BENCH_BOOKING_FILE=<booking file> \\
    ROW_BOT_BENCH_LOCOMO_DIR=<folder with data__locomo10.json, errors.json, evaluation__config__prompts.yaml> \\
    uv run python -m pytest tests/e2e/test_memory_bench.py -m e2e

The run lands in ``.tmp/bench/`` like any other (``scripts/bench.py``); the eval profile and
the graphics card are booked first.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e.bench import orchestrate

pytestmark = pytest.mark.e2e

MODEL_ENV = "ROW_BOT_BENCH_SMOKE_MODEL"
DATA_ENV = "ROW_BOT_BENCH_LOCOMO_DIR"


def test_a_locomo_smoke_answers_and_judges_through_the_real_memory_path(tmp_path, monkeypatch) -> None:
    if not orchestrate.enabled() or not os.environ.get(MODEL_ENV) or not os.environ.get(DATA_ENV):
        pytest.skip(f"set ROW_BOT_BENCH=1, {MODEL_ENV} and {DATA_ENV} to run the live smoke")
    folder = Path(os.environ[DATA_ENV])
    model = os.environ[MODEL_ENV]
    run_dir = tmp_path / "run"
    code = orchestrate.main([
        "run", "--benchmark", "locomo", "--data", str(folder / "data__locomo10.json"),
        "--key", str(folder / "errors.json"), "--protocol", str(folder / "evaluation__config__prompts.yaml"),
        "--model", model, "--conversations", "0", "--questions", "6", "--run-dir", str(run_dir), "--judge", model,
    ])
    assert code == 0
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    assert scores["questions"]["asked"] == 6 and scores["questions"]["not_done"] == 0
    assert scores["ingestion"]["memories_saved"] > 0
    assert {item["key"] for item in scores["scores"]} == {"original", "audited"}
